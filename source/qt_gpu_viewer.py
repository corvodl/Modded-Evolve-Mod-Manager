"""GPU-accelerated Qt model viewer for supported Evolve CrChF meshes.

Unlike the older Win32/Tk WGL host this uses a Qt-owned OpenGL context.
It never accesses installed PAKs or writes extracted workspace files.
"""
from __future__ import annotations
import math
import time
import sys
import json
from pathlib import Path
import numpy as np

from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QProcess, QObject, Signal, QRunnable, QThreadPool, QEvent, QSize, QPoint
from PySide6.QtGui import QSurfaceFormat, QIcon, QPixmap, QPainter, QPen, QColor, QPolygon
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QComboBox, QFileDialog, QMessageBox, QSlider, QToolButton, QFrame, QScrollArea, QButtonGroup, QLineEdit, QLayout, QListWidget, QListWidgetItem, QAbstractItemView, QPlainTextEdit, QProgressBar)

from gpu_model_preview import prepare_geometry, GPUUnavailable


from qt_navigation_icons import navigation_icon


def _viewport_tool_icon(kind):
    """Use the same offline pictograms as explorer and GFX controls."""
    mapping = {
        "View": "view", "Materials": "material", "Animation": "animation",
        "Grid": "grid", "Wire": "wire", "Solid": "solid",
    }
    return navigation_icon(mapping.get(kind, kind.casefold()), size=18)


class _CompactAnimationList(QListWidget):
    """In-panel searchable animation list; never opens a screen-sized popup.

    The tiny ComboBox-compatible API keeps existing catalog/filter callers
    and tests usable while real Qt widgets display only six scrollable rows.
    """
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('referencedNativeAnimations')
        self.setMinimumHeight(88)
        self.setMaximumHeight(108)
        self.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setSpacing(2)
        self.setStyleSheet(
            'QListWidget#referencedNativeAnimations {background:#22242b;'
            'border:1px solid #3b3e4b;border-radius:8px;padding:4px;}'
            'QListWidget#referencedNativeAnimations::item {color:#e5e6ed;'
            'padding:6px 8px;border-radius:5px;}'
            'QListWidget#referencedNativeAnimations::item:selected {'
            'background:#433b64;color:#fff;}'
            'QListWidget#referencedNativeAnimations::item:hover {background:#343340;}')
        self.setTextElideMode(Qt.ElideMiddle)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.currentRowChanged.connect(self.currentIndexChanged.emit)

    def addItem(self, title, data=None):
        item = QListWidgetItem(str(title))
        item.setData(Qt.UserRole, data)
        super().addItem(item)

    def currentData(self):
        item = self.currentItem()
        return item.data(Qt.UserRole) if item else None

    def itemData(self, index, role=Qt.UserRole):
        item = self.item(index)
        return item.data(role) if item else None

    def setItemData(self, index, value, role=Qt.UserRole):
        item = self.item(index)
        if item:
            item.setData(role, value)

    def itemText(self, index):
        item = self.item(index)
        return item.text() if item else ''

    def currentIndex(self):
        return self.currentRow()

    def setCurrentIndex(self, index):
        # QListWidget inherits QAbstractItemView.setCurrentIndex(QModelIndex).
        # Qt may invoke that native slot while the mouse moves selection;
        # retain its QModelIndex contract as well as the old combo int API.
        from PySide6.QtCore import QModelIndex
        if isinstance(index, QModelIndex):
            return super().setCurrentIndex(index)
        self.setCurrentRow(int(index))


class _CompactDiagnosticText(QPlainTextEdit):
    """Bounded, scrollable information with wrap-anywhere game paths."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setMaximumHeight(90)
        self.setMinimumHeight(60)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setObjectName('nativeAnimationDetail')
        self.setToolTip('Scroll within the details to see full paths and errors.')

    def setText(self, value):
        self.setPlainText(str(value))

    def text(self):
        return self.toPlainText()


class _NativeResult(QObject):
    completed = Signal(object, object, object)


class _NativeConversion(QRunnable):
    """Work outside the Qt GUI thread for native CAF/GLB decoding."""

    def __init__(self, resolver, reference, token, signals):
        super().__init__()
        self.resolver, self.reference, self.token = resolver, reference, token
        self.signals = signals

    def run(self):
        try:
            blob, details = self.resolver(self.reference)
            self.signals.completed.emit(self.token, (blob, details), None)
        except Exception as exc:
            self.signals.completed.emit(self.token, None, str(exc))


class QtGLModelCanvas(QOpenGLWidget):
    """OpenGL 2.1 compatibility vertex arrays, depth and approximate materials."""

    def __init__(self, mesh, materials=None, parent=None, prepared_geometry=None):
        super().__init__(parent)
        format = QSurfaceFormat()
        format.setRenderableType(QSurfaceFormat.OpenGL)
        format.setVersion(2, 1)
        format.setProfile(QSurfaceFormat.CompatibilityProfile)
        format.setDepthBufferSize(24)
        format.setSamples(2)
        self.setFormat(format)
        self.setMinimumSize(260, 180)
        self.mesh = mesh
        # Preserve geometry staging and animation-buffer layout. Authored
        # normals are used when Smooth shading is explicitly selected.
        self.shading = 'faceted'
        self.geometry = prepared_geometry if prepared_geometry is not None else prepare_geometry(mesh, shading=self.shading)
        self.materials = materials
        # Character assembled parts can use CryEngine alpha semantics that
        # are not reproduced by the preview. Solid diagnostic rendering keeps
        # all decoded geometry visible while missing materials are resolved.
        self.force_opaque_preview = False
        self.textures = []
        self.normal_textures = []
        self.specular_textures = []
        self.gloss_textures = []
        self.detail_textures = []
        self.detail_normal_textures = []
        self.ao_textures = []
        self.environment_textures = []
        self._pending_texture_uploads = []
        self._gpu_upload_total_ms = 0.0
        self._frame_started_at = time.perf_counter()
        self._geometry_frame_seen = False
        self._first_texture_frame_seen = False
        self._full_texture_frame_seen = False
        self._drawn_with_textures = False
        self._frame_drawn_with_full_quality = False
        self._textures_final_requested = materials is not None
        self.frameSwapped.connect(self._frame_presented)
        self.translucent_slots = set()
        self._batch_sort_cache = None
        self.enhanced_materials = True
        self.unlit_diffuse = False
        self._renderer3 = None
        self.ssao_quality = 'Off'
        self.normal_diagnostic = 'Source (unchanged)'
        self.ssao_error = ''
        self._ssao_effect = None
        self.rotation = [-30.0, 14.0]
        self.zoom = 1.0
        self.pan = [0., 0.]
        self.navigation_mode = 'orbit'
        self.grid_visible = True
        self._grid_floor = min((vertex[2] for vertex in mesh.vertices), default=0.0)
        self.wireframe = False
        self.textured = True
        self._last = None
        self.gl_error = None
        self.gpu_upload_ms = None
        self.light_strength = 1.0
        self.studio_fill = 0.35
        self.background_mode = 'Studio Grey'
        self.visible_material_slot = None
        self.projection_mode = 'Orthographic'
        self.specular_intensity = 0.35
        self._uv_motion_started = time.monotonic()
        self._uv_motion_timer = QTimer(self)
        self._uv_motion_timer.setInterval(50)
        self._uv_motion_timer.timeout.connect(self.update)
        self._refresh_uv_motion()

    def _refresh_uv_motion(self):
        self._uv_motion_started = time.monotonic()
        groups = (getattr(self.materials, 'uv_motions', ()),)
        groups += tuple(values for _, values in
                        getattr(self.materials, 'map_uv_motions', ()))
        animated = any(any(any(rate for rate in motion) for motion in group)
                       for group in groups)
        if animated:
            self._uv_motion_timer.start()
        else:
            self._uv_motion_timer.stop()

    def _reset_frame_timeline(self):
        self._frame_started_at = time.perf_counter()
        self._geometry_frame_seen = False
        self._first_texture_frame_seen = False
        self._full_texture_frame_seen = False
        self._drawn_with_textures = False
        self._frame_drawn_with_full_quality = False

    def _frame_presented(self):
        """Visible-frame milestones; not a claim of completed GPU timing."""
        from performance_trace import record
        elapsed = (time.perf_counter() - self._frame_started_at) * 1000
        if not self._geometry_frame_seen:
            self._geometry_frame_seen = True
            record('model', 'first_geometry_frame', elapsed_ms=elapsed)
        if self._drawn_with_textures and not self._first_texture_frame_seen:
            self._first_texture_frame_seen = True
            record('model', 'first_texture_frame', elapsed_ms=elapsed)
        if (self._frame_drawn_with_full_quality and
                not self._full_texture_frame_seen):
            self._full_texture_frame_seen = True
            record('model', 'final_quality_frame', elapsed_ms=elapsed)

    def set_mesh(self, mesh, materials=None, prepared_geometry=None):
        self._reset_frame_timeline()
        geometry = (prepared_geometry if prepared_geometry is not None
                    else prepare_geometry(mesh, shading=self.shading))
        # Replacing an existing model must also upload the NEW diffuse maps.
        # Previously the old textures were deleted but the new ones were not
        # uploaded until an unrelated OpenGL context reinitialization.
        self.mesh = mesh
        self.geometry = geometry
        self._grid_floor = min((vertex[2] for vertex in mesh.vertices), default=0.0)
        self._batch_sort_cache = None
        self.materials = materials
        self.force_opaque_preview = False
        if self.isValid():
            self.makeCurrent()
            try:
                self._release_textures()
            finally:
                self.doneCurrent()
        self._queue_texture_uploads()
        self.update()

    def set_enhanced_materials(self, enabled):
        self.enhanced_materials = bool(enabled)
        self.update()

    def set_ssao_quality(self, quality):
        from ssao_postprocess import QUALITY
        if quality not in QUALITY:
            raise ValueError('Unknown SSAO quality')
        self.ssao_quality = quality
        self.ssao_error = ''
        self.update()

    def set_normal_diagnostic(self, mode):
        """Toggle source normal Y for visual comparison, never mutate DDS bytes."""
        modes = ('Source (unchanged)', 'Flip green (DX to GL)', 'Flat (no normal maps)')
        if mode not in modes:
            raise ValueError('Unsupported normal-map comparison mode')
        self.normal_diagnostic = mode
        self.update()

    def set_visible_material_slot(self, slot):
        self.visible_material_slot=None if slot is None else int(slot)
        self.update()

    def set_projection(self, mode):
        if mode not in ('Orthographic','Perspective'):
            raise ValueError('Unknown projection type')
        self.projection_mode=mode
        self.update()

    def set_specular(self, value):
        self.specular_intensity=max(0.,min(1.,float(value)/100.))
        self.update()

    def set_camera_preset(self, choice):
        orientations = {
            'Front': (0.,0.), 'Back':(180.,0.),
            'Left':(-90.,0.), 'Right':(90.,0.),
            'Top':(0.,90.), 'Bottom':(0.,-90.),
            'Isometric':(-30.,14.),
        }
        if choice not in orientations:
            raise ValueError('Unknown camera preset')
        self.rotation=list(orientations[choice])
        self.pan=[0.,0.]
        self.update()

    def set_light_strength(self, percentage):
        self.light_strength=max(.2,min(2.0,float(percentage)/100.))
        self.update()

    def set_background(self, name):
        if name not in ('Dark','Studio Grey'):
            raise ValueError('Unsupported viewport background')
        self.background_mode=name
        self.update()

    def set_shading(self, name):
        value = 'smooth' if name == 'Smooth' else 'faceted'
        if self.shading != value:
            self.shading = value
            self.geometry = prepare_geometry(self.mesh, shading=value)
            self._batch_sort_cache = None
            self.update()

    def set_animated_vertices(self, frame):
        """Update GPU vertex arrays without rebuilding textures every frame."""
        values = np.asarray(frame, dtype=np.float32)
        if values.shape != (len(self.mesh.vertices), 3) or not np.isfinite(values).all():
            raise ValueError('Animation pose has invalid vertex coordinates.')
        indices = np.asarray(self.mesh.triangles, dtype=np.int32)
        faces = values[indices]
        if self.shading == 'faceted':
            self.geometry.vertices[:] = faces.reshape(-1, 3)
            normals = np.cross(faces[:, 1]-faces[:, 0],
                               faces[:, 2]-faces[:, 0])
            lengths = np.linalg.norm(normals, axis=1)
            normals /= np.maximum(lengths[:, None], 1e-8)
            self.geometry.normals[:] = np.repeat(normals, 3, axis=0)
        else:
            self.geometry.vertices[:] = values
            normals = np.cross(faces[:, 1]-faces[:, 0],
                               faces[:, 2]-faces[:, 0])
            self.geometry.normals[:] = 0
            for i in range(3):
                np.add.at(self.geometry.normals, indices[:, i], normals)
            norms = np.linalg.norm(self.geometry.normals, axis=1)
            self.geometry.normals[:] /= np.maximum(norms[:,None], 1e-8)
        self.update()

    def set_materials(self, materials):
        self._full_texture_frame_seen = False
        self._frame_drawn_with_full_quality = False
        if self.isValid():
            self.makeCurrent()
            try:
                self._release_textures()
            finally:
                self.doneCurrent()
        self.materials = materials
        self._refresh_uv_motion()
        self._queue_texture_uploads()
        self.update()

    def set_quality(self, quality):
        self.setUpdatesEnabled(False)
        fmt = self.format()
        fmt.setSamples(4 if quality == 'High' else 0)
        self.setFormat(fmt)
        self.setUpdatesEnabled(True)
        self.update()

    def _release_textures(self):
        from OpenGL.GL import glDeleteTextures
        for name in ('textures','normal_textures','specular_textures','gloss_textures','detail_textures','detail_normal_textures','ao_textures','environment_textures'):
            handles = getattr(self,name,[])
            if handles:
                try:
                    glDeleteTextures([handle for handle in handles if handle])
                except Exception:
                    pass
            setattr(self,name,[])
        self.translucent_slots = set()
        self._batch_sort_cache = None
        self._pending_texture_uploads = []

    def _queue_texture_uploads(self):
        """Build no-GL descriptors; the first render can show mesh geometry."""
        self._pending_texture_uploads = []
        self.translucent_slots = set()
        self._batch_sort_cache = None
        self._gpu_upload_total_ms = 0.0
        self.gpu_upload_ms = None
        self._textures_final_requested = self.materials is not None
        self._frame_drawn_with_full_quality = False
        groups = (
            ('textures', getattr(self.materials, 'textures', ()) if self.materials else ()),
            ('normal_textures', getattr(self.materials, 'normal_maps', ()) if self.materials else ()),
            ('specular_textures', getattr(self.materials, 'specular_maps', ()) if self.materials else ()),
            ('gloss_textures', getattr(self.materials, 'gloss_maps', ()) if self.materials else ()),
            ('detail_textures', getattr(self.materials, 'detail_maps', ()) if self.materials else ()),
            ('detail_normal_textures', getattr(self.materials, 'detail_normal_maps', ()) if self.materials else ()),
            ('ao_textures', getattr(self.materials, 'ao_maps', ()) if self.materials else ()),
            ('environment_textures', getattr(self.materials, 'environment_maps', ()) if self.materials else ()),
        )
        for target, images in groups:
            setattr(self, target, [0] * len(images))
            for index, image in enumerate(images):
                if image is not None:
                    self._pending_texture_uploads.append((target, index, image))

    def _upload_textures(self, *, max_uploads=1):
        """Submit at most one verified full-resolution GL image per frame.

        This is a per-frame count budget, not a guarantee that a single driver
        upload meets a wall-clock deadline. All GL calls run on the widget's
        context thread; final diffuse/normal/specular quality is unchanged.
        """
        import time
        from OpenGL.GL import (
            glGenTextures, glBindTexture, glTexImage2D, glTexParameteri,
            glPixelStorei, GL_TEXTURE_2D, GL_RGB, GL_RGBA, GL_UNSIGNED_BYTE,
            GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
            GL_LINEAR_MIPMAP_LINEAR, GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T,
            GL_REPEAT, GL_UNPACK_ALIGNMENT, glGenerateMipmap,
            GL_TEXTURE_CUBE_MAP, GL_TEXTURE_CUBE_MAP_POSITIVE_X,
        )
        if not self._pending_texture_uploads:
            return 0
        started = time.perf_counter()
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        uploaded = 0
        while self._pending_texture_uploads and uploaded < max_uploads:
            target, index, im = self._pending_texture_uploads.pop(0)
            if target == 'environment_textures':
                if len(im) != 6:
                    raise ValueError('Cubemap must have exactly six decoded faces.')
                texid = int(glGenTextures(1))
                glBindTexture(GL_TEXTURE_CUBE_MAP, texid)
                for face, image in enumerate(im):
                    if image is None or image.width != image.height:
                        raise ValueError('Invalid cubemap face dimensions.')
                    from gl_texture_upload import gl_upload_pixels
                    glTexImage2D(GL_TEXTURE_CUBE_MAP_POSITIVE_X + face, 0,
                                 GL_RGB, image.width, image.height, 0, GL_RGB,
                                 GL_UNSIGNED_BYTE, gl_upload_pixels(image, 'RGB'))
                for parameter in (GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER):
                    glTexParameteri(GL_TEXTURE_CUBE_MAP, parameter, GL_LINEAR)
                for parameter in (GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T):
                    glTexParameteri(GL_TEXTURE_CUBE_MAP, parameter, GL_REPEAT)
                getattr(self, target)[index] = texid
                glBindTexture(GL_TEXTURE_CUBE_MAP, 0)
                uploaded += 1
                continue
            rgba = 'A' in im.getbands()
            if (target == 'textures' and rgba and self.materials and
                    index < len(getattr(self.materials, 'alpha_modes', ())) and
                    self.materials.alpha_modes[index] and
                    im.getchannel('A').getextrema()[0] < 255):
                self.translucent_slots.add(index)
                self._batch_sort_cache = None
            mode = 'RGBA' if rgba else 'RGB'
            # Preserve CryEngine DDS orientation and original source mip size.
            from gl_texture_upload import gl_upload_pixels
            pixels = gl_upload_pixels(im, mode)
            fmt = GL_RGBA if rgba else GL_RGB
            texid = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D, texid)
            glTexImage2D(GL_TEXTURE_2D, 0, fmt, im.width, im.height, 0,
                         fmt, GL_UNSIGNED_BYTE, pixels)
            try:
                glGenerateMipmap(GL_TEXTURE_2D)
            except Exception:
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            else:
                glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER,
                                GL_LINEAR_MIPMAP_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            try:
                from OpenGL.GL import glGetFloatv, glTexParameterf
                from OpenGL.GL.EXT.texture_filter_anisotropic import (
                    GL_TEXTURE_MAX_ANISOTROPY_EXT,
                    GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT)
                maximum = float(glGetFloatv(GL_MAX_TEXTURE_MAX_ANISOTROPY_EXT))
                if maximum >= 2:
                    glTexParameterf(GL_TEXTURE_2D,
                                    GL_TEXTURE_MAX_ANISOTROPY_EXT,
                                    min(maximum, 8.))
            except (ImportError, ValueError, RuntimeError):
                pass
            for parameter in (GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T):
                glTexParameteri(GL_TEXTURE_2D, parameter, GL_REPEAT)
            getattr(self, target)[index] = texid
            uploaded += 1
        glBindTexture(GL_TEXTURE_2D, 0)
        elapsed = (time.perf_counter() - started) * 1000
        self._gpu_upload_total_ms += elapsed
        self.gpu_upload_ms = round(self._gpu_upload_total_ms, 2)
        from performance_trace import record
        record('model', 'gl_texture_submitted', submitted=uploaded,
               remaining=len(self._pending_texture_uploads),
               elapsed_ms=elapsed)
        # Upload takes place after drawing the current frame; even the final
        # upload needs one *more* paint before the user can see the texture.
        if uploaded:
            self.update()
        if not self._pending_texture_uploads:
            record('model', 'full_quality_textures_ready',
                   elapsed_ms=self._gpu_upload_total_ms)
        return uploaded

    def initializeGL(self):
        from OpenGL.GL import (
            glClearColor, glEnable, glShadeModel, glLightfv, glLightModelfv,
            glGetString, GL_VENDOR, GL_RENDERER, GL_VERSION,
            GL_DEPTH_TEST, GL_NORMALIZE, GL_LIGHT0, GL_LIGHTING, GL_SMOOTH,
            GL_POSITION, GL_AMBIENT, GL_DIFFUSE, GL_LIGHT_MODEL_AMBIENT,
        )
        from performance_trace import record
        def safe_device(value):
            return (value.decode('ascii', 'replace') if isinstance(value, bytes)
                    else str(value or 'unknown'))
        try:
            record('gpu', 'context_initialized',
                   gpu_vendor=safe_device(glGetString(GL_VENDOR)),
                   gpu_renderer=safe_device(glGetString(GL_RENDERER)),
                   gpu_version=safe_device(glGetString(GL_VERSION)))
        except (RuntimeError, ValueError, TypeError):
            pass  # Optional diagnostic cannot block a supported GL context.
        glClearColor(0.092, 0.09, 0.115, 1.0)
        glEnable(GL_DEPTH_TEST)
        glEnable(GL_NORMALIZE)
        glShadeModel(GL_SMOOTH)
        glEnable(GL_LIGHT0)
        glEnable(GL_LIGHTING)
        glLightfv(GL_LIGHT0,GL_POSITION,[0.5,0.8,1.5,0.0])
        glLightfv(GL_LIGHT0,GL_DIFFUSE,[0.83,0.85,0.91,1.0])
        glLightfv(GL_LIGHT0,GL_AMBIENT,[0.13,0.13,0.16,1.0])
        glLightModelfv(GL_LIGHT_MODEL_AMBIENT,[0.08,0.08,0.09,1.0])
        # Stage textures after the first geometry frame, not inside GL setup.
        self._queue_texture_uploads()
        try:
            from renderer_3 import Renderer3
            self._renderer3 = Renderer3()
            self.gl_error = None
        except Exception as exc:
            self._renderer3 = None
            self.gl_error = 'Enhanced material shader unavailable: '+str(exc)[:250]

    def resizeGL(self, width, height):
        from OpenGL.GL import glViewport
        glViewport(0,0,max(1,width),max(1,height))

    def _draw_reference_grid(self):
        """Blender-like ground reference, drawn below the mesh in model space."""
        if not self.grid_visible:
            return
        from OpenGL.GL import (
            glBegin, glEnd, glVertex3f, glColor3f, glDisable, glEnable,
            GL_LINES, GL_LIGHTING, GL_TEXTURE_2D,
        )
        radius = max(0.001,float(self.mesh.radius))
        step = radius / 8.0
        cx,cy,_ = self.mesh.center
        floor = self._grid_floor - step * .05
        glDisable(GL_LIGHTING)
        glDisable(GL_TEXTURE_2D)
        glBegin(GL_LINES)
        for i in range(-12, 13):
            p = i * step
            if i == 0:
                glColor3f(.58,.28,.31)
            else:
                glColor3f(.28,.28,.31)
            glVertex3f(cx + p,cy - 12*step,floor)
            glVertex3f(cx + p,cy + 12*step,floor)
            if i == 0:
                glColor3f(.32,.53,.34)
            else:
                glColor3f(.28,.28,.31)
            glVertex3f(cx - 12*step,cy + p,floor)
            glVertex3f(cx + 12*step,cy + p,floor)
        glEnd()
        glColor3f(1.0,1.0,1.0)
        glEnable(GL_LIGHTING)

    def paintGL(self):
        from OpenGL.GL import (
            glClear, glMatrixMode, glLoadIdentity, glOrtho,
            glTranslatef, glRotatef, glColor3f, glEnable, glDisable,
            glEnableClientState, glDisableClientState, glVertexPointer,
            glNormalPointer, glTexCoordPointer, glDrawElements,
            glPolygonMode, glBindTexture, glMaterialfv, glMaterialf, glFrustum,
            GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT,
            GL_PROJECTION, GL_MODELVIEW, GL_TEXTURE_2D, GL_FRONT_AND_BACK,
            GL_DIFFUSE, GL_FLOAT, GL_UNSIGNED_INT, GL_TRIANGLES,
            GL_VERTEX_ARRAY, GL_NORMAL_ARRAY, GL_TEXTURE_COORD_ARRAY,
            GL_FILL, GL_LINE, GL_FRONT, GL_BACK, GL_AMBIENT, GL_SPECULAR, GL_SHININESS,
        )
        background = (0.092,0.09,0.115,1.0) if self.background_mode=='Dark' else (0.26,0.27,0.29,1.0)
        from OpenGL.GL import (glClearColor, glLightfv, glActiveTexture,
            glBlendFunc, glDepthMask, GL_LIGHT0, GL_DIFFUSE, GL_LIGHTING,
            GL_TEXTURE0, GL_BLEND, GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glClearColor(*background)
        # Capture state *before* uploading textures at the end of paintGL.
        # frameSwapped reports what was really drawn, not new driver handles.
        self._drawn_with_textures = any(self.textures)
        self._frame_drawn_with_full_quality = (
            self._textures_final_requested and not self._pending_texture_uploads)
        strength=self.light_strength
        glLightfv(GL_LIGHT0,GL_DIFFUSE,
                  [min(1.0,0.83*strength),min(1.0,0.85*strength),
                   min(1.0,0.91*strength),1.0])
        glClear(GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
        if self.geometry is None:
            return
        aspect = max(0.1,self.width()/max(1,self.height()))
        # Mesh is normalized using its known verified center/radius.
        glMatrixMode(GL_PROJECTION)
        glLoadIdentity()
        if self.projection_mode=='Perspective':
            glFrustum(-0.33*aspect,0.33*aspect,-0.33,0.33,1.0,100.0)
        else:
            glOrtho(-1.22*aspect,1.22*aspect,-1.22,1.22,-200,200)
        glMatrixMode(GL_MODELVIEW)
        glLoadIdentity()
        glTranslatef(self.pan[0],self.pan[1],-4.0)
        glRotatef(self.rotation[1],1,0,0)
        glRotatef(self.rotation[0],0,0,1)
        center = self.mesh.center
        scale = self.zoom/max(1e-7,self.mesh.radius)*1.02
        from OpenGL.GL import glScalef
        glScalef(scale,scale,scale)
        glTranslatef(-center[0],-center[1],-center[2])
        glPolygonMode(GL_FRONT_AND_BACK,GL_FILL)
        self._draw_reference_grid()
        glPolygonMode(GL_FRONT_AND_BACK,GL_LINE if self.wireframe else GL_FILL)
        arrays = self.geometry
        glEnableClientState(GL_VERTEX_ARRAY)
        glEnableClientState(GL_NORMAL_ARRAY)
        glVertexPointer(3,GL_FLOAT,0,arrays.vertices)
        glNormalPointer(GL_FLOAT,0,arrays.normals)
        use_uv = len(self.mesh.uvs)==len(self.mesh.vertices) and bool(self.textures) and self.textured
        if use_uv:
            glEnable(GL_TEXTURE_2D)
            glEnableClientState(GL_TEXTURE_COORD_ARRAY)
            glTexCoordPointer(2,GL_FLOAT,0,arrays.uvs)
        shader = self._renderer3 if self.enhanced_materials else None
        if shader:
            glDisable(GL_LIGHTING)
        try:
            # Render translucent material groups after opaque ones. Geometry
            # in a material group still uses source ordering (no invented triangles).
            def is_translucent(slot):
                if self.force_opaque_preview:
                    return False
                values=getattr(self.materials,'opacity',()) if self.materials else ()
                cutoffs=getattr(self.materials,'alpha_cutoffs',())
                if slot<len(cutoffs) and cutoffs[slot]>0:
                    return False
                return slot in self.translucent_slots or (slot<len(values) and values[slot]<.999)
            from renderer_3 import ordered_transparency_batches
            transparent_slots={slot for _,_,slot in arrays.draws if is_translucent(slot)}
            order_key=(id(arrays),tuple(self.rotation),tuple(sorted(transparent_slots)))
            if self._batch_sort_cache is None or self._batch_sort_cache[0]!=order_key:
                batches=ordered_transparency_batches(
                    arrays.draws,arrays.indices,arrays.vertices,
                    transparent_slots,self.rotation)
                self._batch_sort_cache=(order_key,batches)
            else:
                batches=self._batch_sort_cache[1]
            for first,count,slot in batches:
                hidden = getattr(self.materials, 'hidden', ())
                if slot < len(hidden) and hidden[slot]:
                    continue
                if self.visible_material_slot is not None and slot != self.visible_material_slot:
                    continue
                alpha = (self.materials.opacity[slot] if self.materials and
                         slot<len(getattr(self.materials,'opacity',())) else 1.0)
                if self.force_opaque_preview:
                    alpha = 1.0
                cutoffs = getattr(self.materials, 'alpha_cutoffs', ())
                cutoff = cutoffs[slot] if not self.force_opaque_preview and slot < len(cutoffs) else 0.
                from OpenGL.GL import GL_ALPHA_TEST, GL_GEQUAL, glAlphaFunc
                if cutoff > 0:
                    glEnable(GL_ALPHA_TEST); glAlphaFunc(GL_GEQUAL, cutoff)
                else:
                    glDisable(GL_ALPHA_TEST)
                transparent = is_translucent(slot)
                if transparent:
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA)
                    glDepthMask(False)
                else:
                    glDisable(GL_BLEND)
                    glDepthMask(True)
                from texture_transform import transform_for, gl_matrix
                from OpenGL.GL import glLoadMatrixf, GL_TEXTURE
                seconds = time.monotonic() - self._uv_motion_started
                glActiveTexture(GL_TEXTURE0)
                glMatrixMode(GL_TEXTURE)
                glLoadMatrixf(gl_matrix(transform_for(self.materials, slot, seconds=seconds)))
                glMatrixMode(GL_MODELVIEW)
                tex = self.textures[slot] if use_uv and slot < len(self.textures) else 0
                if shader:
                    for unit,group in ((0,self.textures),(1,self.normal_textures),
                                       (2,self.specular_textures),(3,self.gloss_textures),
                                       (4,self.detail_textures),(5,self.detail_normal_textures),
                                       (7,self.ao_textures)):
                        handle = group[slot] if use_uv and slot<len(group) else 0
                        glActiveTexture(GL_TEXTURE0+unit)
                        glBindTexture(GL_TEXTURE_2D,handle)
                    glActiveTexture(GL_TEXTURE0+6)
                    from OpenGL.GL import GL_TEXTURE_CUBE_MAP
                    cubemap = (self.environment_textures[slot]
                               if use_uv and slot < len(self.environment_textures) else 0)
                    glBindTexture(GL_TEXTURE_CUBE_MAP,cubemap)
                    glActiveTexture(GL_TEXTURE0)
                    strengths = getattr(self.materials,'reflection_strengths',())
                    glasses = getattr(self.materials,'glass_materials',())
                    ao_strengths = getattr(self.materials,'ao_strengths',())
                    shader.bind(ao=slot<len(self.ao_textures) and bool(self.ao_textures[slot]),
                                ao_strength=ao_strengths[slot] if slot<len(ao_strengths) else 1.,
                                environment=bool(cubemap),
                                reflection_strength=strengths[slot] if slot<len(strengths) else 0.,
                                glass=bool(glasses[slot]) if slot<len(glasses) else False,
                                opacity=alpha,gloss=self.specular_intensity, alpha_cutoff=cutoff or .02,
                                illumination=strength,diffuse=bool(tex),
                                normal=(self.normal_diagnostic != 'Flat (no normal maps)' and
                                        slot<len(self.normal_textures) and bool(self.normal_textures[slot])),
                                specular=slot<len(self.specular_textures) and bool(self.specular_textures[slot]),
                                gloss_texture=slot<len(self.gloss_textures) and bool(self.gloss_textures[slot]),
                                detail=slot<len(self.detail_textures) and bool(self.detail_textures[slot]),
                                detail_normal=(self.normal_diagnostic != 'Flat (no normal maps)' and
                                               slot<len(self.detail_normal_textures) and bool(self.detail_normal_textures[slot])),
                                invert_normal_y=self.normal_diagnostic == 'Flip green (DX to GL)',
                                uv_matrices={role: transform_for(self.materials,slot,role,seconds)
                                             for role in ('normal','specular','gloss','detail','detail_normal','ao')},
                                alpha_texture=(not self.force_opaque_preview and slot<len(getattr(self.materials,'alpha_modes',())) and bool(self.materials.alpha_modes[slot])),
                                unlit=self.unlit_diffuse,fill=self.studio_fill)
                if tex:
                    glEnable(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D,tex)
                else:
                    glDisable(GL_TEXTURE_2D)
                # For the legacy GL fallback an unlit view shows the actual
                # diffuse DDS rather than applying two darkening multipliers.
                if not shader and self.unlit_diffuse:
                    glDisable(GL_LIGHTING)
                elif not shader:
                    glEnable(GL_LIGHTING)
                glColor3f(1.0,1.0,1.0)
                glMaterialfv(GL_FRONT_AND_BACK,GL_DIFFUSE,[1.0,1.0,1.0,alpha])
                spec=self.specular_intensity
                glMaterialfv(GL_FRONT_AND_BACK,GL_SPECULAR,[spec,spec,spec,1.0])
                glMaterialf(GL_FRONT_AND_BACK,GL_SHININESS,38.0)
                glDrawElements(GL_TRIANGLES,int(count),GL_UNSIGNED_INT,
                               arrays.indices[first:first+count])
        finally:
            from OpenGL.GL import GL_TEXTURE, GL_ALPHA_TEST
            glDisable(GL_ALPHA_TEST)
            glActiveTexture(GL_TEXTURE0)
            glMatrixMode(GL_TEXTURE)
            glLoadIdentity()
            glMatrixMode(GL_MODELVIEW)
            if self.unlit_diffuse and not shader:
                glEnable(GL_LIGHTING)
            if shader:
                shader.unbind()
                from OpenGL.GL import GL_TEXTURE_CUBE_MAP
                glActiveTexture(GL_TEXTURE0+6)
                glBindTexture(GL_TEXTURE_CUBE_MAP,0)
                for unit in (7,5,4,3,2,1,0):
                    glActiveTexture(GL_TEXTURE0+unit)
                    glBindTexture(GL_TEXTURE_2D,0)
                glEnable(GL_LIGHTING)
            glDepthMask(True)
            glDisable(GL_BLEND)
            glDisableClientState(GL_VERTEX_ARRAY)
            glDisableClientState(GL_NORMAL_ARRAY)
            if use_uv:
                glDisableClientState(GL_TEXTURE_COORD_ARRAY)
            glBindTexture(GL_TEXTURE_2D,0)
            glDisable(GL_TEXTURE_2D)
            glPolygonMode(GL_FRONT_AND_BACK,GL_FILL)
        # SSAO is a separate, optional depth-only GPU pass. It is intentionally
        # outside the material shader, so missing textures do not disable it.
        if self.ssao_quality != 'Off' and not self.wireframe:
            try:
                if self._ssao_effect is None:
                    from ssao_postprocess import SsaoEffect
                    self._ssao_effect = SsaoEffect()
                self._ssao_effect.render(
                    arrays, quality=self.ssao_quality,
                    perspective=self.projection_mode == 'Perspective',
                    visible_slot=self.visible_material_slot,
                    hidden=getattr(self.materials,'hidden',()))
            except Exception as error:
                # Never break the primary viewer on unsupported OpenGL FBOs.
                message = str(error)[:240]
                self.ssao_quality = 'Off'
                self.ssao_error = message
                if hasattr(self.parent(), 'ssao_combo'):
                    combo = self.parent().ssao_combo
                    combo.blockSignals(True)
                    combo.setCurrentText('Off')
                    combo.blockSignals(False)
                    combo.setToolTip('SSAO disabled on this GPU: ' + message)
        # Geometry has been submitted; upload one verified texture for the
        # following frame, leaving the GUI event loop responsive.
        self._upload_textures(max_uploads=1)

    def mousePressEvent(self,event):
        self._last = event.position()
        event.accept()

    def mouseMoveEvent(self,event):
        if self._last is not None and event.buttons():
            pos = event.position()
            dx, dy = pos.x()-self._last.x(), pos.y()-self._last.y()
            if (event.buttons() & (Qt.MiddleButton | Qt.RightButton) or
                    (event.buttons() & Qt.LeftButton and
                     (event.modifiers() & Qt.ShiftModifier or self.navigation_mode == 'pan'))):
                unit = 2.44 / max(1, self.height())
                self.pan[0] += dx * unit
                self.pan[1] -= dy * unit
            elif event.buttons() & Qt.LeftButton:
                if self.navigation_mode == 'zoom':
                    self.zoom = max(.18,min(5.0,self.zoom * (1.0 - dy*.008)))
                else:
                    self.rotation[0] += dx*.55
                    self.rotation[1] = max(-89,min(89,self.rotation[1]+dy*.45))
            self._last = pos
            self.update()
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        self._last = None
        event.accept()

    def wheelEvent(self,event):
        delta = event.angleDelta().y()
        self.zoom=max(.18,min(5.0,self.zoom*(1.12 if delta>0 else 1/1.12)))
        self.update()
        event.accept()

    def closeEvent(self,event):
        if self.isValid():
            self.makeCurrent()
            try:
                self._release_textures()
                if self._renderer3 is not None:
                    self._renderer3.close()
                    self._renderer3 = None
                if self._ssao_effect is not None:
                    self._ssao_effect.close()
                    self._ssao_effect = None
            finally:
                self.doneCurrent()
        super().closeEvent(event)


class QtGPUModelPanel(QWidget):
    """Reusable GPU viewer with camera, material and animation controls."""

    def __init__(self,parent,mesh,materials=None, *, compact=False, prepared_geometry=None):
        super().__init__(parent)
        self.compact = compact
        self.setObjectName('gpuModelControlPanel')
        self.setMinimumSize(300, 230)
        layout=QVBoxLayout(self)
        layout.setContentsMargins(3,3,3,3)
        layout.setSpacing(2)
        self._info=QLabel(mesh.source_name+'  •  '+mesh.description)
        self._info.setWordWrap(False)
        self._info.setToolTip(mesh.source_name + '  •  ' + mesh.description)
        self._info.setMaximumHeight(28)
        # A viewport breadcrumb replaces the layout-consuming title row.
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        # Icon-only actions fit compact embedded GPU panes without clipping.
        # Labels remain in Qt accessible names and hover tooltips.
        toolbar.setSpacing(3)
        mode_label = QLabel('Object Mode')
        mode_label.setToolTip('Read-only CryEngine preview')
        mode_label.hide()
        self._overlay_buttons = {}
        for section in ('View', 'Materials', 'Animation'):
            button = QToolButton(self)
            button.setObjectName('gpuOverlayButton' + section)
            button.setText(section + ' ▾')
            button.setIcon(_viewport_tool_icon(section))
            button.setIconSize(QSize(17, 17))
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setFixedSize(32, 32)
            button.setAccessibleName(section + ' tools')
            button.setToolTip('Open '+section+' tools over the 3D preview')
            button.setCheckable(True)
            button.clicked.connect(
                lambda active=False, key=section: self.toggle_overlay(key, active))
            toolbar.addWidget(button)
            self._overlay_buttons[section] = button
        toolbar.addStretch(1)
        self._grid_toggle = QToolButton(self)
        self._grid_toggle.setText('Grid')
        self._grid_toggle.setIcon(_viewport_tool_icon('Grid'))
        self._grid_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._grid_toggle.setCheckable(True)
        self._grid_toggle.setChecked(True)
        self._grid_toggle.toggled.connect(lambda state: (setattr(self.canvas,'grid_visible', state),self.canvas.update()))
        self._grid_toggle.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._grid_toggle.setIconSize(QSize(18, 18))
        self._grid_toggle.setFixedSize(32, 32)
        self._grid_toggle.setAccessibleName(self._grid_toggle.text())
        toolbar.addWidget(self._grid_toggle)
        self._shading_group = QButtonGroup(self)
        self._shading_group.setExclusive(True)
        self._solid_shading = QToolButton(self)
        self._solid_shading.setText('Solid')
        self._solid_shading.setIcon(_viewport_tool_icon('Solid'))
        self._solid_shading.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._solid_shading.setToolTip('Preview without texture colors')
        self._solid_shading.setCheckable(True)
        self._shading_group.addButton(self._solid_shading)
        self._solid_shading.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._solid_shading.setIconSize(QSize(18, 18))
        self._solid_shading.setFixedSize(32, 32)
        self._solid_shading.setAccessibleName(self._solid_shading.text())
        toolbar.addWidget(self._solid_shading)
        self._material_shading = QToolButton(self)
        self._material_shading.setText('Material')
        self._material_shading.setIcon(_viewport_tool_icon('Materials'))
        self._material_shading.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._material_shading.setToolTip('Preview with textures and CryEngine material approximation')
        self._material_shading.setCheckable(True)
        self._material_shading.setChecked(True)
        self._shading_group.addButton(self._material_shading)
        self._material_shading.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._material_shading.setIconSize(QSize(18, 18))
        self._material_shading.setFixedSize(32, 32)
        self._material_shading.setAccessibleName(self._material_shading.text())
        toolbar.addWidget(self._material_shading)
        self._wire_toggle = QToolButton(self)
        self._wire_toggle.setText('Wire')
        self._wire_toggle.setIcon(_viewport_tool_icon('Wire'))
        self._wire_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._wire_toggle.setCheckable(True)
        self._wire_toggle.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._wire_toggle.setIconSize(QSize(18, 18))
        self._wire_toggle.setFixedSize(32, 32)
        self._wire_toggle.setAccessibleName(self._wire_toggle.text())
        toolbar.addWidget(self._wire_toggle)
        self._play_shortcut = QToolButton(self)
        self._play_shortcut.setText('Play')
        self._play_shortcut.setToolTip('Play / pause current animation')
        self._play_shortcut.setIcon(navigation_icon('play', size=18))
        self._play_shortcut.setIconSize(QSize(18, 18))
        self._play_shortcut.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._play_shortcut.setAccessibleName('Play or pause animation')
        self._play_shortcut.setFixedSize(32, 32)
        self._play_shortcut.clicked.connect(self.toggle_play)
        layout.addLayout(toolbar)
        self.canvas=QtGLModelCanvas(mesh,materials,self,prepared_geometry=prepared_geometry)
        self.canvas.setObjectName('embeddedGpuCanvas')
        layout.addWidget(self.canvas,1)
        self._info.setParent(self.canvas)
        self._info.setObjectName('viewportBreadcrumb')
        self._info.setText('User Perspective  •  '+mesh.source_name)
        self._info.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        # Viewport labels use shared semantic chrome, not fixed theme colors.
        self._info.show()
        self._overlay = QScrollArea(self.canvas)
        self._overlay.setObjectName('modelOverlayControls')
        self._overlay.setWidgetResizable(True)
        self._overlay.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._overlay.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self._overlay.setToolTip('Advanced tools are collapsed; expand only the settings you need.')
        # Shared theme colors cover the scrollable inspector in light/dark mode.
        self._overlay_content = QWidget()
        self._overlay_content.setObjectName('modelOverlayContent')
        self._overlay_body = QVBoxLayout(self._overlay_content)
        # Qt must recompute scrollable content height after expanding a
        # disclosure. Otherwise QScrollArea compresses or clips lower rows.
        self._overlay_body.setSizeConstraint(QLayout.SetMinAndMaxSize)
        self._overlay_body.setContentsMargins(7, 5, 7, 5)
        self._overlay.setWidget(self._overlay_content)
        self._overlay_sections = {}
        self._active_overlay = None
        for section in ('View', 'Materials', 'Animation'):
            page = QWidget(self._overlay_content)
            part = QVBoxLayout(page)
            part.setSizeConstraint(QLayout.SetMinAndMaxSize)
            part.setContentsMargins(0, 0, 0, 0)
            part.setSpacing(4)
            self._overlay_body.addWidget(page)
            self._overlay_sections[section] = (page, part)
            page.setVisible(False)
        self._overlay.hide()
        self.canvas.installEventFilter(self)
        self._create_viewport_navigation()
        options = QWidget(self)
        options_box = QVBoxLayout(options)
        options_box.setContentsMargins(0,0,0,0)
        line=QHBoxLayout()
        options_box.addLayout(line)
        wire=QCheckBox('Wireframe')
        self.wire_checkbox = wire
        wire.toggled.connect(lambda value:(setattr(self.canvas,'wireframe',value),self.canvas.update()))
        wire.toggled.connect(self._wire_toggle.setChecked)
        self._wire_toggle.toggled.connect(wire.setChecked)
        line.addWidget(wire)
        textured=QCheckBox('Textures')
        self.texture_checkbox = textured
        textured.setChecked(True)
        textured.toggled.connect(lambda value:(setattr(self.canvas,'textured',value),self.canvas.update()))
        textured.toggled.connect(lambda value: self._material_shading.setChecked(value) if value else self._solid_shading.setChecked(True))
        self._solid_shading.clicked.connect(lambda: textured.setChecked(False))
        self._material_shading.clicked.connect(lambda: textured.setChecked(True))
        line.addWidget(textured)
        line.addWidget(QLabel('Shading'))
        shading = QComboBox()
        self.shading_combo = shading
        shading.addItems(('Faceted (hard edges)', 'Smooth'))
        shading.currentIndexChanged.connect(
            lambda i: self.canvas.set_shading('Smooth' if i else 'Faceted'))
        line.addWidget(shading)
        line.addStretch()
        detail_line = QHBoxLayout()
        options_box.addLayout(detail_line)
        detail_line.addWidget(QLabel('Quality'))
        quality=QComboBox()
        self.quality_combo = quality
        quality.addItems(('Performance','High'))
        quality.currentTextChanged.connect(self.canvas.set_quality)
        detail_line.addWidget(quality)
        reset=QPushButton('Reset Camera')
        reset.clicked.connect(self.reset_view)
        detail_line.addWidget(reset)
        detail_line.addStretch()
        self._view_advanced = self._compact_group(
            self._overlay_sections['View'][1], 'Display & quality', opened=False)
        self._view_advanced.addWidget(options)
        # Short, always-visible controls also work in the embedded viewer.
        studio = QHBoxLayout()
        enhanced=QCheckBox('Renderer 3.0')
        enhanced.setToolTip('Enhanced shader supports normal, specular and explicit gloss/smoothness maps plus opacity. Legacy fallback remains available.')
        enhanced.setChecked(True)
        enhanced.toggled.connect(self.canvas.set_enhanced_materials)
        self.enhanced_checkbox=enhanced
        studio.addWidget(enhanced)
        studio.addWidget(QLabel('Camera'))
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(('Isometric','Front','Back','Left','Right','Top','Bottom'))
        self.camera_combo.currentTextChanged.connect(self.canvas.set_camera_preset)
        studio.addWidget(self.camera_combo)
        lighting_row = QHBoxLayout()
        lighting_row.addWidget(QLabel('Light'))
        self.lighting_slider = QSlider(Qt.Horizontal)
        self.lighting_slider.setRange(20,200)
        self.lighting_slider.setValue(100)
        self.lighting_slider.setMaximumWidth(125)
        self.lighting_slider.valueChanged.connect(self.canvas.set_light_strength)
        lighting_row.addWidget(self.lighting_slider, 1)
        lighting_row.addWidget(QLabel('SSAO'))
        self.ssao_combo = QComboBox()
        self.ssao_combo.setObjectName('modelSsaoQuality')
        self.ssao_combo.addItems(('Off','Low','Medium','High'))
        self.ssao_combo.setCurrentText('Off')
        self.ssao_combo.setToolTip(
            'GPU screen-space contact shadows. Off by default; higher levels '
            'sample more depth neighbours. Does not require AO texture maps.')
        self.ssao_combo.currentTextChanged.connect(self.canvas.set_ssao_quality)
        lighting_row.addWidget(self.ssao_combo)
        studio.addStretch()
        studio_detail = QHBoxLayout()
        self.background_combo = QComboBox()
        self.background_combo.addItems(('Dark','Studio Grey'))
        self.background_combo.setCurrentText('Studio Grey')
        self.background_combo.currentTextChanged.connect(self.canvas.set_background)
        studio_detail.addWidget(QLabel('Background'))
        studio_detail.addWidget(self.background_combo)
        save_view = QPushButton('Save View…')
        save_view.clicked.connect(self.save_viewport)
        studio_detail.addWidget(save_view)
        studio_detail.addStretch()
        self._overlay_sections['View'][1].insertLayout(0,studio)
        self._overlay_sections['View'][1].insertLayout(1,lighting_row)
        self._view_advanced.addLayout(studio_detail)
        inspector=QHBoxLayout()
        inspector.addWidget(QLabel('Part'))
        self.part_select=QComboBox()
        self.part_select.setObjectName('modelMaterialPartInspector')
        self.part_select.currentIndexChanged.connect(
            lambda _: self.canvas.set_visible_material_slot(self.part_select.currentData()))
        inspector.addWidget(self.part_select,1)
        uvbutton=QPushButton('UV…')
        uvbutton.setToolTip('Inspect the selected mesh UV layout')
        uvbutton.clicked.connect(self.show_uv_layout)
        inspector.addWidget(uvbutton)
        view_projection = QHBoxLayout()
        view_projection.addWidget(QLabel('Projection'))
        self.projection_select=QComboBox()
        self.projection_select.addItems(('Orthographic','Perspective'))
        self.projection_select.currentTextChanged.connect(self.canvas.set_projection)
        view_projection.addWidget(self.projection_select)
        view_projection.addStretch()
        self._view_advanced.addLayout(view_projection)
        inspector_secondary = QHBoxLayout()
        inspector_secondary.addWidget(QLabel('Gloss'))
        self.gloss_slider=QSlider(Qt.Horizontal)
        self.gloss_slider.setRange(0,100)
        self.gloss_slider.setValue(35)
        self.gloss_slider.setMaximumWidth(95)
        self.gloss_slider.setToolTip(
            'Overall specular highlight strength. Explicit gloss/smoothness maps '
            'control highlight sharpness across the texture.')
        self.gloss_slider.valueChanged.connect(self.canvas.set_specular)
        inspector_secondary.addWidget(self.gloss_slider)
        inspector_secondary.addStretch()
        self._overlay_sections['Materials'][1].addLayout(inspector)
        self._overlay_sections['Materials'][1].addLayout(inspector_secondary)
        self.solid_geometry = QCheckBox('Show all character geometry (ignore alpha)')
        self.solid_geometry.setToolTip('Diagnostic view: shows every decoded part even if a CryEngine material uses unsupported transparency. Does not create absent mesh parts.')
        self.solid_geometry.setChecked(self.canvas.force_opaque_preview)
        self.solid_geometry.toggled.connect(lambda checked: (setattr(self.canvas, 'force_opaque_preview', checked), self.canvas.update()))
        self._material_advanced = self._compact_group(
            self._overlay_sections['Materials'][1],
            'Diagnostics & lighting', opened=False)
        self._material_advanced.addWidget(self.solid_geometry)
        self.unlit_diffuse_checkbox=QCheckBox('Unlit diffuse (inspect dark textures)')
        self.unlit_diffuse_checkbox.setObjectName('modelUnlitDiffuseDiagnostic')
        self.unlit_diffuse_checkbox.setToolTip(
            'Show the original diffuse texture pixels without lighting, normal maps, '
            'or specular shading. A black surface here may have a genuinely dark '
            'DDS or an unresolved material slot.')
        self.unlit_diffuse_checkbox.toggled.connect(
            lambda checked:(setattr(self.canvas,'unlit_diffuse',checked),
                            self.canvas.update()))
        self._material_advanced.addWidget(self.unlit_diffuse_checkbox)
        normal_row = QHBoxLayout()
        normal_row.addWidget(QLabel('Normal Y'))
        self.normal_convention_combo = QComboBox(self)
        self.normal_convention_combo.setObjectName('modelNormalYDiagnostic')
        self.normal_convention_combo.addItems((
            'Source (unchanged)', 'Flip green (DX to GL)', 'Flat (no normal maps)'))
        self.normal_convention_combo.setToolTip(
            'Compare a fixed camera/light with the normal map unchanged, '
            'its green channel inverted, or with normal details disabled. '
            'Preview only: neither original DDS nor Blender exports change.')
        self.normal_convention_combo.currentTextChanged.connect(
            self.canvas.set_normal_diagnostic)
        normal_row.addWidget(self.normal_convention_combo, 1)
        self._material_advanced.addLayout(normal_row)
        self.normal_diagnostic_info = QLabel(self)
        self.normal_diagnostic_info.setObjectName('modelNormalYSourceStatus')
        self.normal_diagnostic_info.setWordWrap(True)
        self._material_advanced.addWidget(self.normal_diagnostic_info)
        ambient_row = QHBoxLayout()
        ambient_row.addWidget(QLabel('Studio fill'))
        self.studio_fill_slider = QSlider(Qt.Horizontal)
        self.studio_fill_slider.setObjectName('modelStudioFillLighting')
        self.studio_fill_slider.setRange(0, 100)
        self.studio_fill_slider.setValue(35)
        self.studio_fill_slider.setToolTip(
            'Add a soft secondary light for dark materials. Does not alter source textures.')
        self.studio_fill_slider.valueChanged.connect(
            lambda value: (setattr(self.canvas, 'studio_fill', value/100.),
                           self.canvas.update()))
        ambient_row.addWidget(self.studio_fill_slider, 1)
        self._material_advanced.addLayout(ambient_row)
        self.diffuse_source_info = QLabel('Diffuse DDS diagnostics will appear here.')
        self.diffuse_source_info.setObjectName('modelDiffuseSourceInfo')
        self.diffuse_source_info.setWordWrap(True)
        self.diffuse_source_info.setObjectName('viewHint')
        self._material_advanced.addWidget(self.diffuse_source_info)
        self.part_select.currentIndexChanged.connect(
            lambda _index: (self._update_source_brightness(),
                            self._update_normal_source_status()))
        self._populate_material_slots(mesh,materials)
        self._prepare_animation_controls(self._overlay_sections['Animation'][1])
        self.native_info = _CompactDiagnosticText(self)
        self.native_info.setText('Native game animations: not scanned')
        # Verbose CAF/DBA diagnostics are available on demand rather than
        # consuming the model viewport every time Animations is opened.
        self._native_details = self._compact_group(
            self.animation_sections['Animations'][1].layout(),
            'Selection details', opened=False)
        self.native_info.setMaximumHeight(84)
        self._native_details.addWidget(self.native_info)
        foot=QLabel('Renderer 3.0 approximates CryEngine materials; check optional maps in the texture diagnostics. '
                    'Left drag: rotate · Right/middle/Shift drag: pan · Wheel: zoom. '
                    'Faceted shading keeps hard edges; smooth shading is optional.')
        foot.setWordWrap(True)
        self._view_advanced.addWidget(foot)
        self._build_compact_timeline(layout)
        QTimer.singleShot(0,self._position_overlay)

    def _create_viewport_navigation(self):
        self._tool_rail = QFrame(self.canvas)
        self._tool_rail.setObjectName('viewportToolRail')
        # Reusable toolbar surface comes from qt_design_system.
        rail = QVBoxLayout(self._tool_rail)
        rail.setContentsMargins(3,4,3,4)
        rail.setSpacing(3)
        self._navigation_group = QButtonGroup(self)
        self._navigation_group.setExclusive(True)
        for symbol,mode,hint in (
            ('R','orbit','Orbit with left drag'),
            ('H','pan','Pan with left drag'),
            ('Z','zoom','Zoom with vertical left drag')):
            button = QToolButton(self._tool_rail)
            button.setText(symbol)
            button.setToolTip(hint)
            button.setFixedSize(31,34)
            button.setCheckable(True)
            button.setChecked(mode == 'orbit')
            self._navigation_group.addButton(button)
            button.clicked.connect(lambda _checked=False,m=mode: setattr(self.canvas,'navigation_mode',m))
            rail.addWidget(button)
        fit = QToolButton(self._tool_rail)
        fit.setText('F')
        fit.setToolTip('Frame the character')
        fit.setFixedSize(31,34)
        fit.clicked.connect(self.reset_view)
        rail.addWidget(fit)
        rail.addStretch()
        self._axis_widget = QFrame(self.canvas)
        self._axis_widget.setObjectName('viewportAxisControls')
        # The camera axis rail shares the same neutral surface.
        axes = QHBoxLayout(self._axis_widget)
        axes.setContentsMargins(3,3,3,3)
        axes.setSpacing(2)
        for name,preset,color in (
            ('X','Right','#d87982'), ('Y','Back','#91b88b'), ('Z','Top','#78a6d2')):
            button = QToolButton(self._axis_widget)
            button.setText(name)
            button.setToolTip('View along '+name+' axis')
            button.setStyleSheet('QToolButton { color: '+color+'; font-weight: bold; }')
            button.clicked.connect(lambda _checked=False,p=preset: self.canvas.set_camera_preset(p))
            axes.addWidget(button)

    def _position_navigation(self):
        if not hasattr(self,'_tool_rail'):
            return
        self._tool_rail.setGeometry(8,43,40,min(174,max(34,self.canvas.height()-48)))
        self._tool_rail.raise_()
        self._axis_widget.setGeometry(max(0,self.canvas.width()-117),8,108,36)
        self._axis_widget.raise_()
        self._info.setGeometry(12,7,max(30,self.canvas.width()-145),29)
        self._info.raise_()

    def _build_compact_timeline(self,parent_layout):
        strip = QFrame(self)
        strip.setObjectName('viewportTimeline')
        # Timeline retains compact Qt-native controls and inherited colors.
        bar = QHBoxLayout(strip)
        bar.setContentsMargins(6,2,6,2)
        bar.setSpacing(5)
        bar.addWidget(QLabel('Timeline'))
        prev = QToolButton(strip)
        prev.setText('◀')
        prev.setToolTip('Previous frame')
        prev.clicked.connect(lambda: self.step_frame(-1))
        bar.addWidget(prev)
        bar.addWidget(self._play_shortcut)
        nxt = QToolButton(strip)
        nxt.setText('▶|')
        nxt.setToolTip('Next frame')
        nxt.clicked.connect(lambda: self.step_frame(1))
        bar.addWidget(nxt)
        bar.addWidget(self.seek,1)
        bar.addWidget(self.anim_time)
        more = QToolButton(strip)
        more.setText('⋮')
        more.setToolTip('Animation tools')
        more.clicked.connect(lambda: self.toggle_overlay('Animation'))
        bar.addWidget(more)
        strip.setFixedHeight(38)
        parent_layout.addWidget(strip)

    def _compact_group(self, layout, label, *, opened=False):
        """Small disclosure for optional controls; retains all tool widgets."""
        heading = QToolButton(self)
        heading.setObjectName('gpuCompactGroup' + label.replace(' ', '').replace('&',''))
        heading.setText(('▾  ' if opened else '▸  ') + label)
        heading.setToolButtonStyle(Qt.ToolButtonTextOnly)
        heading.setCheckable(True)
        heading.setChecked(opened)
        heading.setMinimumHeight(24)
        layout.addWidget(heading)
        content = QWidget(self)
        details = QVBoxLayout(content)
        details.setContentsMargins(3, 2, 3, 3)
        details.setSpacing(3)
        layout.addWidget(content)
        content.setVisible(opened)
        def set_open(active):
            content.setVisible(active)
            heading.setText(('▾  ' if active else '▸  ') + label)
            QTimer.singleShot(0,self._update_animation_scrolling)
        heading.toggled.connect(set_open)
        return details

    def toggle_overlay(self, section, shown=None):
        """Display just one model tool group over the viewport."""
        if section not in self._overlay_sections:
            return
        show = (self._active_overlay != section if shown is None else bool(shown))
        self._active_overlay = section if show else None
        for key, (page, _) in self._overlay_sections.items():
            page.setVisible(show and key == section)
            button = self._overlay_buttons[key]
            button.blockSignals(True)
            button.setChecked(show and key == section)
            button.blockSignals(False)
        self._overlay.setVisible(show)
        if show:
            self._position_overlay()
            self._overlay.verticalScrollBar().setValue(0)
            self._overlay.raise_()
            QTimer.singleShot(0, self._update_animation_scrolling)

    def _update_animation_scrolling(self):
        if not hasattr(self, '_overlay') or self._overlay is None:
            return
        self._overlay_content.layout().invalidate()
        self._overlay_content.updateGeometry()
        self._overlay_content.adjustSize()
        self._position_overlay()

    def _position_overlay(self):
        if not hasattr(self, '_overlay') or self._overlay is None:
            return
        width = max(190, min(355 if self._active_overlay == 'Animation' else 370,
                             self.canvas.width() - 70))
        page = self._overlay_sections.get(self._active_overlay)
        wanted = (page[0].sizeHint().height() + 20) if page else 200
        caps = {'View': 240, 'Materials': 255, 'Animation': 315}
        height = max(80, min(wanted, caps.get(self._active_overlay, 260),
                             self.canvas.height() - 55))
        self._overlay.setGeometry(56, 43, width, height)
        self._position_navigation()

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'canvas', None) and event.type() == QEvent.Resize:
            self._position_overlay()
        return super().eventFilter(obj, event)

    def _update_source_brightness(self):
        from material_preview import diffuse_source_stats
        materials = self.canvas.materials
        slots = (sorted({part[2] for part in self.canvas.mesh.subsets})
                 if self.canvas.mesh.subsets else [0])
        selected = self.part_select.currentData()
        if selected is not None:
            slots = [selected]
        rows = [(slot, diffuse_source_stats(materials, slot)) for slot in slots]
        found = [(slot, data) for slot, data in rows if data is not None]
        if not found:
            message = 'No decoded diffuse DDS for the selected material slot(s).'
        elif selected is not None:
            average, black = found[0][1]
            message = ('DDS source brightness: %.0f/255 average; '
                       '%d%% of sampled pixels near black.' % (average, black))
            if black > 65:
                message += ' The source texture itself is dark; try Unlit diffuse.'
            else:
                message += ' Dark shading may come from normals, UVs or lighting.'
        else:
            dark = sum(data[1] > 65 for _,data in found)
            message = ('%d/%d used slots have decoded diffuse DDS; %d are '
                       'mostly dark in the source image.' %
                       (len(found),len(slots),dark))
        if selected is not None and materials:
            if (selected < len(materials.uv_available) and
                    not materials.uv_available[selected]):
                message += ('\nThis CDF part has no decoded UV coordinates; '
                            'the texture cannot be mapped. Untextured shading is used.')
            source = (materials.diffuse_sources[selected]
                      if selected < len(materials.diffuse_sources) else None)
            ref = (materials.diffuse_references[selected]
                   if selected < len(materials.diffuse_references) else '')
            if source:
                message += ('\nActual source: ' + source[0] +
                            '  ·  PAK: ' + source[1])
            elif ref:
                message += ('\nMissing cooked DDS for ' + ref +
                            '. Check the owning texture PAK in Asset Library.')
            else:
                message += '\nNo Diffuse map declared in the MTL for this slot.'
        self.diffuse_source_info.setText(message)

    def _update_normal_source_status(self):
        """Explain the selected slot's actual decoded maps and preview-only mode."""
        materials = self.canvas.materials
        if materials is None:
            message = 'No material maps resolved yet. Wait for background DDS loading.'
        else:
            slots = (sorted({subset[2] for subset in self.canvas.mesh.subsets})
                     if self.canvas.mesh.subsets else [0])
            selected = self.part_select.currentData()
            if selected is not None:
                slots = [selected]
            primary = sum(1 for slot in slots
                          if 0 <= slot < len(getattr(materials, 'normal_maps', ()))
                          and materials.normal_maps[slot] is not None)
            detail = sum(1 for slot in slots
                         if 0 <= slot < len(getattr(materials, 'detail_normal_maps', ()))
                         and materials.detail_normal_maps[slot] is not None)
            message = ('%d base + %d detail normal map(s) decoded across %d part(s).'
                       % (primary, detail, len(slots)))
            if not (primary or detail):
                message += ' No normal mapping to compare on this selection.'
            else:
                message += ' Keep camera and light fixed; compare highlights and indentations.'
        message += ' Preview only — original DDS and GLB export stay unchanged.'
        if hasattr(self, 'normal_diagnostic_info'):
            self.normal_diagnostic_info.setText(message)

    def _populate_material_slots(self,mesh,materials):
        from material_preview import diffuse_source_stats
        slots=sorted({part[2] for part in mesh.subsets}) if mesh.subsets else [0]
        self.part_select.blockSignals(True)
        self.part_select.clear()
        self.part_select.addItem('All parts',None)
        for slot in slots:
            title=(materials.names[slot] if materials and slot<len(materials.names)
                   else 'Material slot %d'%slot)
            self.part_select.addItem('%d — %s'%(slot,title[:75]),slot)
            data = diffuse_source_stats(materials,slot)
            source = (materials.diffuse_sources[slot] if materials and
                      slot < len(materials.diffuse_sources) else None)
            expected = (materials.diffuse_references[slot] if materials and
                        slot < len(materials.diffuse_references) else '')
            tip = ('No decoded diffuse DDS for this slot.'
                   if data is None else
                   ('Decoded DDS mean brightness %.0f/255; '
                    '%d%% near-black source pixels' % data))
            if source:
                tip += '\nLoaded: %s\nPAK: %s' % source
            elif expected:
                tip += '\nMTL wants ' + expected
            if (materials and slot < len(materials.uv_available) and
                    not materials.uv_available[slot]):
                tip += '\nNO UV STREAM — original DDS cannot map to this part'
            self.part_select.setItemData(self.part_select.count()-1,tip,Qt.ToolTipRole)
        self.part_select.blockSignals(False)
        self.canvas.set_visible_material_slot(None)
        if hasattr(self,'diffuse_source_info'):
            self._update_source_brightness()
        self._update_normal_source_status()

    def show_uv_layout(self):
        from io import BytesIO
        from PIL import Image, ImageDraw
        from PySide6.QtGui import QPixmap
        mesh=self.canvas.mesh
        if len(mesh.uvs)!=len(mesh.vertices):
            QMessageBox.information(self,'UV layout','This mesh has no decoded UV coordinates.')
            return
        image=Image.new('RGB',(800,800),(25,25,29))
        painter=ImageDraw.Draw(image)
        for line in range(0,801,100):
            painter.line(((line,0),(line,799)),fill=(58,58,64))
            painter.line(((0,line),(799,line)),fill=(58,58,64))
        slot=self.part_select.currentData()
        faces=mesh.triangles
        parts=mesh.subsets or ((0,len(faces)*3,0),)
        for start,count,material in parts:
            if slot is not None and material!=slot:
                continue
            for face in faces[start//3:(start+count)//3]:
                coords=[(mesh.uvs[i][0]*800,(1-mesh.uvs[i][1])*800)
                        for i in face]
                painter.line(coords+[coords[0]],fill=(229,103,119),width=1)
        output=BytesIO()
        image.save(output,format='PNG')
        dialog=QDialog(self)
        dialog.setWindowTitle('UV Layout — '+mesh.source_name)
        lay=QVBoxLayout(dialog)
        preview=QLabel()
        pix=QPixmap()
        pix.loadFromData(output.getvalue(),'PNG')
        preview.setPixmap(pix)
        lay.addWidget(preview)
        dialog.resize(830,850)
        dialog.exec()

    def set_static_mesh(self, mesh, materials=None, prepared_geometry=None):
        """Switch project model while ensuring prior clip playback is stopped."""
        self.anim_timer.stop()
        self.anim_playing = False
        self.play_button.setText('Play')
        self._play_shortcut.setText('▶  Play')
        self.animated = None
        self.timeline_markers.clear()
        self.timeline_markers.addItem('Markers',None)
        self.playlist_items.clear()
        self.clip_select.blockSignals(True)
        self.clip_select.clear()
        self.clip_select.blockSignals(False)
        self.anim_position = 0.
        self.seek.setValue(0)
        self.anim_time.setText('0.00 / 0.00 s')
        self.anim_status.setText(
            'Load an animated GLB or import original clips using an external converter.')
        self._native_generation += 1
        self.native_clip_select.clear()
        self.native_info.setText('Native game animations: not scanned')
        self.canvas.set_mesh(mesh, materials, prepared_geometry=prepared_geometry)
        self.solid_geometry.blockSignals(True)
        self.solid_geometry.setChecked(self.canvas.force_opaque_preview)
        self.solid_geometry.blockSignals(False)
        self._populate_material_slots(mesh,materials)
        self._info.setText('User Perspective  •  ' + mesh.source_name)
        self._info.setToolTip(mesh.source_name + '  •  ' + mesh.description)

    def save_viewport(self):
        filename,_=QFileDialog.getSaveFileName(
            self,'Save GPU model viewport','Evolve-Model-Preview.png',
            'PNG images (*.png)')
        if not filename:
            return
        try:
            if not self.canvas.isValid():
                raise ValueError('GPU viewport is unavailable.')
            if not self.canvas.grabFramebuffer().save(filename, 'PNG'):
                raise ValueError('Could not write the viewport screenshot.')
        except (ValueError,OSError) as exc:
            QMessageBox.warning(self,'Viewport screenshot',str(exc))

    def reset_view(self):
        self.canvas.rotation[:]=[-30.,14.]
        self.canvas.zoom=1.0
        self.canvas.pan[:]=[0., 0.]
        self.canvas.update()

    def _prepare_animation_controls(self, layout):
        from PySide6.QtWidgets import QSlider, QToolButton, QWidget
        # Reusable Blender-like disclosure rows: collapsed sections consume
        # no height and keep the narrow GPU popover usable at 720p.
        self.animation_sections = {}
        def disclosure(title, opened):
            heading = QToolButton(self)
            heading.setObjectName('animationSection' + title.replace(' ', ''))
            heading.setText(('▾  ' if opened else '▸  ') + title)
            heading.setCheckable(True)
            heading.setChecked(opened)
            heading.setToolButtonStyle(Qt.ToolButtonTextOnly)
            heading.setMinimumHeight(25)
            heading.setObjectName('disclosureToggle')
            body = QWidget(self)
            inner = QVBoxLayout(body)
            inner.setSizeConstraint(QLayout.SetMinAndMaxSize)
            inner.setContentsMargins(3, 3, 3, 6)
            inner.setSpacing(4)
            layout.addWidget(heading)
            layout.addWidget(body)
            body.setVisible(opened)
            def toggle(active):
                body.setVisible(active)
                heading.setText(('▾  ' if active else '▸  ') + title)
                QTimer.singleShot(0, self._update_animation_scrolling)
            heading.toggled.connect(toggle)
            self.animation_sections[title] = (heading, body)
            return inner
        catalog_layout = disclosure('Animations', True)
        playback_layout = disclosure('Playback', False)
        playlist_layout = disclosure('Playlists & markers', False)
        import_row = QVBoxLayout()
        import_button = QPushButton('Load Animation GLB…')
        import_button.clicked.connect(self.load_animation)
        import_row.addWidget(import_button)
        original = QPushButton('Import Original CAF/DBA…')
        original.clicked.connect(self.import_original)
        import_row.addWidget(original)
        playback_layout.addLayout(import_row)
        line = QHBoxLayout()
        self.clip_select = QComboBox()
        self.clip_select.setMinimumWidth(130)
        self.clip_select.currentTextChanged.connect(self.select_clip)
        line.addWidget(self.clip_select, 1)
        self.play_button = QPushButton('Play')
        self.play_button.setIcon(navigation_icon('play'))
        self.play_button.clicked.connect(self.toggle_play)
        line.addWidget(self.play_button)
        playback_layout.addLayout(line)
        native_header = QVBoxLayout()
        native_header.setSpacing(5)
        self.native_search = QLineEdit()
        self.native_search.setObjectName('nativeAnimationSearch')
        self.native_search.setPlaceholderText('Search game animations…')
        self.native_search.setClearButtonEnabled(True)
        self.native_search.textChanged.connect(self._filter_native_catalog)
        native_header.addWidget(self.native_search, 1)
        self.native_refresh_button = QPushButton('Refresh Library')
        self.native_refresh_button.setIcon(navigation_icon('refresh'))
        self.native_refresh_button.setObjectName('refreshNativeLibrary')
        self.native_refresh_button.setToolTip(
            'Re-read the current Asset Library and update this model\'s animation references.')
        self.native_refresh_button.clicked.connect(self.refresh_native_catalog)
        self.native_refresh_button.setText('')
        self.native_refresh_button.setFixedWidth(35)
        self.native_refresh_button.setToolTip('Refresh animations from the Asset Library')
        search_row=QHBoxLayout()
        search_row.setSpacing(5)
        native_header.removeWidget(self.native_search)
        search_row.addWidget(self.native_search,1)
        search_row.addWidget(self.native_refresh_button)
        native_header.addLayout(search_row)
        catalog_layout.addLayout(native_header)

        native_filters = QHBoxLayout()
        native_filters.setSpacing(5)
        self.native_state_filter = QComboBox()
        self.native_state_filter.setObjectName('nativeAnimationStateFilter')
        self.native_state_filter.addItems((
            'All sources', 'Ready in library', 'In PAK (extract on play)',
            'Not found', 'Layered / additive', 'Graph / unavailable', 'DBA converter'))
        self.native_state_filter.currentIndexChanged.connect(self._filter_native_catalog)
        native_filters.addWidget(self.native_state_filter)
        self.native_motion_filter = QComboBox()
        self.native_motion_filter.setObjectName('nativeAnimationMotionFilter')
        self.native_motion_filter.addItems((
            'All motions', 'Idle / Stand', 'Move / Run', 'Combat / Aim',
            'Actions / Other'))
        self.native_motion_filter.currentIndexChanged.connect(self._filter_native_catalog)
        native_filters.addWidget(self.native_motion_filter)
        catalog_layout.addLayout(native_filters)
        self.native_result_count = QLabel('No animation references loaded')
        self.native_result_count.setStyleSheet('color:#aeb1c1;font-size:11px;')
        self.native_result_count.setWordWrap(False)
        self.native_result_count.setToolTip('Showing filtered entries; other animations stay searchable.')
        catalog_layout.addWidget(self.native_result_count)
        self.native_capabilities = QLabel('')
        self.native_capabilities.setObjectName('historicalNativeCapabilityStatus')
        self.native_capabilities.setStyleSheet('color:#acb0bd;font-size:10px;')
        self.native_capabilities.setWordWrap(True)
        self.native_capabilities.hide()
        catalog_layout.addWidget(self.native_capabilities)

        self.native_clip_select = _CompactAnimationList(self)
        self.native_clip_select.setToolTip(
            'Search or filter the animation index above. Only a manageable '
            'subset is listed; no unrelated CAF is substituted.')
        self.native_clip_select.currentIndexChanged.connect(self._native_selection)
        self.play_native_button = QPushButton('▶  Play')
        self.play_native_button.setObjectName('playSelectedNativeAnimation')
        self.play_native_button.setMinimumHeight(30)
        self.play_native_button.setStyleSheet(
            'QPushButton#playSelectedNativeAnimation {background:#4e3f73;'
            'color:#fff;font-weight:600;border-radius:7px;padding:6px 14px;}'
            'QPushButton#playSelectedNativeAnimation:hover {background:#665394;}'
            'QPushButton#playSelectedNativeAnimation:disabled {'
            'background:#33333d;color:#888995;}')
        self.play_native_button.clicked.connect(self.load_native)
        catalog_layout.addWidget(self.native_clip_select, 1)
        native_controls = QHBoxLayout()
        native_controls.setSpacing(5)
        native_controls.addWidget(self.play_native_button, 1)
        self.cancel_native_button = QPushButton('×')
        self.cancel_native_button.setObjectName('cancelNativeAnimationDecode')
        self.cancel_native_button.setFixedWidth(34)
        self.cancel_native_button.setToolTip(
            'Cancel current character motion conversion without editing assets.')
        self.cancel_native_button.setEnabled(False)
        self.cancel_native_button.clicked.connect(self.cancel_native_conversion)
        native_controls.addWidget(self.cancel_native_button)
        catalog_layout.addLayout(native_controls)
        self.native_clip_select.itemDoubleClicked.connect(lambda _item: self.load_native())
        self.layer_row_widget = QWidget(self)
        layer_row = QVBoxLayout(self.layer_row_widget)
        layer_row.setContentsMargins(0, 1, 0, 1)
        base_row = QHBoxLayout()
        base_row.addWidget(QLabel('Base clip'))
        self.layer_base_select = QComboBox()
        self.layer_base_select.setObjectName('layeredBaseAnimation')
        self.layer_base_select.setToolTip(
            'Choose a real neutral/movement CAF from the same skeleton. '
            'The overlay affects only its animated joints.')
        base_row.addWidget(self.layer_base_select, 1)
        layer_row.addLayout(base_row)
        weight_row = QHBoxLayout()
        weight_row.addWidget(QLabel('Layer weight'))
        self.layer_weight = QSlider(Qt.Horizontal)
        self.layer_weight.setObjectName('layeredAnimationWeight')
        self.layer_weight.setRange(0, 100)
        self.layer_weight.setValue(65)
        self.layer_weight.setMinimumWidth(60)
        self.layer_weight.valueChanged.connect(self._layer_weight_changed)
        weight_row.addWidget(self.layer_weight, 1)
        self.layer_weight_label = QLabel('65%')
        weight_row.addWidget(self.layer_weight_label)
        layer_row.addLayout(weight_row)
        catalog_layout.addWidget(self.layer_row_widget)
        self.layer_row_widget.hide()
        self._layered_pair = None
        # Scrubber lives in the always-visible narrow playback strip.
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderMoved.connect(self.seek_to)
        self.anim_time = QLabel('0.00 / 0.00 s')
        timeline=QVBoxLayout()
        playback_options = QHBoxLayout()
        playback_options.addWidget(QLabel('Speed'))
        self.playback_speed=QComboBox()
        for label,number in (('0.25×',0.25),('0.5×',0.5),('1×',1.0),
                             ('1.5×',1.5),('2×',2.0)):
            self.playback_speed.addItem(label,number)
        self.playback_speed.setCurrentIndex(2)
        playback_options.addWidget(self.playback_speed)
        self.loop_enabled=QCheckBox('Loop')
        self.loop_enabled.setChecked(True)
        playback_options.addWidget(self.loop_enabled)
        timeline.addLayout(playback_options)
        step_controls = QHBoxLayout()
        previous=QPushButton('◀ Frame')
        previous.clicked.connect(lambda: self.step_frame(-1))
        step_controls.addWidget(previous)
        nxt=QPushButton('Frame ▶')
        nxt.clicked.connect(lambda: self.step_frame(1))
        step_controls.addWidget(nxt)
        step_controls.addStretch()
        timeline.addLayout(step_controls)
        timeline_secondary = QHBoxLayout()
        marker=QPushButton('Add Marker')
        marker.clicked.connect(self.add_timeline_marker)
        timeline_secondary.addWidget(marker)
        self.timeline_markers=QComboBox()
        self.timeline_markers.addItem('Markers',None)
        self.timeline_markers.currentIndexChanged.connect(self.goto_timeline_marker)
        timeline_secondary.addWidget(self.timeline_markers,1)
        playback_layout.addLayout(timeline)
        playback_layout.addLayout(timeline_secondary)
        navigation = QHBoxLayout()
        for title, seconds in (('−1s', -1.), ('−0.1s', -.1), ('+0.1s', .1), ('+1s', 1.)):
            item = QPushButton(title)
            item.clicked.connect(lambda checked=False, delta=seconds: self.nudge_timeline(delta))
            navigation.addWidget(item)
        playback_layout.addLayout(navigation)
        navigation_secondary = QVBoxLayout()
        self.playlist_items = QComboBox()
        self.playlist_items.setToolTip('Ordered decoded animation clips; no unconverted CAF data is inferred.')
        navigation_secondary.addWidget(self.playlist_items)
        playlist_buttons = QHBoxLayout()
        for title, callback in (('+ Clip', self.add_playlist_clip),
                                ('− Clip', self.remove_playlist_clip),
                                ('Prev', lambda: self.move_playlist(-1)),
                                ('Next', lambda: self.move_playlist(1))):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            playlist_buttons.addWidget(btn)
        navigation_secondary.addLayout(playlist_buttons)
        playlist_layout.addLayout(navigation_secondary)
        presets = QVBoxLayout()
        for title, callback in (('Save Playlist…', self.save_animation_playlist),
                                ('Load Playlist…', self.load_animation_playlist),
                                ('Compare Clips', self.compare_playlist_clips)):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            presets.addWidget(btn)
        playlist_layout.addLayout(presets)
        self.anim_status = QLabel('Animation playback: load a rigged GLB containing clips, or '
                                  'use an optional CryEngine converter for CAF/DBA files.')
        self.anim_status.setWordWrap(True)
        self.anim_status.setVisible(True)
        playback_layout.addWidget(self.anim_status)
        # Converter diagnostics must be readable without opening Playback
        # Controls; the live progress strip stays in the browser section.
        self.conversion_progress = QProgressBar(self)
        self.conversion_progress.setObjectName('animationConversionProgress')
        self.conversion_progress.setRange(0, 0)
        self.conversion_progress.setFixedHeight(11)
        self.conversion_progress.hide()
        catalog_layout.insertWidget(0, self.conversion_progress)
        self.conversion_status = _CompactDiagnosticText(self)
        self.conversion_status.setMaximumHeight(72)
        self.conversion_status.setText('Converter idle.')
        self.conversion_status.hide()
        catalog_layout.insertWidget(0, self.conversion_status)
        self.stop_conversion_button = QPushButton('Stop Conversion', self)
        self.stop_conversion_button.setObjectName('stopAnimationConversion')
        self.stop_conversion_button.clicked.connect(self.stop_conversion)
        self.stop_conversion_button.hide()
        catalog_layout.insertWidget(2, self.stop_conversion_button)
        self.animated = None
        self.anim_position = 0.
        self.anim_playing = False
        self.anim_timer = QTimer(self)
        self.anim_timer.setInterval(40)
        self.anim_timer.timeout.connect(self._animation_tick)
        self.anim_clock = QElapsedTimer()
        self.converter_process = None
        self._native_generation = 0
        self._native_references = ()
        self._auto_dba_loading = False
        self._native_loader = None
        self._pending_playback_preferences = None
        self._native_pool = QThreadPool(self)
        self._native_pool.setMaxThreadCount(2)
        self._native_signals = _NativeResult(self)
        self._native_signals.completed.connect(self._native_ready)

    def set_native_catalog(self, references, idle=None, description='',
                           loader=None, refresh_library=None, character=None,
                           historical_character=False):
        """Filter a potentially huge native animation catalog on the GUI thread."""
        from character_animation_catalog import (
            native_clip_playability, preferred_third_person_dba)
        self._native_generation += 1
        self._auto_dba_loading = False
        self.cancel_native_button.setEnabled(False)
        # A new character should never inherit the old character's filter,
        # which could conceal the exact 3P DBA and prevent default playback.
        identity = str(character or description).casefold()
        if identity != getattr(self, '_catalog_character', None):
            self.native_search.blockSignals(True)
            self.native_state_filter.blockSignals(True)
            self.native_motion_filter.blockSignals(True)
            self.native_search.clear()
            self.native_state_filter.setCurrentIndex(0)
            self.native_motion_filter.setCurrentIndex(0)
            self.native_search.blockSignals(False)
            self.native_state_filter.blockSignals(False)
            self.native_motion_filter.blockSignals(False)
        self._catalog_character = identity
        self._native_loader = loader
        self._native_references = tuple(references)
        self._historical_character = bool(historical_character)
        if historical_character:
            found = any(ref.kind == 'dba' and ref.available for ref in references)
            self.native_capabilities.setText(
                '2012 CryTek • Mesh: available • Rig: not yet validated • '
                'DBA: ' + ('found' if found else 'not indexed') +
                ' • Clips: not decoded • Playback: not verified • '
                'Rigged export: requires validation')
            self.native_capabilities.show()
        else:
            self.native_capabilities.hide()
        # The selected row from another character must not carry over:
        # the numeric catalog index may now name a completely different clip.
        self.native_clip_select.blockSignals(True)
        self.native_clip_select.clear()
        self.native_clip_select.blockSignals(False)
        previous_base = self.layer_base_select.currentData()
        self.layer_base_select.clear()
        for idx, ref in enumerate(references):
            state, _ = native_clip_playability(ref)
            if state not in ('candidate', 'extractable') or ref.kind != 'caf':
                continue
            if not (ref.available or ref.in_archive):
                continue
            self.layer_base_select.addItem(ref.name, idx)
        if previous_base is not None:
            for idx in range(self.layer_base_select.count()):
                if self.layer_base_select.itemData(idx) == previous_base:
                    self.layer_base_select.setCurrentIndex(idx)
                    break
        if self.layer_base_select.count() and self.layer_base_select.currentIndex() < 0:
            self.layer_base_select.setCurrentIndex(0)
        self._native_refresh = refresh_library
        self.native_refresh_button.setEnabled(refresh_library is not None)
        available = [(i, ref) for i, ref in enumerate(references)
                     if native_clip_playability(ref)[0] == 'candidate']
        preferred_db = preferred_third_person_dba(references, character)
        auto = next((i for i, ref in enumerate(references)
                     if preferred_db is not None and
                     ref.virtual_path == preferred_db.virtual_path
                     and ref.kind == 'dba'), -1)
        if auto < 0:
            auto = next((i for i, ref in available
                         if idle is not None and ref.virtual_path == idle.virtual_path
                         and ref.name == idle.name), -1)
        if auto < 0 and available:
            auto = min(available, key=lambda pair: (
                not any(x in pair[1].name.casefold()
                        for x in ('idle', 'stand', 'walk', 'run')),
                pair[1].name.casefold()))[0]
        self._native_suggested = auto
        counts = {k: 0 for k in
                  ('candidate', 'extractable', 'missing', 'layered', 'metadata', 'converter')}
        for ref in references:
            state, _ = native_clip_playability(ref)
            counts[state] += 1
        self._native_catalog_summary = (
            '%d loose CAF candidates • %d packed CAF paths • %d no standalone CAF '
            '• %d layered • %d graph • %d DBA databases • %d total mappings' %
            (counts['candidate'], counts['extractable'], counts['missing'],
             counts['layered'], counts['metadata'], counts['converter'],
             len(references)))
        if description:
            self._native_catalog_summary += ' • Map: ' + description[:75]
        self._filter_native_catalog()
        if auto >= 0 and loader:
            generation = self._native_generation
            def start_default():
                if (generation != self._native_generation or
                        self.native_clip_select.currentData() != auto):
                    return
                self._auto_dba_loading = (
                    preferred_db is not None and references[auto].kind == 'dba')
                self.load_native()
            QTimer.singleShot(120, start_default)

    @staticmethod
    def _animation_motion(name):
        label = name.casefold()
        if any(word in label for word in ('idle', 'stand', 'relax')):
            return 1
        if any(word in label for word in
               ('walk', 'run', 'sprint', 'jump', 'swim', 'move')):
            return 2
        if any(word in label for word in
               ('fire', 'shoot', 'aim', 'reload', 'combat', 'hit', 'attack')):
            return 3
        return 4

    def _filter_native_catalog(self, *_args):
        from character_animation_catalog import native_clip_playability
        selected = self.native_clip_select.currentData()
        if not isinstance(selected, int) or selected < 0:
            selected = getattr(self, '_native_suggested', -1)
        search = self.native_search.text().strip().casefold()
        filt = self.native_state_filter.currentIndex()
        motion = self.native_motion_filter.currentIndex()
        kinds = {1: 'candidate', 2: 'extractable', 3: 'missing',
                 4: 'layered', 5: 'metadata', 6: 'converter'}
        matching = []
        for i, ref in enumerate(self._native_references):
            state, _ = native_clip_playability(ref)
            if search and search not in (ref.name + ' ' + ref.virtual_path).casefold():
                continue
            if filt and state != kinds[filt]:
                continue
            if motion and self._animation_motion(ref.name) != motion:
                continue
            matching.append((i, ref, state))
        max_rows = 90
        shown = matching[:max_rows]
        # Keep the selected clip reachable even when it falls after row 90.
        if any(i == selected for i, _, _ in matching) and not any(
                i == selected for i, _, _ in shown):
            chosen = next(item for item in matching if item[0] == selected)
            shown = [chosen] + shown[:max_rows-1]
        self.native_clip_select.blockSignals(True)
        self.native_clip_select.clear()
        self.native_clip_select.addItem('Choose a clip…', -1)
        suffixes = {
            'candidate': '  • Ready', 'extractable': '  • Packed',
            'missing': '  • Missing', 'layered': '  • Layered',
            'metadata': '  • Graph',
            'converter': '  • DBA'}
        preferred = 0
        for position, (i, ref, state) in enumerate(shown, 1):
            self.native_clip_select.addItem(ref.name + suffixes[state], i)
            source = ('\nExtracted: ' + str(ref.file)
                      if ref.file is not None else '')
            if ref.dba_file is not None:
                source += ('\nPacked database: ' + str(ref.dba_file) +
                           '\nVirtual DBA: ' + str(ref.dba_virtual_path))
            self.native_clip_select.setItemData(
                position, native_clip_playability(ref)[1] +
                '\nVirtual: ' + ref.virtual_path + source,
                Qt.ToolTipRole)
            if i == selected:
                preferred = position
        self.native_clip_select.setCurrentIndex(preferred)
        self.native_clip_select.blockSignals(False)
        self.native_result_count.setText(
            '%d shown  ·  %d matching  ·  %d total' %
            (len(shown), len(matching), len(self._native_references)))
        self._native_selection(self.native_clip_select.currentIndex())

    def refresh_native_catalog(self):
        self.native_info.setText(
            'Refreshing the current Asset Library and model dependencies…')
        callback = getattr(self, '_native_refresh', None)
        if callback is not None:
            callback()

    def _native_selection(self, index):
        from character_animation_catalog import native_clip_playability
        self._auto_dba_loading = False
        # A pending asynchronous conversion belongs to the previous choice.
        # Do not let it replace the currently selected animation after a click.
        self._native_generation += 1
        ref_index = self.native_clip_select.itemData(index)
        if not isinstance(ref_index, int) or ref_index < 0 or ref_index >= len(self._native_references):
            self.play_native_button.setEnabled(False)
            self.native_info.setText(getattr(self, '_native_catalog_summary',
                                             'Choose a native animation.'))
            return
        ref = self._native_references[ref_index]
        state, detail = native_clip_playability(ref)
        if ref.annotation:
            detail += '\nSource annotation: ' + ref.annotation + ' (base motion preview)'
        self.layer_row_widget.setVisible(state == 'layered')
        layered_ready = (
            state == 'layered' and self._native_loader is not None
            and self.layer_base_select.currentData() is not None
            and bool(ref.available or ref.in_archive))
        self.play_native_button.setEnabled(
            bool(layered_ready or state == 'converter') or bool(
                self._native_loader and state in ('candidate', 'extractable', 'missing')))
        self.play_native_button.setText('▶  Play')
        self.play_native_button.setToolTip(
            detail + '\\nThe matching file will be extracted or converted '
            'when necessary. Animation and rig compatibility are validated.')
        if state == 'layered':
            available_note = (
                'Select a compatible base and adjust the layer weight, then play.'
                if layered_ready else
                'Layer source is not extracted or no compatible base CAF is indexed. '
                'Import its PAK or convert its DBA first.')
            self.native_info.setText(
                'Layered pose: ' + ref.name + '\n' + available_note +
                '\nThis is an approximate skeletal blend, not native CryEngine aim IK.')
        elif state == 'candidate':
            self.native_info.setText(
                'Ready to test: ' + ref.virtual_path +
                '\nNative decoder checks skeleton compatibility before playback.')
        else:
            source = str(ref.file or ref.dba_file or 'Not indexed')
            self.native_info.setText(
                detail + '\nGame path: ' + ref.virtual_path +
                '\nAsset Library path: ' + source)

    def cancel_native_conversion(self):
        # The background converter polls this generation and terminates its
        # child process. Old QThreadPool results are ignored by token.
        self._native_generation += 1
        self._auto_dba_loading = False
        self.cancel_native_button.setEnabled(False)
        self.play_native_button.setEnabled(True)
        self.native_info.setText('Motion decoding cancelled. Source assets were not changed.')

    def load_native(self):
        from character_animation_catalog import native_clip_playability
        i = self.native_clip_select.currentData()
        if (not isinstance(i, int)
                or i < 0 or i >= len(self._native_references)):
            return
        ref = self._native_references[i]
        state, reason = native_clip_playability(ref)
        if state == 'converter' and self._native_loader is None:
            self.import_original()
            return
        if self._native_loader is None:
            return
        if state == 'layered':
            base_index = self.layer_base_select.currentData()
            if (not isinstance(base_index, int) or
                    not 0 <= base_index < len(self._native_references)):
                self.native_info.setText('Choose a compatible base CAF.')
                return
            if not (ref.available or ref.in_archive):
                self.native_info.setText(
                    'This layered CAF has no verified extractable standalone track. '
                    'A DBA conversion may be needed.')
                return
            base = self._native_references[base_index]
            self._native_generation += 1
            token = self._native_generation
            self.play_native_button.setEnabled(False)
            self.native_info.setText(
                'Decoding base + additive/aimpose CAF on one verified skeleton…')
            self.cancel_native_button.setEnabled(True)
            self._native_pool.start(_NativeConversion(
                lambda unused: self._native_loader(ref, base_ref=base),
                ref, token, self._native_signals))
            return
        if state not in ('candidate', 'extractable', 'missing', 'converter'):
            self.native_info.setText(reason + '\n' + ref.virtual_path)
            self.play_native_button.setEnabled(False)
            return
        self._native_generation += 1
        token = self._native_generation
        self.native_info.setText(
            ('Converting DBA with the selected character rig…' if state == 'converter' else
             'Locating and extracting exact CAF from verified game PAK…'
             if state != 'candidate' else 'Decoding standalone CAF and rigged model…'))
        self.play_native_button.setEnabled(False)
        self.cancel_native_button.setEnabled(True)
        job = _NativeConversion(self._native_loader, ref, token, self._native_signals)
        self._native_pool.start(job)

    def _native_ready(self, token, result, error):
        if token != self._native_generation:
            return
        self.cancel_native_button.setEnabled(False)
        self.play_native_button.setEnabled(True)
        was_default_dba = self._auto_dba_loading
        self._auto_dba_loading = False
        if error:
            if getattr(self, '_historical_character', False):
                self.native_capabilities.setText(
                    '2012 CryTek • Mesh: available • Rig/clip: not verified • '
                    'DBA: indexed only • Playback/export: needs validated rig; '
                    'see diagnostic below')
            self.native_info.setText('Native animation unavailable: ' + error[:230])
            return
        payload, details = result
        try:
            if isinstance(payload, tuple):
                imported, geometry = payload
            else:
                from model_animation import AnimatedGLB
                imported = AnimatedGLB(payload, 'Native Evolve animation')
                geometry = None
            pair = details.get('_layered_pair')
            self._show_animated(
                imported, auto_play=not bool(pair),
                prepared_geometry=geometry, choose_dba_fallback=was_default_dba)
            self._layered_pair = pair
            if pair:
                base_name, overlay_name = pair
                if base_name not in imported.clips or overlay_name not in imported.clips:
                    raise ValueError('Converted layered animation lost its base or overlay track.')
                self.clip_select.setCurrentText(base_name)
                self._display_animation_frame()
                if not self.anim_playing:
                    self.toggle_play()
        except (ValueError, OSError) as exc:
            self.native_info.setText('Animation playback rejected: ' + str(exc)[:230])
            return
        if getattr(self, '_historical_character', False):
            self.native_capabilities.setText(
                '2012 CryTek • Mesh: available • Rig: validated • '
                'DBA: found • Clips: decoded and verified • '
                'Playback: available • Rigged export: available')
        resolved = details.get('_resolved_caf_reference')
        if resolved is not None:
            from character_animation_catalog import native_clip_playability
            i = self.native_clip_select.currentData()
            if isinstance(i, int) and 0 <= i < len(self._native_references):
                updated = list(self._native_references)
                updated[i] = resolved
                self._native_references = tuple(updated)
                self._filter_native_catalog()
                self.play_native_button.setText('▶  Play')
        origin = details.get('_animation_asset_origin', '')
        fetched = 'Extracted on demand from ' + origin + ' • ' if details.get('_extracted_on_play') else ''
        self.native_info.setText(
            fetched + 'Native motion playing • %d bone channels • %s' %
            (details.get('bone_channels',0), self.clip_select.currentText()))

    def load_animation(self):
        path, _ = QFileDialog.getOpenFileName(
            self, 'Choose a skinned GLB containing animation clips', '',
            'Animated GLB (*.glb)')
        if path:
            self._load_animation_file(path)

    def _load_animation_file(self, path):
        try:
            from model_animation import AnimatedGLB
            imported = AnimatedGLB.from_file(path)
            self._show_animated(imported, auto_play=True)
            self.anim_status.setText(
                '%d playable clips from %s. Original game files unchanged.' %
                (len(imported.clips), path))
        except Exception as exc:
            QMessageBox.warning(self, 'Animation import failed', str(exc))

    def _show_animated(self, imported, *, auto_play, prepared_geometry=None,
                       choose_dba_fallback=False):
        self.anim_timer.stop()
        self.anim_playing = False
        self.play_button.setText('Play')
        self._layered_pair = None
        self.animated = imported
        self.canvas.set_mesh(imported.preview_mesh, imported.materials,
                             prepared_geometry=prepared_geometry)
        self.clip_select.blockSignals(True)
        self.clip_select.clear()
        self.clip_select.addItems(tuple(imported.clips))
        self.playlist_items.clear()
        self.playlist_items.addItems(tuple(imported.clips))
        idle = next((name for name in imported.clips if
                     'idle' in name.casefold() and 'additive' not in name.casefold()), None)
        if idle is None and choose_dba_fallback:
            idle = next((name for name in imported.clips if
                         'stand' in name.casefold() and
                         'additive' not in name.casefold()), None)
            if idle is None and imported.clips:
                idle = next(iter(imported.clips))
                self.anim_status.setText(
                    'No named idle in this DBA; playing the first verified '
                    'clip. Choose another motion from Playback if needed.')
        if idle:
            self.clip_select.setCurrentText(idle)
        self.clip_select.blockSignals(False)
        self.anim_position = 0.
        if self._pending_playback_preferences:
            self.restore_playback_preferences(self._pending_playback_preferences)
        self._display_animation_frame()
        if auto_play and (idle or len(imported.clips)==1):
            self.toggle_play()

    def playback_preferences(self):
        return {'speed':self.playback_speed.currentData(),
                'loop':self.loop_enabled.isChecked(),
                'clip':self.clip_select.currentText(),
                'playlist':[self.playlist_items.itemText(i)
                            for i in range(self.playlist_items.count())][:100],
                'markers':[self.timeline_markers.itemData(i)
                           for i in range(1,self.timeline_markers.count())
                           if self.timeline_markers.itemData(i)][:500]}

    def restore_playback_preferences(self, config):
        if not isinstance(config,dict):
            return
        self._pending_playback_preferences = config
        speed=config.get('speed',1.)
        if type(speed) in (int,float) and 0.1<=speed<=4:
            idx=min(range(self.playback_speed.count()),
                    key=lambda i:abs(self.playback_speed.itemData(i)-speed))
            self.playback_speed.setCurrentIndex(idx)
        if type(config.get('loop')) is bool:
            self.loop_enabled.setChecked(config['loop'])
        if self.animated is None:
            return
        names=[x for x in config.get('playlist',[]) if isinstance(x,str)
               and x in self.animated.clips][:100]
        if names:
            self.playlist_items.clear()
            self.playlist_items.addItems(names)
        self.timeline_markers.clear()
        self.timeline_markers.addItem('Markers',None)
        for row in config.get('markers',[])[:500]:
            if (isinstance(row,(tuple,list)) and len(row)==2 and
                isinstance(row[0],str) and row[0] in self.animated.clips and
                type(row[1]) in (float,int) and 0<=row[1]<=self.animated.clips[row[0]][0]):
                self.timeline_markers.addItem('%s at %.2fs'%(row[0],row[1]),
                                              (row[0],float(row[1])))
        clip=config.get('clip')
        if clip in self.animated.clips:
            self.clip_select.setCurrentText(clip)
        self._pending_playback_preferences=None

    def nudge_timeline(self, seconds):
        if self.animated is None or not self.clip_select.currentText():
            return
        duration = self.animated.clips[self.clip_select.currentText()][0]
        self.anim_position = max(0., min(duration, self.anim_position + float(seconds)))
        self._display_animation_frame()

    def add_playlist_clip(self):
        clip = self.clip_select.currentText()
        if self.animated is not None and clip and self.playlist_items.findText(clip) < 0:
            self.playlist_items.addItem(clip)
            self.playlist_items.setCurrentText(clip)

    def remove_playlist_clip(self):
        index = self.playlist_items.currentIndex()
        if index >= 0:
            self.playlist_items.removeItem(index)

    def move_playlist(self, step):
        clips = [self.playlist_items.itemText(i) for i in range(self.playlist_items.count())]
        if not clips or self.animated is None:
            return
        index = self.playlist_items.currentIndex()
        index = (index + step) % len(clips)
        self.playlist_items.setCurrentIndex(index)
        self.clip_select.setCurrentText(clips[index])
        self.anim_position = 0.
        self._display_animation_frame()

    def save_animation_playlist(self):
        if self.animated is None:
            QMessageBox.information(self,'Animation Workspace','Load a playable animation first.')
            return
        from quality_lab_116 import animation_playlist
        names = [self.playlist_items.itemText(i) for i in range(self.playlist_items.count())]
        if not names:
            names = [self.clip_select.currentText()]
        markers = [self.timeline_markers.itemData(i) for i in range(1,self.timeline_markers.count())
                   if self.timeline_markers.itemData(i)]
        try:
            value = animation_playlist(names,markers,loop=self.loop_enabled.isChecked(),
                      speed=self.playback_speed.currentData())
            path,_=QFileDialog.getSaveFileName(self,'Save animation playlist',
                      'Evolve-Animation-Playlist.json','JSON (*.json)')
            if path:
                Path(path).write_text(json.dumps(value,indent=2),encoding='utf-8')
                self.anim_status.setText('Saved local animation playlist: '+Path(path).name)
        except (OSError,ValueError) as exc:
            QMessageBox.warning(self,'Invalid playlist',str(exc))

    def load_animation_playlist(self):
        if self.animated is None:
            QMessageBox.information(self,'Animation Workspace','Load a playable GLB or CAF clip first.')
            return
        from quality_lab_116 import validate_animation_playlist
        path,_=QFileDialog.getOpenFileName(self,'Load animation playlist','','JSON (*.json)')
        if not path:
            return
        try:
            file=Path(path)
            if file.stat().st_size>512*1024:
                raise ValueError('Playlist exceeds 512 KiB.')
            data,matched,missing=validate_animation_playlist(
                json.loads(file.read_text('utf-8')),self.animated.clips)
            self.playlist_items.clear()
            self.playlist_items.addItems(matched)
            self.timeline_markers.clear()
            self.timeline_markers.addItem('Markers',None)
            for clip,seconds in data['markers']:
                if clip in matched and seconds <= self.animated.clips[clip][0]:
                    self.timeline_markers.addItem('%s at %.2fs'%(clip,seconds),(clip,seconds))
            self.loop_enabled.setChecked(data['loop'])
            best=min(range(self.playback_speed.count()),key=lambda i:
                     abs(self.playback_speed.itemData(i)-data['speed']))
            self.playback_speed.setCurrentIndex(best)
            if matched:
                self.clip_select.setCurrentText(matched[0])
            self.anim_status.setText('%d clips loaded; %d unavailable in current model.'%
                                     (len(matched),len(missing)))
        except (OSError,ValueError,TypeError) as exc:
            QMessageBox.warning(self,'Playlist load failed',str(exc))

    def compare_playlist_clips(self):
        if self.animated is None:
            return
        from quality_lab_116 import compare_animation_clips
        clips = [self.playlist_items.itemText(i) for i in range(self.playlist_items.count())]
        if len(clips)<2:
            QMessageBox.information(self,'Compare clips','Add at least two playable clips to the playlist.')
            return
        try:
            comparison=compare_animation_clips(self.animated.clips, clips[0], clips[1])
            QMessageBox.information(self,'Clip Duration Comparison',
                                    json.dumps(comparison,indent=2))
        except ValueError as exc:
            QMessageBox.warning(self,'Compare clips',str(exc))

    def step_frame(self, direction):
        if self.animated is None or not self.clip_select.currentText():
            return
        duration=self.animated.clips[self.clip_select.currentText()][0]
        self.anim_position=min(duration,max(0.,self.anim_position+direction/30.))
        self._display_animation_frame()

    def add_timeline_marker(self):
        if self.animated is None or not self.clip_select.currentText():
            return
        label='%s at %.2fs'%(self.clip_select.currentText(),self.anim_position)
        self.timeline_markers.addItem(label,
            (self.clip_select.currentText(),self.anim_position))
        self.timeline_markers.setCurrentIndex(0)

    def goto_timeline_marker(self,index):
        data=self.timeline_markers.itemData(index)
        if data and self.animated is not None and data[0] in self.animated.clips:
            self.clip_select.setCurrentText(data[0])
            self.anim_position=data[1]
            self._display_animation_frame()

    def _layer_weight_changed(self, weight):
        self.layer_weight_label.setText('%d%%' % weight)
        if self._layered_pair and self.animated is not None:
            self._display_animation_frame()

    def select_clip(self, _name):
        self.anim_position = 0.
        self._display_animation_frame()

    def _display_animation_frame(self):
        if self.animated is None or not self.clip_select.currentText():
            return
        name = self.clip_select.currentText()
        duration = self.animated.clips[name][0]
        try:
            pair = self._layered_pair
            if pair and name == pair[0]:
                frame = self.animated.sample_layered(
                    pair[0], pair[1], self.anim_position,
                    self.layer_weight.value() / 100.)
            else:
                frame = self.animated.sample(name, self.anim_position)
            self.canvas.set_animated_vertices(frame)
            self.anim_time.setText('%.2f / %.2f s' % (self.anim_position, duration))
            self.seek.blockSignals(True)
            self.seek.setValue(int(self.anim_position / max(.01,duration)*1000))
            self.seek.blockSignals(False)
        except Exception as exc:
            self.anim_timer.stop()
            self.anim_playing = False
            self.play_button.setText('Play')
            self._play_shortcut.setText('▶  Play')
            self.anim_status.setText('Animation stopped: '+str(exc))

    def toggle_play(self):
        if self.animated is None:
            if self._native_loader and self.native_clip_select.currentIndex() > 0:
                self.load_native()
            else:
                self.load_animation()
            return
        self.anim_playing = not self.anim_playing
        self.play_button.setText('Pause' if self.anim_playing else 'Play')
        self._play_shortcut.setText('❚❚  Pause' if self.anim_playing else '▶  Play')
        if self.anim_playing:
            self.anim_clock.start()
            self.anim_timer.start()
        else:
            self.anim_timer.stop()

    def _animation_tick(self):
        if not self.anim_playing or self.animated is None:
            return
        elapsed = min(.15, self.anim_clock.restart()/1000.)
        self.anim_position += elapsed*self.playback_speed.currentData()
        duration = self.animated.clips[self.clip_select.currentText()][0]
        if self.anim_position >= duration:
            if self.loop_enabled.isChecked() and duration>0:
                self.anim_position %= duration
            else:
                clips = [self.playlist_items.itemText(i) for i in range(self.playlist_items.count())]
                here = self.clip_select.currentText()
                next_index = clips.index(here) + 1 if here in clips else len(clips)
                if next_index < len(clips):
                    self.playlist_items.setCurrentIndex(next_index)
                    self.clip_select.setCurrentText(clips[next_index])
                    self.anim_position = 0.
                    self._display_animation_frame()
                    return
                self.anim_position = max(0.,duration)
                self.anim_playing = False
                self.anim_timer.stop()
                self.play_button.setText('Play')
        self._display_animation_frame()

    def seek_to(self, value):
        if self.animated and self.clip_select.currentText():
            duration = self.animated.clips[self.clip_select.currentText()][0]
            self.anim_position = duration * value/1000.
            self._display_animation_frame()

    def stop_conversion(self):
        process = self.converter_process
        if process is None or process.state() == QProcess.NotRunning:
            return
        self.conversion_status.show()
        self.conversion_status.setText('Stopping converter process…')
        process.terminate()
        QTimer.singleShot(
            2500, lambda: process.kill()
            if process.state() != QProcess.NotRunning else None)

    def _converter_output(self):
        """Stream real converter stdout/stderr into the always-visible browser."""
        process = self.converter_process
        if process is None:
            return
        chunk = bytes(process.readAllStandardOutput()).decode('utf-8', 'replace')
        if not chunk:
            return
        self._converter_log = (getattr(self, '_converter_log', '') + chunk)[-8000:]
        tail = self._converter_log.strip()[-1400:]
        self.conversion_status.setText(tail)
        self.conversion_status.verticalScrollBar().setValue(
            self.conversion_status.verticalScrollBar().maximum())

    def _converter_error(self, error):
        process = self.converter_process
        detail = process.errorString() if process is not None else str(error)
        self.conversion_progress.hide()
        self.stop_conversion_button.hide()
        self.conversion_status.show()
        self.conversion_status.setText('Converter process error: ' + detail)
        self.anim_status.setText('Animation conversion failed: ' + detail)
        self.play_native_button.setEnabled(True)

    def import_original(self):
        """Convert selected CAF/DBA with the bundled independent executable."""
        from animation_converter_bridge import validate_conversion
        if self.converter_process is not None and self.converter_process.state() != QProcess.NotRunning:
            self.conversion_status.setText(
                'A conversion is already running. Wait for it to finish before starting another.')
            return
        selected = self.native_clip_select.currentData()
        ref = (self._native_references[selected]
               if isinstance(selected,int) and
               0 <= selected < len(self._native_references) else None)
        if (self._native_loader is not None and ref is not None and
                (ref.kind == 'dba' or (not ref.available and ref.dba_file is not None))):
            self.play_native()
            return
        self._pending_dba_clip = (
            ref.virtual_path if ref is not None and ref.kind == 'caf'
            and not ref.available and ref.dba_file is not None else None)
        source = (
            str(ref.dba_file) if self._pending_dba_clip else
            str(ref.file) if ref is not None and ref.available and ref.file else None)
        if not source:
            source, _ = QFileDialog.getOpenFileName(
                self, 'Select native character or original animation source', '',
                'CryEngine models and animations (*.chr *.skin *.caf *.dba)')
        if not source:
            return
        is_dba = Path(source).suffix.casefold() == '.dba'
        from animation_converter_bridge import (
            bundled_converter, extracted_asset_root, preview_output_folder)
        try:
            converter = bundled_converter()
        except FileNotFoundError as exc:
            QMessageBox.warning(self, 'Bundled animation converter', str(exc))
            return
        asset_root = extracted_asset_root(source)
        if asset_root is None:
            root = QFileDialog.getExistingDirectory(
                self, 'Choose extracted game asset root (not an executable)')
            if not root:
                return
            asset_root = Path(root)
        output = preview_output_folder()
        try:
            program, args = validate_conversion(
                converter, source, asset_root, output)
            self.converter_process = QProcess(self)
            self.converter_process.setProgram(program)
            self.converter_process.setArguments(args)
            # PySide6 QProcess requires QString/str, not WindowsPath.
            self.converter_process.setWorkingDirectory(str(output))
            self.converter_process.setProcessChannelMode(QProcess.MergedChannels)
            self._dba_conversion = is_dba
            self._converter_log = ''
            self._converter_output_folder = str(output)
            self.converter_process.readyReadStandardOutput.connect(self._converter_output)
            self.converter_process.finished.connect(
                lambda code, status: self._conversion_finished(code, status, output))
            self.converter_process.errorOccurred.connect(self._converter_error)
            self.play_native_button.setEnabled(False)
            self.conversion_progress.show()
            self.stop_conversion_button.show()
            self.conversion_status.show()
            self.conversion_status.setText(
                'Starting bundled converter…\nSource: ' + str(source) +
                '\nOutput: ' + str(output))
            self.converter_process.start()
            self.anim_status.setText('Converting animation…')
            QTimer.singleShot(0, self._update_animation_scrolling)
        except Exception as exc:
            self.conversion_progress.hide()
            self.stop_conversion_button.hide()
            self.conversion_status.show()
            self.play_native_button.setEnabled(True)
            self.conversion_status.setText('Conversion setup failed: ' + str(exc))
            QMessageBox.warning(self, 'Animation conversion', str(exc))

    def _conversion_finished(self, code, status, folder):
        self._converter_output()
        self.conversion_progress.hide()
        self.stop_conversion_button.hide()
        self.play_native_button.setEnabled(True)
        if code != 0 or status != QProcess.NormalExit:
            message = (
                'Converter exited with code %d. The animation/skeleton may be '
                'unsupported or dependencies may be in other imported PAKs.' % code)
            self.anim_status.setText(message)
            self.conversion_status.setText(
                message + '\n' + getattr(self, '_converter_log', '')[-2400:])
            return
        # A missing CAF may live inside a DBA. Never play the wrong packed
        # motion just because it was the first converted output.
        exact_clip = getattr(self, '_pending_dba_clip', None)
        self._pending_dba_clip = None
        if exact_clip:
            try:
                from converted_motion import converted_motion
                file, clip = converted_motion(folder, exact_clip)
                self._load_animation_file(str(file))
                if self.animated is not None:
                    self.clip_select.setCurrentText(clip)
                    self.select_clip(clip)
                    self.toggle_play() if not self.anim_playing else None
            except (OSError, ValueError) as exc:
                self.anim_status.setText('Exact DBA clip unavailable: ' + str(exc)[:220])
                self.conversion_status.setText(
                    'Conversion finished but the selected motion was not found.\n' +
                    str(exc) + '\n' + getattr(self, '_converter_log', '')[-900:])
            return
        paths = [path for path in Path(folder).rglob('*.glb')
                 if path.is_file() and not path.is_symlink()]
        if len(paths) == 1:
            self._load_animation_file(str(paths[0]))
            return
        self.anim_status.setText(
            'Converter produced %d GLB(s). Select the corresponding animation.' % len(paths))
        if not paths:
            self.conversion_status.setText(
                'Conversion completed without any .glb output.\n' +
                getattr(self, '_converter_log', '')[-2000:])
            return
        file, _ = QFileDialog.getOpenFileName(
            self, 'Select converted animation GLB', folder, 'Animated GLB (*.glb)')
        if file:
            self._load_animation_file(file)

    def closeEvent(self, event):
        self.anim_timer.stop()
        if self.converter_process is not None and self.converter_process.state() != QProcess.NotRunning:
            reply = QMessageBox.question(
                self, 'Conversion running', 'Stop the external animation converter and close?',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                event.ignore()
                return
            self.converter_process.kill()
            self.converter_process.waitForFinished(3000)
        super().closeEvent(event)


class QtGPUModelDialog(QDialog):
    """Optional pop-out version of the same panel used in the editor."""

    def __init__(self, parent, mesh, materials=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.Window | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint)
        self.setSizeGripEnabled(True)
        self.setWindowTitle('CryEngine Model — GPU Viewer')
        self.resize(990, 750)
        self.setMinimumSize(620, 460)
        self.setStyleSheet(parent.styleSheet())
        layout = QVBoxLayout(self)
        self.panel = QtGPUModelPanel(self, mesh, materials)
        layout.addWidget(self.panel)
        self.canvas = self.panel.canvas

    def reset_view(self):
        self.panel.reset_view()
