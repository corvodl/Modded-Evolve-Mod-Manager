"""Embedded, nonblocking Scaleform static frame / sprite preview widget.

The displayed checkerboard is UI-only. PNG export returns FFDec's RGBA
raster directly, never the zoomed QLabel or a screenshot of this widget.
"""
from __future__ import annotations

from threading import Event
from io import BytesIO

from PySide6.QtCore import QSize, Qt, QRect, QThread, Signal
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QSpinBox,
    QPushButton, QScrollArea, QFileDialog, QMessageBox, QTabWidget,
)
from qt_navigation_icons import navigation_icon
from gfx_ui_dependencies import related_ui_textures
from gfx_preview_backend import (
    inspect_gfx, resolve_imports, ffdec_command, render_gfx, GfxError,
)


class _Canvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._pixmap = QPixmap()
        self._zoom = 1.0
        self._fit = True
        self.setMinimumSize(160, 120)

    def set_png(self, content):
        picture = QPixmap()
        if not picture.loadFromData(content, "PNG"):
            raise ValueError("FFDec returned a PNG Qt cannot display.")
        self._pixmap = picture
        self.update()

    def set_view(self, zoom=1.0, fit=True):
        self._zoom, self._fit = zoom, fit
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#dedee2"))
        box = 16
        for y in range(0, self.height(), box):
            for x in range(0, self.width(), box):
                if (x // box + y // box) % 2 == 0:
                    painter.fillRect(x, y, box, box, QColor("#a9a9b0"))
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
                 context, backend):
        super().__init__()
        self.token, self.data, self.virtual = token, data, virtual
        self.frame, self.symbol, self.scale = frame, symbol, scale
        self.imports, self.context, self.backend = imports, context, backend
        self._cancel = Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            png = render_gfx(
                self.data, virtual=self.virtual, frame=self.frame,
                symbol=self.symbol, scale=self.scale, imports=self.imports,
                build_context=self.context, backend=self.backend,
                cancelled=self._cancel.is_set)
            self.result.emit(self.token, png, None)
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
        self._texture_paths = ()
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
        self.texture_select.currentIndexChanged.connect(self._texture_changed)
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
        self._texture_paths = ()
        self.texture_select.blockSignals(True)
        self.texture_select.clear()
        self.texture_select.blockSignals(False)
        self.tabs.setTabEnabled(1, False)
        self.texture_image.clear()
        self.texture_status.setText("No matching same-build UI textures found.")
        self.canvas._pixmap = QPixmap()
        self.canvas.update()
        try:
            info = inspect_gfx(data)
            resolved, warnings = resolve_imports(
                info, relative, resolve or (lambda _name: None))
            self._info, self._raw = info, data
            self._imports, self._warnings = resolved, warnings
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
        self._changed()

    def _changed(self, *_):
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
        token = self._serial
        worker = _RenderTask(
            token, self._raw, self._virtual, frame=frame, symbol=symbol,
            scale=scale, imports=self._imports, context=self._context,
            backend=self._backend)
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
        self._png = png
        try:
            self.canvas.set_png(png)
        except ValueError as exc:
            self.note.setText(str(exc))
            self._png = None
            return
        self._zoom_changed()
        self.export.setEnabled(True)
        diagnostics = (*self._info.notes, *self._warnings)
        self.note.setText("Static PNG ready. " + " ".join(diagnostics))

    def _done(self, worker):
        if worker in self._jobs:
            self._jobs.remove(worker)
        worker.deleteLater()

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
