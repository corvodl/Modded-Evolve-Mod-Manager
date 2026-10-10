"""Read-only preview of verified CryEngine CrChF v7 skinned render meshes.

This decoder currently understands the Goliath-family POSITION half4 stream
(type 0, stride 8) and 16-bit triangle-index stream (type 5, stride 2).
Unsupported layouts are never guessed; no source file is modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from model_asset import DATA_STREAM_CHUNK, inspect_model

MAX_VERTICES = 250_000
MAX_TRIANGLES = 500_000
MAX_DRAW_TRIANGLES = 22_000


@dataclass(frozen=True)
class PreviewMesh:
    vertices: tuple
    triangles: tuple
    center: tuple
    radius: float
    source_name: str = ''
    uvs: tuple = ()
    subsets: tuple = ()  # (first_index, count_indices, material_id)

    @property
    def description(self):
        return f'{len(self.vertices):,} vertices | {len(self.triangles):,} triangles'


def read_preview_mesh(data: bytes, source_name: str = '') -> PreviewMesh:
    """Decode known stream types, validating all mesh bounds before rendering."""
    info = inspect_model(data)
    if info.format_name != 'CrChF' or info.version != 7:
        raise ValueError('3D preview currently supports CrChF v7 geometry only.')
    streams = {}
    for i in range(info.chunks):
        kind, chunk_id, size, offset = struct.unpack_from('<4I', info.chunk_table, i * 16)
        if kind != DATA_STREAM_CHUNK:
            continue
        _flags, stream_type, count, stride, _a, _b = struct.unpack_from('<6I', data, offset)
        if stream_type in (0, 2, 5):
            if stream_type in streams:
                raise ValueError('Multiple mesh position/index streams; grouped meshes are not yet supported.')
            streams[stream_type] = (count, stride, offset + 24)
    if 0 not in streams or 5 not in streams:
        raise ValueError('No supported render mesh: select its .skinm or .chrm companion, if available.')
    vertex_count, vertex_stride, vertex_at = streams[0]
    index_count, index_stride, index_at = streams[5]
    if not (3 <= vertex_count <= MAX_VERTICES and 3 <= index_count <= MAX_TRIANGLES * 3):
        raise ValueError('Mesh exceeds the preview safety limits.')
    if index_count % 3:
        raise ValueError('Index stream does not contain complete triangles.')
    if vertex_stride != 8:
        raise ValueError(f'Unsupported position encoding (stride {vertex_stride}). This model cannot yet be previewed.')
    if index_stride != 2:
        raise ValueError(f'Unsupported index encoding (stride {index_stride}). This model cannot yet be previewed.')
    positions = []
    xs = []; ys = []; zs = []
    for x, y, z, w in struct.iter_unpack('<4e', memoryview(data)[vertex_at:vertex_at + vertex_count * 8]):
        if not all(math.isfinite(v) for v in (x,y,z,w)) or abs(w - 1) > 0.01:
            raise ValueError('Invalid packed half-float vertex coordinates.')
        positions.append((x,y,z))
        xs.append(x);ys.append(y);zs.append(z)
    uv_coordinates = ()
    if 2 in streams:
        count_uv, uv_stride, uv_at = streams[2]
        if count_uv == vertex_count and uv_stride == 8:
            values = tuple(struct.iter_unpack('<ff', memoryview(data)[uv_at:uv_at + count_uv * 8]))
            if all(math.isfinite(u) and math.isfinite(v) and abs(u) <= 1e4 and abs(v) <= 1e4 for u,v in values):
                uv_coordinates = values
    subsets = ()
    for i in range(info.chunks):
        kind, _cid, size, offset = struct.unpack_from('<4I', info.chunk_table, i * 16)
        if kind != 0x08001017 or size < 16:
            continue
        num = struct.unpack_from('<I', data, offset)[0]
        if not (1 <= num <= 128) or 16 + num * 36 > size:
            continue
        rows = []
        for index in range(num):
            first, count, _vfirst, _vcount, material = struct.unpack_from('<5I', data, offset + 16 + index * 36)
            rows.append((first, count, material))
        if (rows[0][0] == 0 and sum(row[1] for row in rows) == index_count
            and all(x[0] % 3 == 0 and x[1] % 3 == 0 and x[0]+x[1] <= index_count for x in rows)
            and all(a[0] + a[1] == b[0] for a,b in zip(rows, rows[1:]))):
            subsets = tuple(rows)
        break
    indices = struct.unpack_from('<' + str(index_count) + 'H', data, index_at)
    if max(indices) >= vertex_count:
        raise ValueError('Model triangle index is outside the vertex array.')
    center = ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2)
    radius = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)) / 2
    if radius <= 1e-7:
        raise ValueError('Mesh bounds are degenerate.')
    triangles = tuple(zip(indices[::3], indices[1::3], indices[2::3]))
    return PreviewMesh(tuple(positions), triangles, center, radius, source_name, uv_coordinates, subsets)


def find_preview_mesh(workspace, relative, raw, *, projects_root=None, stage_root=None):
    """Prefer the selected render mesh; fall back to its same-folder companion."""
    suffix = Path(relative).suffix.lower()
    if suffix in ('.skin', '.chr', '.cgf', '.cga'):
        companion_rel = relative + 'm'
        root = (Path(workspace) / 'files').resolve()
        file = (root / companion_rel).resolve()
        if file.is_relative_to(root) and file.is_file() and not file.is_symlink():
            return read_preview_mesh(file.read_bytes(), companion_rel)
        # Companion assets can live in another extracted PAK from the same batch.
        from multi_pak_assets import locate_related_asset
        linked = locate_related_asset(workspace, companion_rel, projects_root, stage_root)
        if linked is not None:
            return read_preview_mesh(linked.read_bytes(), companion_rel)
        raise ValueError('This character file stores skeleton/metadata. A .skinm or .chrm render-mesh companion is needed for preview.')
    return read_preview_mesh(raw, relative)


def render_mesh(mesh: PreviewMesh, width=720, height=480, yaw=-0.5, elevation=0.24,
                zoom=1.0, wireframe=False, max_draw_triangles=MAX_DRAW_TRIANGLES) -> Image.Image:
    """Simple non-GPU shaded/outline render. No material/texture decoding."""
    width = max(128, min(int(width), 1600))
    height = max(128, min(int(height), 1200))
    zoom = max(.2, min(float(zoom), 6.0))
    picture = Image.new('RGB',(width, height), '#20212a')
    canvas = ImageDraw.Draw(picture)
    cx, cy, cz = mesh.center
    ca, sa = math.cos(yaw), math.sin(yaw)
    ce, se = math.cos(elevation), math.sin(elevation)
    scale = min(width, height) * .42 / mesh.radius * zoom
    points=[]; depths=[]
    for x,y,z in mesh.vertices:
        a,b,c=x-cx,y-cy,z-cz
        rx=a*ca - b*sa
        ry=a*sa + b*ca
        screen_x=width*.5 + rx*scale
        screen_y=height*.5 + (-c*ce + ry*se)*scale
        depth=ry*ce + c*se
        points.append((screen_x, screen_y))
        depths.append(depth)
    # Avoid huge draw times on dense models; evenly sample only for display.
    every = max(1, math.ceil(len(mesh.triangles)/max(1, max_draw_triangles)))
    faces=[]
    for i in range(0,len(mesh.triangles),every):
        a,b,c=mesh.triangles[i]
        p,q,r=points[a],points[b],points[c]
        signed = (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
        if abs(signed)<.15:
            continue
        ax,ay,az=mesh.vertices[a]
        bx,by,bz=mesh.vertices[b]
        cx0,cy0,cz0=mesh.vertices[c]
        ux,uy,uz=bx-ax,by-ay,bz-az
        vx,vy,vz=cx0-ax,cy0-ay,cz0-az
        nx,ny,nz=uy*vz-uz*vy,uz*vx-ux*vz,ux*vy-uy*vx
        mag=math.sqrt(nx*nx+ny*ny+nz*nz)
        if mag<1e-12:continue
        # Two-sided diffuse approximation. This is geometry only; the
        # original vertex normals and CryEngine materials aren't recreated.
        light=abs((.45*nx-.37*ny+.8*nz)/mag)
        tone=int(100 + 125*light)
        faces.append(((depths[a]+depths[b]+depths[c])/3,(p,q,r), tone))
    # Painter's depth sort: correct for simple surfaces, imperfect for intersections.
    faces.sort(key=lambda row: row[0],reverse=True)
    for _depth, polygon, tone in faces:
        if wireframe:
            canvas.line((*polygon[0],*polygon[1],*polygon[2],*polygon[0]), fill=(80,175,184), width=1)
        else:
            canvas.polygon(polygon, fill=(int(tone*.63),int(tone*.75),tone))
    canvas.text((12,height-26), f'Untextured preview | {mesh.description}',fill='#cad2df')
    return picture


class ModelPreview:
    """Responsive Tk preview: cheap geometry while moving, UV render after idle.

    Software UV rasterization is expensive. Never queue full textured renders
    for every mouse motion; cancel obsolete workers and render one final frame.
    """
    QUALITY_LIMITS = {
        'Fast': (420, 320),
        'Balanced': (650, 440),
        'Detailed': (1000, 720),
    }

    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk
        self.frame = ttk.Frame(parent)
        toolbar = ttk.Frame(self.frame)
        toolbar.pack(fill='x', padx=5, pady=(4, 2))
        self.details = tk.StringVar(value='Choose a supported mesh')
        ttk.Label(toolbar, textvariable=self.details).pack(side='left', fill='x', expand=True)
        # Prefer GPU rendering on Windows with a real OpenGL driver. Leave the
        # software renderer available on unsupported PCs and remote desktops.
        self.renderer_mode = tk.StringVar(value='GPU (Auto)')
        self.renderer_picker = ttk.Combobox(toolbar, width=12,
            textvariable=self.renderer_mode, values=('GPU (Auto)', 'Software'),
            state='readonly')
        self.renderer_picker.pack(side='right', padx=3)
        self.renderer_picker.bind('<<ComboboxSelected>>', lambda _: self._change_renderer())
        self.quality = tk.StringVar(value='Balanced')
        ttk.Label(toolbar, text='Quality').pack(side='right', padx=(4, 2))
        self.quality_picker = ttk.Combobox(toolbar, width=9, textvariable=self.quality,
                                           values=tuple(self.QUALITY_LIMITS), state='readonly')
        self.quality_picker.pack(side='right', padx=3)
        self.quality_picker.bind('<<ComboboxSelected>>', lambda _: self._quality_changed())
        self.wireframe = tk.BooleanVar(value=False)
        self.use_textures = tk.BooleanVar(value=True)
        self.textures_button = ttk.Checkbutton(toolbar, text='Textures', variable=self.use_textures,
                                               command=self.schedule)
        self.textures_button.pack(side='right', padx=4)
        self.textures_button.state(['disabled'])
        ttk.Checkbutton(toolbar, text='Wireframe', variable=self.wireframe,
                        command=self.schedule).pack(side='right', padx=4)
        ttk.Button(toolbar, text='Reset view', command=self.reset).pack(side='right', padx=4)
        self.label = tk.Label(self.frame, background='#20212a', foreground='white',
                              text='Select a model to preview')
        self.label.pack(fill='both', expand=True)
        self.label.bind('<ButtonPress-1>', self.press)
        self.label.bind('<B1-Motion>', self.drag)
        self.label.bind('<ButtonRelease-1>', self.release)
        self.label.bind('<MouseWheel>', self.scroll)
        self.label.bind('<Button-4>', lambda _: self.change_zoom(1.15))
        self.label.bind('<Button-5>', lambda _: self.change_zoom(1/1.15))
        self.label.bind('<Configure>', self.configure)
        self.label.bind('<Destroy>', self._on_destroy)
        self.mesh = None
        self.materials = None
        self._base_details = 'Choose a supported mesh'
        self._last = None
        self._photo = None
        self._pending = None
        self._settle_pending = None
        self._interacting = False
        self._scrolling = False
        self._texture_generation = 0
        self._shown_generation = -1
        self._render_busy = False
        self._render_cancel = None
        self._result_queue = None
        self._polling = False
        self._poll_pending = None
        self._want_textured = False
        self._gpu = None
        self._gpu_unavailable = None
        self.yaw = -.5
        self.elevation = .24
        self.zoom = 1.0

    def _on_destroy(self, event):
        if event.widget is not self.label:
            return
        self._cancel_inflight()
        self._close_gpu()
        for name in ('_pending', '_settle_pending', '_poll_pending'):
            try:
                self._cancel_timer(name)
            except Exception:
                pass  # Widget is closing; don't register new Tk work.

    def _close_gpu(self):
        if self._gpu is not None:
            self._gpu.close()
            self._gpu = None
        try:
            if self.label.winfo_exists():
                self.label.pack(fill='both', expand=True)
        except Exception:
            pass

    def _quality_changed(self):
        if self._gpu is not None:
            self._gpu.set_quality(self.quality.get())
        self.schedule()

    def _change_renderer(self):
        self._cancel_inflight()
        if self.renderer_mode.get() == 'Software':
            self._close_gpu()
        else:
            # Explicit retry after switching from Software to GPU.
            self._gpu_unavailable = None
        self.schedule()

    def _render_gpu(self):
        if self.renderer_mode.get() != 'GPU (Auto)' or self._gpu_unavailable is not None:
            return False
        from gpu_model_preview import Win32GPUPreview, GPUUnavailable, gpu_supported_platform
        if not gpu_supported_platform():
            self._gpu_unavailable = 'GPU preview is supported on Windows only.'
            return False
        try:
            if self._gpu is None:
                viewer = Win32GPUPreview(self.frame)
                try:
                    viewer.set_mesh(self.mesh)
                    viewer.set_quality(self.quality.get())
                    self._gpu = viewer
                    self.label.pack_forget()
                    for event, handler in (
                        ('<ButtonPress-1>', self.press), ('<B1-Motion>', self.drag),
                        ('<ButtonRelease-1>', self.release), ('<MouseWheel>', self.scroll),
                        ('<Configure>', self.configure),
                        ('<Expose>', lambda _: self.schedule()),
                    ):
                        viewer.frame.bind(event, handler)
                    viewer.frame.bind('<Button-4>', lambda _: self.change_zoom(1.15))
                    viewer.frame.bind('<Button-5>', lambda _: self.change_zoom(1/1.15))
                except BaseException:
                    viewer.close()
                    raise
            self._cancel_inflight()  # No CPU UV worker should keep rendering.
            # Rendering requires the OpenGL context on the Tk event thread.
            self._gpu.draw(self.mesh, self.materials, yaw=self.yaw,
                           elevation=self.elevation, zoom=self.zoom,
                           wireframe=self.wireframe.get(),
                           textured=self.use_textures.get() and bool(self.materials))
            self.details.set(self._base_details + ' | GPU OpenGL: ' + self._gpu.renderer)
            return True
        except Exception as error:
            # Any unsupported Win32/OpenGL function should safely fall back.
            self._gpu_unavailable = str(error)
            self._close_gpu()
            return False

    def _schedule_poll(self):
        if self._poll_pending is None:
            self._poll_pending = self.label.after(35, self._drain_results)
        self._polling = True

    def _cancel_inflight(self):
        if self._render_cancel is not None:
            self._render_cancel.set()

    def _cancel_timer(self, name):
        ident = getattr(self, name)
        if ident is not None:
            self.label.after_cancel(ident)
            setattr(self, name, None)

    def _settle(self):
        self._settle_pending = None
        self._scrolling = False
        if not self._interacting:
            self.render()

    def _queue_settle(self):
        self._cancel_timer('_settle_pending')
        # Allow bursts of wheel events to stop before rasterizing full UVs.
        self._settle_pending = self.label.after(170, self._settle)

    def set_mesh(self, mesh):
        self._cancel_inflight()
        self.mesh = mesh
        if self._gpu is not None:
            self._gpu.set_mesh(mesh)
        self._base_details = f'{Path(mesh.source_name).name} | {mesh.description} | Drag to rotate, wheel to zoom'
        self.details.set(self._base_details)
        self.materials = None
        self.textures_button.state(['disabled'])
        self.reset()

    def set_materials(self, materials):
        self._cancel_inflight()
        self.materials = materials
        if self._gpu is not None:
            self._gpu.set_materials(materials)
        self.use_textures.set(True)
        self.textures_button.state(['!disabled'] if materials is not None else ['disabled'])
        self.schedule()

    def reset(self):
        self._cancel_timer('_settle_pending')
        self._interacting = False
        self._scrolling = False
        self.yaw = -.5
        self.elevation = .24
        self.zoom = 1.0
        self.schedule()

    def press(self, event):
        self._last = (event.x, event.y)
        self._interacting = True
        self._cancel_inflight()

    def drag(self, event):
        if self._last is None or self.mesh is None:
            return
        dx, dy = event.x - self._last[0], event.y - self._last[1]
        self._last = (event.x, event.y)
        if dx == 0 and dy == 0:
            return
        self.yaw += dx * .012
        self.elevation = max(-1.45, min(1.45, self.elevation + dy * .008))
        self._cancel_inflight()
        self.schedule()

    def release(self, event):
        self._last = None
        if self._interacting:
            self._interacting = False
            self._cancel_timer('_pending')
            self._queue_settle()

    def scroll(self, event):
        if event.delta:
            self.change_zoom(1.12 if event.delta > 0 else 1/1.12)

    def change_zoom(self, factor):
        self.zoom = max(.2, min(6., self.zoom * factor))
        self._scrolling = True
        self._cancel_inflight()
        self.schedule()
        self._queue_settle()

    def configure(self, event):
        if self.mesh is not None and event.width > 150:
            self.schedule()

    def schedule(self):
        if self._pending is None:
            # GPU frames can refresh on the next display tick (about 60 FPS).
            delay = 16 if self._gpu is not None else 40
            self._pending = self.label.after(delay, self.render)

    def _textured_requested(self):
        return (self.mesh is not None and self.materials is not None
                and self.use_textures.get() and not self.wireframe.get()
                and bool(self.mesh.uvs) and not self._interacting and not self._scrolling)

    def render(self):
        # A manual render can overtake a scheduled redraw; cancel its timer
        # rather than losing the after-id and leaving a Tcl callback behind.
        self._cancel_timer('_pending')
        if self.mesh is None:
            return
        if self._render_gpu():
            return
        self._texture_generation += 1
        self._want_textured = self._textured_requested()
        self._cancel_inflight()
        if self._want_textured:
            self._render_textured_async()
            return
        width = max(300, self.label.winfo_width())
        height = max(260, self.label.winfo_height())
        moving = self._interacting or self._scrolling
        if moving:
            # Fast shaded stand-in while moving: no expensive UV raster.
            ratio = min(1.0, 440 / max(width, height))
            w, h = max(160, int(width * ratio)), max(130, int(height * ratio))
            image = render_mesh(self.mesh, w, h, self.yaw, self.elevation,
                                self.zoom, self.wireframe.get(), max_draw_triangles=2100)
            self.details.set(self._base_details + ' | Fast rotation preview')
            self._show_image(image, scale_to_window=True)
        else:
            image = render_mesh(self.mesh, width, height, self.yaw,
                                self.elevation, self.zoom, self.wireframe.get())
            self.details.set(self._base_details)
            self._show_image(image)

    def _show_image(self, image, *, scale_to_window=False):
        if not self.label.winfo_exists():
            return
        if scale_to_window:
            size = (max(1, self.label.winfo_width()), max(1, self.label.winfo_height()))
            if image.size != size:
                image = image.resize(size, Image.Resampling.BILINEAR)
        self._photo = ImageTk.PhotoImage(image, master=self.label)
        self.label.configure(image=self._photo, text='')

    def _render_textured_async(self):
        import queue
        import threading
        if not self._want_textured or self.mesh is None:
            return
        if self._result_queue is None:
            self._result_queue = queue.Queue()
        if self._render_busy:
            # The previous render is cancelled, not queued behind new frames.
            self._cancel_inflight()
            if not self._polling:
                self._schedule_poll()
            return
        self._render_busy = True
        event = threading.Event()
        self._render_cancel = event
        generation = self._texture_generation
        width = max(300, self.label.winfo_width())
        height = max(260, self.label.winfo_height())
        quality = self.QUALITY_LIMITS.get(self.quality.get(), self.QUALITY_LIMITS['Balanced'])
        scale = min(1., quality[0] / width, quality[1] / height)
        w, h = max(128, round(width * scale)), max(128, round(height * scale))
        args = (self.mesh, self.materials, w, h, self.yaw, self.elevation, self.zoom)

        def work():
            try:
                from material_preview import render_textured_mesh, RenderCancelled
                image = render_textured_mesh(*args, cancel=event.is_set)
            except Exception as exc:
                image = exc
            self._result_queue.put((generation, image))

        threading.Thread(target=work, daemon=True, name='EvolveModelPreview').start()
        if not self._polling:
            self._schedule_poll()

    def _drain_results(self):
        self._poll_pending = None
        self._polling = False
        if not self.label.winfo_exists():
            return
        while self._result_queue is not None:
            try:
                generation, result = self._result_queue.get_nowait()
            except Exception:
                break
            self._render_busy = False
            self._render_cancel = None
            if generation != self._texture_generation or not self._want_textured:
                continue
            from material_preview import RenderCancelled
            if isinstance(result, RenderCancelled):
                continue
            if isinstance(result, Exception):
                # Mark a failed generation as handled; otherwise the polling
                # loop would retry the same unsupported render indefinitely.
                self._shown_generation = generation
                self.details.set('Texture preview unavailable: ' + str(result))
            else:
                self._shown_generation = generation
                self.details.set(self._base_details + ' | ' + self.quality.get() + ' textured preview')
                self._show_image(result, scale_to_window=True)
        if self._render_busy:
            self._schedule_poll()
        elif self._want_textured and self._shown_generation != self._texture_generation:
            self._render_textured_async()

    def clear(self):
        self._cancel_inflight()
        self._cancel_timer('_pending')
        self._cancel_timer('_settle_pending')
        self._texture_generation += 1
        self._want_textured = False
        self._interacting = False
        self._scrolling = False
        self._close_gpu()
        self.mesh = None
        self._photo = None
        self.materials = None
        self.textures_button.state(['disabled'])
        self._base_details = 'Choose a supported mesh'
        self.details.set(self._base_details)
        self.label.configure(image='', text='Select a model to preview')
