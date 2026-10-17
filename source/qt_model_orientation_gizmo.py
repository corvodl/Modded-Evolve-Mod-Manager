"""Blender-inspired interactive orientation gizmo for the read-only GPU canvas.

Independent of material/texture rendering: orbit changes camera rotation only.
"""
from __future__ import annotations
import math
from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget


class ModelOrientationGizmo(QWidget):
    """Click signed XYZ axes to snap; drag anywhere to orbit the preview."""

    orientationChanged = Signal()

    _AXES = (
        ('X', (1., 0., 0.), 'Right', '#de6c72'),
        ('Y', (0., 1., 0.), 'Back', '#75c384'),
        ('Z', (0., 0., 1.), 'Top', '#6aa8e4'),
    )

    def __init__(self, canvas, parent=None):
        super().__init__(parent or canvas)
        self.canvas = canvas
        self.setObjectName('modelOrientationGizmo')
        self.setAccessibleName('Drag XYZ orientation gizmo to orbit; click an axis for a view')
        self.setToolTip('Drag to orbit · click X/Y/Z to align · click the opposite dot for reverse view')
        self.setFixedSize(112, 112)
        self.setCursor(Qt.OpenHandCursor)
        self._press = None
        self._moved = False
        self._hit = None
        canvas.cameraChanged.connect(self.update)

    def _axis_points(self):
        yaw, pitch = (math.radians(float(value)) for value in self.canvas.rotation)
        cy, sy, cp, sp = math.cos(yaw), math.sin(yaw), math.cos(pitch), math.sin(pitch)
        center = self.width() / 2, self.height() / 2
        points = []
        for name, (x, y, z), preset, color in self._AXES:
            for direction in (-1, 1):
                x1, y1, z1 = x*direction, y*direction, z*direction
                horizontal = x1*cy - y1*sy
                vertical = (x1*sy + y1*cy)*sp + z1*cp
                depth = (x1*sy + y1*cy)*cp - z1*sp
                position = QPointF(center[0]+horizontal*33, center[1]-vertical*33)
                target = preset if direction > 0 else {
                    'Right':'Left', 'Back':'Front', 'Top':'Bottom'}[preset]
                points.append((depth, name if direction > 0 else '', color,
                               position, target))
        return sorted(points, key=lambda point: point[0])

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        center = QPointF(self.width()/2, self.height()/2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(28, 30, 36, 206))
        painter.drawEllipse(center, 52, 52)
        for depth, letter, color, point, _preset in self._axis_points():
            front = depth >= 0.
            pigment = QColor(color)
            if not front:
                pigment.setAlpha(115)
            painter.setPen(QPen(pigment, 2 if front else 1))
            painter.drawLine(center, point)
            painter.setPen(QPen(QColor('#f6f7fa') if front else QColor('#bdc0cc'), 1))
            painter.setBrush(pigment)
            radius = 11 if front else 6
            painter.drawEllipse(point, radius, radius)
            if letter:
                painter.drawText(int(point.x()-8), int(point.y()-9), 16, 18,
                                 Qt.AlignCenter, letter)
        painter.end()

    def _target_at(self, point):
        candidates = sorted(self._axis_points(), key=lambda item: item[0], reverse=True)
        for _depth, _letter, _color, endpoint, preset in candidates:
            dx, dy = point.x()-endpoint.x(), point.y()-endpoint.y()
            if dx*dx + dy*dy <= 13*13:
                return preset
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._press = event.position()
            self._moved = False
            self._hit = self._target_at(self._press)
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._press is not None and event.buttons() & Qt.LeftButton:
            point = event.position()
            dx, dy = point.x()-self._press.x(), point.y()-self._press.y()
            if abs(dx)+abs(dy) >= 2:
                self._moved = True
                self.canvas.rotation[0] += dx*0.55
                self.canvas.rotation[1] = max(-89., min(89.,
                    self.canvas.rotation[1]+dy*0.45))
                self.canvas.update()
                self.orientationChanged.emit()
                self.update()
            self._press = point
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            if not self._moved and self._hit is not None:
                self.canvas.set_camera_preset(self._hit)
                self.orientationChanged.emit()
            self._press = None
            self._hit = None
            self.setCursor(Qt.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)
