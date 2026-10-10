"""GPU-accelerated Qt model viewer for supported Evolve CrChF meshes.

Unlike the older Win32/Tk WGL host this uses a Qt-owned OpenGL context.
It never accesses installed PAKs or writes extracted workspace files.
"""
from __future__ import annotations
import math
import sys
import numpy as np

from PySide6.QtCore import Qt
from PySide6.QtGui import QSurfaceFormat
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QComboBox

from gpu_model_preview import prepare_geometry, GPUUnavailable


class QtGLModelCanvas(QOpenGLWidget):
    """OpenGL 2.1 compatibility vertex arrays, depth and approximate materials."""

    def __init__(self, mesh, materials=None, parent=None):
        super().__init__(parent)
        format = QSurfaceFormat()
        format.setRenderableType(QSurfaceFormat.OpenGL)
        format.setVersion(2, 1)
        format.setProfile(QSurfaceFormat.CompatibilityProfile)
        format.setDepthBufferSize(24)
        format.setSamples(2)
        self.setFormat(format)
        self.setMinimumSize(450, 340)
        self.mesh = mesh
        self.geometry = prepare_geometry(mesh)
        self.materials = materials
        self.textures = []
        self.rotation = [-30.0, 14.0]
        self.zoom = 1.0
        self.wireframe = False
        self.textured = True
        self._last = None
        self.gl_error = None

    def set_mesh(self, mesh, materials=None):
        self.makeCurrent()
        try:
            self._release_textures()
        finally:
            self.doneCurrent()
        self.mesh = mesh
        self.geometry = prepare_geometry(mesh)
        self.materials = materials
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
        glLightfv(GL_LIGHT0,GL_DIFFUSE,[0.94,0.9,0.84,1.0])
        glLightfv(GL_LIGHT0,GL_AMBIENT,[0.28,0.28,0.32,1.0])
        glLightModelfv(GL_LIGHT_MODEL_AMBIENT,[0.1,0.1,0.14,1.0])
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
        glTranslatef(0,0,-4.0)
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
        if self._last is not None and (event.buttons() & Qt.LeftButton):
            pos = event.position()
            self.rotation[0] += (pos.x()-self._last.x())*.55
            self.rotation[1] = max(-89,min(89,self.rotation[1]+(pos.y()-self._last.y())*.45))
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


class QtGPUModelDialog(QDialog):
    """Actual native Qt GPU model window with mesh and material details."""

    def __init__(self,parent,mesh,materials=None):
        super().__init__(parent)
        self.setWindowTitle('CryEngine Model — Qt GPU Viewer')
        self.resize(990,750)
        self.setMinimumSize(620,460)
        self.setStyleSheet(parent.styleSheet())
        layout=QVBoxLayout(self)
        info=QLabel(mesh.source_name+'  •  '+mesh.description)
        info.setWordWrap(True)
        layout.addWidget(info)
        self.canvas=QtGLModelCanvas(mesh,materials,self)
        layout.addWidget(self.canvas,1)
        line=QHBoxLayout()
        wire=QCheckBox('Wireframe')
        wire.toggled.connect(lambda value:(setattr(self.canvas,'wireframe',value),self.canvas.update()))
        line.addWidget(wire)
        textured=QCheckBox('Textures')
        textured.setChecked(True)
        textured.toggled.connect(lambda value:(setattr(self.canvas,'textured',value),self.canvas.update()))
        line.addWidget(textured)
        line.addWidget(QLabel('Quality'))
        quality=QComboBox()
        quality.addItems(('Performance','High'))
        quality.currentTextChanged.connect(self.canvas.set_quality)
        line.addWidget(quality)
        reset=QPushButton('Reset Camera')
        reset.clicked.connect(self.reset_view)
        line.addWidget(reset)
        line.addStretch()
        layout.addLayout(line)
        foot=QLabel('Drag to rotate · Scroll to zoom · GPU rendering with depth testing, lighting and approximate UV diffuse maps.')
        foot.setWordWrap(True)
        layout.addWidget(foot)

    def reset_view(self):
        self.canvas.rotation[:]=[-30.,14.]
        self.canvas.zoom=1.0
        self.canvas.update()
