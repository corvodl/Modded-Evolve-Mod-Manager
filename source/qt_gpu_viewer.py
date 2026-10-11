"""GPU-accelerated Qt model viewer for supported Evolve CrChF meshes.

Unlike the older Win32/Tk WGL host this uses a Qt-owned OpenGL context.
It never accesses installed PAKs or writes extracted workspace files.
"""
from __future__ import annotations
import math
import sys
import json
from pathlib import Path
import numpy as np

from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QProcess, QObject, Signal, QRunnable, QThreadPool, QEvent, QSize, QPoint
from PySide6.QtGui import QSurfaceFormat, QIcon, QPixmap, QPainter, QPen, QColor, QPolygon
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QComboBox, QFileDialog, QMessageBox, QSlider, QToolButton, QFrame, QScrollArea, QButtonGroup, QLineEdit)

from gpu_model_preview import prepare_geometry, GPUUnavailable


def _viewport_tool_icon(kind):
    """Draw toolbar icons without an emoji font or SVG runtime dependency."""
    canvas = QPixmap(18, 18)
    canvas.fill(Qt.transparent)
    painter = QPainter(canvas)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(QColor("#e9e2e7"), 1.6))
    painter.setBrush(Qt.NoBrush)
    if kind == "View":
        painter.drawEllipse(2, 5, 14, 8)
        painter.drawEllipse(7, 7, 4, 4)
    elif kind == "Materials":
        for x, y in ((3, 3), (10, 3), (3, 10), (10, 10)):
            painter.drawRect(x, y, 5, 5)
    elif kind == "Animation":
        painter.drawRect(2, 3, 14, 12)
        painter.drawPolygon(QPolygon([QPoint(7, 6), QPoint(7, 12),
                                       QPoint(12, 9)]))
    elif kind == "Grid":
        for v in (4, 9, 14):
            painter.drawLine(v, 2, v, 16)
            painter.drawLine(2, v, 16, v)
    elif kind == "Wire":
        painter.drawRect(3, 3, 11, 11)
        painter.drawLine(3, 3, 14, 14)
        painter.drawLine(14, 3, 3, 14)
    elif kind == "Solid":
        painter.setBrush(QColor("#aaa6b6"))
        painter.drawEllipse(3, 3, 12, 12)
    painter.end()
    return QIcon(canvas)


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
        self.shading = 'faceted'
        self.geometry = prepared_geometry if prepared_geometry is not None else prepare_geometry(mesh, shading=self.shading)
        self.materials = materials
        # Character assembled parts can use CryEngine alpha semantics that
        # are not reproduced by the preview. Solid diagnostic rendering keeps
        # all decoded geometry visible while missing materials are resolved.
        self.force_opaque_preview = mesh.source_name.startswith('Character assembly:')
        self.textures = []
        self.normal_textures = []
        self.specular_textures = []
        self.translucent_slots = set()
        self._batch_sort_cache = None
        self.enhanced_materials = True
        self._renderer3 = None
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
        self.background_mode = 'Studio Grey'
        self.visible_material_slot = None
        self.projection_mode = 'Orthographic'
        self.specular_intensity = 0.35

    def set_mesh(self, mesh, materials=None, prepared_geometry=None):
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
        self.force_opaque_preview = mesh.source_name.startswith('Character assembly:')
        if self.isValid():
            self.makeCurrent()
            try:
                self._release_textures()
                self._upload_textures()
            finally:
                self.doneCurrent()
        self.update()

    def set_enhanced_materials(self, enabled):
        self.enhanced_materials = bool(enabled)
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
        self.makeCurrent()
        try:
            self._release_textures()
            self.materials = materials
            self._upload_textures()
        finally:
            self.doneCurrent()
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
        for name in ('textures','normal_textures','specular_textures'):
            handles = getattr(self,name,[])
            if handles:
                try:
                    glDeleteTextures([handle for handle in handles if handle])
                except Exception:
                    pass
            setattr(self,name,[])
        self.translucent_slots = set()
        self._batch_sort_cache = None

    def _upload_textures(self):
        import time
        started=time.perf_counter()
        from OpenGL.GL import (
            glGenTextures, glBindTexture, glTexImage2D, glTexParameteri,
            glPixelStorei, GL_TEXTURE_2D, GL_RGB, GL_UNSIGNED_BYTE,
            GL_TEXTURE_MIN_FILTER, GL_TEXTURE_MAG_FILTER, GL_LINEAR,
            GL_TEXTURE_WRAP_S, GL_TEXTURE_WRAP_T, GL_REPEAT,
            GL_UNPACK_ALIGNMENT,
        )
        if not self.materials:
            return
        from OpenGL.GL import GL_RGBA
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        from PIL import Image as PILImage
        sources = (('textures', self.materials.textures),
                   ('normal_textures',getattr(self.materials,'normal_maps',())),
                   ('specular_textures',getattr(self.materials,'specular_maps',())))
        for target,images in sources:
            uploads=[]
            for index,im in enumerate(images):
                if im is None:
                    uploads.append(0)
                    continue
                rgba = 'A' in im.getbands()
                # A diffuse texture's alpha may encode a mask unrelated to
                # transparency. Trust the material flag, not raw alpha bytes.
                if (target == 'textures' and rgba and self.materials and
                        index < len(getattr(self.materials, 'alpha_modes', ())) and
                        self.materials.alpha_modes[index] and
                        im.getchannel('A').getextrema()[0] < 255):
                    self.translucent_slots.add(index)
                mode = 'RGBA' if rgba else 'RGB'
                sample = im.convert(mode).transpose(PILImage.Transpose.FLIP_TOP_BOTTOM)
                format_id = GL_RGBA if rgba else GL_RGB
                texid = int(glGenTextures(1))
                glBindTexture(GL_TEXTURE_2D,texid)
                glTexImage2D(GL_TEXTURE_2D,0,format_id,sample.width,sample.height,0,
                             format_id,GL_UNSIGNED_BYTE,sample.tobytes())
                for param in (GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER):
                    glTexParameteri(GL_TEXTURE_2D,param,GL_LINEAR)
                for param in (GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T):
                    glTexParameteri(GL_TEXTURE_2D,param,GL_REPEAT)
                uploads.append(texid)
            setattr(self,target,uploads)
        glBindTexture(GL_TEXTURE_2D,0)
        self.gpu_upload_ms=round((time.perf_counter()-started)*1000,2)

    def initializeGL(self):
        from OpenGL.GL import (
            glClearColor, glEnable, glShadeModel, glLightfv, glLightModelfv,
            GL_DEPTH_TEST, GL_NORMALIZE, GL_LIGHT0, GL_LIGHTING, GL_SMOOTH,
            GL_POSITION, GL_AMBIENT, GL_DIFFUSE, GL_LIGHT_MODEL_AMBIENT,
        )
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
        self._upload_textures()
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
                if self.visible_material_slot is not None and slot != self.visible_material_slot:
                    continue
                alpha = (self.materials.opacity[slot] if self.materials and
                         slot<len(getattr(self.materials,'opacity',())) else 1.0)
                if self.force_opaque_preview:
                    alpha = 1.0
                transparent = is_translucent(slot)
                if transparent:
                    glEnable(GL_BLEND)
                    glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA)
                    glDepthMask(False)
                else:
                    glDisable(GL_BLEND)
                    glDepthMask(True)
                tex = self.textures[slot] if use_uv and slot < len(self.textures) else 0
                if shader:
                    for unit,group in ((0,self.textures),(1,self.normal_textures),(2,self.specular_textures)):
                        handle = group[slot] if use_uv and slot<len(group) else 0
                        glActiveTexture(GL_TEXTURE0+unit)
                        glBindTexture(GL_TEXTURE_2D,handle)
                    glActiveTexture(GL_TEXTURE0)
                    shader.bind(opacity=alpha,gloss=self.specular_intensity,
                                illumination=strength,diffuse=bool(tex),
                                normal=slot<len(self.normal_textures) and bool(self.normal_textures[slot]),
                                specular=slot<len(self.specular_textures) and bool(self.specular_textures[slot]),
                                alpha_texture=(not self.force_opaque_preview and slot<len(getattr(self.materials,'alpha_modes',())) and bool(self.materials.alpha_modes[slot])))
                if tex:
                    glEnable(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D,tex)
                else:
                    glDisable(GL_TEXTURE_2D)
                glColor3f(0.84,0.82,0.82)
                glMaterialfv(GL_FRONT_AND_BACK,GL_DIFFUSE,[0.81,0.8,0.82,alpha])
                spec=self.specular_intensity
                glMaterialfv(GL_FRONT_AND_BACK,GL_SPECULAR,[spec,spec,spec,1.0])
                glMaterialf(GL_FRONT_AND_BACK,GL_SHININESS,38.0)
                glDrawElements(GL_TRIANGLES,int(count),GL_UNSIGNED_INT,
                               arrays.indices[first:first+count])
        finally:
            if shader:
                shader.unbind()
                for unit in (2,1,0):
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
        mode_label = QLabel('Object Mode')
        mode_label.setToolTip('Read-only CryEngine preview')
        toolbar.addWidget(mode_label)
        self._overlay_buttons = {}
        for section in ('View', 'Materials', 'Animation'):
            button = QToolButton(self)
            button.setObjectName('gpuOverlayButton' + section)
            button.setText(section + ' ▾')
            button.setIcon(_viewport_tool_icon(section))
            button.setIconSize(QSize(17, 17))
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
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
        toolbar.addWidget(self._solid_shading)
        self._material_shading = QToolButton(self)
        self._material_shading.setText('Material')
        self._material_shading.setIcon(_viewport_tool_icon('Materials'))
        self._material_shading.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._material_shading.setToolTip('Preview with textures and CryEngine material approximation')
        self._material_shading.setCheckable(True)
        self._material_shading.setChecked(True)
        self._shading_group.addButton(self._material_shading)
        toolbar.addWidget(self._material_shading)
        self._wire_toggle = QToolButton(self)
        self._wire_toggle.setText('Wire')
        self._wire_toggle.setIcon(_viewport_tool_icon('Wire'))
        self._wire_toggle.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self._wire_toggle.setCheckable(True)
        toolbar.addWidget(self._wire_toggle)
        self._play_shortcut = QToolButton(self)
        self._play_shortcut.setText('▶  Play')
        self._play_shortcut.setToolTip('Play / pause current animation')
        self._play_shortcut.clicked.connect(self.toggle_play)
        layout.addLayout(toolbar)
        self.canvas=QtGLModelCanvas(mesh,materials,self,prepared_geometry=prepared_geometry)
        self.canvas.setObjectName('embeddedGpuCanvas')
        layout.addWidget(self.canvas,1)
        self._info.setParent(self.canvas)
        self._info.setObjectName('viewportBreadcrumb')
        self._info.setText('User Perspective  •  '+mesh.source_name)
        self._info.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._info.setStyleSheet('QLabel#viewportBreadcrumb { background: rgba(22,22,26,165); color: #f0edf2; padding: 4px 7px; border-radius: 4px; }')
        self._info.show()
        self._overlay = QScrollArea(self.canvas)
        self._overlay.setObjectName('modelOverlayControls')
        self._overlay.setWidgetResizable(True)
        self._overlay.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._overlay.setStyleSheet(
            '#modelOverlayControls { background: #1b1b21; border: 1px solid #45414a; '
            'border-radius: 9px; }'
            'QWidget#modelOverlayContent { background: #1b1b21; }')
        self._overlay_content = QWidget()
        self._overlay_content.setObjectName('modelOverlayContent')
        self._overlay_body = QVBoxLayout(self._overlay_content)
        self._overlay_body.setContentsMargins(12, 10, 12, 10)
        self._overlay.setWidget(self._overlay_content)
        self._overlay_sections = {}
        self._active_overlay = None
        for section in ('View', 'Materials', 'Animation'):
            page = QWidget(self._overlay_content)
            part = QVBoxLayout(page)
            part.setContentsMargins(0, 0, 0, 0)
            part.setSpacing(7)
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
        self._overlay_sections['View'][1].addWidget(options)
        # Short, always-visible controls also work in the embedded viewer.
        studio = QHBoxLayout()
        enhanced=QCheckBox('Renderer 3.0')
        enhanced.setToolTip('Enhanced shader supports normal/specular maps and opacity. Legacy fallback remains available.')
        enhanced.setChecked(True)
        enhanced.toggled.connect(self.canvas.set_enhanced_materials)
        self.enhanced_checkbox=enhanced
        studio.addWidget(enhanced)
        studio.addWidget(QLabel('Camera'))
        self.camera_combo = QComboBox()
        self.camera_combo.addItems(('Isometric','Front','Back','Left','Right','Top','Bottom'))
        self.camera_combo.currentTextChanged.connect(self.canvas.set_camera_preset)
        studio.addWidget(self.camera_combo)
        studio.addWidget(QLabel('Lighting'))
        self.lighting_slider = QSlider(Qt.Horizontal)
        self.lighting_slider.setRange(20,200)
        self.lighting_slider.setValue(100)
        self.lighting_slider.setMaximumWidth(125)
        self.lighting_slider.valueChanged.connect(self.canvas.set_light_strength)
        studio.addWidget(self.lighting_slider)
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
        self._overlay_sections['View'][1].addLayout(studio)
        self._overlay_sections['View'][1].addLayout(studio_detail)
        inspector=QHBoxLayout()
        inspector.addWidget(QLabel('Mesh materials'))
        self.part_select=QComboBox()
        self.part_select.setObjectName('modelMaterialPartInspector')
        self.part_select.currentIndexChanged.connect(
            lambda _: self.canvas.set_visible_material_slot(self.part_select.currentData()))
        inspector.addWidget(self.part_select,1)
        uvbutton=QPushButton('Show UV Layout…')
        uvbutton.clicked.connect(self.show_uv_layout)
        inspector.addWidget(uvbutton)
        inspector_secondary = QHBoxLayout()
        inspector_secondary.addWidget(QLabel('Projection'))
        self.projection_select=QComboBox()
        self.projection_select.addItems(('Orthographic','Perspective'))
        self.projection_select.currentTextChanged.connect(self.canvas.set_projection)
        inspector_secondary.addWidget(self.projection_select)
        inspector_secondary.addWidget(QLabel('Gloss'))
        self.gloss_slider=QSlider(Qt.Horizontal)
        self.gloss_slider.setRange(0,100)
        self.gloss_slider.setValue(35)
        self.gloss_slider.setMaximumWidth(95)
        self.gloss_slider.valueChanged.connect(self.canvas.set_specular)
        inspector_secondary.addWidget(self.gloss_slider)
        inspector_secondary.addStretch()
        self._overlay_sections['Materials'][1].addLayout(inspector)
        self._overlay_sections['Materials'][1].addLayout(inspector_secondary)
        self.solid_geometry = QCheckBox('Show all character geometry (ignore alpha)')
        self.solid_geometry.setToolTip('Diagnostic view: shows every decoded part even if a CryEngine material uses unsupported transparency. Does not create absent mesh parts.')
        self.solid_geometry.setChecked(self.canvas.force_opaque_preview)
        self.solid_geometry.toggled.connect(lambda checked: (setattr(self.canvas, 'force_opaque_preview', checked), self.canvas.update()))
        self._overlay_sections['Materials'][1].addWidget(self.solid_geometry)
        self._populate_material_slots(mesh,materials)
        self._prepare_animation_controls(self._overlay_sections['Animation'][1])
        self.native_info = QLabel('Native game animations: not scanned')
        self.native_info.setWordWrap(True)
        self._overlay_sections['Animation'][1].addWidget(self.native_info)
        # Keep the selected clip's status immediately under its browser.
        self._overlay_sections['Animation'][1].removeWidget(self.native_info)
        self.animation_sections['Game Animation Browser'][1].layout().addWidget(self.native_info)
        foot=QLabel('Renderer 3.0 approximates CryEngine materials; check optional maps in the texture diagnostics. '
                    'Left drag: rotate · Right/middle/Shift drag: pan · Wheel: zoom. '
                    'Faceted shading keeps hard edges; smooth shading is optional.')
        foot.setWordWrap(True)
        self._overlay_sections['View'][1].addWidget(foot)
        self._build_compact_timeline(layout)
        QTimer.singleShot(0,self._position_overlay)

    def _create_viewport_navigation(self):
        self._tool_rail = QFrame(self.canvas)
        self._tool_rail.setObjectName('viewportToolRail')
        self._tool_rail.setStyleSheet('QFrame#viewportToolRail { background: rgba(31,31,36,220); border: 1px solid #49464e; border-radius: 5px; }')
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
        self._axis_widget.setStyleSheet('QFrame#viewportAxisControls { background: rgba(31,31,36,220); border: 1px solid #49464e; border-radius: 14px; }')
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
        strip.setStyleSheet('QFrame#viewportTimeline { background: #212126; border-top: 1px solid #47424a; }')
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
            self._overlay.raise_()

    def _position_overlay(self):
        if not hasattr(self, '_overlay') or self._overlay is None:
            return
        width = max(180, min(570, self.canvas.width() - 70))
        page = self._overlay_sections.get(self._active_overlay)
        wanted = (page[0].sizeHint().height() + 35) if page else 200
        height = max(80, min(wanted, 510, self.canvas.height() - 55))
        self._overlay.setGeometry(56, 43, width, height)
        self._position_navigation()

    def eventFilter(self, obj, event):
        if obj is getattr(self, 'canvas', None) and event.type() == QEvent.Resize:
            self._position_overlay()
        return super().eventFilter(obj, event)

    def _populate_material_slots(self,mesh,materials):
        slots=sorted({part[2] for part in mesh.subsets}) if mesh.subsets else [0]
        self.part_select.blockSignals(True)
        self.part_select.clear()
        self.part_select.addItem('All parts',None)
        for slot in slots:
            title=(materials.names[slot] if materials and slot<len(materials.names)
                   else 'Material slot %d'%slot)
            self.part_select.addItem('%d — %s'%(slot,title[:75]),slot)
        self.part_select.blockSignals(False)
        self.canvas.set_visible_material_slot(None)

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
            body = QWidget(self)
            inner = QVBoxLayout(body)
            inner.setContentsMargins(3, 3, 3, 6)
            inner.setSpacing(4)
            layout.addWidget(heading)
            layout.addWidget(body)
            body.setVisible(opened)
            def toggle(active):
                body.setVisible(active)
                heading.setText(('▾  ' if active else '▸  ') + title)
            heading.toggled.connect(toggle)
            self.animation_sections[title] = (heading, body)
            return inner
        catalog_layout = disclosure('Game Animation Browser', True)
        playback_layout = disclosure('Playback Controls', False)
        playlist_layout = disclosure('Clips & Playlists', False)
        line = QHBoxLayout()
        import_button = QPushButton('Load Animation GLB…')
        import_button.clicked.connect(self.load_animation)
        line.addWidget(import_button)
        original = QPushButton('Import Original CAF/DBA…')
        original.clicked.connect(self.import_original)
        line.addWidget(original)
        self.clip_select = QComboBox()
        self.clip_select.setMinimumWidth(130)
        self.clip_select.currentTextChanged.connect(self.select_clip)
        line.addWidget(self.clip_select, 1)
        self.play_button = QPushButton('Play')
        self.play_button.clicked.connect(self.toggle_play)
        line.addWidget(self.play_button)
        playback_layout.addLayout(line)
        native_header = QHBoxLayout()
        native_header.addWidget(QLabel('Game animations'))
        self.native_search = QLineEdit()
        self.native_search.setObjectName('nativeAnimationSearch')
        self.native_search.setPlaceholderText('Find an animation by name or file path…')
        self.native_search.setClearButtonEnabled(True)
        self.native_search.textChanged.connect(self._filter_native_catalog)
        native_header.addWidget(self.native_search, 1)
        self.native_refresh_button = QPushButton('Refresh Library')
        self.native_refresh_button.setObjectName('refreshNativeLibrary')
        self.native_refresh_button.setToolTip(
            'Re-read the current Asset Library and update this model\'s animation references.')
        self.native_refresh_button.clicked.connect(self.refresh_native_catalog)
        native_header.addWidget(self.native_refresh_button)
        catalog_layout.addLayout(native_header)

        native_filters = QHBoxLayout()
        self.native_state_filter = QComboBox()
        self.native_state_filter.setObjectName('nativeAnimationStateFilter')
        self.native_state_filter.addItems((
            'All availability', 'Ready in library', 'In PAK (extract on play)',
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
        self.native_result_count.setWordWrap(True)
        catalog_layout.addWidget(self.native_result_count)

        native_row = QHBoxLayout()
        self.native_clip_select = QComboBox()
        self.native_clip_select.setObjectName('referencedNativeAnimations')
        self.native_clip_select.setMaxVisibleItems(16)
        self.native_clip_select.setToolTip(
            'Search or filter the animation index above. Only a manageable '
            'subset is listed; no unrelated CAF is substituted.')
        self.native_clip_select.currentIndexChanged.connect(self._native_selection)
        native_row.addWidget(self.native_clip_select, 1)
        self.play_native_button = QPushButton('Play Game Clip')
        self.play_native_button.clicked.connect(self.load_native)
        native_row.addWidget(self.play_native_button)
        catalog_layout.addLayout(native_row)
        # Scrubber lives in the always-visible narrow playback strip.
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderMoved.connect(self.seek_to)
        self.anim_time = QLabel('0.00 / 0.00 s')
        timeline=QHBoxLayout()
        timeline.addWidget(QLabel('Speed'))
        self.playback_speed=QComboBox()
        for label,number in (('0.25×',0.25),('0.5×',0.5),('1×',1.0),
                             ('1.5×',1.5),('2×',2.0)):
            self.playback_speed.addItem(label,number)
        self.playback_speed.setCurrentIndex(2)
        timeline.addWidget(self.playback_speed)
        self.loop_enabled=QCheckBox('Loop')
        self.loop_enabled.setChecked(True)
        timeline.addWidget(self.loop_enabled)
        previous=QPushButton('◀ Frame')
        previous.clicked.connect(lambda: self.step_frame(-1))
        timeline.addWidget(previous)
        nxt=QPushButton('Frame ▶')
        nxt.clicked.connect(lambda: self.step_frame(1))
        timeline.addWidget(nxt)
        timeline.addStretch()
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
        navigation_secondary = QHBoxLayout()
        self.playlist_items = QComboBox()
        self.playlist_items.setToolTip('Ordered decoded animation clips; no unconverted CAF data is inferred.')
        navigation_secondary.addWidget(self.playlist_items, 1)
        for title, callback in (('+ Clip', self.add_playlist_clip),
                                ('− Clip', self.remove_playlist_clip),
                                ('Prev', lambda: self.move_playlist(-1)),
                                ('Next', lambda: self.move_playlist(1))):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            navigation_secondary.addWidget(btn)
        playlist_layout.addLayout(navigation_secondary)
        presets = QHBoxLayout()
        for title, callback in (('Save Playlist…', self.save_animation_playlist),
                                ('Load Playlist…', self.load_animation_playlist),
                                ('Compare Clips', self.compare_playlist_clips)):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            presets.addWidget(btn)
        presets.addStretch()
        playlist_layout.addLayout(presets)
        self.anim_status = QLabel('Animation playback: load a rigged GLB containing clips, or '
                                  'use an optional CryEngine converter for CAF/DBA files.')
        self.anim_status.setWordWrap(True)
        self.anim_status.setVisible(not self.compact)
        playback_layout.addWidget(self.anim_status)
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
        self._native_loader = None
        self._pending_playback_preferences = None
        self._native_pool = QThreadPool(self)
        self._native_pool.setMaxThreadCount(2)
        self._native_signals = _NativeResult(self)
        self._native_signals.completed.connect(self._native_ready)

    def set_native_catalog(self, references, idle=None, description='',
                           loader=None, refresh_library=None):
        """Filter a potentially huge native animation catalog on the GUI thread."""
        from character_animation_catalog import native_clip_playability
        self._native_generation += 1
        self._native_loader = loader
        self._native_references = tuple(references)
        self._native_refresh = refresh_library
        self.native_refresh_button.setEnabled(refresh_library is not None)
        available = [(i, ref) for i, ref in enumerate(references)
                     if native_clip_playability(ref)[0] == 'candidate']
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
        if auto >= 0 and loader and idle is not None:
            generation = self._native_generation
            QTimer.singleShot(
                80, lambda: self.load_native()
                if generation == self._native_generation else None)

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
        self.native_clip_select.addItem('Select an animation…', -1)
        suffixes = {
            'candidate': ' [Library]', 'extractable': ' [Packed]',
            'missing': ' [CAF not indexed]', 'layered': ' [Layered]',
            'metadata': ' [Graph / DBA unavailable]',
            'converter': ' [DBA converter]'}
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
            'Showing %d of %d matching / %d total' %
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
        self.play_native_button.setEnabled(
            (state == 'converter') or bool(
                self._native_loader and state in ('candidate', 'extractable', 'missing')))
        self.play_native_button.setText(
            ('Find in DBA & Play' if ref.kind == 'caf' else 'Convert DBA & Play')
            if state == 'converter'
            else 'Extract & Play' if state == 'extractable'
            else 'Search PAK & Play' if state == 'missing'
            else 'Play Game Clip')
        if state == 'layered':
            self.native_info.setText(
                'Layered pose: ' + ref.name + '\n' + detail +
                '\nChoose a standalone stand, idle or movement CAF from the list.')
        elif state == 'candidate':
            self.native_info.setText(
                'Ready to test: ' + ref.virtual_path +
                '\nNative decoder checks skeleton compatibility before playback.')
        else:
            source = str(ref.file or ref.dba_file or 'Not indexed')
            self.native_info.setText(
                detail + '\nGame path: ' + ref.virtual_path +
                '\nAsset Library path: ' + source)

    def load_native(self):
        from character_animation_catalog import native_clip_playability
        i = self.native_clip_select.currentData()
        if (not isinstance(i, int)
                or i < 0 or i >= len(self._native_references)):
            return
        ref = self._native_references[i]
        state, reason = native_clip_playability(ref)
        if state == 'converter':
            self.import_original()
            return
        if self._native_loader is None:
            return
        if state not in ('candidate', 'extractable', 'missing'):
            self.native_info.setText(reason + '\n' + ref.virtual_path)
            self.play_native_button.setEnabled(False)
            return
        self._native_generation += 1
        token = self._native_generation
        self.native_info.setText(
            ('Locating and extracting exact CAF from verified game PAK…'
             if state != 'candidate' else 'Decoding standalone CAF and rigged model…'))
        self.play_native_button.setEnabled(False)
        job = _NativeConversion(self._native_loader, ref, token, self._native_signals)
        self._native_pool.start(job)

    def _native_ready(self, token, result, error):
        if token != self._native_generation:
            return
        self.play_native_button.setEnabled(True)
        if error:
            self.native_info.setText('Native CAF unavailable: ' + error[:230])
            return
        payload, details = result
        try:
            if isinstance(payload, tuple):
                imported, geometry = payload
            else:
                from model_animation import AnimatedGLB
                imported = AnimatedGLB(payload, 'Native Evolve animation')
                geometry = None
            self._show_animated(imported, auto_play=True,
                                prepared_geometry=geometry)
        except (ValueError, OSError) as exc:
            self.native_info.setText('CAF playback rejected: ' + str(exc)[:230])
            return
        resolved = details.get('_resolved_caf_reference')
        if resolved is not None:
            from character_animation_catalog import native_clip_playability
            i = self.native_clip_select.currentData()
            if isinstance(i, int) and 0 <= i < len(self._native_references):
                updated = list(self._native_references)
                updated[i] = resolved
                self._native_references = tuple(updated)
                self._filter_native_catalog()
                self.play_native_button.setText('Play Game Clip')
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

    def _show_animated(self, imported, *, auto_play, prepared_geometry=None):
        self.anim_timer.stop()
        self.anim_playing = False
        self.play_button.setText('Play')
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

    def select_clip(self, _name):
        self.anim_position = 0.
        self._display_animation_frame()

    def _display_animation_frame(self):
        if self.animated is None or not self.clip_select.currentText():
            return
        name = self.clip_select.currentText()
        duration = self.animated.clips[name][0]
        try:
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

    def import_original(self):
        """Explicit optional local converter; never downloads or bundles GPL code."""
        from animation_converter_bridge import validate_conversion
        selected = self.native_clip_select.currentData()
        ref = (self._native_references[selected]
               if isinstance(selected,int) and
               0 <= selected < len(self._native_references) else None)
        self._pending_dba_clip = (
            ref.name if ref is not None and ref.kind == 'caf'
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
            self._dba_conversion = is_dba
            self.converter_process.finished.connect(
                lambda code, status: self._conversion_finished(code, status, output))
            self.converter_process.errorOccurred.connect(
                lambda _err: self.anim_status.setText(
                    'Converter failed to start. Check its installation and permissions.'))
            self.converter_process.start()
            self.anim_status.setText(
            'Converting packed animation with bundled Cryengine-Converter…')
        except Exception as exc:
            QMessageBox.warning(self, 'Animation conversion', str(exc))

    def _conversion_finished(self, code, status, folder):
        if code != 0 or status != QProcess.NormalExit:
            self.anim_status.setText(
                'External conversion failed (exit %d). Verify that CAF/DBA '
                'tracks and matching skeleton are unpacked.' % code)
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
                self.anim_status.setText(str(exc)[:300])
            return
        paths = [path for path in Path(folder).rglob('*.glb')
                 if path.is_file() and not path.is_symlink()]
        if len(paths) == 1:
            self._load_animation_file(str(paths[0]))
            return
        self.anim_status.setText(
            'Converted %d animated GLBs. Select the corresponding motion.' % len(paths))
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
