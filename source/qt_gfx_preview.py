"""Embedded, nonblocking Scaleform static frame / sprite preview widget.

The displayed checkerboard is UI-only. PNG export returns FFDec's RGBA
raster directly, never the zoomed QLabel or a screenshot of this widget.
"""
from __future__ import annotations

from threading import Event
from io import BytesIO

from PIL import Image

from PySide6.QtCore import QSize, Qt, QRect, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QScrollArea, QFileDialog, QMessageBox, QTabWidget,
    QCheckBox,
)
from qt_navigation_icons import navigation_icon
from gfx_ui_dependencies import related_ui_textures
from gfx_placement_diagnostics import inspect_bitmap_placement
from gfx_root_layer_preview import root_layers, likely_main_menu_dialog
from gfx_preview_backend import (
    inspect_gfx, resolve_imports, ffdec_command, render_gfx, GfxError,
)


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._original = QPixmap()
        self._visible_rect = QRect()
        self._fully_opaque = None
        self._background = "transparent"
        self._trim = False
        self._zoom = 1.0
        self._fit = True
        self.setMinimumSize(160, 120)

    def set_png(self, content):
        picture = QPixmap()
        if not picture.loadFromData(content, "PNG"):
            raise ValueError("FFDec returned a PNG Qt cannot display.")
        # Alpha bounding boxes are computed from FFDec's actual PNG, never
        # from opaque white/black guesses. Cropping affects the viewer only.
        bounds = None
        opaque = None
        if picture.width() * picture.height() <= 16_000_000:
            with Image.open(BytesIO(content)) as image:
                if "A" in image.getbands():
                    alpha = image.getchannel("A")
                    bounds = alpha.getbbox()
                    opaque = alpha.getextrema()[0] == 255
                else:
                    opaque = True
        self._fully_opaque = opaque
        self._original = picture
        self._visible_rect = (QRect(bounds[0], bounds[1],
                                   bounds[2] - bounds[0], bounds[3] - bounds[1])
                              if bounds else QRect())
        self._refresh_pixmap()

    def clear_png(self):
        self._original = QPixmap()
        self._pixmap = QPixmap()
        self._visible_rect = QRect()
        self._fully_opaque = None
        self.update()

    def _refresh_pixmap(self):
        if self._trim and not self._visible_rect.isNull():
            self._pixmap = self._original.copy(self._visible_rect)
        else:
            self._pixmap = self._original
        self.update()

    def set_background(self, mode):
        if mode not in ("transparent", "dark", "light", "white"):
            raise ValueError("Unsupported GFX canvas background.")
        self._background = mode
        self.update()

    def set_trim(self, enabled):
        self._trim = bool(enabled)
        self._refresh_pixmap()

    def set_view(self, zoom=1.0, fit=True):
        self._zoom, self._fit = zoom, fit
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        colors = {
            "dark": "#20232b",
            "light": "#e1e5ed",
            "white": "#ffffff",
        }
        if self._background == "transparent":
            # Deliberately use a darker checkerboard: an opaque white fill
            # baked into the artwork must be distinguishable from alpha.
            painter.fillRect(self.rect(), QColor("#4e5662"))
            box = 16
            for y in range(0, self.height(), box):
                for x in range(0, self.width(), box):
                    if (x // box + y // box) % 2 == 0:
                        painter.fillRect(x, y, box, box, QColor("#343b46"))
        else:
            painter.fillRect(self.rect(), QColor(colors[self._background]))
        if not self._pixmap.isNull():
            if self._fit:
                size = self._pixmap.size()
                size.scale(self.size(), Qt.KeepAspectRatio)
            else:
                size = self._pixmap.size() * self._zoom
            dest = QRect((self.width() - size.width()) // 2,
                         (self.height() - size.height()) // 2,
                         size.width(), size.height())
            painter.drawPixmap(dest, self._pixmap)
        painter.end()


class _RenderTask(QThread):
    result = Signal(int, object, object)

    def __init__(self, token, data, virtual, *, frame, symbol, scale, imports,
                 context, backend, texture_loader=None, available_paths=(),
                 root_layer_mode="all", root_layer_depth=None):
        super().__init__()
        self.token, self.data, self.virtual = token, data, virtual
        self.frame, self.symbol, self.scale = frame, symbol, scale
        self.imports, self.context, self.backend = imports, context, backend
        self.texture_loader = texture_loader
        self.available_paths = available_paths
        self.root_layer_mode = root_layer_mode
        self.root_layer_depth = root_layer_depth
        self._cancel = Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            diagnostics = []
            png = render_gfx(
                self.data, virtual=self.virtual, frame=self.frame,
                symbol=self.symbol, scale=self.scale, imports=self.imports,
                build_context=self.context, backend=self.backend,
                cancelled=self._cancel.is_set,
                texture_loader=self.texture_loader,
                available_paths=self.available_paths,
                render_diagnostics=diagnostics,
                root_layer_mode=self.root_layer_mode,
                root_layer_depth=self.root_layer_depth)
            self.result.emit(self.token, (png, tuple(diagnostics)), None)
        except Exception as exc:
            self.result.emit(self.token, None, str(exc))


class GfxPreviewWidget(QWidget):
    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self._serial = 0
        self._jobs = []
        self._raw = None
        self._png = None
        self._virtual = None
        self._context = None
        self._imports = {}
        self._warnings = ()
        self._texture_loader = None
        self._available_paths = ()
        self._render_notes = ()
        self._texture_paths = ()
        self._bitmap_bindings = ()
        self._root_layers = ()
        self._detected_dialog_depth = None
        self._info = None
        self._backend = None
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        controls.addWidget(QLabel("Symbol:"))
        self.symbol = QComboBox()
        self.symbol.setObjectName("gfxSymbolSelection")
        self.symbol.setToolTip("Select a movie frame or sprite symbol")
        controls.addWidget(self.symbol, 2)
        controls.addWidget(QLabel("Frame:"))
        self.frame = QSpinBox()
        self.frame.setMinimum(1)
        self.frame.setObjectName("gfxFrameSelection")
        controls.addWidget(self.frame)
        controls.addWidget(QLabel("Raster:"))
        self.scale = QComboBox()
        self.scale.setObjectName("gfxRasterScale")
        for n in (1, 2, 4):
            self.scale.addItem(f"{n}×", n)
        controls.addWidget(self.scale)
        self.fit = QPushButton("Fit")
        self.fit.setIcon(navigation_icon("fit"))
        self.fit.setCheckable(True)
        self.fit.setChecked(True)
        self.fit.setObjectName("gfxFit")
        controls.addWidget(self.fit)
        self.zoom = QComboBox()
        self.zoom.setObjectName("gfxDisplayZoom")
        for n in (0.5, 1.0, 2.0, 4.0):
            self.zoom.addItem(f"{round(n*100)}%", n)
        self.zoom.setCurrentIndex(1)
        controls.addWidget(self.zoom)
        layout.addLayout(controls)
        canvas_controls = QHBoxLayout()
        canvas_controls.addWidget(QLabel("Canvas:"))
        self.background = QComboBox()
        self.background.setObjectName("gfxCanvasBackground")
        for label, mode in (
            ("Transparent checkerboard", "transparent"),
            ("Dark", "dark"),
            ("Light", "light"),
            ("White", "white"),
        ):
            self.background.addItem(label, mode)
        self.background.setToolTip(
            "Preview background only. White artwork drawn in the movie stays opaque.")
        canvas_controls.addWidget(self.background)
        self.trim = QCheckBox("Zoom to visible content (preview only)")
        self.trim.setObjectName("gfxTrimTransparentMargins")
        self.trim.setToolTip(
            "Trim transparent PNG margins only in the viewer; exported PNG "
            "keeps the original full frame. Opaque white is never discarded.")
        self.trim.setChecked(False)
        canvas_controls.addWidget(self.trim)
        self.external_images = QCheckBox("Render referenced DDS in frame")
        self.external_images.setObjectName("gfxRenderExternalTextures")
        self.external_images.setToolTip(
            "Uses verified Scaleform 1008/1009 atlas regions in a temporary "
            "render-only movie. Correct static FillStyle transforms and masks "
            "are retained; ActionScript/game callbacks are not simulated.")
        self.external_images.setChecked(True)
        self.external_images.setEnabled(False)
        canvas_controls.addWidget(self.external_images)
        canvas_controls.addStretch(1)
        layout.addLayout(canvas_controls)
        layer_controls = QHBoxLayout()
        layer_controls.addWidget(QLabel("Root layers:"))
        self.root_mode = QComboBox()
        self.root_mode.setObjectName("gfxRootLayerMode")
        self.root_mode.addItem("All layers", "all")
        self.root_mode.addItem("Hide selected layer", "hide")
        self.root_mode.addItem("Show selected layer only", "isolate")
        self.root_mode.setToolTip(
            "Diagnostic preview only. Hide or isolate root display-list "
            "depths to identify an opaque popup without changing the GFX.")
        layer_controls.addWidget(self.root_mode)
        self.root_layer = QComboBox()
        self.root_layer.setObjectName("gfxRootLayerSelection")
        self.root_layer.setMinimumContentsLength(24)
        self.root_layer.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        layer_controls.addWidget(self.root_layer, 2)
        self.root_hint = QLabel("No root layer selected.")
        self.root_hint.setObjectName("gfxRootLayerHint")
        self.root_hint.setWordWrap(True)
        layer_controls.addWidget(self.root_hint, 3)
        layout.addLayout(layer_controls)
        self.details = QLabel("Select a .gfx movie")
        self.details.setObjectName("gfxMovieInfo")
        self.details.setWordWrap(True)
        layout.addWidget(self.details)
        self.area = QScrollArea()
        self.area.setWidgetResizable(True)
        self.area.setAlignment(Qt.AlignCenter)
        self.canvas = _Canvas(self)
        self.area.setWidget(self.canvas)
        self.tabs = QTabWidget()
        self.tabs.setObjectName("gfxPreviewTabs")
        self.tabs.addTab(self.area, "GFX frame")
        textures = QWidget()
        tex_layout = QVBoxLayout(textures)
        self.texture_select = QComboBox()
        self.texture_select.setObjectName("gfxReferencedTexture")
        self.texture_select.setMinimumContentsLength(26)
        self.texture_select.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        tex_layout.addWidget(self.texture_select)
        self.texture_status = QLabel("UI_Data and compiled GFX textures are read-only.")
        self.texture_status.setObjectName("gfxTextureStatus")
        self.texture_status.setWordWrap(True)
        tex_layout.addWidget(self.texture_status)
        self.texture_image = QLabel("Select a referenced DDS image.")
        self.texture_image.setAlignment(Qt.AlignCenter)
        self.texture_image.setWordWrap(True)
        tex_scroll = QScrollArea()
        tex_scroll.setWidgetResizable(True)
        tex_scroll.setAlignment(Qt.AlignCenter)
        tex_scroll.setWidget(self.texture_image)
        tex_layout.addWidget(tex_scroll, 1)
        self.tabs.addTab(textures, "Referenced textures")
        self.tabs.setTabEnabled(1, False)

        bitmap_page = QWidget()
        bitmap_layout = QVBoxLayout(bitmap_page)
        self.bitmap_select = QComboBox()
        self.bitmap_select.setObjectName("gfxBitmapSymbol")
        self.bitmap_select.setMinimumContentsLength(26)
        self.bitmap_select.setSizeAdjustPolicy(
            QComboBox.AdjustToMinimumContentsLengthWithIcon)
        bitmap_layout.addWidget(self.bitmap_select)
        self.bitmap_status = QLabel("No Scaleform bitmap mappings inspected.")
        self.bitmap_status.setObjectName("gfxPlacementStatus")
        self.bitmap_status.setWordWrap(True)
        bitmap_layout.addWidget(self.bitmap_status)
        self.bitmap_image = QLabel("Select a bitmap symbol to inspect its atlas crop.")
        self.bitmap_image.setAlignment(Qt.AlignCenter)
        self.bitmap_image.setWordWrap(True)
        bitmap_scroll = QScrollArea()
        bitmap_scroll.setWidgetResizable(True)
        bitmap_scroll.setAlignment(Qt.AlignCenter)
        bitmap_scroll.setWidget(self.bitmap_image)
        bitmap_layout.addWidget(bitmap_scroll, 1)
        self.tabs.addTab(bitmap_page, "Bitmap placement")
        self.tabs.setTabEnabled(2, False)
        layout.addWidget(self.tabs, 1)
        self.note = QLabel("Static movie preview; original GFX remains unchanged.")
        self.note.setObjectName("gfxPreviewStatus")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        bar = QHBoxLayout()
        self.setup = QPushButton("FFDec…")
        self.setup.setToolTip("Configure the externally installed FFDec tool")
        self.setup.setIcon(navigation_icon("settings", size=15))
        self.setup.setIconSize(QSize(15, 15))
        self.setup.clicked.connect(self.configure_backend)
        bar.addWidget(self.setup)
        self.export = QPushButton("Export PNG…")
        self.export.setToolTip("Export the selected frame or sprite as a PNG")
        self.export.setIcon(navigation_icon("export", size=15))
        self.export.setIconSize(QSize(15, 15))
        self.export.setObjectName("gfxExportPng")
        self.export.setEnabled(False)
        self.export.clicked.connect(owner.export_png)
        bar.addWidget(self.export)
        self.edit_movie = QPushButton(
            "Edit in Project…" if getattr(owner, "read_only_library", False)
            else "Edit GFX…")
        self.edit_movie.setToolTip("Open the complete GFX editor for this movie")
        self.edit_movie.setObjectName("gfxOpenEditor")
        self.edit_movie.setIcon(navigation_icon("gfx", size=15))
        self.edit_movie.setIconSize(QSize(15, 15))
        self.edit_movie.clicked.connect(
            owner.edit_in_project if getattr(owner, "read_only_library", False)
            else owner.edit_gfx)
        bar.addWidget(self.edit_movie)
        bar.addStretch(1)
        layout.addLayout(bar)
        self.symbol.currentIndexChanged.connect(self._symbol_changed)
        self.frame.valueChanged.connect(self._changed)
        self.scale.currentIndexChanged.connect(self._changed)
        self.fit.toggled.connect(self._zoom_changed)
        self.zoom.currentIndexChanged.connect(self._zoom_changed)
        self.background.currentIndexChanged.connect(self._background_changed)
        self.trim.toggled.connect(self.canvas.set_trim)
        self.external_images.toggled.connect(self._changed)
        self.root_mode.currentIndexChanged.connect(self._root_changed)
        self.root_layer.currentIndexChanged.connect(self._root_changed)
        self.texture_select.currentIndexChanged.connect(self._texture_changed)
        self.bitmap_select.currentIndexChanged.connect(self._bitmap_changed)
        self.tabs.currentChanged.connect(self._texture_tab_changed)

    def cancel_pending(self):
        self._serial += 1
        for worker in list(self._jobs):
            worker.cancel()
        self._png = None
        self.export.setEnabled(False)

    def open_movie(self, data, relative, *, resolve=None, context=None,
                   backend=None, available_paths=(), texture_preview=None):
        self.cancel_pending()
        self._raw = None
        self._virtual = relative
        self._context = context
        self._backend = backend
        self._imports = {}
        self._warnings = ()
        self._texture_loader = texture_preview
        self._available_paths = tuple(available_paths)
        self._render_notes = ()
        self._texture_paths = ()
        self._root_layers = ()
        self._detected_dialog_depth = None
        self.root_mode.blockSignals(True)
        self.root_mode.setCurrentIndex(0)
        self.root_mode.blockSignals(False)
        self.root_layer.blockSignals(True)
        self.root_layer.clear()
        self.root_layer.blockSignals(False)
        self.root_hint.setText("No root layer selected.")
        self.external_images.setEnabled(False)
        self._bitmap_bindings = ()
        self.bitmap_select.blockSignals(True)
        self.bitmap_select.clear()
        self.bitmap_select.blockSignals(False)
        self.tabs.setTabEnabled(2, False)
        self.bitmap_image.clear()
        self.bitmap_status.setText("No Scaleform bitmap mappings inspected.")
        self.texture_select.blockSignals(True)
        self.texture_select.clear()
        self.texture_select.blockSignals(False)
        self.tabs.setTabEnabled(1, False)
        self.texture_image.clear()
        self.texture_status.setText("No matching same-build UI textures found.")
        self.canvas.clear_png()
        try:
            info = inspect_gfx(data)
            resolved, warnings = resolve_imports(
                info, relative, resolve or (lambda _name: None))
            self._info, self._raw = info, data
            self._imports, self._warnings = resolved, warnings
            self.external_images.setEnabled(bool(
                texture_preview and 1008 in info.extension_tags
                and 1009 in info.extension_tags))
            self._texture_paths = related_ui_textures(
                relative, available_paths, resolve or (lambda _path: None),
                imports=info.imports, movie_bytes=data)
            self.texture_select.blockSignals(True)
            for path, origin in self._texture_paths:
                self.texture_select.addItem(f"{path}  [{origin}]", path)
            self.texture_select.blockSignals(False)
            self.tabs.setTabEnabled(1, bool(self._texture_paths and texture_preview))
            self.tabs.setTabText(1, f"Referenced textures ({len(self._texture_paths)})")
            if self._texture_paths:
                self.texture_status.setText(
                    f"{len(self._texture_paths)} matching images; selected DDS is "
                    "decoded on demand. UI_Data preloads do not imply "
                    "the image is visible in this GFX frame.")
            try:
                report = inspect_bitmap_placement(data, relative, available_paths)
                self._bitmap_bindings = report.movies
                self.bitmap_select.blockSignals(True)
                for binding in report.movies:
                    self.bitmap_select.addItem(
                        f"#{binding.bitmap_character}  "
                        f"{binding.texture.rsplit('/', 1)[-1]}  "
                        f"rect {binding.atlas_rectangle}",
                        binding)
                self.bitmap_select.blockSignals(False)
                self.bitmap_status.setText(
                    f"{report.images} compiled images · "
                    f"{len(report.movies)} atlas bindings · "
                    f"{sum(bool(x.shape_ids) for x in report.movies)} bitmap-filled shapes · "
                    f"{sum(x.static_first_frame for x in report.movies)} "
                    "first-frame shape references. "
                    + " ".join(report.messages))
                self.tabs.setTabText(
                    2, f"Bitmap placement ({len(report.movies)})")
                self.tabs.setTabEnabled(2, bool(report.movies))
            except (ValueError, OSError, TypeError) as exc:
                self.bitmap_status.setText(
                    "Bitmap placement diagnostics unavailable: " + str(exc)[:240])
            self.symbol.blockSignals(True)
            self.symbol.clear()
            self.symbol.addItem("Main timeline", (0, info.frames))
            display_names = {
                character_id: exported for character_id, exported in info.exports
                if exported and not exported.startswith("__Packages.")
            }
            for character_id, frames in info.sprites:
                if frames == 0:
                    continue  # Exported classes without timelines cannot render.
                label = display_names.get(character_id, f"Sprite #{character_id}")
                self.symbol.addItem(f"{label} (#{character_id}, {frames} frames)",
                                    (character_id, frames))
            self.symbol.setCurrentIndex(0)
            self.symbol.blockSignals(False)
            self.frame.blockSignals(True)
            self.frame.setMaximum(info.frames)
            self.frame.setValue(1)
            self.frame.blockSignals(False)
            self._sync_root_layers()
            self._detected_dialog_depth = (
                matched.depth if (matched := likely_main_menu_dialog(
                    data, relative)) is not None else None)
            if self._detected_dialog_depth is not None:
                self.root_mode.blockSignals(True)
                self.root_layer.blockSignals(True)
                self.root_mode.setCurrentIndex(1)  # Preview-only hide.
                for index in range(self.root_layer.count()):
                    if self.root_layer.itemData(index) == self._detected_dialog_depth:
                        self.root_layer.setCurrentIndex(index)
                        break
                self.root_mode.blockSignals(False)
                self.root_layer.blockSignals(False)
                self._sync_root_layers()
            self.details.setText(
                f"GFX v{info.version} · {info.compression} · "
                f"{info.width}×{info.height} · {info.fps:g} fps · "
                f"{info.frames} root frames · "
                f"{sum(n > 0 for _, n in info.sprites)} playable sprites · "
                f"{sum(n == 0 for _, n in info.sprites)} nonvisual definitions · "
                f"{len(info.images)} embedded image symbols")
            self._start_render()
        except (ValueError, OSError) as exc:
            self._info = None
            self.note.setText(str(exc))
            self.details.setText("Unsupported or damaged Scaleform movie.")

    def _texture_tab_changed(self, tab):
        if tab == 1:
            self._texture_changed()
        elif tab == 2:
            self._bitmap_changed()

    def _bitmap_changed(self, *_):
        if self.tabs.currentIndex() != 2:
            return
        binding = self.bitmap_select.currentData()
        if binding is None:
            return
        x0, y0, x1, y1 = binding.atlas_rectangle
        placement = ("referenced by first static frame"
                     if binding.static_first_frame else
                     "not placed in first root frame (may be runtime-driven)")
        shape = (", ".join(f"#{value}" for value in binding.shape_ids)
                 if binding.shape_ids else "not parsed / nonstandard fill")
        detail = (
            f"Bitmap #{binding.bitmap_character} · Atlas image #{binding.atlas_id}"
            f" · Shapes: {shape} · Atlas rectangle:"
            f" ({x0}, {y0}) to ({x1}, {y1}) · {placement}. "
            "This is a verified atlas crop, not an exact on-screen position."
        )
        self.bitmap_image.clear()
        if not binding.file_found:
            self.bitmap_image.setText("DDS not imported in this build.")
            self.bitmap_status.setText(detail + " Matching DDS is unavailable.")
            return
        if self._texture_loader is None:
            self.bitmap_image.setText("No read-only texture decoder configured.")
            self.bitmap_status.setText(detail)
            return
        try:
            image = self._texture_loader(binding.texture)
            if image is None or x1 > image.width or y1 > image.height:
                raise ValueError("DDS missing or smaller than the validated atlas crop.")
            crop = image.crop((x0, y0, x1, y1))
            crop.thumbnail((960, 640))
            output = BytesIO()
            crop.convert("RGBA").save(output, "PNG")
            preview = QPixmap()
            if not preview.loadFromData(output.getvalue(), "PNG"):
                raise ValueError("Failed to preview the cropped bitmap.")
            self.bitmap_image.setPixmap(preview)
            self.bitmap_status.setText(detail)
        except (OSError, ValueError, TypeError) as exc:
            self.bitmap_image.setText("Cannot decode this atlas crop.")
            self.bitmap_status.setText(detail + " " + str(exc)[:240])

    def _texture_changed(self, *_):
        if self.tabs.currentIndex() != 1 or self._texture_loader is None:
            return
        path = self.texture_select.currentData()
        if not path:
            return
        self.texture_image.clear()
        try:
            image = self._texture_loader(path)
            if image is None:
                raise ValueError("Matching texture is not available in this build.")
            image = image.copy()
            image.thumbnail((960, 640))
            payload = BytesIO()
            image.convert("RGBA").save(payload, format="PNG")
            pixmap = QPixmap()
            if not pixmap.loadFromData(payload.getvalue(), "PNG"):
                raise ValueError("Qt could not display the decoded DDS image.")
            self.texture_image.setPixmap(pixmap)
            self.texture_status.setText(path + f" — {image.width}×{image.height} preview")
        except (OSError, ValueError, TypeError) as exc:
            self.texture_image.setText("Referenced DDS could not be decoded.")
            self.texture_status.setText(path + ": " + str(exc)[:240])

    def _symbol_changed(self, *_):
        selected = self.symbol.currentData()
        if not selected:
            return
        _, count = selected
        self.frame.blockSignals(True)
        self.frame.setMaximum(count)
        self.frame.setValue(1)
        self.frame.blockSignals(False)
        self._sync_root_layers()
        self._changed()

    def _root_changed(self, *_):
        self._sync_root_layers()
        self._start_render()

    def _sync_root_layers(self):
        if not self._raw or not self._info:
            return
        symbol = (self.symbol.currentData() or (0,))[0]
        is_root = symbol == 0
        if is_root:
            try:
                choices = root_layers(self._raw, frame=self.frame.value())
            except GfxError:
                choices = ()
        else:
            choices = ()
        selected = self.root_layer.currentData()
        self._root_layers = choices
        self.root_layer.blockSignals(True)
        self.root_layer.clear()
        for item in choices:
            self.root_layer.addItem(
                f"Depth {item.depth} — {item.label} (#{item.character})",
                item.depth)
        if selected is not None:
            for index, item in enumerate(choices):
                if item.depth == selected:
                    self.root_layer.setCurrentIndex(index)
                    break
        self.root_layer.blockSignals(False)
        self.root_mode.setEnabled(bool(choices) and is_root)
        self.root_layer.setEnabled(
            bool(choices) and is_root and self.root_mode.currentData() != "all")
        if is_root and self._detected_dialog_depth is not None:
            self.root_hint.setText(
                f"Dialog-style overlay at depth {self._detected_dialog_depth} "
                "(sprite #270, shape #265). "
                "Hidden in this preview by default; select All layers to compare.")
        else:
            self.root_hint.setText(
                f"{len(choices)} first-frame root depths. "
                "Hiding a suspected popup affects this preview only."
                if choices else
                "Select Main timeline for root-layer diagnostics.")

    def _changed(self, *_):
        self._sync_root_layers()
        self._start_render()

    def _start_render(self):
        if not self._raw or not self._info or self.symbol.currentData() is None:
            return
        self.cancel_pending()
        current = self.symbol.currentData()
        symbol, _length = current
        frame = self.frame.value()
        scale = self.scale.currentData() or 1
        if self._info.width * self._info.height * scale * scale > 36_000_000:
            self.note.setText("Choose a smaller raster resolution (36 megapixel limit).")
            return
        diagnostics = (*self._info.notes, *self._warnings)
        self.note.setText("Rendering static frame… " + " ".join(diagnostics))
        try:
            ffdec_command(self._backend)
        except GfxError as exc:
            self.note.setText(str(exc) + "\n" + " ".join(diagnostics))
            return
        layer_mode = self.root_mode.currentData() if symbol == 0 else "all"
        if not self._root_layers or layer_mode not in ("hide", "isolate"):
            layer_mode = "all"
        layer_depth = self.root_layer.currentData() if layer_mode != "all" else None
        token = self._serial
        worker = _RenderTask(
            token, self._raw, self._virtual, frame=frame, symbol=symbol,
            scale=scale, imports=self._imports, context=self._context,
            backend=self._backend,
            texture_loader=(self._texture_loader if self.external_images.isChecked()
                            and self.external_images.isEnabled() else None),
            available_paths=self._available_paths,
            root_layer_mode=layer_mode, root_layer_depth=layer_depth)
        self._jobs.append(worker)
        worker.result.connect(self._finished)
        worker.finished.connect(lambda job=worker: self._done(job))
        worker.start()

    def _finished(self, token, png, error):
        if token != self._serial:
            return  # A new file/selection superseded this worker.
        if error:
            self.note.setText("Could not render selected GFX content: " + str(error))
            return
        if isinstance(png, tuple):
            png, self._render_notes = png
        else:
            self._render_notes = ()
        self._png = png
        try:
            self.canvas.set_png(png)
        except ValueError as exc:
            self.note.setText(str(exc))
            self._png = None
            return
        self._zoom_changed()
        self.export.setEnabled(True)
        diagnostics = (*self._render_notes, *self._info.notes, *self._warnings)
        if self.canvas._fully_opaque is True:
            diagnostics += (
                "This rendered frame is fully opaque. Canvas color cannot "
                "remove white shapes painted in the GFX.",
            )
        self.note.setText("Static PNG ready. " + " ".join(diagnostics))

    def _done(self, worker):
        if worker in self._jobs:
            self._jobs.remove(worker)
        worker.deleteLater()

    def _background_changed(self, *_):
        self.canvas.set_background(self.background.currentData() or "transparent")

    def _zoom_changed(self, *_):
        self.canvas.set_view(self.zoom.currentData() or 1.0, self.fit.isChecked())
        self.zoom.setEnabled(not self.fit.isChecked())

    def selected_png(self):
        if self._png is None:
            raise GfxError("Render the selected GFX frame/sprite successfully before exporting PNG.")
        return self._png

    def configure_backend(self):
        current = self._backend or ""
        chosen, _ = QFileDialog.getOpenFileName(
            self, "Select FFDec 26.3.0 renderer (external, separately installed)",
            current, "FFDec (ffdec-cli.exe ffdec.jar);;All files (*)")
        if not chosen:
            return
        try:
            ffdec_command(chosen)
            self._backend = chosen
            self.owner.manager.settings["gfx_ffdec_path"] = chosen
            self.owner.manager.persist()
            self._start_render()
        except GfxError as exc:
            QMessageBox.warning(self, "Scaleform renderer", str(exc))
