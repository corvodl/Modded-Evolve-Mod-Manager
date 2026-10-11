"""Interactive, throttled CPU fallback for the Qt CryEngine preview.

Keeps camera controls usable even without a working OpenGL context.
No game files are written. Interactive frames use a limited triangle sample.
"""
from __future__ import annotations
import math

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPainter, QPixmap
from PySide6.QtWidgets import QWidget

from model_preview import render_mesh


class QtCPUModelCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.mesh = None
        self.yaw = -0.5
        self.elevation = 0.24
        self.zoom = 1.0
        self.pan = [0.0, 0.0]
        self._last = None
        self._dragging = False
        self._pixmap = QPixmap()
        self.setMouseTracking(True)
        self.setMinimumSize(240, 180)
        self._frame = QTimer(self)
        self._frame.setSingleShot(True)
        self._frame.timeout.connect(self._render)
        self._settle = QTimer(self)
        self._settle.setSingleShot(True)
        self._settle.timeout.connect(self._render)
        self._warning = ''

    def set_mesh(self, mesh, warning=''):
        self.mesh = mesh
        self._warning = warning
        self.pan[:] = [0., 0.]
        self.yaw, self.elevation, self.zoom = -0.5, .24, 1.
        self.request_render()

    def request_render(self):
        if not self._frame.isActive():
            self._frame.start(35)
        if self._dragging:
            self._settle.start(200)

    def _render(self):
        if self.mesh is None:
            return
        # Quality recovers as soon as the pointer becomes idle.
        count = 1700 if self._dragging or self._settle.isActive() else 11000
        image = render_mesh(
            self.mesh, width=max(128, self.width()), height=max(128, self.height()),
            yaw=self.yaw, elevation=self.elevation, zoom=self.zoom,
            pan=tuple(self.pan), max_draw_triangles=count)
        from io import BytesIO
        with BytesIO() as buffer:
            image.save(buffer, format='PNG')
            pix = QPixmap()
            if pix.loadFromData(buffer.getvalue(), 'PNG'):
                self._pixmap = pix
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self._pixmap.isNull():
            painter.drawPixmap(self.rect(), self._pixmap)
        else:
            painter.fillRect(self.rect(), self.palette().window())
            painter.drawText(self.rect(), Qt.AlignCenter, 'Select a CryEngine model.')
        painter.end()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.request_render()

    def mousePressEvent(self, event):
        if event.button() in (Qt.LeftButton, Qt.MiddleButton, Qt.RightButton):
            self._last = event.position()
            self._dragging = True
            event.accept()

    def mouseMoveEvent(self, event):
        if self._last is None:
            return
        delta = event.position() - self._last
        if event.buttons() & (Qt.MiddleButton | Qt.RightButton) or (
                event.buttons() & Qt.LeftButton and event.modifiers() & Qt.ShiftModifier):
            self.pan[0] += delta.x()
            self.pan[1] += delta.y()
        elif event.buttons() & Qt.LeftButton:
            self.yaw += delta.x() * .007
            self.elevation = max(-1.5, min(1.5, self.elevation + delta.y() * .007))
        self._last = event.position()
        self.request_render()
        event.accept()

    def mouseReleaseEvent(self, event):
        self._last = None
        self._dragging = False
        self._settle.stop()
        self.request_render()
        event.accept()

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom = max(.15, min(8., self.zoom * math.exp(delta/1200.)))
            self.request_render()
        event.accept()
