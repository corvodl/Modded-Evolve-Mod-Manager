"""Reusable native Qt Widgets used across manager workspaces.

Components are deliberately lightweight: no WebView/QML, blur effects or
periodic repolishing. They inherit the application's semantic Qt palette.
Controls keep their original actions, tooltips and keyboard behavior.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QVBoxLayout, QLabel, QWidget, QPushButton,
    QToolButton, QMenu, QScrollArea, QSizePolicy, QSplitter
)

from qt_navigation_icons import navigation_icon


class WorkspaceHeader(QWidget):
    """A compact heading and supporting context for editor and list pages."""
    def __init__(self, title, description="", parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceHeader")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 2)
        outer.setSpacing(4)
        self.title = QLabel(title)
        self.title.setObjectName("sectionTitle")
        outer.addWidget(self.title)
        self.description = QLabel(description)
        self.description.setObjectName("muted")
        self.description.setWordWrap(True)
        self.description.setVisible(bool(description))
        outer.addWidget(self.description)


class SurfacePanel(QFrame):
    """Flat tonal grouping; supports an optional compact heading."""
    def __init__(self, heading="", parent=None):
        super().__init__(parent)
        self.setObjectName("workspaceSurface")
        self.content = QVBoxLayout(self)
        self.content.setContentsMargins(12, 10, 12, 10)
        self.content.setSpacing(8)
        if heading:
            title = QLabel(heading)
            title.setObjectName("panelTitle")
            self.content.addWidget(title)


class DisclosurePanel(QWidget):
    """Progressively reveal advanced tools without deleting their widgets."""
    def __init__(self, title, *, expanded=False, parent=None):
        super().__init__(parent)
        self.setObjectName("disclosurePanel")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)
        self.toggle = QPushButton(title)
        self.toggle.setObjectName("disclosureToggle")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setAccessibleName("Show or hide " + title)
        self.toggle.setToolTip("Expand or collapse " + title)
        self.toggle.setIcon(navigation_icon("layers", size=16))
        outer.addWidget(self.toggle)
        self.body = QWidget(self)
        self.body.setObjectName("disclosureBody")
        self.body_layout = QVBoxLayout(self.body)
        self.body_layout.setContentsMargins(10, 6, 10, 8)
        self.body_layout.setSpacing(8)
        outer.addWidget(self.body)
        self.body.setVisible(expanded)
        self.toggle.toggled.connect(self.body.setVisible)

    def addWidget(self, widget):
        self.body_layout.addWidget(widget)

    def addLayout(self, layout):
        self.body_layout.addLayout(layout)


class ContextActions(QWidget):
    """Frequent actions visible; secondary commands in a single native menu."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("contextActions")
        self.layout_row = QHBoxLayout(self)
        self.layout_row.setContentsMargins(0, 0, 0, 0)
        self.layout_row.setSpacing(6)
        self.layout_row.addStretch(1)
        self.more = QToolButton(self)
        self.more.setObjectName("contextOverflow")
        self.more.setText("More")
        self.more.setIcon(navigation_icon("layers", size=16))
        self.more.setToolTip("Additional actions")
        self.more.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.more)
        self.more.setMenu(self.menu)
        self.layout_row.addWidget(self.more)

    def add_action(self, label, callback, *, primary=False, overflow=False, tooltip=""):
        if overflow:
            action = self.menu.addAction(label)
            action.triggered.connect(lambda _checked=False: callback())
            if tooltip:
                action.setToolTip(tooltip)
            return action
        button = QPushButton(label, self)
        button.setObjectName("primary" if primary else "secondary")
        button.setToolTip(tooltip or label)
        button.clicked.connect(lambda _checked=False: callback())
        self.layout_row.insertWidget(self.layout_row.count() - 2, button)
        return button


def configure_splitter(splitter: QSplitter, *, sizes=None):
    """Accessible grab handle and no silently collapsed editor viewport."""
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(5)
    if sizes:
        splitter.setSizes(list(sizes))
    return splitter


def compact_editor_scroll(content: QWidget, *, name="workspaceInspector"):
    """Scrollable inspector so small Windows screens never clip tools."""
    area = QScrollArea()
    area.setObjectName(name)
    area.setFrameShape(QFrame.NoFrame)
    area.setWidgetResizable(True)
    area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
    area.setMinimumWidth(210)
    area.setWidget(content)
    return area
