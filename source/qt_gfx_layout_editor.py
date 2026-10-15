"""Visual, guarded editor for root Scaleform layout and runtime promo presets.

Boxes are drawn only when a real DefineShape bounding RECT exists. Missing
runtime state is not approximated with invented screen rectangles. All edits
use the parent GFX session's undo/Apply-to-Mod-Project workflow.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPen, QBrush, QTransform, QPixmap
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QTreeWidget,
    QTreeWidgetItem, QGraphicsView, QGraphicsScene, QGraphicsRectItem,
    QGraphicsPixmapItem, QDoubleSpinBox, QComboBox, QLineEdit, QCheckBox,
    QGroupBox, QWidget, QMessageBox, QSplitter,
)

from gfx_preview_backend import inspect_gfx, GfxError
from gfx_layout_backend import static_elements, stage_static_layout
from gfx_featured_promo import stage_featured_promo


class _BoundedPlacement(QGraphicsRectItem):
    def __init__(self, entry, on_drag):
        rect = entry.rect
        super().__init__(QRectF(rect[0], rect[2],
                                rect[1] - rect[0], rect[3] - rect[2]))
        self.entry = entry
        self._on_drag = on_drag
        self.setPen(QPen(QColor("#ed3131"), 2, Qt.DashLine))
        self.setBrush(QBrush(QColor(235, 49, 49, 28)))
        self.setFlag(QGraphicsRectItem.ItemIsSelectable, True)
        self.setFlag(QGraphicsRectItem.ItemIsMovable, entry.editable)
        self.setToolTip(f"Root depth {entry.depth}: {entry.label}")
        self.setZValue(50 + entry.depth)
        self.apply_transform(entry.x, entry.y, entry.scale_x,
                             entry.scale_y, entry.rotation)

    def apply_transform(self, x, y, sx, sy, rotation):
        a = math.radians(rotation)
        # Same SWF affine order as gfx_layout_backend._packed_matrix.
        m = QTransform(sx * math.cos(a), sx * math.sin(a),
                       -sy * math.sin(a), sy * math.cos(a),
                       x, y)
        self.setPos(QPointF(0, 0))
        self.setTransform(m)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        if self.isSelected() and self.entry.editable:
            delta = self.pos()
            if abs(delta.x()) > .01 or abs(delta.y()) > .01:
                self._on_drag(self.entry, delta.x(), delta.y())


class GfxLayoutDialog(QDialog):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor
        self.setWindowTitle("Visual GFX layout editor — " + editor.relative)
        self.resize(1260, 740)
        self.setMinimumSize(780, 490)
        self.entries = ()
        self._items = {}
        self._lock = False
        self._source = None
        root = QVBoxLayout(self)

        help_label = QLabel(
            "Move outlined static shapes in the GFX coordinate space. "
            "Unbounded sprite instances support numeric edits only. "
            "The runtime featured title/TRY/BUY controls are ActionScript "
            "overrides; their true in-game positions cannot be inferred "
            "from this static preview. Custom labels are experimental and "
            "can be replaced by game callbacks or localization.")
        help_label.setWordWrap(True)
        root.addWidget(help_label)

        bar = QHBoxLayout()
        self.refresh = QPushButton("Refresh layers and preview")
        self.refresh.setObjectName("gfxLayoutRefresh")
        self.refresh.clicked.connect(self.reload)
        bar.addWidget(self.refresh)
        self.show_background = QCheckBox("Show current GFX raster")
        self.show_background.setChecked(True)
        self.show_background.toggled.connect(self._backdrop)
        bar.addWidget(self.show_background)
        bar.addStretch(1)
        root.addLayout(bar)

        split = QSplitter(Qt.Horizontal, self)
        root.addWidget(split, 1)

        self.tree = QTreeWidget()
        self.tree.setObjectName("gfxLayoutHierarchy")
        self.tree.setHeaderLabels(["Layer / character", "Depth"])
        self.tree.currentItemChanged.connect(self._selected)
        self.tree.setMinimumWidth(225)
        split.addWidget(self.tree)

        self.scene = QGraphicsScene(self)
        self.scene.setBackgroundBrush(QBrush(QColor("#20242b")))
        self.canvas = QGraphicsView(self.scene)
        self.canvas.setObjectName("gfxLayoutCanvas")
        self.canvas.setRenderHints(self.canvas.renderHints())
        self.canvas.setDragMode(QGraphicsView.RubberBandDrag)
        self.canvas.setMinimumWidth(260)
        split.addWidget(self.canvas)

        self.side = QWidget(self)
        right = QVBoxLayout(self.side)
        self.identity = QLabel("Select a layer")
        self.identity.setWordWrap(True)
        right.addWidget(self.identity)
        self.property_group = QGroupBox("Layer properties")
        fields = QVBoxLayout(self.property_group)
        self.x = self._numeric(fields, "X (stage pixels)", -100000, 100000, 0, 1)
        self.y = self._numeric(fields, "Y (stage pixels)", -100000, 100000, 0, 1)
        self.sx = self._numeric(fields, "X scale (%)", 1, 2000, 100, 1)
        self.sy = self._numeric(fields, "Y scale (%)", 1, 2000, 100, 1)
        self.angle = self._numeric(fields, "Rotation (degrees)", -360, 360, 0, 1)
        self.visible = QCheckBox("Visible")
        self.visible.setChecked(True)
        fields.addWidget(self.visible)
        self.property_group.setEnabled(False)
        right.addWidget(self.property_group)

        self.runtime_group = QGroupBox("Featured UI runtime override")
        runtime_layout = QVBoxLayout(self.runtime_group)
        self.promo_action = QComboBox()
        self.promo_action.setObjectName("gfxLayoutRuntimeAction")
        for name, action in (("Move / resize / rotate", "layout"),
                             ("Hide", "hide"), ("Remove / disable", "remove"),
                             ("Replace selected text (experimental)", "custom")):
            self.promo_action.addItem(name, action)
        runtime_layout.addWidget(self.promo_action)
        self.custom = QLineEdit()
        self.custom.setPlaceholderText("Type any replacement label")
        self.custom.setObjectName("gfxLayoutCustomTitle")
        self.custom.setMaxLength(160)
        runtime_layout.addWidget(self.custom)
        self.promo_action.currentIndexChanged.connect(self._runtime_options)
        self.custom.textChanged.connect(self._runtime_options)
        self.runtime_group.setVisible(False)
        right.addWidget(self.runtime_group)

        self.stage = QPushButton("Stage selected layout edit")
        self.stage.setObjectName("gfxLayoutStage")
        self.stage.setEnabled(False)
        self.stage.clicked.connect(self._stage)
        right.addWidget(self.stage)
        self.message = QLabel(
            "Stages in a private GFX edit session; Undo staged edit and "
            "Apply to Mod Project remain in the parent GFX editor.")
        self.message.setWordWrap(True)
        right.addWidget(self.message)
        right.addStretch(1)
        split.addWidget(self.side)
        split.setSizes([270, 670, 310])
        self.reload()

    def _numeric(self, parent, name, lo, hi, value, step):
        row = QHBoxLayout()
        row.addWidget(QLabel(name))
        spin = QDoubleSpinBox()
        spin.setObjectName("gfxLayout" + name.split(" ")[0])
        spin.setRange(lo, hi)
        spin.setDecimals(2)
        spin.setSingleStep(step)
        spin.setValue(value)
        spin.valueChanged.connect(self._properties)
        row.addWidget(spin)
        parent.addLayout(row)
        return spin

    def reload(self):
        from gfx_edit_backend import read_gfx_session
        snapshot = read_gfx_session(self.editor.owner.workspace,
                                    self.editor.snapshot.session)
        self._source = snapshot.working_file.read_bytes()
        movie_info = inspect_gfx(self._source)
        self.entries = static_elements(self._source)
        self._lock = True
        self.tree.clear()
        self.scene.clear()
        self._items.clear()
        self.scene.setSceneRect(0, 0, movie_info.width, movie_info.height)
        self._background = None
        self._backdrop(self.show_background.isChecked())
        group = QTreeWidgetItem(self.tree, ["Static timeline", ""])
        for entry in self.entries:
            title = entry.label + ("" if entry.editable else "  [read-only]")
            node = QTreeWidgetItem(group, [title, str(entry.depth)])
            node.setData(0, Qt.UserRole, ("static", entry.depth, entry.character))
            node.setToolTip(0, entry.reason)
            if entry.rect is not None:
                element = _BoundedPlacement(entry, self._dragged)
                self.scene.addItem(element)
                self._items[(entry.depth, entry.character)] = element

        if self.editor.relative.casefold() == "libs/ui/main_menu.gfx":
            dynamic = QTreeWidgetItem(self.tree, ["Runtime featured promotion", ""])
            for label, target in (("Featured title (dynamic text)", "title"),
                                  ("TRY controls", "try"),
                                  ("BUY controls", "buy")):
                item = QTreeWidgetItem(dynamic, [label, "runtime"])
                item.setData(0, Qt.UserRole, ("runtime", target))
                item.setToolTip(0, "Requires game ActionScript; not a static frame box.")
        # Exported classes can expose menu sprites created at runtime. They
        # are not confirmed first-frame placements or editable text fields.
        placed = {entry.character for entry in self.entries}
        other = [(cid, name) for cid, name in movie_info.exports
                 if cid not in placed and
                 any(token in name.casefold()
                     for token in ("menu", "button", "btn", "play", "option",
                                   "settings", "back", "quit", "store", "nav",
                                   "profile"))]
        if other:
            definitions = QTreeWidgetItem(
                self.tree, ["Other UI symbols (definitions only)", ""])
            for cid, name in other[:160]:
                node = QTreeWidgetItem(definitions, [name, f"#{cid}"])
                node.setData(0, Qt.UserRole, ("definition", cid, name))
                node.setToolTip(0,
                    "Exported class/movie definition, not a confirmed first-frame "
                    "placement. May be created by ActionScript at runtime.")
        self.tree.expandAll()
        if other:
            definitions.setExpanded(False)
        self._lock = False
        self._selected(self.tree.currentItem(), None)
        self.canvas.fitInView(self.scene.sceneRect(), Qt.KeepAspectRatio)

    def _backdrop(self, shown):
        if self._background is not None:
            self.scene.removeItem(self._background)
            self._background = None
        # The source is the parent's current FFDec preview, never an edited
        # screenshot written back as an asset.
        png = self.editor.viewer._png
        if shown and png and self.editor.viewer._raw == self._source:
            image = QPixmap()
            if image.loadFromData(png, "PNG"):
                self._background = self.scene.addPixmap(image)
                self._background.setZValue(-100)
                self._background.setOpacity(.65)
        self.scene.update()

    def _entry(self, data):
        if not data or data[0] != "static":
            return None
        for entry in self.entries:
            if (entry.depth, entry.character) == (data[1], data[2]):
                return entry
        return None

    def _selected(self, current, _previous):
        if self._lock:
            return
        self._lock = True
        data = current.data(0, Qt.UserRole) if current else None
        entry = self._entry(data)
        is_runtime = bool(data and data[0] == "runtime")
        self.runtime_group.setVisible(is_runtime)
        self.property_group.setEnabled(bool((entry and entry.editable) or is_runtime))
        self.stage.setEnabled(bool((entry and entry.editable) or is_runtime))
        self.identity.setText(
            f"{entry.label} · root depth {entry.depth}\n{entry.reason}"
            if entry else
            ("Runtime " + data[1] +
             "\nAbsolute layout values; may be overridden by game callbacks."
             if is_runtime else
             ("Defined UI symbol " + data[2] +
              "\\nNot placed in the root first frame. Inspect its movie and "
              "ActionScript in FFDec to locate editable text."
              if data and data[0] == "definition"
              else "Select a supported layer")))
        values = ((entry.x, entry.y, entry.scale_x * 100,
                   entry.scale_y * 100, entry.rotation)
                  if entry else (0, 0, 100, 100, 0))
        for control, value in zip(
                (self.x, self.y, self.sx, self.sy, self.angle), values):
            control.setValue(value)
        self.visible.setChecked(True)
        self._lock = False
        self._runtime_options()
        for key, item in self._items.items():
            item.setSelected(bool(entry and key == (entry.depth, entry.character)))

    def _runtime_options(self, *_):
        if self._lock:
            return
        current = self.tree.currentItem()
        data = current.data(0, Qt.UserRole) if current else None
        runtime = bool(data and data[0] == "runtime")
        action = self.promo_action.currentData()
        text_active = runtime and action == "custom"
        self.custom.setVisible(text_active)
        self.custom.setEnabled(text_active)
        if runtime:
            self.custom.setPlaceholderText(
                {"title": "Replacement featured title",
                 "try": "Replacement TRY button label",
                 "buy": "Replacement BUY button label"}[data[1]])
        controls = action == "layout" or not runtime
        for field in (self.x, self.y, self.sx, self.sy, self.angle, self.visible):
            field.setEnabled(controls)
        self.stage.setEnabled(
            bool(data and (self._entry(data) and self._entry(data).editable or
                           runtime and (action != "custom" or
                                        text_active and self.custom.text().strip()))))

    def _dragged(self, entry, dx, dy):
        # A box is movable only if the physical shape bounds are verified.
        for index in range(self.tree.topLevelItem(0).childCount()):
            node = self.tree.topLevelItem(0).child(index)
            if node.data(0, Qt.UserRole) == ("static", entry.depth, entry.character):
                self.tree.setCurrentItem(node)
                break
        self._lock = True
        self.x.setValue(entry.x + dx)
        self.y.setValue(entry.y + dy)
        self._lock = False
        self._properties()

    def _properties(self, *_):
        if self._lock:
            return
        current = self.tree.currentItem()
        data = current.data(0, Qt.UserRole) if current else None
        entry = self._entry(data)
        if entry is not None:
            box = self._items.get((entry.depth, entry.character))
            if box is not None:
                box.apply_transform(self.x.value(), self.y.value(),
                                    self.sx.value() / 100, self.sy.value() / 100,
                                    self.angle.value())

    def _stage(self):
        current = self.tree.currentItem()
        data = current.data(0, Qt.UserRole) if current else None
        entry = self._entry(data)
        if not data:
            return
        coords = dict(x=self.x.value(), y=self.y.value(),
                      scale_x=self.sx.value() / 100,
                      scale_y=self.sy.value() / 100,
                      rotation=self.angle.value(), visible=self.visible.isChecked())
        if entry:
            if not entry.editable:
                return
            label = "static placement"
            def edit():
                return stage_static_layout(
                    self.editor.owner.workspace,
                    self.editor.snapshot.session,
                    depth=entry.depth, character=entry.character, **coords)
        elif data[0] == "runtime":
            target = data[1]
            mode = self.promo_action.currentData()
            kwargs = dict(target=target, configured=self.editor._backend)
            if mode == "layout":
                kwargs["layout"] = (coords["x"], coords["y"],
                                    coords["scale_x"], coords["scale_y"],
                                    coords["rotation"], coords["visible"])
            if mode == "custom":
                kwargs["custom"] = self.custom.text()
            label = "runtime featured " + target
            def edit():
                return stage_featured_promo(
                    self.editor.owner.workspace, self.editor.snapshot.session,
                    mode, **kwargs)
        else:
            return
        result = self.editor._run(label, edit)
        if result is not None:
            self.reload()
            self.message.setText(
                "Staged safely in your GFX session. Use Undo staged edit "
                "or Apply to Mod Project in the main GFX editor. "
                "Runtime/ActionScript results need an offline game test.")
