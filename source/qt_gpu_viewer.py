"""GPU-accelerated Qt model viewer for supported Evolve CrChF meshes.

Unlike the older Win32/Tk WGL host this uses a Qt-owned OpenGL context.
It never accesses installed PAKs or writes extracted workspace files.
"""
from __future__ import annotations
import math
import sys
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
        self.rotation = [-30.0, 14.0]
        self.zoom = 1.0
        self.pan = [0., 0.]
        self.wireframe = False
        self.textured = True
        self._last = None
        self.gl_error = None
        self.light_strength = 1.0
        self.background_mode = 'Dark'

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
        if self.textures:
            try:
                from OpenGL.GL import glDeleteTextures
                glDeleteTextures(list(self.textures))
            except Exception:
                pass
            self.textures = []

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
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1)
        for im in self.materials.textures:
            if im is None:
                self.textures.append(0)
                continue
            sample = im.convert('RGB').transpose(__import__('PIL.Image',fromlist=['Transpose']).Transpose.FLIP_TOP_BOTTOM)
            texid = int(glGenTextures(1))
            glBindTexture(GL_TEXTURE_2D,texid)
            glTexImage2D(GL_TEXTURE_2D,0,GL_RGB,sample.width,sample.height,0,
                         GL_RGB,GL_UNSIGNED_BYTE,sample.tobytes())
            for param in (GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER):
                glTexParameteri(GL_TEXTURE_2D,param,GL_LINEAR)
            for param in (GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T):
                glTexParameteri(GL_TEXTURE_2D,param,GL_REPEAT)
            self.textures.append(texid)
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

    def resizeGL(self, width, height):
        from OpenGL.GL import glViewport
        glViewport(0,0,max(1,width),max(1,height))

    def paintGL(self):
        from OpenGL.GL import (
            glClear, glMatrixMode, glLoadIdentity, glOrtho,
            glTranslatef, glRotatef, glColor3f, glEnable, glDisable,
            glEnableClientState, glDisableClientState, glVertexPointer,
            glNormalPointer, glTexCoordPointer, glDrawElements,
            glPolygonMode, glBindTexture, glMaterialfv,
            GL_COLOR_BUFFER_BIT, GL_DEPTH_BUFFER_BIT,
            GL_PROJECTION, GL_MODELVIEW, GL_TEXTURE_2D, GL_FRONT_AND_BACK,
            GL_DIFFUSE, GL_FLOAT, GL_UNSIGNED_INT, GL_TRIANGLES,
            GL_VERTEX_ARRAY, GL_NORMAL_ARRAY, GL_TEXTURE_COORD_ARRAY,
            GL_FILL, GL_LINE, GL_FRONT, GL_BACK, GL_AMBIENT,
        )
        background = (0.092,0.09,0.115,1.0) if self.background_mode=='Dark' else (0.26,0.27,0.29,1.0)
        from OpenGL.GL import glClearColor, glLightfv, GL_LIGHT0, GL_DIFFUSE
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
        try:
            # Draw independent submaterial groups without duplicating geometry.
            for first,count,slot in arrays.draws:
                tex = self.textures[slot] if use_uv and slot < len(self.textures) else 0
                if tex:
                    glEnable(GL_TEXTURE_2D)
                    glBindTexture(GL_TEXTURE_2D,tex)
                else:
                    glDisable(GL_TEXTURE_2D)
                glColor3f(0.84,0.82,0.82)
                glMaterialfv(GL_FRONT_AND_BACK,GL_DIFFUSE,[0.81,0.8,0.82,1.0])
                glDrawElements(GL_TRIANGLES,int(count),GL_UNSIGNED_INT,
                               arrays.indices[first:first+count])
        finally:
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
        self._prepare_animation_controls(layout)
        self.native_info = QLabel('Native game animations: not scanned')
        self.native_info.setWordWrap(True)
        layout.addWidget(self.native_info)
        foot=QLabel('Left drag: rotate · Right/middle/Shift drag: pan · Wheel: zoom. '
                    'Faceted shading keeps hard edges; smooth shading is optional.')
        foot.setWordWrap(True)
        foot.setVisible(not compact)
        layout.addWidget(foot)

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
        idle = next((name for name in imported.clips if
                     'idle' in name.casefold() and 'additive' not in name.casefold()), None)
        if idle:
            self.clip_select.setCurrentText(idle)
        self.clip_select.blockSignals(False)
        self.anim_position = 0.
        self._display_animation_frame()
        if auto_play and (idle or len(imported.clips)==1):
            self.toggle_play()

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
        self.anim_position += elapsed
        duration = self.animated.clips[self.clip_select.currentText()][0]
        if self.anim_position >= duration:
            self.anim_position %= duration
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
