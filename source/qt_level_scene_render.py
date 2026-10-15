"""Depth-tested level mesh viewport for copied CryEngine map projects.

Only proven CrChF static streams are rendered. Decoding and DDS preparation
run in a bounded background thread; OpenGL calls are exclusively on the Qt
context thread. No terrain, collision or navmesh is synthesized.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import math
import time
from pathlib import Path, PurePosixPath
from queue import Empty, Queue
from threading import Event, Thread
from performance_trace import span, record

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QWidget

SUPPORTED_STATIC = frozenset((".cgf", ".skinm", ".chrm", ".cgam", ".cgfm"))
MAX_MESH_FILE = 32 * 1024 * 1024
MAX_SCENE_MEMORY = 240 * 1024 * 1024
MAX_RENDER_DISTANCE = 20000.0


@dataclass(frozen=True)
class RenderAsset:
    mesh: object
    geometry: object
    materials: object
    size: int
    model_file: str
    bounds: tuple


def _asset_key(item):
    return item.model.casefold(), item.material.casefold()


def look_at_matrix(eye, center):
    """OpenGL column-major camera transform without a system GLU dependency."""
    import numpy as np
    a=np.asarray(eye,dtype=np.float64)
    c=np.asarray(center,dtype=np.float64)
    forward=c-a
    length=np.linalg.norm(forward)
    if length<1e-8:
        raise ValueError("Camera eye and target overlap.")
    forward/=length
    side=np.cross(forward,(0.,0.,1.))
    norm=np.linalg.norm(side)
    if norm<1e-8:
        side=np.asarray((1.,0.,0.),dtype=np.float64)
    else:
        side/=norm
    up=np.cross(side,forward)
    matrix=np.eye(4,dtype=np.float64)
    matrix[0,:3]=side
    matrix[1,:3]=up
    matrix[2,:3]=-forward
    matrix[0,3]=-np.dot(side,a)
    matrix[1,3]=-np.dot(up,a)
    matrix[2,3]=np.dot(forward,a)
    return np.ascontiguousarray(matrix.T,dtype=np.float32)


def prepare_scene(project, cancel, progress=None, *, game=None, stage=None, cache_root=None,
                  terrain_stride=2, preview=True, partial=None):
    """CPU-only preparation. Return shared geometry and explicit diagnostics."""
    from level_asset_mount import mount_map_index, resolve_scoped
    from model_preview import read_preview_mesh
    from gpu_model_preview import prepare_geometry
    from material_preview import load_preview_materials

    dependency = {}
    if game is not None and stage is not None and cache_root is not None:
        from level_dependency_loader import ensure_map_archives
        try:
            dependency = ensure_map_archives(
                project, game, stage, cache_root, cancel=cancel,
                progress=(lambda label, current, total:
                          progress(label, current, total)) if progress else None)
        except (OSError, ValueError, RuntimeError) as exc:
            dependency = {"errors": ["Dependency import: " + str(exc)[:200]]}
    if cancel.is_set():
        return None
    index, details = mount_map_index(project.workspace, project.level_name,
                                     cache_root=cache_root)
    from level_map_assembly import assemble_map_scene
    scene, assembly_issues = assemble_map_scene(project, index, cancel=cancel, progress=(
        lambda label,current,total: progress(label,current,total)) if progress else None)
    details["scene"] = scene
    details["assembly_diagnostics"] = assembly_issues
    details["dependencies"] = dependency
    material_names = sorted(
        (name for name in index if name.endswith(".mtl")), key=str.casefold)
    details["materials"] = tuple(material_names[:20000])
    details["material_catalog_truncated"] = len(material_names) > 20000
    assets, issues, models = {}, Counter(), {}
    details["terrain"] = None
    # Make the complete placement scene available to the GUI before
    # optional terrain/atlas decoding or material residency. Qt publishes
    # generation-checked results and keeps this a pending scene, not a
    # fully textured one. Never change source/export fidelity.
    if partial and not cancel.is_set():
        partial({}, dict(issues), dict(details, pending=True))
    height_source = "levels/" + str(project.level_name) + "/leveldata/heightmap.dat"
    source_record, terrain_state = resolve_scoped(
        index, height_source, project.workspace)
    if source_record is not None:
        from level_terrain_decode import decode_terrain, MAX_SOURCE_BYTES
        try:
            source_file = Path(source_record["file"])
            if source_file.is_symlink() or not 0 < source_file.stat().st_size <= MAX_SOURCE_BYTES:
                raise ValueError("Unsafe or oversized source terrain payload")
            from level_terrain_decode import decode_terrain_chunks
            raw = source_file.read_bytes()
            source = str(source_record.get("relative") or height_source)
            from level_terrain_decode import _samples
            _,width,height,_,_ = _samples(raw)
            actual_stride = terrain_stride if preview else 1
            if preview:
                while width*height*56/(actual_stride**2) > 64*1024*1024 and actual_stride < 8:
                    actual_stride *= 2
                if actual_stride != terrain_stride:
                    issues['terrain detail reduced to fit 64 MiB preview budget'] += 1
            chunks = tuple(decode_terrain_chunks(raw, source=source,
                stride=actual_stride, cancel=cancel))
            details["terrain_chunks"] = chunks
            details["terrain"] = chunks[0] if chunks else None
        except (OSError, ValueError) as exc:
            issues["Terrain HeightmapData: " + str(exc)[:180]] += 1
    elif "terrain/terrain.dat" in index:
        issues["compiled terrain present, but no verified source Heightmap.dat"] += 1
    else:
        issues["terrain source " + terrain_state + ": " + height_source] += 1
    from level_terrain_tiles import load_tile_atlas
    try:
        details["terrain_atlas"] = load_tile_atlas(index, project.workspace, cancel=cancel)
        if details["terrain_atlas"] is not None:
            issues["terrain tile mapping/channel order unverified; opt-in preview only"] += 1
    except (OSError, ValueError) as exc:
        issues["Terrain tiles: " + str(exc)[:160]] += 1
    candidates = {_asset_key(o): o for o in scene if o.model}
    def decode(obj, models):
        extension = PurePosixPath(obj.model).suffix.casefold()
        if extension not in SUPPORTED_STATIC:
            raise ValueError("unsupported geometry: " + extension)
        record, status = resolve_scoped(index, obj.model, project.workspace)
        if record is None:
            raise ValueError("model " + status)
        path = Path(record["file"])
        if not 0 < path.stat().st_size <= MAX_MESH_FILE:
            raise ValueError("mesh exceeds decoder bounds")
        cache_key = str(path.resolve())
        if cache_key in models:
            mesh, geometry = models[cache_key]
        else:
            mesh = read_preview_mesh(path.read_bytes(), obj.model)
            geometry = prepare_geometry(mesh, "smooth")
        try:
            materials = load_preview_materials(
                project.workspace, obj.model, index=index,
                material_override=obj.material or None, texture_quality="balanced")
        except (OSError, ValueError) as exc:
            materials = None
            issues["material " + obj.model + ": " + str(exc)[:120]] += 1
        lower = tuple(float(v) for v in geometry.vertices.min(axis=0))
        upper = tuple(float(v) for v in geometry.vertices.max(axis=0))
        return RenderAsset(mesh, geometry, materials, 0, cache_key, (lower,upper))
    from level_residency import SceneResidency
    # Export decodes original resources independently, one at a time below.
    from level_residency import retained_bytes
    terrain_cost = retained_bytes(details.get('terrain_chunks', ()))
    atlas = details.get('terrain_atlas')
    terrain_cost += atlas.width*atlas.height*4 if atlas is not None else 0
    store = SceneResidency(candidates, decode, max(0,MAX_SCENE_MEMORY-terrain_cost))
    details["residency"] = store
    if preview:
        points = [obj.world.pos for obj in scene]
        center = tuple(sorted(p[i] for p in points)[len(points)//2] for i in range(3)) if points else (0,0,0)
        ordered = sorted(candidates, key=lambda k: sum((candidates[k].world.pos[i]-center[i])**2 for i in range(3)))
        assets = store.load(ordered, cancel, progress)
        if assets is None:
            return None
        issues.update({key[0] + ': ' + message: 1 for key,message in store.failures.items()})
    else:
        assets = {}
    if progress:
        progress(len(assets), len(candidates))
    return assets, dict(issues), details


class LevelGLViewport(QOpenGLWidget):
    """Render multiple world-space instances without per-model normalization."""
    selected = Signal(str, int)
    moved = Signal(str, int, object)
    statusChanged = Signal(str)
    materialsReady = Signal(object)
    progressChanged = Signal(object)
    _workerProgress = Signal(object)

    def __init__(self, project, parent=None, *, game=None, stage=None, cache_root=None):
        super().__init__(parent)
        fmt = QSurfaceFormat()
        fmt.setRenderableType(QSurfaceFormat.OpenGL)
        fmt.setVersion(2, 1)
        fmt.setProfile(QSurfaceFormat.CompatibilityProfile)
        fmt.setDepthBufferSize(24)
        self.setFormat(fmt)
        self.setMinimumSize(300, 260)
        self.setFocusPolicy(Qt.StrongFocus)
        self.project = project
        self.scene = project.scene
        self.game_root = game
        self.stage_root = stage
        self.cache_root = cache_root
        self.terrain_stride = 2
        self.mode = "select"
        self.active = None
        self.yaw = .6
        self.pitch = .6
        self.camera = [0., 0., 0.]
        self.distance = 500.
        self.projection_mode = "Perspective"
        self.assets = {}
        self.terrain = None
        self.terrain_chunks = ()
        self.terrain_atlas = None
        self.show_terrain_tiles = False
        self._textures = {}
        self._residency = None
        self._streaming = False
        self._preparing = True
        self._last_request = None
        self._placement_index = None
        self._placement_scene = None
        self._upload_remaining = 0
        self._upload_bytes_remaining = 0
        self._upload_bytes_frame = 0
        self._terrain_uvs = {}
        self._terrain_texture = 0
        self._cancel = Event()
        self._results = Queue(maxsize=2)
        self._generation = 1
        self._workerProgress.connect(self._apply_progress)
        self._mouse = None
        self._dragged = False
        self._press_origin = None
        self._preview = None
        self._lookup = {}
        for item in project.objects:
            node = project._node(item.layer, item.index)
            guid = (node.get("Id") or node.get("EntityId") or f"{item.layer}#{item.index}").casefold()
            self._lookup.setdefault(guid, (item.layer, item.index))
        self._visible = ()
        self._issues = {}
        self._dependency_report = {}
        self.frame_all()
        self._poll = QTimer(self)
        self._poll.timeout.connect(self._receive)
        self._poll.start(80)
        token, cancel, results = self._generation, self._cancel, self._results

        def job():
            try:
                def report(*args):
                    self._report_progress(token, cancel, args)
                with span('map', 'initial_scene_prepare', generation=token):
                    value = prepare_scene(project, cancel, report,
                                          game=self.game_root, stage=self.stage_root,
                                          cache_root=self.cache_root, terrain_stride=self.terrain_stride,
                                          partial=lambda a,i,d: results.put((token,(a,i,d))))
            except Exception as exc:
                value = (None, {"scene initialization: " + str(exc)[:160]: 1}, {})
            if not cancel.is_set():
                results.put((token, value))
        self._loader = Thread(target=job, name="Evolve-Level-CPU-Loader", daemon=True)
        self._loader.start()
        self.statusChanged.emit("Preparing map meshes and textures in background…")

    def _report_progress(self, token, cancel, args):
        if cancel.is_set():
            return
        from level_progress import LoadProgress
        if len(args) == 3:
            label, current, total = args
            phase = ('placements' if label.startswith('Parsing') else
                     'verification' if label.startswith('Scanning') else
                     'import' if label.startswith('Importing') else 'dependencies')
        else:
            current,total = args
            phase,label = 'decode','Preparing visible resources'
        self._workerProgress.emit(LoadProgress(token,phase,current,total,label))

    def _apply_progress(self, event):
        if event.generation != self._generation or self._cancel.is_set():
            return
        self.progressChanged.emit(event)
        self.statusChanged.emit(event.label)

    def cancel_loading(self):
        self._cancel.set()
        self._generation += 1
        self.statusChanged.emit('Loading cancelled; current scene retained')

    def reload_scene(self):
        """Replace results safely; late responses from old tasks are ignored."""
        self._cancel.set()
        self._generation += 1
        self._cancel = Event()
        self._results = Queue(maxsize=2)
        self.assets = {}
        self.scene = self.project.scene
        self._residency = None
        self._streaming = False
        self._preparing = True
        self._last_request = None
        self._placement_index = None
        self._placement_scene = None
        self.terrain = None
        self.terrain_chunks = ()
        self.terrain_atlas = None
        self._lookup.clear()
        for item in self.project.objects:
            node = self.project._node(item.layer, item.index)
            guid = (node.get("Id") or node.get("EntityId") or f"{item.layer}#{item.index}").casefold()
            self._lookup.setdefault(guid, (item.layer, item.index))
        token, cancel, queue = self._generation, self._cancel, self._results
        project = self.project
        def job():
            try:
                def report(*args):
                    self._report_progress(token, cancel, args)
                with span('map', 'scene_reload_prepare', generation=token):
                    result = prepare_scene(project, cancel, report,
                                           game=self.game_root, stage=self.stage_root,
                                           cache_root=self.cache_root, terrain_stride=self.terrain_stride,
                                           partial=lambda a,i,d: queue.put((token,(a,i,d))))
            except Exception as exc:
                result = ({}, {"scene initialization: " + str(exc)[:160]: 1}, {})
            if not cancel.is_set():
                queue.put((token, result))
        self._loader = Thread(target=job, name="Evolve-Level-CPU-Reload", daemon=True)
        self._loader.start()
        self.statusChanged.emit("Refreshing copied scene assets…")
        self.update()

    def _receive(self):
        try:
            token, result = self._results.get_nowait()
        except Empty:
            self._request_visible_resources()
            return
        if token != self._generation or result is None:
            return
        assets, issues, details = result
        if details.get("streamed"):
            self._streaming = False
            # Camera residency churn is not a new scene or GL context.
            # Retain uploaded pixels which still belong to live assets, and
            # keep the independent terrain atlas/UV buffers untouched.
            next_assets = assets or {}
            self._retain_streamed_textures(next_assets)
            record('map', 'residency_installed', generation=token,
                   assets=len(next_assets), uploaded_textures=len(self._textures))
            self.assets = next_assets
            if self._residency:
                self._issues.update({k[0]+': '+v:1 for k,v in self._residency.failures.items()})
            self.update()
            return
        self._preparing = details.get("pending",False)
        self.scene = details.get("scene", self.project.scene)
        self._placement_index = None
        self._placement_scene = None
        self._residency = details.get("residency")
        self._issues = issues
        self._dependency_report = details.get('dependencies', {})
        if details.get("materials"):
            self.materialsReady.emit(details["materials"])
        self.assets = assets or {}
        self.terrain = details.get('terrain')
        self.terrain_chunks = details.get('terrain_chunks', (self.terrain,) if self.terrain is not None else ())
        self.terrain_atlas = details.get('terrain_atlas')
        self._assembly_diagnostics = details.get('assembly_diagnostics', ())
        self._clear_gpu_textures()
        count = sum(1 for obj in self.scene if _asset_key(obj) in self.assets)
        detail = ", ".join(f"{k}: {v}" for k, v in list(issues.items())[:5])
        self.statusChanged.emit(
            f"3D geometry: {count:,} / {len(self.scene):,} placed nodes; "
            f"{len(self.assets)} decoded material variants; "
            + (f"Terrain {sum(len(c.indices)//3 for c in self.terrain_chunks):,} verified triangles; "
               if self.terrain is not None else "Terrain unavailable; ")
            + f"{details.get('mounts',0)} scoped map archives. "
            + (("Issues: " + detail) if detail else "No decoding warnings."))
        from level_progress import LoadProgress
        self.progressChanged.emit(LoadProgress(self._generation,'scene',count,len(self.scene),
            'Scene available; resources load as the camera moves'))
        self.update()

    def _retain_streamed_textures(self, next_assets):
        """Drop only evicted image uploads on a camera residency update.

        Texture keys are Python image identities. The previous self.assets
        stays referenced until this method returns, preventing id() reuse
        during comparison. Full scene/build changes still invalidate all GL
        resources through _clear_gpu_textures().
        """
        live_images = {
            id(image)
            for asset in next_assets.values()
            for image in (asset.materials.textures
                          if getattr(asset, 'materials', None) else ())
            if image is not None
        }
        removed = {
            ident for pixel_id, ident in self._textures.items()
            if pixel_id not in live_images and ident
        }
        if removed and self.isValid():
            self.makeCurrent()
            try:
                from OpenGL.GL import glDeleteTextures
                glDeleteTextures(list(removed))
            finally:
                self.doneCurrent()
        self._textures = {
            pixel_id: ident for pixel_id, ident in self._textures.items()
            if pixel_id in live_images
        }

    def _clear_gpu_textures(self):
        if self.isValid():
            self.makeCurrent()
            try:
                from OpenGL.GL import glDeleteTextures
                ids = set(value for value in self._textures.values() if value)
                if self._terrain_texture:
                    ids.add(self._terrain_texture)
                if ids:
                    glDeleteTextures(list(ids))
            finally:
                self.doneCurrent()
        self._textures.clear()
        self._terrain_texture = 0
        self._terrain_uvs.clear()

    def _request_visible_resources(self):
        if self._residency is None or self._streaming or self._cancel.is_set():
            return
        request = tuple(round(v / max(5., self.distance*.1)) for v in self.camera) + (round(self.distance), round(self.yaw,1),round(self.pitch,1))
        if request == self._last_request:
            return
        self._last_request = request
        # Build the grid only when a scene is installed/replaced. Camera
        # changes probe nearby occupied cells, not every placement twice.
        if self._placement_index is None or self._placement_scene is not self.scene:
            from level_working_set import PlacementGrid
            self._placement_index = PlacementGrid(self.scene, _asset_key)
            self._placement_scene = self.scene
        eye = self._eye()
        forward = tuple((self.camera[i] - eye[i]) / max(self.distance, 1e-8)
                        for i in range(3))
        # A margin keeps already decoded resources resident under small pans.
        nearby = self._placement_index.nearby(
            self.camera, max(100., self.distance * 3.), eye=eye,
            forward=forward, retain=self.assets.keys(), margin=1.2)
        keys = sorted(nearby, key=lambda key: (
            not nearby[key][1], nearby[key][0]))
        if set(keys) == set(self.assets):
            return
        store, cancel, token, queue = self._residency,self._cancel,self._generation,self._results
        self._streaming = True
        def job():
            try:
                value = store.load(keys,cancel)
            except Exception:
                value = dict(store.assets)
            if not cancel.is_set():
                queue.put((token,(value,{}, {"streamed":True})))
        Thread(target=job,name='Evolve-Level-Residency',daemon=True).start()

    def _upload_terrain_texture(self):
        if not self.show_terrain_tiles or self.terrain_atlas is None:
            return 0
        if not self._terrain_texture and self._upload_remaining > 0:
            required_bytes = self.terrain_atlas.width * self.terrain_atlas.height * 4
            if (required_bytes > self._upload_bytes_remaining and
                    self._upload_bytes_frame > 0):
                self.update()
                return 0
            from OpenGL.GL import (glGenTextures,glBindTexture,glTexParameteri,glTexImage2D,
                GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER,GL_LINEAR,
                GL_RGBA,GL_UNSIGNED_BYTE,GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T,GL_CLAMP_TO_EDGE)
            self._upload_remaining -= 1
            self._upload_bytes_remaining -= required_bytes
            self._upload_bytes_frame += required_bytes
            image = self.terrain_atlas.convert('RGBA')
            self._terrain_texture = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D,self._terrain_texture)
            for parameter in (GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER):
                glTexParameteri(GL_TEXTURE_2D,parameter,GL_LINEAR)
            for parameter in (GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T):
                glTexParameteri(GL_TEXTURE_2D,parameter,GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,image.width,image.height,0,
                         GL_RGBA,GL_UNSIGNED_BYTE,image.tobytes())
        return self._terrain_texture

    def frame_all(self):
        points = [x.world.pos for x in self.project.scene]
        if not points:
            return
        coords = [sorted(point[i] for point in points) for i in range(3)]
        mid = len(points) // 2
        self.camera = [axis[mid] for axis in coords]
        spans = [axis[min(len(axis)-1, int(len(axis)*.9))] -
                 axis[min(len(axis)-1, int(len(axis)*.1))] for axis in coords]
        self.distance = max(15., min(MAX_RENDER_DISTANCE, max(spans)*1.5))
        self.update()

    def focus_object(self, obj):
        node = self.project._node(obj.layer, obj.index)
        guid = (node.get("Id") or node.get("EntityId") or f"{obj.layer}#{obj.index}").casefold()
        selected = next((x for x in self.project.scene if x.id == guid), None)
        self.camera = list(selected.world.pos if selected else obj.pos)
        self.distance = max(3., min(self.distance, 75.))
        self.update()

    def _eye(self):
        cp = math.cos(self.pitch)
        return (self.camera[0]+self.distance*cp*math.cos(self.yaw),
                self.camera[1]+self.distance*cp*math.sin(self.yaw),
                self.camera[2]+self.distance*math.sin(self.pitch))

    def initializeGL(self):
        from OpenGL.GL import (
            glClearColor, glEnable, glShadeModel, glLightfv,
            GL_DEPTH_TEST, GL_LIGHTING, GL_LIGHT0, GL_NORMALIZE,
            GL_POSITION, GL_AMBIENT, GL_DIFFUSE, GL_SMOOTH,
            GL_COLOR_MATERIAL, GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE
        )
        glClearColor(.12,.12,.14,1)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_NORMALIZE)
        glEnable(GL_LIGHTING)
        glEnable(GL_LIGHT0)
        glEnable(GL_COLOR_MATERIAL)
        from OpenGL.GL import glColorMaterial
        glColorMaterial(GL_FRONT_AND_BACK, GL_AMBIENT_AND_DIFFUSE)
        glShadeModel(GL_SMOOTH)
        glLightfv(GL_LIGHT0, GL_POSITION, (.6,.4,1.,0.))
        glLightfv(GL_LIGHT0, GL_DIFFUSE, (.8,.8,.8,1.))
        glLightfv(GL_LIGHT0, GL_AMBIENT, (.23,.23,.25,1.))

    def resizeGL(self, width, height):
        from OpenGL.GL import glViewport
        glViewport(0,0,max(1,width),max(1,height))

    def _texture(self, key, slot, asset):
        """Lazy GPU uploads on context owner thread, shared by instances."""
        from OpenGL.GL import (
            glGenTextures,glBindTexture,glTexImage2D,glTexParameteri,
            GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER,
            GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T,GL_LINEAR,GL_REPEAT,
            GL_RGBA,GL_UNSIGNED_BYTE
        )
        materials = asset.materials
        image = (materials.textures[slot] if materials and slot<len(materials.textures) else None)
        if image is None:
            return 0
        tex_id = id(image)
        if tex_id in self._textures:
            return self._textures[tex_id]
        if self._upload_remaining <= 0 or time.monotonic() >= self._upload_deadline:
            self.update()
            return 0
        rgba = image.convert("RGBA")
        if rgba.width > 2048 or rgba.height > 2048:
            rgba.thumbnail((2048,2048))
        required_bytes = rgba.width * rgba.height * 4
        if (required_bytes > self._upload_bytes_remaining and
                self._upload_bytes_frame > 0):
            self.update()
            return 0
        self._upload_remaining -= 1
        self._upload_bytes_remaining -= required_bytes
        self._upload_bytes_frame += required_bytes
        ident = int(glGenTextures(1))
        glBindTexture(GL_TEXTURE_2D,ident)
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT)
        glTexParameteri(GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT)
        glTexImage2D(GL_TEXTURE_2D,0,GL_RGBA,rgba.width,rgba.height,0,
                     GL_RGBA,GL_UNSIGNED_BYTE,rgba.tobytes())
        self._textures[tex_id] = ident
        return ident

    def paintGL(self):
        from OpenGL.GL import (
            glClear,glMatrixMode,glLoadIdentity,glFrustum,glOrtho,
            glPushMatrix,glPopMatrix,glTranslatef,glRotatef,glScalef,
            glColor3f,glEnable,glDisable,glBindTexture,
            glEnableClientState,glDisableClientState,glVertexPointer,
            glNormalPointer,glTexCoordPointer,glDrawElements,
            glMatrixMode,glLoadMatrixf,GL_TEXTURE,
            GL_COLOR_BUFFER_BIT,GL_DEPTH_BUFFER_BIT,GL_PROJECTION,
            GL_MODELVIEW,GL_VERTEX_ARRAY,GL_NORMAL_ARRAY,
            GL_TEXTURE_COORD_ARRAY,GL_TEXTURE_2D,GL_FLOAT,
            GL_TRIANGLES,GL_UNSIGNED_INT
        )
        import numpy as np
        glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT)
        ratio = self.width()/max(1,self.height())
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        half=math.tan(math.radians(60)/2)
        near=.25
        far=max(1000., min(50000.,self.distance*6))
        if self.projection_mode=="Orthographic":
            extent=max(2.,self.distance*.48)
            glOrtho(-extent*ratio,extent*ratio,-extent,extent,near,far)
        else:
            glFrustum(-half*ratio*near,half*ratio*near,-half*near,half*near,near,far)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        eye=self._eye()
        glLoadMatrixf(look_at_matrix(eye,self.camera))
        forward=tuple((self.camera[i]-eye[i])/max(1e-8,self.distance) for i in range(3))
        right=(-forward[1],forward[0],0.)
        right_len=max(1e-8,math.hypot(right[0],right[1]))
        right=tuple(v/right_len for v in right)
        up=(right[1]*forward[2]-right[2]*forward[1],
            right[2]*forward[0]-right[0]*forward[2],
            right[0]*forward[1]-right[1]*forward[0])
        # The real source HeightmapData is drawn in authored world coordinates.
        # No flat proxy, inferred terrain texture, or generated collision.
        self._upload_remaining = 2
        self._upload_bytes_remaining = 8 * 1024 * 1024
        self._upload_bytes_frame = 0
        self._upload_deadline = time.monotonic()+.004
        for terrain in self.terrain_chunks:
            glDisable(GL_TEXTURE_2D)
            glColor3f(.52,.59,.51)
            terrain_tex = self._upload_terrain_texture()
            if terrain_tex:
                from level_terrain_decode import terrain_uvs
                uv = self._terrain_uvs.get(id(terrain))
                if uv is None:
                    uv = terrain_uvs(terrain)
                    self._terrain_uvs[id(terrain)] = uv
                glEnable(GL_TEXTURE_2D)
                glBindTexture(GL_TEXTURE_2D,terrain_tex)
                glEnableClientState(GL_TEXTURE_COORD_ARRAY)
                glTexCoordPointer(2,GL_FLOAT,0,uv)
                glColor3f(1.,1.,1.)
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_NORMAL_ARRAY)
            glVertexPointer(3,GL_FLOAT,0,terrain.vertices)
            glNormalPointer(GL_FLOAT,0,terrain.normals)
            glDrawElements(GL_TRIANGLES,len(terrain.indices),
                           GL_UNSIGNED_INT,terrain.indices)
            glDisableClientState(GL_NORMAL_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glDisable(GL_TEXTURE_2D)
        visible=[]
        for item in self.scene:
            key=_asset_key(item)
            asset=self.assets.get(key)
            if asset is None or item.status=="hidden in editor":
                continue
            center=item.world.pos
            delta=tuple(center[i]-eye[i] for i in range(3))
            depth=sum(delta[i]*forward[i] for i in range(3))
            bounding=max(1.,1.74*asset.mesh.radius*max(abs(s) for s in item.world.scale))
            if depth+bounding<near or depth-bounding>far:
                continue
            limit=max(0.,depth)*half
            if (abs(sum(delta[i]*right[i] for i in range(3))) > ratio*limit+bounding
                    or abs(sum(delta[i]*up[i] for i in range(3))) > limit+bounding):
                continue
            visible.append(item)
            glPushMatrix()
            glTranslatef(*center)
            qw,qx,qy,qz=item.world.rotate
            angle=2*math.degrees(math.acos(max(-1.,min(1.,qw))))
            norm=math.hypot(qx,qy,qz)
            if norm>1e-8:
                glRotatef(angle,qx/norm,qy/norm,qz/norm)
            glScalef(*item.world.scale)
            geom=asset.geometry
            glEnableClientState(GL_VERTEX_ARRAY)
            glEnableClientState(GL_NORMAL_ARRAY)
            glVertexPointer(3,GL_FLOAT,0,geom.vertices)
            glNormalPointer(GL_FLOAT,0,geom.normals)
            has_uv=bool(asset.materials) and len(asset.mesh.uvs)==len(asset.mesh.vertices)
            if has_uv:
                glEnableClientState(GL_TEXTURE_COORD_ARRAY)
                glTexCoordPointer(2,GL_FLOAT,0,geom.uvs)
            for first,count,slot in geom.draws:
                tex=self._texture(key,slot,asset) if has_uv else 0
                if tex:
                    from texture_transform import transform_for, gl_matrix
                    glEnable(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D,tex)
                    glMatrixMode(GL_TEXTURE)
                    glPushMatrix()
                    glLoadMatrixf(np.asarray(gl_matrix(transform_for(asset.materials,slot)),dtype=np.float32))
                    glMatrixMode(GL_MODELVIEW)
                else:
                    glDisable(GL_TEXTURE_2D)
                glColor3f(1.,.74,.4) if self.active == self._lookup.get(item.id) else glColor3f(.86,.86,.87)
                glDrawElements(GL_TRIANGLES,count,GL_UNSIGNED_INT,geom.indices[first:first+count])
                if tex:
                    glMatrixMode(GL_TEXTURE)
                    glPopMatrix()
                    glMatrixMode(GL_MODELVIEW)
            glDisable(GL_TEXTURE_2D)
            glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glDisableClientState(GL_NORMAL_ARRAY)
            glDisableClientState(GL_VERTEX_ARRAY)
            if self.active == self._lookup.get(item.id):
                self._selection_bounds(asset.bounds)
            glPopMatrix()
        self._visible=tuple(visible)
        if self._upload_bytes_frame:
            # Even a single 4K driver upload can exceed a 4ms CPU deadline.
            # Report the observed overrun; do not silently reduce source
            # resolution or drop the final required material.
            record('map', 'texture_upload_frame',
                   submitted_bytes=self._upload_bytes_frame,
                   exceeded_byte_budget=self._upload_bytes_remaining < 0,
                   deadline_overrun_ms=max(
                       0., (time.monotonic()-self._upload_deadline)*1000))
        from level_progress import LoadProgress
        required = {id(im) for item in visible
                    for im in (self.assets[_asset_key(item)].materials.textures
                               if self.assets[_asset_key(item)].materials else ()) if im is not None}
        uploaded = len(required & set(self._textures))
        complete = uploaded == len(required) and not self._preparing and not self._streaming
        label = ('Visible resources loaded; missing content reported in diagnostics'
                 if complete and (self._issues or self._dependency_report.get('missing')) else
                 'Visible resources loaded' if complete else 'Uploading visible textures')
        event_key = (uploaded,len(required),label)
        if not self._preparing and getattr(self,'_last_upload_progress',None) != event_key:
            self._last_upload_progress = event_key
            self.progressChanged.emit(LoadProgress(self._generation,
                'complete' if complete else 'GPU upload',uploaded,len(required),label))

    @staticmethod
    def _selection_bounds(bounds):
        """Visible orange wireframe bounds for the selected real mesh."""
        from OpenGL.GL import (
            glBegin,glEnd,glVertex3f,glDisable,glEnable,glColor3f,
            glLineWidth,GL_LINES,GL_LIGHTING,GL_DEPTH_TEST
        )
        low, high = bounds
        verts = [(x,y,z) for x in (low[0],high[0])
                         for y in (low[1],high[1])
                         for z in (low[2],high[2])]
        glDisable(GL_LIGHTING)
        glDisable(GL_DEPTH_TEST)
        glLineWidth(2.)
        glColor3f(1.,.55,.18)
        glBegin(GL_LINES)
        for i in range(8):
            for bit in (1,2,4):
                j=i ^ bit
                if i<j:
                    glVertex3f(*verts[i])
                    glVertex3f(*verts[j])
        glEnd()
        glLineWidth(1.)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_LIGHTING)

    def _pick(self, px, py):
        import numpy as np
        eye=np.asarray(self._eye(),dtype=float)
        target=np.asarray(self.camera,dtype=float)
        f=(target-eye);f/=max(1e-9,np.linalg.norm(f))
        right=np.cross(f,(0.,0.,1.));right/=max(1e-9,np.linalg.norm(right))
        up=np.cross(right,f)
        aspect=self.width()/max(1,self.height())
        k=math.tan(math.radians(60)/2)
        x=(2*px/max(1,self.width())-1)*aspect*k
        y=(1-2*py/max(1,self.height()))*k
        direction=f+x*right+y*up
        direction/=max(1e-9,np.linalg.norm(direction))
        best=None
        best_t=float("inf")
        for obj in self._visible:
            asset=self.assets[_asset_key(obj)]
            local=tuple(asset.mesh.center[i]*obj.world.scale[i] for i in range(3))
            from level_scene_core import rotate_vector
            world_center=np.asarray(obj.world.pos)+np.asarray(rotate_vector(obj.world.rotate,local))
            rad=max(0.25,asset.mesh.radius*max(abs(v) for v in obj.world.scale))
            delta=eye-world_center
            b=float(np.dot(direction,delta))
            c=float(np.dot(delta,delta)-rad*rad)
            discriminant=b*b-c
            if discriminant<0:
                continue
            t=-b-math.sqrt(discriminant)
            if 0<t<best_t:
                best_t,best=t,obj
        return best

    def mousePressEvent(self,event):
        self._mouse=event.position()
        self._dragged=False
        self._press_origin=event.position()
        if event.button()==Qt.LeftButton:
            item=self._pick(event.position().x(),event.position().y())
            if item and item.id in self._lookup:
                layer,index=self._lookup[item.id]
                self.active=(layer,index)
                self.selected.emit(layer,index)
                self.update()

    def mouseMoveEvent(self,event):
        if self._mouse is None:
            return
        pos=event.position()
        dx=pos.x()-self._mouse.x()
        dy=pos.y()-self._mouse.y()
        self._mouse=pos
        if abs(dx)+abs(dy)>0:
            self._dragged=True
        if event.buttons()&Qt.RightButton:
            self.yaw-=dx*.006
            self.pitch=max(-1.48,min(1.48,self.pitch+dy*.006))
        elif event.buttons()&Qt.MiddleButton:
            scale=self.distance/max(160.,self.height())
            self.camera[0]+=dx*scale*math.sin(self.yaw)+dy*scale*math.cos(self.yaw)
            self.camera[1]+=-dx*scale*math.cos(self.yaw)+dy*scale*math.sin(self.yaw)
        elif event.buttons()&Qt.LeftButton and self.mode=="move" and self.active:
            if self._preview is None:
                obj=self.project.get(*self.active)
                self._preview=tuple(obj.pos)
            scale=self.distance/max(160.,self.height())
            self._preview=(self._preview[0]+dx*scale*math.sin(self.yaw),
                           self._preview[1]-dx*scale*math.cos(self.yaw),
                           self._preview[2])
        self.update()

    def mouseReleaseEvent(self,event):
        if (event.button()==Qt.LeftButton and self._preview is not None
                and self.active and self._dragged):
            self.moved.emit(*self.active,self._preview)
        self._preview=None
        self._mouse=None
        self._press_origin=None

    def wheelEvent(self,event):
        self.distance=max(.5,min(MAX_RENDER_DISTANCE,self.distance*math.pow(.87,event.angleDelta().y()/120.)))
        self.update()

    def closeEvent(self,event):
        self._generation+=1
        self._cancel.set()
        self._poll.stop()
        self._clear_gpu_textures()
        super().closeEvent(event)
