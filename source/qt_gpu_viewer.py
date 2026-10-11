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

from PySide6.QtCore import Qt, QTimer, QElapsedTimer, QProcess, QObject, Signal, QRunnable, QThreadPool
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import (
    QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox,
    QComboBox, QFileDialog, QMessageBox, QSlider)

from gpu_model_preview import prepare_geometry, GPUUnavailable


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
        self.textures = []
        self.normal_textures = []
        self.specular_textures = []
        self.translucent_slots = set()
        self.enhanced_materials = True
        self._renderer3 = None
        self.rotation = [-30.0, 14.0]
        self.zoom = 1.0
        self.pan = [0., 0.]
        self.wireframe = False
        self.textured = True
        self._last = None
        self.gl_error = None
        self.light_strength = 1.0
        self.background_mode = 'Dark'
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
        self.materials = materials
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

    def _upload_textures(self):
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
                if target == 'textures' and rgba and im.getchannel('A').getextrema()[0] < 255:
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
                values=getattr(self.materials,'opacity',()) if self.materials else ()
                return slot in self.translucent_slots or (slot<len(values) and values[slot]<.999)
            batches = sorted(arrays.draws,key=lambda d:is_translucent(d[2]))
            for first,count,slot in batches:
                if self.visible_material_slot is not None and slot != self.visible_material_slot:
                    continue
                alpha = (self.materials.opacity[slot] if self.materials and
                         slot<len(getattr(self.materials,'opacity',())) else 1.0)
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
                                specular=slot<len(self.specular_textures) and bool(self.specular_textures[slot]))
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
                     event.modifiers() & Qt.ShiftModifier)):
                unit = 2.44 / max(1, self.height())
                self.pan[0] += dx * unit
                self.pan[1] -= dy * unit
            elif event.buttons() & Qt.LeftButton:
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
        self._info=QLabel(mesh.source_name+'  •  '+mesh.description)
        self._info.setWordWrap(True)
        layout.addWidget(self._info)
        self.canvas=QtGLModelCanvas(mesh,materials,self,prepared_geometry=prepared_geometry)
        self.canvas.setObjectName('embeddedGpuCanvas')
        layout.addWidget(self.canvas,1)
        options = QWidget(self)
        line=QHBoxLayout(options)
        line.setContentsMargins(0, 0, 0, 0)
        wire=QCheckBox('Wireframe')
        self.wire_checkbox = wire
        wire.toggled.connect(lambda value:(setattr(self.canvas,'wireframe',value),self.canvas.update()))
        line.addWidget(wire)
        textured=QCheckBox('Textures')
        self.texture_checkbox = textured
        textured.setChecked(True)
        textured.toggled.connect(lambda value:(setattr(self.canvas,'textured',value),self.canvas.update()))
        line.addWidget(textured)
        line.addWidget(QLabel('Shading'))
        enhanced.setToolTip('Normal/specular maps and opacity with a GLSL shader; legacy fallback if unavailable.')
        enhanced.setChecked(True)
        enhanced.toggled.connect(self.canvas.set_enhanced_materials)
        self.enhanced_checkbox=enhanced

        shading = QComboBox()
        self.shading_combo = shading
        shading.addItems(('Faceted (hard edges)', 'Smooth'))
        shading.currentIndexChanged.connect(
            lambda i: self.canvas.set_shading('Smooth' if i else 'Faceted'))
        line.addWidget(shading)
        line.addWidget(QLabel('Quality'))
        quality=QComboBox()
        self.quality_combo = quality
        quality.addItems(('Performance','High'))
        quality.currentTextChanged.connect(self.canvas.set_quality)
        line.addWidget(quality)
        reset=QPushButton('Reset Camera')
        reset.clicked.connect(self.reset_view)
        line.addWidget(reset)
        line.addStretch()
        layout.addWidget(options)
        options.setVisible(not compact)
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
        self.background_combo = QComboBox()
        self.background_combo.addItems(('Dark','Studio Grey'))
        self.background_combo.currentTextChanged.connect(self.canvas.set_background)
        studio.addWidget(self.background_combo)
        save_view = QPushButton('Save View…')
        save_view.clicked.connect(self.save_viewport)
        studio.addWidget(save_view)
        studio.addStretch()
        layout.addLayout(studio)
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
        inspector.addWidget(QLabel('Projection'))
        self.projection_select=QComboBox()
        self.projection_select.addItems(('Orthographic','Perspective'))
        self.projection_select.currentTextChanged.connect(self.canvas.set_projection)
        inspector.addWidget(self.projection_select)
        inspector.addWidget(QLabel('Gloss'))
        self.gloss_slider=QSlider(Qt.Horizontal)
        self.gloss_slider.setRange(0,100)
        self.gloss_slider.setValue(35)
        self.gloss_slider.setMaximumWidth(95)
        self.gloss_slider.valueChanged.connect(self.canvas.set_specular)
        inspector.addWidget(self.gloss_slider)
        layout.addLayout(inspector)
        self._populate_material_slots(mesh,materials)
        self._prepare_animation_controls(layout)
        self.native_info = QLabel('Native game animations: not scanned')
        self.native_info.setWordWrap(True)
        layout.addWidget(self.native_info)
        foot=QLabel('Renderer 3.0 approximates CryEngine materials; check optional maps in the texture diagnostics. '
                    'Left drag: rotate · Right/middle/Shift drag: pan · Wheel: zoom. '
                    'Faceted shading keeps hard edges; smooth shading is optional.')
        foot.setWordWrap(True)
        foot.setVisible(not compact)
        layout.addWidget(foot)

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
        self.animated = None
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
        self._populate_material_slots(mesh,materials)
        self._info.setText(mesh.source_name + '  •  ' + mesh.description)

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
        from PySide6.QtWidgets import QSlider
        line = QHBoxLayout()
        import_button = QPushButton('Load Animation GLB…')
        import_button.clicked.connect(self.load_animation)
        import_button.setVisible(not self.compact)
        line.addWidget(import_button)
        original = QPushButton('Import Original CAF/DBA…')
        original.clicked.connect(self.import_original)
        original.setVisible(not self.compact)
        line.addWidget(original)
        self.clip_select = QComboBox()
        self.clip_select.setMinimumWidth(130)
        self.clip_select.currentTextChanged.connect(self.select_clip)
        line.addWidget(self.clip_select, 1)
        self.play_button = QPushButton('Play')
        self.play_button.clicked.connect(self.toggle_play)
        line.addWidget(self.play_button)
        layout.addLayout(line)
        native_row = QHBoxLayout()
        native_row.addWidget(QLabel('Game clips'))
        self.native_clip_select = QComboBox()
        self.native_clip_select.setObjectName('referencedNativeAnimations')
        self.native_clip_select.setToolTip(
            'CryEngine references are indexed here. Only converted GLB clips can be played.')
        self.native_clip_select.currentIndexChanged.connect(self._native_selection)
        native_row.addWidget(self.native_clip_select, 1)
        self.play_native_button = QPushButton('Play Game Clip')
        self.play_native_button.clicked.connect(self.load_native)
        native_row.addWidget(self.play_native_button)
        layout.addLayout(native_row)
        line2 = QHBoxLayout()
        self.seek = QSlider(Qt.Horizontal)
        self.seek.setRange(0, 1000)
        self.seek.sliderMoved.connect(self.seek_to)
        line2.addWidget(self.seek, 1)
        self.anim_time = QLabel('0.00 / 0.00 s')
        line2.addWidget(self.anim_time)
        layout.addLayout(line2)
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
        marker=QPushButton('Add Marker')
        marker.clicked.connect(self.add_timeline_marker)
        timeline.addWidget(marker)
        self.timeline_markers=QComboBox()
        self.timeline_markers.addItem('Markers',None)
        self.timeline_markers.currentIndexChanged.connect(self.goto_timeline_marker)
        timeline.addWidget(self.timeline_markers,1)
        layout.addLayout(timeline)
        navigation = QHBoxLayout()
        for title, seconds in (('−1s', -1.), ('−0.1s', -.1), ('+0.1s', .1), ('+1s', 1.)):
            item = QPushButton(title)
            item.clicked.connect(lambda checked=False, delta=seconds: self.nudge_timeline(delta))
            navigation.addWidget(item)
        self.playlist_items = QComboBox()
        self.playlist_items.setToolTip('Ordered decoded animation clips; no unconverted CAF data is inferred.')
        navigation.addWidget(self.playlist_items, 1)
        for title, callback in (('+ Clip', self.add_playlist_clip),
                                ('− Clip', self.remove_playlist_clip),
                                ('Prev', lambda: self.move_playlist(-1)),
                                ('Next', lambda: self.move_playlist(1))):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            navigation.addWidget(btn)
        layout.addLayout(navigation)
        presets = QHBoxLayout()
        for title, callback in (('Save Playlist…', self.save_animation_playlist),
                                ('Load Playlist…', self.load_animation_playlist),
                                ('Compare Clips', self.compare_playlist_clips)):
            btn=QPushButton(title)
            btn.clicked.connect(lambda checked=False, fn=callback: fn())
            presets.addWidget(btn)
        presets.addStretch()
        layout.addLayout(presets)
        self.anim_status = QLabel('Animation playback: load a rigged GLB containing clips, or '
                                  'use an optional CryEngine converter for CAF/DBA files.')
        self.anim_status.setWordWrap(True)
        self.anim_status.setVisible(not self.compact)
        layout.addWidget(self.anim_status)
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
        self._native_pool = QThreadPool(self)
        self._native_pool.setMaxThreadCount(2)
        self._native_signals = _NativeResult(self)
        self._native_signals.completed.connect(self._native_ready)

    def set_native_catalog(self, references, idle=None, description='', loader=None):
        """Only submitted matching native clips are decoded, off the GUI thread."""
        self._native_generation += 1
        self._native_loader = loader
        self._native_references = tuple(references)
        self.native_clip_select.blockSignals(True)
        self.native_clip_select.clear()
        self.native_clip_select.addItem('Select referenced animation', -1)
        for i, ref in enumerate(references):
            label = ref.name + ('' if ref.available else ' (not extracted)')
            self.native_clip_select.addItem(label, i)
        auto = -1
        if idle is not None:
            auto = next((i for i, ref in enumerate(references)
                         if ref.name == idle.name and ref.virtual_path == idle.virtual_path), -1)
            if auto >= 0:
                self.native_clip_select.setCurrentIndex(auto+1)
        self.native_clip_select.blockSignals(False)
        count = sum(ref.kind == 'caf' and ref.available for ref in references)
        self.native_info.setText(
            '%d available CAFs of %d mappings%s' %
            (count, len(references), ' • Idle: ' + idle.name if idle else ''))
        self.play_native_button.setEnabled(bool(loader and count))
        if auto >= 0 and loader:
            token = self._native_generation
            QTimer.singleShot(80, lambda: self.load_native()
                              if token == self._native_generation else None)

    def _native_selection(self, index):
        i = self.native_clip_select.itemData(index)
        if not isinstance(i, int) or i < 0 or i >= len(self._native_references):
            return
        ref = self._native_references[i]
        self.native_info.setText(
            ('Available: ' if ref.available else 'Missing: ') + ref.virtual_path)

    def load_native(self):
        i = self.native_clip_select.currentData()
        if (self._native_loader is None or not isinstance(i,int)
                or i < 0 or i >= len(self._native_references)):
            return
        ref = self._native_references[i]
        if ref.kind != 'caf' or not ref.available:
            self.native_info.setText('This native clip has not been extracted.')
            return
        self._native_generation += 1
        token = self._native_generation
        self.native_info.setText('Decoding native CAF and rigged model in background…')
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
        self.native_info.setText(
            'Native motion playing • %d bone channels • %s' %
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
        self._display_animation_frame()
        if auto_play and (idle or len(imported.clips)==1):
            self.toggle_play()

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
        source = (str(self._native_references[selected].file)
                  if isinstance(selected,int) and 0 <= selected < len(self._native_references)
                  and self._native_references[selected].available else None)
        if not source:
            source, _ = QFileDialog.getOpenFileName(
                self, 'Select native character or original animation source', '',
                'CryEngine models and animations (*.chr *.skin *.caf *.dba)')
        if not source:
            return
        converter, _ = QFileDialog.getOpenFileName(
            self, 'Select installed Cryengine-Converter executable (not bundled)',
            '', 'Converter executable (*.exe)')
        if not converter:
            return
        asset_root = QFileDialog.getExistingDirectory(
            self, 'Choose unpacked game asset root containing animation files')
        if not asset_root:
            return
        output = QFileDialog.getExistingDirectory(
            self, 'Choose an empty or dedicated output folder for converted GLBs')
        if not output:
            return
        if QMessageBox.question(self, 'Run external converter?',
                'The manager will launch the executable you selected locally '
                'to convert original CAF/DBA tracks into GLB. '
                'Only run software you trust. Continue?',
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No) != QMessageBox.Yes:
            return
        try:
            program, args = validate_conversion(
                converter, source, asset_root, output)
            self.converter_process = QProcess(self)
            self.converter_process.setProgram(program)
            self.converter_process.setArguments(args)
            self.converter_process.setWorkingDirectory(output)
            self.converter_process.finished.connect(
                lambda code, status: self._conversion_finished(code, status, output))
            self.converter_process.errorOccurred.connect(
                lambda _err: self.anim_status.setText(
                    'Converter failed to start. Check its installation and permissions.'))
            self.converter_process.start()
            self.anim_status.setText('Converting original CryEngine animations…')
        except Exception as exc:
            QMessageBox.warning(self, 'Animation conversion', str(exc))

    def _conversion_finished(self, code, status, folder):
        if code != 0 or status != QProcess.NormalExit:
            self.anim_status.setText(
                'External conversion failed (exit %d). Verify that CAF/DBA '
                'tracks and matching skeleton are unpacked.' % code)
            return
        self.anim_status.setText('Conversion finished. Select its animated GLB.')
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
