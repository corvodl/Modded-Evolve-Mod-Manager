"""Production PySide6 Evolve Mod Manager frontend.

Qt text/font health is checked before generating a compiled screenshot so
blank glyphs cannot pass publication without detection.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import webbrowser
import traceback

# PyInstaller's windowed bootloader does not provide sys.stdout/stderr.
# Third-party imports or Windows native callbacks may still write there.
# Record preview errors privately rather than exiting without diagnostics.
_QT_STARTUP_LOG = None
if os.name == 'nt' and getattr(sys, 'frozen', False):
    try:
        _app_dir = Path(sys.executable).resolve().parent
        # Packaging smoke tests execute from .build/app-dist/EvolveModManager.
        # Never write logs into that clean, allowlisted distributable folder.
        if _app_dir.parent.name.casefold() == 'app-dist':
            _app_dir = (Path(os.environ.get('LOCALAPPDATA')
                             or os.environ.get('TEMP') or '.').resolve()
                        / 'EvolveModManager')
        _QT_LOG_DIR = _app_dir / 'Data' / 'Logs'
        _QT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        _QT_STARTUP_LOG = open(_QT_LOG_DIR / 'startup.log', 'a',
                               encoding='utf-8', buffering=1)
        if sys.stdout is None:
            sys.stdout = _QT_STARTUP_LOG
        if sys.stderr is None:
            sys.stderr = _QT_STARTUP_LOG
        _QT_STARTUP_LOG.write('Qt preview startup, Windows frozen executable.\n')
    except OSError:
        sys.stdout = sys.stdout or open(os.devnull, 'w')
        sys.stderr = sys.stderr or open(os.devnull, 'w')


from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QRect, Signal, QObject, QTimer, QProcess, QProcessEnvironment, QUrl, QSize, QItemSelectionModel
from PySide6.QtGui import QAction, QIcon, QPixmap, QFont, QDesktopServices, QColor, QPainter
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QScrollArea, QPushButton, QLineEdit, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QPlainTextEdit, QMessageBox,
    QFileDialog, QDialog, QDialogButtonBox, QFormLayout, QProgressDialog,
    QCheckBox, QMenu, QAbstractItemView, QSizePolicy, QProgressBar, QTabWidget,
    QToolButton, QStyle, QStyleOptionButton, QInputDialog, QColorDialog, QMenuBar,
)

from app_runtime import APP_HOME, DATA_HOME, SETTINGS_FILE, settings_source, save_settings, worker_command
from app_updater import (discover_latest, download_and_prepare,
                         launch_apply, installed_commit, installed_revision, report_startup_ready)
from version_info import app_version, footer_version
from qt_theme_116 import PALETTES, ROLES, palette, qt_palette_style
from studio_polish_117 import theme_preset, validate_theme_preset, read_theme_file
from pak_browser import archive_description, describe_archive, matching_archives, format_size, import_target
from universal_stage import allowed_paks
from launch_integration import state_status, launcher_commands, restore_command, package_status, add_missing_scripts
from orphaned_swap_recovery import plan_recovery
from injector_guard import inspect_injector
from multi_pak_assets import load_collection
from old_prepared import scan_prepared
from desktop_shortcut import create_desktop_shortcut
from ui_copy import load_text
from qt_font_health import ensure_readable_ui_font, ui_family, glyphs_readable
from portable_bundle import MARKER, bind_home
from developer_access import verify_code
from developer_memory_panel import DeveloperMemoryPanel
from online_guard import set_offline, target_executable
from release_notes import RELEASE_NOTES
from qt_navigation_icons import navigation_icon
from qt_ui_components import WorkspaceHeader, ContextActions, configure_splitter

ROOT = Path(__file__).resolve().parent
COLORS = {'bg':'#0b0b0d','surface':'#17171d','panel':'#202027',
          'text':'#f5f3f6','muted':'#a6a3ae','accent':'#cf394b','outline':'#353139'}
SECTIONS = ('Instructions','Asset Library','Modding','Play & Restore','Settings','Credits',
            "What's New",'Developer')


def _widget(cls, *args, name=None, **kwargs):
    item = cls(*args, **kwargs)
    if name:
        item.setObjectName(name)
    return item


def _label(text, muted=False, wrap=True):
    item = QLabel(text)
    item.setWordWrap(wrap)
    item.setObjectName('muted' if muted else 'normal')
    return item


def _button(text, callback, primary=False):
    b = QPushButton(text)
    b.setCursor(Qt.PointingHandCursor)
    b.setObjectName('primary' if primary else 'secondary')
    # Ignore Qt's clicked(bool) parameter: otherwise a Menu(bool show=None)
    # handler receives False and the drawer never opens for real users.
    b.clicked.connect(lambda _checked=False: callback())
    return b


def _info_icon(message):
    """Hover help plus a clickable, keyboard-accessible information panel."""
    info = QToolButton()
    info.setObjectName('infoIcon')
    info.setText('i')  # ASCII glyph avoids missing circled-i fonts on Windows.
    info.setFixedSize(21, 21)
    info.setToolTip(message)
    info.setAccessibleName('Information: ' + message)
    info.setCursor(Qt.PointingHandCursor)
    info.setFocusPolicy(Qt.StrongFocus)
    info.clicked.connect(lambda _checked=False: QMessageBox.information(
        info.window(), 'Information', message))
    return info


def _action_with_info(title, callback, hint, primary=False):
    widget = QWidget()
    widget.setObjectName('actionWithInfo')
    layout = QHBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(4)
    button = _button(title, callback, primary=primary)
    button.setToolTip(hint)
    layout.addWidget(button, 1)
    layout.addWidget(_info_icon(hint), 0, Qt.AlignVCenter)
    return widget


ACTION_HINTS = {
    'Unpack PAK': 'Extract a selected signed PAK into a separate editable project. The installed game is not changed.',
    'Unpack Selected PAKs': 'Extract 2–30 selected signed archives into linked project workspaces. Uses extra disk space.',
    'Edit Files': 'Open the native Qt editor for extracted XML, textures, models, or private project copies.',
    'Level Editor 1.0': 'Open an extracted level PAK to inspect CryEngine object layers and safely edit placement transforms.',
    'Review Changes': 'Compare the extracted workspace with its source PAK before building a mod.',
    'Build Mod': 'Build and sign a modified PAK from the current project. This does not install it into the game.',
    'Add Mod': 'Stage a built mod for a later offline launch; installed original files stay untouched until the swap.',
    'Import Modified PAK': 'Stage an externally modified, compatible PAK after validation.',
    'Undo Staged Mod': 'Restore an earlier staged PAK from a dated project backup; not the installed live game.',
    'Play With Mods': 'Begin offline mod testing. Already-prepared files launch faster. Restore originals after your session.',
    'Restore Game Files': 'Safely return original game PAKs and injector from lightweight rename backups; retain mod files for reuse.',
    'Status Details': 'Show counts and recovery details from the real swap journal.',
    'Set Up Launcher': 'Install missing trusted launcher helper scripts without overwriting existing journal or scripts.',
    'Refresh Original PAKs': 'After client verification, rebuild signing from updated original game PAKs.',
    'Prepare Mod Files Now': 'Perform the slow verified preparation ahead of time. Does not start Evolve or replace installed originals.',
    'Check for Updates': 'Compare verified Stable and Beta releases; offer only the newest version.',
}


def _row(*items):
    lay = QHBoxLayout()
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(9)
    for item in items:
        lay.addWidget(item)
    return lay


def _card(title, note=''):
    frame = _widget(QFrame, name='card')
    frame.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(16, 12, 16, 14)
    lay.setSpacing(8)
    if title:
        heading = _label(title)
        heading.setObjectName('cardTitle')
        lay.addWidget(heading)
    if note:
        lay.addWidget(_label(note, muted=True))
    return frame, lay


def _settings_heading(layout, title, description):
    """A divider and heading within Settings, never a new Menu entry."""
    line = _widget(QFrame, name='settingsDivider')
    line.setFrameShape(QFrame.HLine)
    line.setFixedHeight(1)
    layout.addWidget(line)
    name = _label(title)
    name.setObjectName('settingsHeading')
    layout.addWidget(name)
    layout.addWidget(_label(description, muted=True))


def _scroll_page():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(12, 10, 12, 14)
    layout.setSpacing(12)
    scroll.setWidget(wrapper)
    return scroll, layout


def qt_style():
    return """
    QMainWindow, QWidget { background:#0b0b0d; color:#f5f3f6;
        font-family:'Segoe UI'; font-size:10pt; }
    QFrame#card { background:#17171d; border:1px solid #353139; border-radius:16px; }
    QFrame#drawer { background:#16151b; border-right:1px solid #45323a; }
    QMenuBar { background:#16151b; color:#f5f3f6; padding:4px 8px;
               border-bottom:1px solid #353139; }
    QMenuBar::item { padding:6px 13px; margin-right:3px; border-radius:6px; }
    QMenuBar::item:selected { background:#48303a; }
    QMenu { background:#201d24; color:#f5f3f6; border:1px solid #49404a;
            padding:6px; }
    QMenu::item { padding:7px 24px; border-radius:5px; }
    QMenu::item:selected { background:#74313f; color:white; }
    QMenu::separator { height:1px; background:#463741; margin:5px 12px; }
    QFrame#settingsDivider { background:#41313a; border:none; min-height:1px; max-height:1px; }
    QLabel#settingsHeading { color:#f7f4f6; font-size:14pt; font-weight:650; background:transparent; }
    QLabel#muted { color:#a6a3ae; background:transparent; }
    QLabel#normal { color:#f5f3f6; background:transparent; }
    QLabel#cardTitle { color:#ffffff; font-size:12pt; font-weight:650; background:transparent; }
    QLabel#sectionTitle { color:#fff; font-size:19pt; font-weight:700; }
    QLabel#brand { color:#fa4557; font-size:17pt; font-weight:750; letter-spacing:.5px; }
    QLabel#version { color:#fa586c; font-size:10pt; font-weight:600; }
    QPushButton { background:#26242c; color:#f5f3f6; border:1px solid #3a353f;
        border-radius:10px; padding:8px 13px; font-weight:600; min-height:16px; }
    QPushButton:hover { background:#38313c; border-color:#69434b; }
    QPushButton:pressed { background:#211d25; }
    QPushButton:disabled { color:#736e79; background:#201e23; }
    QPushButton#primary { background:#c73649; color:#fff; border:1px solid #cf4858; }
    QPushButton#primary:hover { background:#dc475b; }
    QPushButton#nav { background:transparent; color:#d9d5dd;
         border:none; border-radius:9px; padding:0px; }
    QPushButton#nav:hover { background:#35262f; }
    QPushButton#nav:focus, QPushButton#navActive:focus { border:2px solid #fa586c; }
    QPushButton#navActive { background:#682734; color:white; border:none;
         border-radius:9px; padding:0px; }
    QPushButton:focus { border:2px solid #fa586c; }
    QLineEdit:focus, QComboBox:focus, QTableWidget:focus { border:2px solid #fa586c; }
    QLineEdit, QComboBox, QPlainTextEdit, QTableWidget {
       background:#18171d; color:#f4f2f4; border:1px solid #39353d;
       border-radius:9px; padding:7px; selection-background-color:#87293b;
       selection-color:white; }
    QTableWidget { gridline-color:#28262c; alternate-background-color:#202027; padding:0px; }
    QHeaderView::section { background:#29262e; color:#eeeaf0; border:0;
        border-right:1px solid #38343d; padding:9px; font-weight:600; }
    QTableWidget::item { padding:6px; }
    QScrollArea { border:none; background:transparent; }
    QScrollBar:vertical { background:#141319; width:10px; }
    QScrollBar::handle:vertical { background:#4b434d; border-radius:5px; min-height:25px; }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
    QSplitter::handle { background:#35252c; height:5px; }
    QCheckBox { color:#d6d3db; spacing:8px; }
    QToolButton#infoIcon { background:#201920; color:#fa586c;
        border:1px solid #864052; border-radius:10px; font-weight:700;
        padding:0px; font-size:10pt; }
    QToolButton#infoIcon:hover, QToolButton#infoIcon:focus {
        background:#55303c; border-color:#fa586c; }
    QToolButton#refreshStatusIcon { background:#261e26;
        border:1px solid #693647; border-radius:9px; }
    QToolButton#refreshStatusIcon:hover { background:#55303c; }
    QToolTip { background:#222026; color:#f7f4f7; border:1px solid #864052;
        padding:6px; }
    QMenu { background:#1e1c23; color:#eee; border:1px solid #403943; padding:5px; }
    QMenu::item { padding:7px 18px; border-radius:7px; }
    QMenu::item:selected { background:#702c3c; }
    """.replace("font-family:'Segoe UI';", f'font-family:"{ui_family()}";')


class NavigationButton(QPushButton):
    """Always paint the full navigation icon in an explicit, inset rectangle.

    Qt's stylesheet-aligned QPushButton icon can be clipped by text/padding
    calculations on Windows at non-default display scaling. Paint the bevel,
    icon and elided text separately so the icon never occupies clipped padding
    or disappears under the label. Keep the QPushButton API/accessibility.
    """

    def icon_bounds(self):
        side = min(30, max(0, self.height() - 12), max(0, self.width() - 90))
        return QRect(10, (self.height() - side) // 2, side, side)

    def paintEvent(self, event):
        option = QStyleOptionButton()
        self.initStyleOption(option)
        option.text = ''
        option.icon = QIcon()
        painter = QPainter(self)
        self.style().drawControl(QStyle.CE_PushButton, option, painter, self)
        bounds = self.icon_bounds()
        x = 10
        if not self.icon().isNull():
            mode = QIcon.Normal if self.isEnabled() else QIcon.Disabled
            self.icon().paint(painter, bounds, Qt.AlignCenter, mode)
            x = bounds.right() + 9
        label = QRect(x, 0, max(0, self.width() - x - 8), self.height())
        painter.setFont(self.font())
        painter.setPen(QColor('#ffffff' if self.objectName() == 'navActive'
                              else '#d9d5dd' if self.isEnabled() else '#736e79'))
        visible = self.fontMetrics().elidedText(
            self.text().replace('&&', '&'), Qt.ElideRight, label.width())
        painter.drawText(label, Qt.AlignLeft | Qt.AlignVCenter, visible)
        painter.end()


class TaskSignals(QObject):
    completed = Signal(str, int)
    output = Signal(str)
    update_checked = Signal(object, str, bool)
    update_staged = Signal(object, object, str)
    progress = Signal(str, int, int)


class QtManager(QMainWindow):
    """Native Qt interface; no Tk window or Tcl event loop is created here."""

    def __init__(self):
        super().__init__()
        self.setObjectName('qtManager')
        self.setWindowTitle('Evolve Mod Manager')
        icon = ROOT / 'assets' / 'hunt.ico'
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1150, 785)
        self.setMinimumSize(880, 620)
        self.setStyleSheet(qt_style())
        self.copy, self.ui_warning = load_text()
        self.bundle_error = ''
        self.bundle = None
        try:
            self.bundle = bind_home(APP_HOME)
        except Exception as exc:
            self.bundle_error = str(exc)

        self.settings = {}
        path = settings_source()
        if path.is_file():
            try:
                self.settings = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError):
                self.settings = {}
        self.stage_path = self.settings.get('stage', str(DATA_HOME/'Setup'/'staged'))
        self.swap_path = self.settings.get('swap', str(DATA_HOME/'Setup'/'swap'))
        self.projects_path = self.settings.get('projects', str(DATA_HOME/'Projects'))
        self.current_workspace = self.settings.get('current_workspace', '')
        self.output_pak = self.settings.get('output_pak', '')
        self.batch_root = self.settings.get('batch_root', '')
        self.archive_label = self.settings.get('archive_label', '')
        self.ui_density = (self.settings.get('ui_density', 'Comfortable')
                           if self.settings.get('ui_density') in ('Comfortable', 'Compact')
                           else 'Comfortable')
        self.reduce_motion = bool(self.settings.get('reduce_motion', False))
        self.keep_original_snapshots = bool(self.settings.get('keep_original_snapshots', True))
        self.show_activity_on_tasks = bool(self.settings.get('show_activity_on_tasks', True))
        self.appearance_mode = self.settings.get('appearance_mode', 'Dark')
        if self.appearance_mode not in PALETTES:
            self.appearance_mode = 'Dark'
        self.appearance_overrides = self.settings.get('appearance_colors', {})
        candidates=self.settings.get('theme_presets',{})
        self.saved_theme_presets={}
        if isinstance(candidates,dict):
            for name,value in list(candidates.items())[:20]:
                try:
                    preset=validate_theme_preset(value)
                    self.saved_theme_presets[preset['name']]=preset
                except (ValueError,TypeError):
                    pass
        try:
            palette(self.appearance_mode, self.appearance_overrides)
        except (ValueError, AttributeError):
            self.appearance_overrides = {}
        self._developer_allow_online = False
        self._developer_allow_writes = False
        self._developer_online_write_ack = False
        self._dev_trace_env = {}
        self.external_pak = ''
        self.game_root = None
        self.archive_entries = []
        self.batch_workspaces = {}
        self.busy = False
        self.task_queue = []
        self._queued_task_running = None
        self.process = None
        self.editors = []
        self.library_previews = []
        self.level_editors = []
        self.available_update = None
        self.update_running = False
        self.signals = TaskSignals()
        self.signals.completed.connect(self._task_finished)
        self.signals.output.connect(self._log)
        self.signals.update_checked.connect(self._on_update_checked)
        self.signals.update_staged.connect(self._on_update_staged)
        self.signals.progress.connect(self._update_progress)
        self.update_progress_dialog = None
        self._success = None
        self._drawer_open = False
        # The Instructions page is the first window. Delay disk-heavy
        # archive/project inventory until the corresponding page is used.
        # This makes first paint independent of enormous Asset Libraries.
        self._startup_inventory_pending = True
        self._library_inventory_initialized = False

        base = QWidget()
        self.setCentralWidget(base)
        body = QVBoxLayout(base)
        body.setContentsMargins(16, 12, 16, 10)
        body.setSpacing(10)

        top = QHBoxLayout()
        self.menu_button = _button('☰  Menu', self.toggle_menu)
        self.menu_button.setObjectName('menuTrigger')
        self.menu_button.setToolTip('Show navigation (Esc to close)')
        top.addWidget(self.menu_button)
        app_icon = QLabel()
        png = ROOT / 'assets' / 'hunt.png'
        if png.is_file():
            app_icon.setPixmap(QPixmap(str(png)).scaled(
                33, 33, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        top.addWidget(app_icon)
        brand = _label('EVOLVE / MOD MANAGER')
        brand.setObjectName('brand')
        top.addWidget(brand)
        self.page_location = _label('  /  Instructions', muted=True, wrap=False)
        self.page_location.setObjectName('currentPageContext')
        top.addWidget(self.page_location)
        top.addStretch()
        top.addWidget(_button('Help', self.show_help))
        top.addWidget(_button('Activity', self.toggle_activity))
        body.addLayout(top)

        self.stage_holder = QWidget()
        self.stage_holder.setObjectName('stageHolder')
        self.stage_holder.setMinimumHeight(250)
        self.stage_holder.setLayout(QVBoxLayout())
        self.stage_holder.layout().setContentsMargins(0, 0, 0, 0)
        self.stack = QStackedWidget()
        self.stack.setObjectName('pages')
        self.stage_holder.layout().addWidget(self.stack)
        body.addWidget(self.stage_holder, stretch=1)

        self.drawer = _widget(QFrame, self.stage_holder, name='drawer')
        self.drawer.setFixedWidth(194)
        navlay = QVBoxLayout(self.drawer)
        navlay.setContentsMargins(10, 15, 10, 15)
        navlay.setSpacing(6)
        navlay.addWidget(_label('WORKSPACE', muted=True))
        self.nav_buttons = []
        for index, title in enumerate(SECTIONS):
            # Qt interprets & as a keyboard-mnemonic marker in QPushButton.
            # Double it to render a literal "Play & Restore", not "Play _Restore".
            btn = NavigationButton(title.replace('&', '&&'))
            btn.clicked.connect(lambda _checked=False, idx=index: self.open_page(idx))
            btn.setAccessibleName(title)
            btn.setToolTip(title)
            btn.setIcon(navigation_icon(
                title, palette(self.appearance_mode, self.appearance_overrides)['text'], 32))
            btn.setIconSize(QSize(30, 30))
            # The drawer has 194px width and 10px side margins. Set the
            # initial widget geometry explicitly: hidden drawer children may
            # still have Qt's default 100x30 size before first layout on
            # Windows/offscreen, which clips the 30px icon to 18px.
            btn.setFixedSize(174, 48)
            btn.setFocusPolicy(Qt.StrongFocus)
            btn.setObjectName('navActive' if index == 0 else 'nav')
            navlay.addWidget(btn)
            self.nav_buttons.append(btn)
        navlay.addStretch()
        self.drawer.move(-194, 0)
        self.drawer.hide()
        self.menu_animation = QPropertyAnimation(self.drawer, b'geometry', self)
        self.menu_animation.setDuration(205)
        self.menu_animation.setEasingCurve(QEasingCurve.OutCubic)
        self.menu_animation.finished.connect(self._after_drawer_animation)

        self.pages = {}
        self._instructions_page()
        # Library folder migration runs at the first actual Library refresh,
        # before any paths are exposed to the preview/editor.
        self._asset_library_page()
        self._modding_page()
        self._play_page()
        from qt_asset_library import AssetLibraryController
        self.asset_library_controller = AssetLibraryController(self)
        self.asset_library_controller.finished.connect(self._asset_library_finished)
        self.asset_library_controller.started.connect(self._library_task_started)
        self.asset_library_controller.progress.connect(self._library_task_progress)
        self._auto_library_scanned_game = None
        self._settings_page()
        self._credits_page()
        self._whats_new_page()
        self._last_public_page = 0
        self._developer_index = SECTIONS.index('Developer')
        self.pages['developer'] = QWidget()
        self.stack.addWidget(self.pages['developer'])
        self.stack.setCurrentIndex(0)
        self._log_dialog = None
        self.log_lines = ['Manager started. No installed game files were changed.']
        self.status = _label('Ready — read Instructions before connecting an installed game.', muted=True)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        self.operation_progress = QProgressBar(self)
        self.operation_progress.setObjectName('fileOperationProgress')
        self.operation_progress.setRange(0, 100)
        self.operation_progress.setValue(0)
        self.operation_progress.setFormat('%p%')
        self.operation_progress.setFixedWidth(190)
        self.operation_progress.setToolTip('Progress of the current file operation')
        self.operation_progress.hide()
        footer.addWidget(self.operation_progress)
        self.shared_asset_label = _label('Asset Library: ready to import', muted=True)
        self.shared_asset_label.setToolTip('Extracted files from verified shared, animation and user-added PAKs.')
        footer.addWidget(self.shared_asset_label)
        self.version_label = _label(footer_version())
        self.version_label.setObjectName('version')
        footer.addWidget(self.version_label)
        body.addLayout(footer)
        # Beta.34 workspaces can yield review warnings during migration.
        # Previously this ran above, before self.status/self.log_lines existed,
        # causing Qt startup to abort on existing installations.
        self._apply_appearance()
        self._load_batch()
        # Archive and project validation may traverse large staged/game PAK
        # collections. Modding loads them on first use, not before first paint.
        # Restore status is cheap and remains visible on launch.
        self.refresh_swap_status()
        # Verify the six Shared asset directories off the GUI thread.
        QTimer.singleShot(500, self._warm_shared_assets)
        QTimer.singleShot(1300, self._warm_asset_library)
        self._shared_catalog_last_status = ''
        self._shared_catalog_timer = QTimer(self)
        self._shared_catalog_timer.setInterval(1000)
        self._shared_catalog_timer.timeout.connect(self._shared_catalog_tick)
        self._shared_catalog_timer.start()
        QTimer.singleShot(0, self._position_drawer)
        QTimer.singleShot(400, lambda: report_startup_ready(APP_HOME))
        # Both release channels are verified; beta updates always need approval.
        if (self.build_channel() in ('main','beta') and
                not any(flag in sys.argv for flag in
                        ('--capture-screenshot','--capture-modding-screenshot',
                         '--capture-settings-screenshot','--capture-library-screenshot',
                         '--capture-play-screenshot'))):
            QTimer.singleShot(2500, lambda: self.check_updates(silent=True))
        if self.bundle_error:
            QTimer.singleShot(500, lambda: self.failure(self.bundle_error))

    @staticmethod
    def build_channel():
        try:
            return (APP_HOME/'BUILD_CHANNEL.txt').read_text(encoding='ascii').strip().lower()
        except OSError:
            return ''

    def setup_is_ready(self):
        if self.bundle and not self.bundle.get('configured', True):
            return False
        return (Path(self.stage_path)/'stage_status.json').is_file()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._position_drawer()

    def _position_drawer(self):
        height = self.stage_holder.height()
        if height <= 0:
            return
        x = self.drawer.x()
        width = self.drawer.width()
        if self.menu_animation.state() != QPropertyAnimation.Running:
            x = 0 if self._drawer_open else -width
        self.drawer.setGeometry(x, 0, width, height)
        if self._drawer_open:
            self.drawer.raise_()

    def toggle_menu(self, show=None):
        open_it = not self._drawer_open if show is None else bool(show)
        if open_it == self._drawer_open:
            return
        self._position_drawer()
        self._drawer_open = open_it
        self.drawer.show()
        # Lay out children immediately, even if the geometry animation has
        # only just started; icon painting must never use stale initial sizes.
        self.drawer.layout().activate()
        self.drawer.raise_()
        self.menu_button.setText('✕  Close' if open_it else '☰  Menu')
        self.menu_animation.stop()
        left = self.drawer.geometry()
        right = QRect(0 if open_it else -self.drawer.width(), 0,
                      self.drawer.width(), self.stage_holder.height())
        self.menu_animation.setStartValue(left)
        self.menu_animation.setEndValue(right)
        self.menu_animation.start()

    def _after_drawer_animation(self):
        if not self._drawer_open:
            self.drawer.hide()
            return
        # A child of an initially hidden animated drawer can retain its
        # default 30px geometry on Windows even with a fixed 48px size hint.
        # Re-layout after the final animation geometry has been applied and
        # explicitly resize each visible button. setFixedSize() alone does
        # not correct stale native child geometry in this situation.
        layout = self.drawer.layout()
        layout.invalidate()
        layout.activate()
        for button in self.nav_buttons:
            button.resize(174, 48)
        self.drawer.update()

    def open_page(self, index):
        if not 0 <= index < self.stack.count():
            return
        # Destroy protected content before prompting again or leaving the page.
        # No remembered session or settings flag can skip this prompt.
        if self.stack.currentIndex() == self._developer_index:
            self.stack.setCurrentIndex(self._last_public_page)
            if hasattr(self, 'dev_memory_panel'):
                self.dev_memory_panel.stop()
            if hasattr(self, 'dev_lab_panel'):
                self.dev_lab_panel.stop()
            # Leaving the gated page resets all memory-write consent.
            self._developer_allow_writes = False
            self._developer_online_write_ack = False
            old = self.pages['developer']
            self.stack.removeWidget(old)
            old.hide()
            old.setParent(None)
            old.deleteLater()
            self.pages['developer'] = QWidget()
            self.stack.insertWidget(self._developer_index, self.pages['developer'])
            self._highlight_page(self._last_public_page)
        if index == self._developer_index:
            self.toggle_menu(False)
            code, accepted = QInputDialog.getText(
                self, 'Developer', 'Enter access code:', QLineEdit.Password)
            if not accepted:
                return
            valid = verify_code(code)
            del code
            if not valid:
                QMessageBox.warning(self, 'Access denied', 'Incorrect access code.')
                return
            self._developer_page()
        else:
            self._last_public_page = index
        self.stack.setCurrentIndex(index)
        if SECTIONS[index] == 'Asset Library':
            self.refresh_library_table()
        elif SECTIONS[index] == 'Modding' and self._startup_inventory_pending:
            self.refresh_project_list()
            self.refresh_archives()
            self._startup_inventory_pending = False
        self._highlight_page(index)
        self.page_location.setText('  /  ' + SECTIONS[index])
        self.toggle_menu(False)

    def _highlight_page(self, index):
        for i, button in enumerate(self.nav_buttons):
            button.setObjectName('navActive' if i == index else 'nav')
            button.style().unpolish(button)
            button.style().polish(button)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._drawer_open:
            self.toggle_menu(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def t(self, key):
        return self.copy.get(key, key.replace('_',' ').capitalize())

    def _instructions_page(self):
        scroll, lay = _scroll_page()
        heading = _label(self.t('instructions_title'))
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        lay.addWidget(_label(self.t('instructions_intro'), muted=True))
        card, panel = _card(self.t('instructions_required_title'),
                            self.t('instructions_required_body'))
        lay.addWidget(card)
        row = QWidget()
        row.setLayout(_row(
            _button(self.t('instructions_website_button'),
                    lambda: webbrowser.open('https://modded-evolve.com/'),
                    primary=True),
            _button(self.t('setup_button'), self.initial_setup)))
        panel.addWidget(row)
        workflow = _label(self.t('instructions_steps_title'),muted=True)
        lay.addWidget(workflow)
        for title_key, body_key in (
            ('instructions_step_setup','instructions_step_setup_body'),
            ('instructions_step_mod','instructions_step_mod_body'),
            ('instructions_step_play','instructions_step_play_body'),
            ('instructions_step_restore','instructions_step_restore_body'),
        ):
            step, _ = _card(self.t(title_key), self.t(body_key))
            lay.addWidget(step)
        lay.addWidget(_label(self.t('instructions_safety'),muted=True))
        lay.addWidget(_button(self.t('instructions_modding_button'),
                              lambda: self.open_page(SECTIONS.index('Modding'))))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['instructions'] = scroll

    def _asset_library_page(self):
        """Modding-style table for fully extracted, reference-only PAKs."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        heading = _label('Asset Library')
        heading.setObjectName('sectionTitle')
        layout.addWidget(heading)
        layout.addWidget(_label(
            'Preview and export PAKs you have already extracted. The Asset Library '
            'is read-only: editing, building and signing are performed in Modding.',
            muted=True))
        bar = QMenuBar(page)
        bar.setNativeMenuBar(False)
        file_menu = bar.addMenu('File')
        file_menu.addAction('Import PAKs / Manage Library…', self.open_asset_library)
        file_menu.addAction('Edit Legacy Evolve (2012) PAK…', self.open_legacy_pak)
        file_menu.addAction('Open Legacy PAK Project…', self.open_legacy_project)
        file_menu.addAction('Preview Selected PAK…', self.preview_library_archive)
        file_menu.addAction('Edit / Add to Mod Project…', self.edit_selected_library_archive)
        file_menu.addAction('Open Map in Level Editor…', self.edit_selected_library_map)
        file_menu.addAction('Export Selected PAK Layout…', self.export_selected_library_archive)
        file_menu.addAction('Open Asset Library Folder', self.open_library_folder)
        view_menu = bar.addMenu('View')
        view_menu.addAction('Refresh Imported PAK List', self.refresh_library_table)
        tools_menu = bar.addMenu('Tools')
        tools_menu.addAction('Verify / Remove Imported PAKs…', self.open_asset_library)
        layout.addWidget(bar)
        self.asset_toolbar = ContextActions(page)
        self.asset_toolbar.add_action('Import PAKs', self.open_asset_library, primary=True)
        self.asset_toolbar.add_action('Refresh', self.refresh_library_table)
        self.asset_toolbar.add_action('Open Library Folder', self.open_library_folder, overflow=True)
        self.asset_toolbar.add_action('Verify Library', self.open_asset_library, overflow=True)
        layout.addWidget(self.asset_toolbar)
        # A visible build view is inventory-only; previews carry an explicit build ID.
        self.asset_view_tabs = QTabWidget(page)
        self.asset_view_tabs.setObjectName('assetCollectionViews')
        all_tab = QWidget()
        all_tab.setLayout(_row(_label('All imported archives across snapshots',
                                     muted=True)))
        build_tab = QWidget()
        build_row = QHBoxLayout(build_tab)
        build_row.setContentsMargins(8, 4, 8, 4)
        build_row.addWidget(_label('Build snapshot:'))
        self.asset_build_filter = QComboBox()
        self.asset_build_filter.setObjectName('assetBuildSnapshotFilter')
        self.asset_build_filter.setToolTip(
            'Source previews use only dependencies in this build snapshot.')
        self.asset_build_filter.currentIndexChanged.connect(self.refresh_library_table)
        build_row.addWidget(self.asset_build_filter, 1)
        self.asset_view_tabs.addTab(all_tab, 'All Assets')
        self.asset_view_tabs.addTab(build_tab, 'By Build')
        self.asset_view_tabs.currentChanged.connect(self.refresh_library_table)
        layout.addWidget(self.asset_view_tabs)
        controls = QHBoxLayout()
        self.asset_search = QLineEdit()
        self.asset_search.setPlaceholderText('Search imported PAKs, names, folders…')
        self.asset_search.setClearButtonEnabled(True)
        self.asset_search.textChanged.connect(self.refresh_library_table)
        controls.addWidget(self.asset_search, 1)
        self.asset_filter = QComboBox()
        self.asset_filter.addItems(('All imported PAKs', 'Characters / Shared',
                                   'Animations', 'Other PAKs'))
        self.asset_filter.currentIndexChanged.connect(self.refresh_library_table)
        controls.addWidget(self.asset_filter)
        controls.addWidget(_button('Refresh', self.refresh_library_table))
        layout.addLayout(controls)
        self.asset_table = QTableWidget(0, 6)
        self.asset_table.setObjectName('importedLibraryPakTable')
        self.asset_table.setHorizontalHeaderLabels(
            ('Dependency Group', 'Folder', 'Description', 'File', 'Extracted Files', 'Size'))
        self.asset_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.asset_table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.asset_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.asset_table.setAlternatingRowColors(True)
        self.asset_table.setSortingEnabled(True)
        self.asset_table.verticalHeader().hide()
        for index in (0, 1):
            self.asset_table.horizontalHeader().setSectionResizeMode(
                index, QHeaderView.Interactive)
        self.asset_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.asset_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        for index in (4, 5):
            self.asset_table.horizontalHeader().setSectionResizeMode(
                index, QHeaderView.ResizeToContents)
        self.asset_table.setColumnWidth(0, 215)
        self.asset_table.setColumnWidth(1, 150)
        self.asset_table.itemDoubleClicked.connect(
            lambda _item: self.preview_library_archive())
        self.asset_table.itemSelectionChanged.connect(self._sync_library_quick_actions)
        layout.addWidget(self.asset_table, 1)
        self.asset_count = _label('No imported PAKs', muted=True)
        layout.addWidget(self.asset_count)
        # Keep actions discoverable in File/Tools, but only show quick actions
        # for a selected archive. The old six-button row clipped at 880 px.
        actions = QHBoxLayout()
        actions.setSpacing(8)
        self.asset_quick = {}
        for title, callback, primary in (
            ('Import / Manage PAKs…', self.open_asset_library, False),
            ('Preview Files', self.preview_library_archive, False),
            ('Edit / Add to Mod Project', self.edit_selected_library_archive, True),
            ('Open Map', self.edit_selected_library_map, False),
            ('Export PAK Layout…', self.export_selected_library_archive, False),
            ('Open Asset Library Folder', self.open_library_folder, False),
        ):
            button = _button(title, callback, primary=primary)
            button.setToolTip(title + ' (also available from the menu bar)')
            self.asset_quick[title] = button
            if title in ('Preview Files', 'Edit / Add to Mod Project', 'Open Map'):
                actions.addWidget(button)
            else:
                # Preserve action objects for migration compatibility, but the
                # corresponding commands live under File rather than a huge row.
                button.setParent(page)
                button.hide()
        actions.addStretch(1)
        layout.addLayout(actions)
        self._sync_library_quick_actions()
        self.stack.addWidget(page)
        self.pages['asset_library'] = page

    def open_library_folder(self):
        """Open readable archive directories in Windows Explorer."""
        from asset_library import LIBRARY_HOME
        folder = Path(LIBRARY_HOME).resolve()
        if not folder.is_dir():
            self.info('Asset Library',
                      'The library folder will be created when the first PAK is imported.')
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder))):
            self.failure('Windows Explorer could not open the Asset Library folder.')

    def refresh_library_table(self, *_unused):
        if not hasattr(self, 'asset_table'):
            return
        if not self._library_inventory_initialized:
            # One-time folder migration must finish before handing out paths.
            # When the user never opens the library, no manifest crawl delays
            # the initial Instructions screen.
            from asset_library import migrate_readable_archive_folders
            migrate_readable_archive_folders()
            self._library_inventory_initialized = True
        from asset_library import imported_archives
        from pak_dependencies import group_title
        selected_ids = self._selected_library_ids()
        focused_id = self._selected_library_id()
        archives = imported_archives()
        from build_collections import catalog
        builds = catalog()['builds']
        current_build = self.asset_build_filter.currentData()
        self.asset_build_filter.blockSignals(True)
        self.asset_build_filter.clear()
        for bid, entry in builds.items():
            self.asset_build_filter.addItem(
                entry['label'] + ' (' + entry['family'] + ')', bid)
        selected_index = self.asset_build_filter.findData(current_build)
        self.asset_build_filter.setCurrentIndex(max(0, selected_index))
        self.asset_build_filter.blockSignals(False)
        viewing_build = self.asset_view_tabs.currentIndex() == 1
        filtered_build = self.asset_build_filter.currentData() if viewing_build else None
        self._library_rows = {entry['id']: entry for entry in archives}
        text = self.asset_search.text().strip().casefold()
        mode = self.asset_filter.currentIndex()
        visible = []
        for entry in archives:
            if filtered_build and entry['id'] not in builds[filtered_build]['members']:
                continue
            name = entry['name']
            lowered = name.casefold()
            is_character = lowered.startswith('characters_')
            is_animation = lowered.startswith('animations')
            category = 1 if is_character else 2 if is_animation else 3
            if mode and mode != category:
                continue
            if text and text not in (name + ' ' + entry['source'] +
                                    ' ' + group_title(name)).casefold():
                continue
            visible.append(entry)
        table = self.asset_table
        table.setSortingEnabled(False)
        table.setRowCount(0)
        for entry in visible:
            name = entry['name']
            from library_map_actions import library_map_identity
            map_id = library_map_identity(entry)
            group = ('Level • ' + map_id.replace('_', ' ').title()
                     if map_id else group_title(name))
            folder = str(Path(entry['source']).parent)
            if len(folder) > 75:
                folder = '…' + folder[-72:]
            from character_aliases import character_name_for_internal
            from pak_dependencies import group_identity
            family = group_identity(name)
            friendly = (character_name_for_internal(family[2])
                        if family[0] == 'character' else None)
            description = archive_description(name)
            if friendly:
                description = friendly + ' (internal: ' + family[2] + ') • ' + description
            values = (group, folder,
                      description, name,
                      str(entry['entries']),
                      format_size(entry['bytes']))
            row = table.rowCount()
            table.insertRow(row)
            for col, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(entry['source'] + '\nImported reference files (read-only)')
                cell.setFlags(cell.flags() & ~Qt.ItemIsEditable)
                if col == 0:
                    cell.setData(Qt.UserRole, entry['id'])
                table.setItem(row, col, cell)
        table.setSortingEnabled(True)
        table.sortItems(0, Qt.AscendingOrder)
        if selected_ids:
            # Refreshes can occur while shared PAK imports finish. Preserve
            # visible multi-selection by archive ID even after sorting or
            # filtering; selectRow() would collapse Shift/Ctrl selections.
            model = table.selectionModel()
            for row in range(table.rowCount()):
                cell = table.item(row, 0)
                if cell and cell.data(Qt.UserRole) in selected_ids:
                    index = table.model().index(row, 0)
                    model.select(index, QItemSelectionModel.Select |
                                        QItemSelectionModel.Rows)
                    if cell.data(Qt.UserRole) == focused_id:
                        model.setCurrentIndex(index, QItemSelectionModel.NoUpdate)
        self.asset_count.setText(
            '%d of %d imported PAKs • %.1f GiB extracted • read-only previews' %
            (len(visible), len(archives),
             sum(a['bytes'] for a in archives) / (1024 ** 3)))
        self._sync_library_quick_actions()

    def _sync_library_quick_actions(self):
        """Only offer asset actions applicable to the selected library PAK."""
        buttons = getattr(self, 'asset_quick', {})
        if not buttons:
            return
        identifiers = self._selected_library_ids()
        records = [getattr(self, '_library_rows', {}).get(identifier)
                   for identifier in identifiers]
        has_pak = bool(records) and all(record is not None for record in records)
        from library_map_actions import library_map_identity
        # The UI must also recognize level.pak/terraintexture.pak when their
        # original source is Game/levels/Tutorial, not only levels_X.pak.
        is_map = bool(has_pak and all(library_map_identity(r) for r in records))
        for label in ('Preview Files', 'Edit / Add to Mod Project', 'Open Map'):
            button = buttons[label]
            relevant = bool(is_map if label == 'Open Map' else has_pak)
            button.setVisible(relevant)
            button.setEnabled(relevant)

    def _library_build_context(self):
        if hasattr(self, 'asset_view_tabs') and self.asset_view_tabs.currentIndex() == 1:
            return self.asset_build_filter.currentData()
        return None

    def edit_selected_library_archive(self, open_level=False):
        record = self._selected_library_record()
        if record is None:
            return
        build_id = self._library_build_context()
        if build_id in ('unassigned', 'all'):
            build_id = None
        self.create_mod_project_from_asset_library(
            record['id'], source_build_id=build_id, open_level=open_level)

    def edit_selected_library_map(self):
        identifiers = self._selected_library_ids()
        if not identifiers:
            self.info('Open Map', 'Select one or more imported map PAKs.')
            return
        from library_map_actions import validated_map_selection
        records = [getattr(self, '_library_rows', {}).get(identifier)
                   for identifier in identifiers]
        try:
            primary, _ = validated_map_selection(records)
        except ValueError as exc:
            self.info('Open Map', str(exc))
            return
        self.open_library_map_from_archive(
            primary['id'], build_id=self._library_build_context(),
            selected_ids=identifiers)

    def open_library_map_from_archive(self, identifier, *, build_id=None,
                                      selected_ids=None):
        """Extract map family as separate editable Stage 2 target workspaces."""
        try:
            if self.busy or self.asset_library_controller.busy:
                raise RuntimeError('Another operation is already running.')
            from asset_library import imported_archives
            from asset_library_bulk import stage_source_for_import
            from build_collections import catalog, resolve_archive_build
            from library_map_actions import (library_map_identity,
                                             validated_map_selection)
            records = {item['id']: item for item in imported_archives()}
            requested = tuple(dict.fromkeys(selected_ids or (identifier,)))
            if identifier not in requested:
                raise ValueError('The primary map PAK must be selected.')
            if any(archive_id not in records for archive_id in requested):
                raise ValueError('One selected map PAK is no longer imported.')
            primary, map_id = validated_map_selection(
                [records[archive_id] for archive_id in requested])
            if primary['id'] != identifier:
                raise ValueError('Selected primary map PAK changed; select it again.')
            selected = records[identifier]
            if not build_id or build_id == 'all':
                build_id = resolve_archive_build(identifier)
            builds = catalog()['builds']
            if build_id not in builds or builds[build_id]['family'] != 'stage2':
                raise ValueError(
                    'Assign the selected map archives to one registered Stage 2 '
                    'snapshot in Asset Library > By Build first. Historical '
                    'maps remain reference-only until a verified target exists.')
            if any(archive_id not in builds[build_id]['members']
                   for archive_id in requested):
                raise ValueError(
                    'Selected level PAKs must all belong to the same registered '
                    'Stage 2 build. Use By Build to select that snapshot first.')
            same_map = [
                item for item in records.values()
                if item['id'] in builds[build_id]['members']
                and library_map_identity(item) == map_id]
            if any(archive_id not in {item['id'] for item in same_map}
                   for archive_id in requested):
                raise ValueError('Selected PAKs do not form one verified map family.')
            if not 1 <= len(same_map) <= 30:
                raise ValueError('Map family exceeds the 30-PAK bounded extraction limit.')
            game = self.game_root or self._find_game_root()
            staged = {}
            for archive in same_map:
                relative, _ = stage_source_for_import(
                    archive['id'], self.stage_path, game, build_id=build_id)
                if relative in staged and staged[relative] != archive['id']:
                    raise ValueError('Conflicting archives map to one staged target.')
                staged[relative] = archive['id']
            selected_relative, _ = stage_source_for_import(
                identifier, self.stage_path, game, build_id=build_id)
            self.refresh_archives()
            if any(relative not in self.archive_entries for relative in staged):
                raise ValueError('One or more map PAKs are missing from the staged inventory.')
            self._pending_map_members = (build_id, staged)
            started = self._start_batch_with_dependencies(
                sorted(staged), selected_relative, auto_level=True)
            if not started:
                self._pending_map_members = None
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def _selected_library_ids(self):
        """Stable archive IDs from selected rows, including Shift/Ctrl ranges.

        Sorting and filtered table positions are not archive identifiers.
        Keep the single-row helpers for preview/project operations.
        """
        table = getattr(self, 'asset_table', None)
        if table is None or table.selectionModel() is None:
            return ()
        values = []
        for index in table.selectionModel().selectedRows(0):
            cell = table.item(index.row(), 0)
            if cell is not None:
                identifier = cell.data(Qt.UserRole)
                if identifier is not None and identifier not in values:
                    values.append(identifier)
        return tuple(values)

    def _selected_library_id(self):
        identifiers = self._selected_library_ids()
        if not identifiers:
            return None
        table = self.asset_table
        row = table.currentRow()
        cell = table.item(row, 0) if row >= 0 else None
        current = cell.data(Qt.UserRole) if cell is not None else None
        return current if current in identifiers else identifiers[0]

    def _selected_library_record(self):
        identifier = self._selected_library_id()
        record = getattr(self, '_library_rows', {}).get(identifier)
        if record is None:
            self.info('Asset Library', 'Select an already imported PAK first.')
        return record

    def preview_library_archive(self):
        record = self._selected_library_record()
        if record is None:
            return
        try:
            from qt_asset_preview import QtAssetPreviewWindow
            for existing in self.library_previews:
                if existing.isVisible() and existing.identifier == record['id']:
                    existing.raise_()
                    existing.activateWindow()
                    return
            window = QtAssetPreviewWindow(self, dict(
                record, build_id=self._library_build_context()))
            window.setAttribute(Qt.WA_DeleteOnClose, False)
            self.library_previews.append(window)
            window.show()
            window.raise_()
        except (OSError, ValueError) as exc:
            self.failure(exc)

    def export_selected_library_archive(self):
        record = self._selected_library_record()
        if record is not None:
            self.export_library_archive_by_id(record['id'])

    def export_library_archive_by_id(self, identifier):
        record = getattr(self, '_library_rows', {}).get(identifier)
        if record is None:
            self.refresh_library_table()
            record = getattr(self, '_library_rows', {}).get(identifier)
        if record is None:
            self.failure('The imported archive is no longer available.')
            return
        if self.asset_library_controller.busy:
            self.info('Asset Library', 'Wait until the current library task finishes.')
            return
        destination = QFileDialog.getExistingDirectory(
            self, 'Choose folder for complete CryEngine virtual-path export',
            str(Path.home()))
        if not destination:
            return
        if not self.confirm(
                'Export extracted PAK layout?',
                'Copy %d imported files (%.1f MiB) from %s into a NEW folder '
                'under:\n%s\n\nOriginal virtual paths are preserved. '
                'The Asset Library and installed game are not modified.' %
                (record['entries'], record['bytes'] / 1048576,
                 record['name'], destination)):
            return
        from asset_library_preview import export_archive_layout
        if not self.asset_library_controller._start(
                'export-layout',
                lambda: export_archive_layout(
                    identifier, destination,
                    progress=lambda label, done, total:
                        self.asset_library_controller.progress.emit(
                            label, done, total))):
            self.info('Asset Library', 'Another library task is still running.')

    def create_library_project_from_tab(self):
        record = self._selected_library_record()
        if record is not None:
            self.create_mod_project_from_asset_library(record['id'])

    def _modding_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        title = _label('Modding')
        title.setObjectName('sectionTitle')
        root.addWidget(title)
        root.addWidget(_label('Browse signed PAKs and project copies. Readable names are inferred from filenames; signed paths are unchanged.', muted=True))
        menu_bar = QMenuBar(page)
        menu_bar.setObjectName('moddingMenuBar')
        self.modding_menu = menu_bar
        self._modding_menus = {}  # Keep Python wrappers for native Qt menu lifetime.
        menu_bar.setNativeMenuBar(False)
        def menu_commands(label, commands):
            menu = menu_bar.addMenu(label)
            self._modding_menus[label] = menu
            for title, callback in commands:
                if title is None:
                    menu.addSeparator()
                else:
                    act = menu.addAction(title)
                    act.triggered.connect(lambda _checked=False, cb=callback: cb())
        menu_commands('File', (
            ('Unpack PAK + known dependencies', self.extract),
            ('Unpack selected PAKs + dependencies', self.extract_batch),
            ('Import External PAK…', self.browse_external_pak),
            ('Edit Legacy Evolve (2012) PAK…', self.open_legacy_pak),
            ('Open Legacy PAK Project…', self.open_legacy_project),
            (None, None),
            ('Projects…', self.open_project_dashboard),
            ('Refresh Project List', self.refresh_project_list),
            ('Open Unpacked Files', self.open_unpacked),
        ))
        menu_commands('Edit', (
            ('Edit Files', self.open_editor),
            ('Level Editor 1.0', self.open_level_editor),
            ('Review Changes', self.diff),
            (None, None),
            ('Build Mod', self.build),
            ('Add Mod', self.install),
            ('Import Modified PAK', self.import_modified_pak),
            ('Undo Staged Mod', self.rollback),
        ))
        menu_commands('View', (
            ('Refresh PAK list', self.refresh_archives),
            ('PAK / dependencies information', self.inspect_pak),
            ('Show staged PAK location', lambda: self.reveal_pak(original=False)),
            ('Show original PAK location', lambda: self.reveal_pak(original=True)),
        ))
        menu_commands('Tools', (
            ('Mod Profiles…', self.open_mod_profiles),
            ('Asset Library / Add PAKs…', self.open_asset_library),
            ('Export Model Folder…', self.export_model_folder),
            ('PAK Diff…', self.open_pak_diff),
            ('Task Manager…', self.open_task_manager),
            ('Offline Test…', self.build_offline_test),
        ))
        menu_commands('Help', (('Modding Help', lambda: self.show_help(0)),))
        root.addWidget(menu_bar)
        root.addWidget(_label(
            'Projects contain your editable copies. The Asset Library keeps the original PAKs.',
            muted=True))
        self.project_table = QTableWidget(0, 6)
        self.project_table.setObjectName('moddingProjectTable')
        self.project_table.setHorizontalHeaderLabels(
            ('Project', 'Target PAK', 'Changed', 'Source Build', 'State', 'Details'))
        self.project_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.project_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.project_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.project_table.setAlternatingRowColors(True)
        self.project_table.setSortingEnabled(True)
        self.project_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.project_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.project_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.Stretch)
        self.project_table.itemDoubleClicked.connect(lambda _: self.open_active_project())
        self.project_table.itemSelectionChanged.connect(self.refresh_project_changes)
        self.project_split = configure_splitter(QSplitter(Qt.Vertical))
        self.project_split.setObjectName('moddingProjectSplitter')
        self.project_split.addWidget(self.project_table)
        changes_panel = QWidget()
        changes_box = QVBoxLayout(changes_panel)
        changes_box.setContentsMargins(0, 0, 0, 0)
        changes_box.setSpacing(5)
        changes_box.addWidget(_label('MODIFIED FILES IN SELECTED PROJECT', muted=True))
        self.project_changes_table = QTableWidget(0, 2)
        self.project_changes_table.setObjectName('projectChangedFilesTable')
        self.project_changes_table.setHorizontalHeaderLabels(('Modified Asset', 'Project Workspace'))
        self.project_changes_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.project_changes_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.project_changes_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.project_changes_table.setSortingEnabled(True)
        self.project_changes_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.project_changes_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.project_changes_table.itemDoubleClicked.connect(
            lambda _: self.open_modified_project_file())
        changes_box.addWidget(self.project_changes_table, 1)
        self.project_split.addWidget(changes_panel)
        self.project_split.setStretchFactor(0, 3)
        self.project_split.setStretchFactor(1, 2)
        self.project_split.setSizes([360, 190])
        root.addWidget(self.project_split, 1)
        # Only the selected project's frequent actions belong on the page.
        # Refresh/review/library remain in File/Edit/Tools and sidebar.
        project_actions = QHBoxLayout()
        self.project_quick = {}
        for title, handler in (
            ('Refresh Projects', self.refresh_project_list),
            ('Continue Editing', self.open_active_project),
            ('Review Changes', self.diff),
            ('Build Mod', self.build),
            ('Asset Library', lambda: self.open_page(SECTIONS.index('Asset Library'))),
        ):
            button = _button(title, handler)
            self.project_quick[title] = button
            if title in ('Continue Editing', 'Build Mod'):
                project_actions.addWidget(button)
            else:
                button.setParent(page)
                button.hide()
        project_actions.addStretch(1)
        root.addLayout(project_actions)
        self._sync_project_quick_actions()
        self.legacy_archive_toggle = _button(
            'Show Advanced Archive Tools', self.toggle_advanced_archive_tools)
        self.legacy_archive_toggle.setIcon(navigation_icon('layers', size=16))
        self.legacy_archive_toggle.setToolTip('Expand signed PAK browsing and specialist operations')
        root.addWidget(self.legacy_archive_toggle)
        self.legacy_archive_panel = QWidget()
        archive_box = QVBoxLayout(self.legacy_archive_panel)
        archive_box.setContentsMargins(0, 0, 0, 0)
        archive_box.setSpacing(8)
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search PAKs, map names, dependency groups…')
        self.search.textChanged.connect(self.refresh_table)
        actions.addWidget(self.search, 1)
        self.file_filter = QComboBox()
        self.file_filter.addItems(('All files', 'PAK archives', 'Game files'))
        self.file_filter.currentTextChanged.connect(self.refresh_table)
        actions.addWidget(self.file_filter)
        actions.addWidget(_button('Refresh', self.refresh_archives))
        archive_box.addLayout(actions)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(
            ('Dependency Group', 'Folder', 'Description', 'File', 'Type / Project', 'Size'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 215)
        self.table.setColumnWidth(1, 160)
        self.table.itemSelectionChanged.connect(self._select_archive)
        self.table.itemDoubleClicked.connect(lambda _: self.open_editor_or_unpack())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.archive_context_menu)
        archive_box.addWidget(self.table, 1)
        self.archive_count = _label('No PAKs loaded', muted=True)
        archive_box.addWidget(self.archive_count)
        # Keep just four high-frequency actions visible; all specialist
        # commands are available through the menu bar and context menu.
        quick = QHBoxLayout()
        for title, fn, main in (
            ('Unpack + Dependencies', self.extract, False),
            ('Edit Files', self.open_editor, True),
            ('Level Editor 1.0', self.open_level_editor, False),
            ('Build Mod', self.build, True),
        ):
            button = _button(title, fn, primary=main)
            button.setToolTip(ACTION_HINTS.get(title,
                'Unpack selected PAK and known companion archives into separate projects.'))
            quick.addWidget(button, 1)
        archive_box.addLayout(quick)
        root.addWidget(self.legacy_archive_panel)
        self.legacy_archive_panel.hide()
        self.stack.addWidget(page)
        self.pages['modding'] = page

    def toggle_advanced_archive_tools(self):
        expanded = not self.legacy_archive_panel.isVisible()
        self.legacy_archive_panel.setVisible(expanded)
        self.legacy_archive_toggle.setText(
            'Hide Advanced Archive Tools' if expanded else 'Show Advanced Archive Tools')

    def refresh_project_list(self):
        if not hasattr(self, 'project_table'):
            return
        from project_catalog import project_rows
        try:
            rows = project_rows(self.projects_path, self.stage_path)
        except (OSError, ValueError, KeyError) as exc:
            self._log('Project list: ' + str(exc)[:240])
            rows = ()
        current = self.current_workspace
        self._project_rows = {record['workspace']: record for record in rows}
        table = self.project_table
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for i, item in enumerate(rows):
            values = (item.get('label', ''), item.get('target_archive', 'unknown'),
                      str(item['changed']), item.get('source_build_id') or 'Unassigned',
                      item['status'], item.get('details') or 'Ready')
            for j, value in enumerate(values):
                cell = QTableWidgetItem(value)
                if j == 0:
                    cell.setData(Qt.UserRole, item['workspace'])
                table.setItem(i, j, cell)
        table.setSortingEnabled(True)
        for i in range(table.rowCount()):
            if table.item(i, 0).data(Qt.UserRole) == current:
                table.selectRow(i)
                break
        self.refresh_project_changes()

    def _sync_project_quick_actions(self):
        buttons = getattr(self, 'project_quick', {})
        if not buttons:
            return
        record = self.selected_project_context()
        ready = bool(record and record['status'] not in ('missing', 'unavailable'))
        for title in ('Continue Editing', 'Build Mod'):
            buttons[title].setVisible(ready)
            buttons[title].setEnabled(ready)

    def selected_project_context(self):
        if not hasattr(self, 'project_table'):
            return None
        item = self.project_table.item(self.project_table.currentRow(), 0)
        if item is None:
            return None
        return getattr(self, '_project_rows', {}).get(item.data(Qt.UserRole))

    def open_active_project(self):
        record = self.selected_project_context()
        if record is None:
            self.info('Mod project', 'Select an editable project first.')
            return
        try:
            if record['status'] in ('unavailable', 'missing'):
                raise ValueError(record['details'])
            workspace = Path(record['workspace']).resolve(strict=True)
            from project_catalog import verified_target
            relative, _ = verified_target(workspace, self.stage_path)
            self.current_workspace = str(workspace)
            self.archive_label = relative
            self.workspace_input.setText(self.current_workspace)
            self.output_pak = ''
            self.output_input.setText('')
            self.persist()
            from qt_workspace_editor import QtWorkspaceEditor
            for existing in self.editors:
                if existing.isVisible() and getattr(existing, 'workspace', None) == workspace:
                    existing.raise_()
                    existing.activateWindow()
                    return
            editor = QtWorkspaceEditor(self, workspace)
            editor.setAttribute(Qt.WA_DeleteOnClose, False)
            self.editors.append(editor)
            editor.show()
            editor.raise_()
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def refresh_project_changes(self):
        table = getattr(self, 'project_changes_table', None)
        if table is None:
            return
        table.setSortingEnabled(False)
        table.setRowCount(0)
        record = self.selected_project_context()
        if record and record['status'] not in ('missing', 'unavailable'):
            try:
                from asset_intelligence import validate_project
                modified, problems = validate_project(Path(record['workspace']))
                for virtual in modified:
                    row = table.rowCount()
                    table.insertRow(row)
                    for col, value in enumerate((virtual, record['workspace'])):
                        cell = QTableWidgetItem(value)
                        if col == 0:
                            cell.setData(Qt.UserRole, (record['workspace'], virtual))
                        table.setItem(row, col, cell)
                if problems:
                    self.status.setToolTip('Project warnings: ' + '; '.join(problems[:5]))
            except (OSError, ValueError, KeyError) as exc:
                self._log('Project changes: ' + str(exc)[:200])
        table.setSortingEnabled(True)
        self._sync_project_quick_actions()

    def open_modified_project_file(self):
        current = self.project_changes_table.item(
            self.project_changes_table.currentRow(), 0)
        if current is None:
            return
        workspace, virtual = current.data(Qt.UserRole)
        try:
            from project_catalog import verified_target
            relative, _ = verified_target(workspace, self.stage_path)
            self.current_workspace = str(Path(workspace).resolve(strict=True))
            self.archive_label = relative
            self.workspace_input.setText(self.current_workspace)
            self.persist()
            self._pending_library_virtual = virtual
            self._open_project_workspace(workspace)
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def _play_page(self):
        scroll, lay = _scroll_page()
        heading = _label('Play & Restore')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        lay.addWidget(_label('Offline mod testing. The live game files must be restored before normal multiplayer.', muted=True))
        card, panel = _card('MOD LAUNCH STATUS', 'Launcher and restoration status are read from the real swap journal and original backups.')
        lay.addWidget(card)
        self.launch_status = _label('Checking current game state…')
        status_line = QHBoxLayout()
        self.launch_status_row = status_line
        status_line.setContentsMargins(0, 0, 0, 0)
        status_line.setSpacing(6)
        status_line.addWidget(self.launch_status)
        # Compact icon directly beside the launch message, not another
        # full-size action in the command row. Qt supplies a native icon.
        self.refresh_status_icon = QToolButton()
        self.refresh_status_icon.setObjectName('refreshStatusIcon')
        self.refresh_status_icon.setFixedSize(32, 32)
        self.refresh_status_icon.setIcon(
            self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_status_icon.setToolTip(
            'Refresh mod launch readiness and recovery status from the swap journal.')
        self.refresh_status_icon.setAccessibleName('Refresh Status')
        self.refresh_status_icon.setCursor(Qt.PointingHandCursor)
        self.refresh_status_icon.clicked.connect(
            lambda _checked=False: self.refresh_swap_status())
        status_line.addWidget(self.refresh_status_icon, 0, Qt.AlignVCenter)
        status_line.addStretch()
        panel.addLayout(status_line)
        box = QWidget()
        box.setLayout(_row(
            _action_with_info('Play With Mods', self.launch,
                              ACTION_HINTS['Play With Mods'], primary=True),
            _action_with_info('Restore Game Files', self.restore,
                              ACTION_HINTS['Restore Game Files'])))
        panel.addWidget(box)
        other = QWidget()
        other.setLayout(_row(
            _action_with_info('Status Details', self.status_details,
                              ACTION_HINTS['Status Details']),
            _action_with_info('Set Up Launcher', self.setup_launcher,
                              ACTION_HINTS['Set Up Launcher']),
            _action_with_info('Refresh Original PAKs', self.refresh_originals,
                              ACTION_HINTS['Refresh Original PAKs'])))
        panel.addWidget(other)
        lay.addWidget(_button('How Play & Restore works', lambda: self.show_help(2)))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['play'] = scroll

    def _settings_page(self):
        # These are sections INSIDE the scrollable Settings page.
        # The on-demand Menu continues to contain only the five main pages.
        scroll, lay = _scroll_page()
        heading = _label('Settings')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        lay.addWidget(_label('Customize the manager without changing your game files.', muted=True))
        self.settings_sections = ()

        _settings_heading(lay, 'UPDATES',
                          'Manual check for verified Stable and Beta updates.')
        update_card, update_row = _card('VERSION & UPDATES',
            'Only newer releases are offered. Installation requires approval.')
        lay.addWidget(update_card)
        update_row.addWidget(_label(
            'Installed: ' + app_version() +
            ' • Channel: ' + self.build_channel().title(), muted=True))
        self.update_status = _label('Updates: not checked', muted=True)
        update_actions = QWidget()
        update_line = QHBoxLayout(update_actions)
        update_line.setContentsMargins(0, 0, 0, 0)
        self.update_button = _button('Check for Updates', self.check_updates, primary=True)
        self.update_button.setToolTip(ACTION_HINTS['Check for Updates'])
        update_line.addWidget(self.update_button)
        update_line.addWidget(_info_icon(ACTION_HINTS['Check for Updates']))
        update_line.addStretch()
        update_row.addWidget(update_actions)
        update_row.addWidget(self.update_status)

        _settings_heading(lay, 'GENERAL',
                          'Appearance, navigation animation, and desktop shortcuts.')
        general, general_row = _card('APPEARANCE',
                                     'Use a compact interface for smaller displays or reduce motion.')
        lay.addWidget(general)
        density = QHBoxLayout()
        density.addWidget(_label('Interface density'))
        self.density_combo = QComboBox()
        self.density_combo.addItems(('Comfortable', 'Compact'))
        self.density_combo.setCurrentText(self.ui_density)
        self.density_combo.currentTextChanged.connect(self._save_ui_preferences)
        density.addWidget(self.density_combo)
        density.addStretch()
        general_row.addLayout(density)
        theme_line = QHBoxLayout()
        theme_line.addWidget(_label('Color theme'))
        self.appearance_combo = QComboBox()
        self.appearance_combo.addItems(('Dark', 'Light'))
        self.appearance_combo.setCurrentText(self.appearance_mode)
        self.appearance_combo.currentTextChanged.connect(self._change_appearance_mode)
        theme_line.addWidget(self.appearance_combo)
        theme_line.addStretch()
        general_row.addLayout(theme_line)
        general_row.addWidget(_label('Customize all interface colors, including the original red accent.', muted=True))
        self.appearance_color_buttons = {}
        for role in ROLES:
            line = QHBoxLayout()
            line.addWidget(_label(role.replace('_', ' ').title()))
            picker = _button('Choose color…', lambda key=role: self._choose_appearance_color(key))
            picker.setObjectName('appearanceColorPicker')
            self.appearance_color_buttons[role] = picker
            line.addWidget(picker)
            line.addStretch()
            general_row.addLayout(line)
        general_row.addWidget(_button('Reset Theme Colors', self._reset_appearance_colors))
        presets=QHBoxLayout()
        presets.addWidget(_label('Saved themes'))
        self.theme_preset_combo=QComboBox()
        self.theme_preset_combo.addItems(sorted(self.saved_theme_presets))
        presets.addWidget(self.theme_preset_combo,1)
        presets.addWidget(_button('Preview / Apply',self._apply_saved_theme))
        presets.addWidget(_button('Save Current',self._save_theme_preset))
        presets.addWidget(_button('Remove',self._remove_theme_preset))
        general_row.addLayout(presets)
        preset_files=QHBoxLayout()
        preset_files.addWidget(_button('Import Theme…',self._import_theme_preset))
        preset_files.addWidget(_button('Export Theme…',self._export_theme_preset))
        preset_files.addStretch()
        general_row.addLayout(preset_files)
        self._update_appearance_labels()
        self.motion_checkbox = QCheckBox('Reduce menu animation')
        self.motion_checkbox.setChecked(self.reduce_motion)
        self.motion_checkbox.toggled.connect(self._save_ui_preferences)
        general_row.addWidget(self.motion_checkbox)
        general_row.addWidget(_button('Create Desktop Shortcut', self.create_shortcut))

        _settings_heading(lay, 'GAME & FILES',
                          'Connected installation, signing stage, projects, and workspace paths.')
        folders, inside = _card('FOLDERS',
                                 'Folders are stored in Data/Settings and are preserved during updates.')
        lay.addWidget(folders)
        self.fields = {}
        for label, name in (('Signing stage', 'stage'), ('Launch/swap folder', 'swap'), ('Projects', 'projects')):
            line = QHBoxLayout()
            line.addWidget(_label(label))
            inp = QLineEdit()
            inp.setText(getattr(self, name + '_path'))
            self.fields[name] = inp
            line.addWidget(inp, 1)
            line.addWidget(_button('Browse…', lambda k=name: self.browse_folder(k)))
            inside.addLayout(line)
        btns = QWidget()
        btns.setLayout(_row(_button('Set Up Manager', self.initial_setup, primary=True),
                             _button('Check Setup', self.check_setup),
                             _button('Open Projects', self.open_projects),
                             _button('Clean Old Projects…', self.open_project_cleanup),
                             _button('Save Settings', self.save_settings)))
        inside.addWidget(btns)

        _settings_heading(lay, 'ASSET LIBRARY',
            'Keep fully extracted reference PAKs for characters, textures, animations and level data.')
        asset_card, asset_card_row = _card('EXTRACTED PAK LIBRARY',
            'Automatically imports the nine standard shared/animation PAKs '
            'from EvolveGame once. Add any other supported signed PAK manually. '
            'Files stay extracted under Data/AssetLibrary; no repeated unpacking for previews.')
        lay.addWidget(asset_card)
        self.asset_library_settings_status = _label(
            'Extracted PAK assets are stored separately from mod projects.', muted=True)
        asset_card_row.addWidget(self.asset_library_settings_status)
        asset_card_row.addWidget(_button('Manage Asset Library / Add PAKs…',
                                        self.open_asset_library))
        asset_card_row.addWidget(_label(
            'Add one or multiple PAKs; verify extracted assets, remove imported '
            'packages, or re-import the standard shared/animation collection. '
            'This never edits your game installation.', muted=True))

        _settings_heading(lay, 'BACKUPS & RECOVERY',
                          'Save disk space safely without sacrificing quick local restoration.')
        backups, backup_row = _card('ORIGINAL GAME FILES',
            'The live swap preserves originals using same-volume renames, not extra full-size copies.')
        lay.addWidget(backups)
        self.snapshots_checkbox = QCheckBox('Keep permanent original snapshots during setup / refresh')
        self.snapshots_checkbox.setChecked(self.keep_original_snapshots)
        self.snapshots_checkbox.toggled.connect(self._save_ui_preferences)
        backup_row.addWidget(self.snapshots_checkbox)
        backup_row.addWidget(_label(
            'Turn off permanent snapshots to save disk space in FUTURE setups and refreshes. '
            'This does not delete existing snapshots or disable the temporary '
            '.customkey-original recovery files used during every launch.', muted=True))
        backup_row.addWidget(_label(
            'The Modded Evolve client can verify and re-download original files after a '
            'modded session. Verification is a manual fallback, not proof the swap journal '
            'was safely restored; do not erase local recovery files while mods are active.',
            muted=True))
        backup_row.addWidget(_button('Client Verification & Recovery Guide',
                                      self.client_recovery_guide))
        backup_row.addWidget(_button('Review Duplicate PAK Copies…',
                                      self.open_pak_copy_cleanup))
        backup_row.addWidget(_label(
            'Scans old game-folder copies without touching live PAKs or restoration '
            'backups. Recycling is manual and only offered for byte-identical '
            'copies after recovery checks.', muted=True))

        _settings_heading(lay, 'LAUNCH PREFERENCES',
                          'Prepare modifications before pressing Play and monitor launch progress.')
        launch_card, launch_row = _card('FASTER REPEAT LAUNCHES',
            'Prepared modified files are automatically recycled on successful Restore. '
            'Renaming the protected originals is quick; the first large preparation may take time.')
        lay.addWidget(launch_card)
        self.preparation_status = _label('Checking prepared file state…', muted=True)
        launch_row.addWidget(self.preparation_status)
        launch_row.addWidget(_action_with_info('Prepare Mod Files Now',
                             self.prepare_mods_now, ACTION_HINTS['Prepare Mod Files Now'],
                             primary=True))
        self.activity_checkbox = QCheckBox('Open Activity automatically while processing tasks')
        self.activity_checkbox.setChecked(self.show_activity_on_tasks)
        self.activity_checkbox.toggled.connect(self._save_ui_preferences)
        launch_row.addWidget(self.activity_checkbox)
        launch_row.addWidget(_label(
            'Preparing ahead moves the waiting time to an earlier step; it does not skip '
            'copying or cryptographic verification. Restoring with Restore Game Files '
            'recycles verified mod files for the next session.', muted=True))

        _settings_heading(lay, 'ADVANCED & MAINTENANCE',
                          'Optional workspace, export and maintenance controls.')
        advanced, advanced_row = _card('ADVANCED FILE OPTIONS',
                                       'Unpack filtering, current workspace, and output location.')
        lay.addWidget(advanced)
        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText('Optional unpack filter (clear for batch extraction)')
        advanced_row.addWidget(self.filter_input)
        self.workspace_input = QLineEdit(self.current_workspace)
        advanced_row.addWidget(_label('Current extracted workspace', muted=True))
        advanced_row.addWidget(self.workspace_input)
        self.output_input = QLineEdit(self.output_pak)
        advanced_row.addWidget(_label('Built PAK path', muted=True))
        advanced_row.addWidget(self.output_input)
        controls = QWidget()
        controls.setLayout(_row(_button('Export Complete Manager ZIP', self.export_bundle),
                                _button('Configure Bundled Setup', self.configure_bundle),
                                _button('Settings Help', lambda: self.show_help(2))))
        advanced_row.addWidget(controls)
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['settings'] = scroll
        self.settings_sections = ('UPDATES', 'GENERAL', 'GAME & FILES', 'ASSET LIBRARY',
                                  'BACKUPS & RECOVERY', 'LAUNCH PREFERENCES',
                                  'ADVANCED & MAINTENANCE')

    def _update_appearance_labels(self):
        colors = palette(self.appearance_mode, self.appearance_overrides)
        for role, control in getattr(self, 'appearance_color_buttons', {}).items():
            control.setText(role.title() + ': ' + colors[role].upper())

    def _change_appearance_mode(self, mode):
        if mode not in PALETTES:
            return
        self.appearance_mode = mode
        self.appearance_overrides = {}
        self._update_appearance_labels()
        self._save_ui_preferences()

    def _choose_appearance_color(self, role):
        current = palette(self.appearance_mode, self.appearance_overrides)[role]
        chosen = QColorDialog.getColor(QColor(current), self, 'Choose ' + role + ' color')
        if not chosen.isValid():
            return
        self.appearance_overrides[role] = chosen.name()
        self._update_appearance_labels()
        self._save_ui_preferences()

    def _reset_appearance_colors(self):
        self.appearance_overrides = {}
        self._update_appearance_labels()
        self._save_ui_preferences()

    def _theme_preview(self, preset):
        dialog=QDialog(self)
        dialog.setWindowTitle('Preview Theme — '+preset['name'])
        dialog.resize(510,250)
        dialog.setStyleSheet(qt_palette_style(qt_style(),preset['mode'],preset['colors']))
        panel=QVBoxLayout(dialog)
        heading=QLabel('EVOLVE MOD MANAGER — Theme Preview')
        heading.setObjectName('sectionTitle')
        panel.addWidget(heading)
        panel.addWidget(QLabel('Preview only. The manager appearance will not change until you select Apply.'))
        example=QHBoxLayout()
        sample=QPushButton('Primary Action')
        sample.setObjectName('primary')
        example.addWidget(sample)
        example.addWidget(QPushButton('Secondary Action'))
        panel.addLayout(example)
        buttons=QDialogButtonBox(QDialogButtonBox.Ok|QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Ok).setText('Apply Theme')
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        panel.addWidget(buttons)
        return dialog.exec()==QDialog.Accepted

    def _refresh_theme_list(self,selected=None):
        self.theme_preset_combo.clear()
        self.theme_preset_combo.addItems(sorted(self.saved_theme_presets))
        if selected in self.saved_theme_presets:
            self.theme_preset_combo.setCurrentText(selected)

    def _save_theme_preset(self):
        name,ok=QInputDialog.getText(self,'Save Theme Preset','Theme name:')
        if not ok:
            return
        try:
            preset=theme_preset(name,self.appearance_mode,
                                palette(self.appearance_mode,self.appearance_overrides))
            if len(self.saved_theme_presets)>=20 and preset['name'] not in self.saved_theme_presets:
                raise ValueError('Save at most 20 theme presets.')
            self.saved_theme_presets[preset['name']]=preset
            self._refresh_theme_list(preset['name'])
            self.persist()
        except (OSError,ValueError) as exc:
            self.failure(exc)

    def _apply_saved_theme(self):
        name=self.theme_preset_combo.currentText()
        if name not in self.saved_theme_presets:
            return
        preset=self.saved_theme_presets[name]
        if self._theme_preview(preset):
            self.appearance_mode=preset['mode']
            self.appearance_overrides=dict(preset['colors'])
            self.appearance_combo.blockSignals(True)
            self.appearance_combo.setCurrentText(self.appearance_mode)
            self.appearance_combo.blockSignals(False)
            self._update_appearance_labels()
            self._save_ui_preferences()

    def _remove_theme_preset(self):
        name=self.theme_preset_combo.currentText()
        if name in self.saved_theme_presets and self.confirm('Remove Theme',
                'Remove saved theme '+name+'? Current appearance will not change.'):
            del self.saved_theme_presets[name]
            self._refresh_theme_list()
            self.persist()

    def _import_theme_preset(self):
        path,_=QFileDialog.getOpenFileName(self,'Import Color Theme','','JSON (*.json)')
        if not path:
            return
        try:
            preset=read_theme_file(path)
            if self._theme_preview(preset):
                if len(self.saved_theme_presets)>=20 and preset['name'] not in self.saved_theme_presets:
                    raise ValueError('Save at most 20 themes.')
                self.saved_theme_presets[preset['name']]=preset
                self._refresh_theme_list(preset['name'])
                self.appearance_mode=preset['mode']
                self.appearance_overrides=dict(preset['colors'])
                self.appearance_combo.blockSignals(True)
                self.appearance_combo.setCurrentText(self.appearance_mode)
                self.appearance_combo.blockSignals(False)
                self._update_appearance_labels()
                self._save_ui_preferences()
        except (OSError,ValueError,TypeError) as exc:
            self.failure(exc)

    def _export_theme_preset(self):
        name=self.theme_preset_combo.currentText()
        try:
            preset=(self.saved_theme_presets[name] if name in self.saved_theme_presets else
                    theme_preset('My Theme',self.appearance_mode,
                                 palette(self.appearance_mode,self.appearance_overrides)))
            destination,_=QFileDialog.getSaveFileName(
                self,'Export Color Theme',preset['name']+'.json','JSON (*.json)')
            if destination:
                Path(destination).write_text(json.dumps(preset,indent=2),encoding='utf-8')
        except (OSError,ValueError) as exc:
            self.failure(exc)

    def _apply_appearance(self):
        style = qt_palette_style(qt_style(), self.appearance_mode,
                                 self.appearance_overrides, density=self.ui_density)
        if self.ui_density == 'Compact':
            style += """
            QPushButton { padding:5px 10px; }
            QPushButton#nav, QPushButton#navActive { padding:6px 10px; }
            QLineEdit, QComboBox { padding:4px; }
            QTableWidget::item { padding:3px; }
            """
        app = QApplication.instance()
        if app:
            app.setStyleSheet(style)
        self.setStyleSheet(style)
        icon_color = palette(self.appearance_mode, self.appearance_overrides)['text']
        for index, button in enumerate(getattr(self, 'nav_buttons', ())):
            button.setIcon(navigation_icon(SECTIONS[index], icon_color, 32))
            # Qt's stylesheet re-polish can reset a button's pre-style
            # 48px height to its 30px size hint on Windows. Restore the
            # fixed geometry *after* applying the global stylesheet so
            # all 30px navigation icons stay unclipped.
            button.setFixedSize(174, 48)
        for editor in getattr(self, 'editors', []):
            if editor is not None:
                editor.setStyleSheet(style)
        if getattr(self, '_log_dialog', None):
            self._log_dialog.setStyleSheet(style)
        self.menu_animation.setDuration(0 if self.reduce_motion else 205)

    def _save_ui_preferences(self, *_):
        self.ui_density = self.density_combo.currentText()
        self.reduce_motion = self.motion_checkbox.isChecked()
        self.keep_original_snapshots = self.snapshots_checkbox.isChecked()
        self.show_activity_on_tasks = self.activity_checkbox.isChecked()
        self._apply_appearance()
        try:
            self.persist()
        except OSError as exc:
            self._log('Could not save preferences: ' + str(exc))

    def open_pak_copy_cleanup(self):
        try:
            game = self._find_game_root()
            if not game:
                chosen = QFileDialog.getExistingDirectory(
                    self, 'Select installed EvolveGame to inspect')
                if not chosen:
                    return
                game = Path(chosen)
            from qt_pak_copy_cleanup import DuplicatePAKCleanupDialog
            dialog = DuplicatePAKCleanupDialog(
                self, game, self.swap_path,
                journal_roots=(DATA_HOME, self.swap_path))
            dialog.exec()
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def client_recovery_guide(self):
        self.info('Client verification and recovery',
            'After playing with mods, close Evolve and the Modded Evolve client.\n\n'
            'Recommended: click Restore Game Files first. The local .customkey-original '
            'files are only renamed back, and the modded files can be reused.\n\n'
            'If restoration fails or installed files were damaged, use the client’s '
            'verify/repair feature to redownload any changed game files. '
            'The client may need significant bandwidth and time.\n\n'
            'Verification does not automatically clear the manager’s swap journal. '
            'Resolve any remaining backups or interrupted-swap state before '
            'launching again. Never delete those recovery files blindly.')

    def prepare_mods_now(self):
        """Run only the existing verified preparation, never launch or swap."""
        try:
            if self.busy:
                raise RuntimeError('Another manager operation is running.')
            st = self.refresh_swap_status()
            if st.phase == 'prepared':
                self.info('Mods already prepared',
                          'Verified prepared files are ready. Play With Mods can skip copying them.')
                return
            if st.phase == 'missing':
                raise RuntimeError(
                    'Set Up Manager first so the exact installed EvolveGame folder is known. '
                    'Do not prepare files using an unconfigured default game path.')
            if st.phase != 'restored':
                raise RuntimeError(
                    'Restore Original Game Files before preparing another modded session.')
            if st.stage and Path(st.stage).resolve() != Path(self.stage_path).resolve():
                raise ValueError('The swap journal belongs to a different signing stage.')
            commands = launcher_commands(Path(self.swap_path), Path(self.stage_path), st.phase)
            prepare = [step for step in commands if step[0] == 'Preparing mod files']
            if len(prepare) != 1:
                raise RuntimeError('The launcher has no valid preparation step.')
            if not self.confirm('Prepare modified PAKs now?',
                'This copies and verifies the signed modified files ahead of time. '
                'The first preparation may take a while and require disk space. '
                'Installed original game files remain untouched. Continue?'):
                return
            self.run_steps(prepare, 'Prepare Mod Files', self.refresh_swap_status,
                           cwd=Path(self.swap_path))
        except Exception as exc:
            self.failure(exc)

    def _credits_page(self):
        scroll, lay = _scroll_page()
        heading = _label(self.t('credits_heading'))
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        card, panel = _card(self.t('credits_discord'),
                             self.t('credits_author_role'))
        lay.addWidget(card)
        panel.addWidget(_label(self.t('credits_github_label'),muted=True))
        panel.addWidget(_button(
            'https://github.com/corvodl/Modded-Evolve-Mod-Manager',
            lambda: webbrowser.open(
                'https://github.com/corvodl/Modded-Evolve-Mod-Manager')))
        panel.addWidget(_label(self.t('credits_website_label'),muted=True))
        panel.addWidget(_button(
            'https://modded-evolve.com/',
            lambda: webbrowser.open('https://modded-evolve.com/')))
        attribution, attribution_panel = _card(
            'Interface icons', 'Uicons by Flaticon — Solid Rounded set.')
        attribution_panel.addWidget(_button(
            'Uicons by Flaticon  ↗',
            lambda: webbrowser.open('https://www.flaticon.com/uicons')))
        lay.addWidget(attribution)
        lay.addWidget(_label(self.t('credits_install_note'), muted=True))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['credits'] = scroll

    def _whats_new_page(self):
        scroll, lay = _scroll_page()
        heading = _label("What's New")
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        for version, notes in RELEASE_NOTES:
            card, panel = _card('v' + version)
            for note in notes:
                panel.addWidget(_label('• ' + note))
            lay.addWidget(card)
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['whats_new'] = scroll

    @staticmethod
    def _developer_fold_card(title, note='', expanded=False):
        """Disclosure cards keep advanced developer controls off-screen until needed."""
        frame = QFrame()
        frame.setObjectName('developerFoldCard')
        outer = QVBoxLayout(frame)
        outer.setContentsMargins(9, 8, 9, 8)
        toggle = QToolButton(frame)
        toggle.setObjectName('developerFoldHeader')
        toggle.setCheckable(True)
        toggle.setChecked(expanded)
        toggle.setToolButtonStyle(Qt.ToolButtonTextOnly)
        toggle.setText(('▾  ' if expanded else '▸  ') + title)
        outer.addWidget(toggle)
        if note:
            hint = _label(note, muted=True)
            hint.setWordWrap(True)
            outer.addWidget(hint)
        details = QWidget(frame)
        panel = QVBoxLayout(details)
        panel.setContentsMargins(8, 5, 8, 5)
        details.setVisible(expanded)
        outer.addWidget(details)
        toggle.toggled.connect(
            lambda checked: (
                details.setVisible(checked),
                toggle.setText(('▾  ' if checked else '▸  ') + title)))
        return frame, panel

    def _developer_page(self):
        """Create protected pages only after code verification."""
        old = self.pages['developer']
        self.stack.removeWidget(old)
        old.deleteLater()
        scroll, lay = _scroll_page()
        heading = _label('Developer')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        card, panel = self._developer_fold_card('Session network policy',
                            'Modded launches block outbound Evolve.exe connections by default.', expanded=True)
        self.dev_online = QCheckBox('Allow online connections during modded play')
        self.dev_online.setChecked(self._developer_allow_online)
        self.dev_online.toggled.connect(self._developer_network)
        panel.addWidget(self.dev_online)
        panel.addWidget(_label('Requires administrator permission for a reversible '
                               'game-specific Windows Firewall rule. No DLL bytes are '
                               'changed. The override is never saved.', muted=True))
        lay.addWidget(card)
        card, panel = self._developer_fold_card('Memory writing permissions',
            'Read-only inspection is always available after Developer unlock. '
            'All writing capabilities are disabled until you opt in.')
        self.dev_writes = QCheckBox('Enable Memory Writing (session only)')
        self.dev_writes.setObjectName('developerEnableMemoryWrites')
        self.dev_writes.setChecked(False)
        self.dev_writes.toggled.connect(self._developer_write_toggle)
        panel.addWidget(self.dev_writes)
        self.dev_online_writes = QCheckBox(
            'Allow Memory Writing While Online — I acknowledge the risks')
        self.dev_online_writes.setObjectName('developerEnableOnlineMemoryWrites')
        self.dev_online_writes.setChecked(False)
        self.dev_online_writes.setEnabled(False)
        self.dev_online_writes.toggled.connect(self._developer_online_write_toggle)
        panel.addWidget(self.dev_online_writes)
        panel.addWidget(_label(
            'Online writes may affect other players, trigger integrity checks '
            'or result in account or server sanctions. Requires online access '
            'enabled above, a separate typed acknowledgment, and confirmation '
            'for every write. Nothing is saved after leaving Developer.',
            muted=True))
        lay.addWidget(card)
        card, panel = self._developer_fold_card('Live Evolve memory',
                            'Browse loaded modules, committed memory regions, scan '
                            'values and inspect or edit hex bytes in a running game.')
        self.dev_memory_panel = DeveloperMemoryPanel(
            offline_game_provider=self._developer_game,
            write_permission=self._developer_memory_write_permission)
        panel.addWidget(self.dev_memory_panel)
        lay.addWidget(card)
        card, panel = self._developer_fold_card('Account profile / progression',
            'Inspect readable backups, compare ownership and XP, and export isolated offline copies.')
        profile_button = _button('Open Account Profile Inspector…', self._open_account_profile_inspector)
        profile_button.setObjectName('developerProfileInspector')
        panel.addWidget(profile_button)
        lay.addWidget(card)
        card, panel = self._developer_fold_card('Developer diagnostics',
            'Live pointer/snapshot tools and isolated relay, session and '
            'trace diagnostics. Local fixture services cannot target public endpoints.', expanded=True)
        from qt_developer_lab import DeveloperLabPanel
        self.dev_lab_panel = DeveloperLabPanel(
            self._developer_game,trace_env_callback=self._set_dev_trace_env,
            write_permission=self._developer_memory_write_permission)
        panel.addWidget(self.dev_lab_panel)
        lay.addWidget(card)
        lay.addWidget(_button('Lock and leave',
                              lambda: self.open_page(self._last_public_page)))
        lay.addStretch()
        self.stack.insertWidget(self._developer_index, scroll)
        self.pages['developer'] = scroll
        # Initialize while the protected widgets are still owned by this page.
        # Queued bound-method callbacks can outlive a quickly locked/deleted
        # page and crash the Windows Qt runtime (0xC0000005).
        self.dev_memory_panel.start()
        self.dev_lab_panel.refresh_pids()

    def _open_account_profile_inspector(self):
        from qt_account_profile import AccountProfileInspector
        dialog = AccountProfileInspector(self)
        dialog.exec()

    def _developer_memory_write_permission(self):
        from developer_write_policy import require_developer_write
        from online_guard import offline_rule_active
        return require_developer_write(
            self._developer_allow_writes,
            self._developer_online_write_ack,
            self._developer_allow_online,
            self._developer_game,
            offline_rule_active)

    def _sync_write_permissions(self):
        if hasattr(self, 'dev_memory_panel'):
            self.dev_memory_panel.set_write_enabled(self._developer_allow_writes)
        if hasattr(self, 'dev_lab_panel'):
            self.dev_lab_panel.set_write_enabled(self._developer_allow_writes)

    def _developer_write_toggle(self,enabled):
        self._developer_allow_writes = bool(enabled)
        if not enabled:
            self._developer_online_write_ack = False
            self.dev_online_writes.blockSignals(True)
            self.dev_online_writes.setChecked(False)
            self.dev_online_writes.blockSignals(False)
        self.dev_online_writes.setEnabled(bool(enabled))
        self._sync_write_permissions()

    def _developer_online_write_toggle(self,enabled):
        if enabled:
            if not self._developer_allow_writes or not self._developer_allow_online:
                self.info('Online memory writing',
                          'Enable Memory Writing and Allow online connections '
                          'during modded play before requesting online-write consent.')
                enabled = False
            elif not self.confirm(
                'Online memory writing — risk acknowledgment',
                'Modifying a live online game can affect other players and '
                'shared sessions, break game integrity, or trigger account '
                'penalties. This permission is only for the current unlocked '
                'Developer session. Continue to the written acknowledgment?'):
                enabled = False
            else:
                phrase,accepted=QInputDialog.getText(
                    self,'Acknowledge Online Writing Risks',
                    'Type I ACCEPT to enable online memory writes for this session:')
                enabled = bool(accepted and phrase.strip() == 'I ACCEPT')
        self._developer_online_write_ack = bool(enabled)
        if self.dev_online_writes.isChecked() != bool(enabled):
            self.dev_online_writes.blockSignals(True)
            self.dev_online_writes.setChecked(bool(enabled))
            self.dev_online_writes.blockSignals(False)
        self._sync_write_permissions()

    def _set_dev_trace_env(self,name,value):
        from developer_network_lab import validate_trace_environment
        name,value=validate_trace_environment(name,value)
        if len(self._dev_trace_env)>=32 and name not in self._dev_trace_env:
            raise ValueError('At most 32 developer trace flags per session.')
        self._dev_trace_env[name]=value

    def _developer_game(self):
        state = state_status(Path(self.swap_path))
        game = Path(state.game) if state.game and Path(state.game).is_dir() else self._find_game_root()
        if game is None:
            raise RuntimeError('Complete setup to locate Evolve.exe.')
        target_executable(game)
        return game

    def _developer_network(self, allow):
        previous = self._developer_allow_online
        if allow and not self.confirm(
                'Allow online access?', 'Remove the game network block? '
                'Modified gameplay may affect other players.'):
            self.dev_online.blockSignals(True)
            self.dev_online.setChecked(False)
            self.dev_online.blockSignals(False)
            return
        try:
            phase = state_status(Path(self.swap_path)).phase
            if allow or phase in ('swapped', 'swapping', 'interrupted'):
                set_offline(self._developer_game(), not allow)
            self._developer_allow_online = bool(allow)
            if not allow:
                self._developer_online_write_ack = False
                if hasattr(self,'dev_online_writes'):
                    self.dev_online_writes.blockSignals(True)
                    self.dev_online_writes.setChecked(False)
                    self.dev_online_writes.blockSignals(False)
        except Exception as exc:
            self._developer_allow_online = previous
            self.dev_online.blockSignals(True)
            self.dev_online.setChecked(previous)
            self.dev_online.blockSignals(False)
            self.failure(exc)

    def _restore_network_after_game(self):
        state = self.refresh_swap_status()
        if state.phase in ('missing', 'prepared', 'restored'):
            try:
                set_offline(self._developer_game(), False)
            except Exception as exc:
                self._log('Firewall cleanup needed: ' + str(exc))
                self.info('Network restriction remains',
                          'Original game files were restored, but Windows denied '
                          'removing the network restriction. Run as administrator.')

    def show_help(self, section=None):
        """Keep complete original help, including offline safety and recovery."""
        headings = (
            ('help_mod_title','help_mod_body'),
            ('help_play_title','help_play_body'),
            ('help_setup_title','help_setup_body'),
            ('help_safety_title','help_safety_body'),
        )
        if section is None:
            section = {0:2,1:0,2:1,3:2,4:2}.get(self.stack.currentIndex(),2)
        dialog = QDialog(self)
        dialog.setWindowTitle(self.t('help_title'))
        dialog.setStyleSheet(qt_style())
        dialog.resize(760,540)
        dialog.setMinimumSize(520,380)
        frame = QVBoxLayout(dialog)
        frame.addWidget(_label(self.t('help_title')))
        tabs = QTabWidget()
        for title_key, body_key in headings:
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            content = QWidget()
            layout = QVBoxLayout(content)
            layout.addWidget(_label(self.t(body_key)))
            layout.addStretch()
            scroll.setWidget(content)
            tabs.addTab(scroll, self.t(title_key))
        tabs.setCurrentIndex(max(0,min(3,section)))
        frame.addWidget(tabs,1)
        frame.addWidget(_button('Close',dialog.accept))
        dialog.exec()

    def info(self, title, message):
        QMessageBox.information(self, title, str(message))

    def confirm(self, title, message):
        return QMessageBox.question(
            self, title, str(message), QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No) == QMessageBox.Yes

    def confirm_storage(self, title, message):
        box = QMessageBox(self)
        box.setWindowTitle(title)
        box.setText(message)
        box.setIcon(QMessageBox.Question)
        yes = box.addButton('Keep full original snapshots', QMessageBox.YesRole)
        no = box.addButton('Space-saving mode', QMessageBox.NoRole)
        box.addButton(QMessageBox.Cancel)
        # Preference determines the default, but this remains an explicit
        # per-operation confirmation to avoid accidental destructive choices.
        preferred = (yes if self.keep_original_snapshots else no)
        box.setDefaultButton(preferred)
        box.exec()
        if box.clickedButton() == yes:
            self.snapshots_checkbox.setChecked(True)
            return True
        if box.clickedButton() == no:
            self.snapshots_checkbox.setChecked(False)
            return False
        return None

    def failure(self, error):
        self._log('ERROR: ' + str(error))
        self.status.setText('Could not continue: ' + str(error))
        QMessageBox.critical(self, 'Could not continue', str(error))

    def toggle_activity(self):
        if self._log_dialog is None:
            box = QDialog(self)
            box.setWindowTitle('Activity | Evolve Mod Manager ' + app_version())
            box.resize(920, 460)
            box.setStyleSheet(self.styleSheet())
            inside = QVBoxLayout(box)
            self.log_widget = QPlainTextEdit()
            self.log_widget.setReadOnly(True)
            self.log_widget.setPlainText('\n'.join(self.log_lines))
            inside.addWidget(self.log_widget)
            inside.addWidget(_button('Close', box.hide))
            self._log_dialog = box
        self._log_dialog.setVisible(not self._log_dialog.isVisible())

    def _log(self, message):
        self.log_lines.append(str(message).rstrip('\n'))
        if self._log_dialog and self._log_dialog.isVisible():
            self.log_widget.appendPlainText(str(message).rstrip('\n'))

    def run_steps(self, steps, label, success=None, cwd=None):
        if self.busy:
            self.failure('Wait for the current operation to finish.')
            return
        self.busy = True
        self.status.setText(label + '…')
        from operation_progress import ProgressStream
        self._file_progress_stream = ProgressStream()
        self._show_file_progress(label)
        self._success = success
        self._steps = list(steps)
        self._task_label = label
        self._task_cwd = Path(cwd or ROOT)
        if self.show_activity_on_tasks and self._log_dialog is None:
            self.toggle_activity()
        self._run_next_step()

    def _run_next_step(self):
        if not self._steps:
            self._task_finished(self._task_label, 0)
            return
        name, command = self._steps.pop(0)
        self._log('> ' + name)
        wrapped = worker_command(command)
        self.process = QProcess(self)
        self.process.setProgram(str(wrapped[0]))
        self.process.setArguments([str(x) for x in wrapped[1:]])
        self.process.setWorkingDirectory(str(self._task_cwd))
        if (self._task_label=='Play with Mods' and
                not self._developer_allow_online and self._dev_trace_env):
            environment=QProcessEnvironment.systemEnvironment()
            for name,value in self._dev_trace_env.items():
                environment.insert(name,value)
            self.process.setProcessEnvironment(environment)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._process_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(
            lambda error: self._log('Process error: ' + self.process.errorString()))
        self.process.start()

    def _process_output(self):
        output = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        if output:
            events, visible = self._file_progress_stream.feed(output)
            for label, completed, total in events:
                self._set_file_progress(label, completed, total)
            if visible:
                self._log(visible)
            if 'HOOK READY' in visible or 'Automatic CreateProcessW hook ready' in visible:
                self.status.setText('Launcher ready — press Play in the Evolve client.')
            if 'SWAP COMPLETE' in visible:
                self.status.setText('Modded files swapped. Evolve is starting…')

    def _process_finished(self, code, status):
        for label, completed, total in self._file_progress_stream.flush()[0]:
            self._set_file_progress(label, completed, total)
        if code:
            self._task_finished(self._task_label, code)
        else:
            self._run_next_step()

    def _task_finished(self, name, code):
        self.operation_progress.hide()
        self.busy = False
        self.process = None
        if code:
            self.status.setText(f'FAILED: {name} (exit {code}). Review Activity.')
            self._log('FAILED — backups and journals have not been deleted.')
            self.refresh_swap_status()
            self._queued_task_running = None
            self.task_queue.clear()  # Failure pauses/clears future mutations.
            return
        self.status.setText('Completed: ' + name)
        self._log('SUCCESS: ' + name)
        callback, self._success = self._success, None
        if callback:
            try:
                callback()
            except Exception as exc:
                self.failure(exc)
        self.refresh_swap_status()
        self._queued_task_running = None
        if self.task_queue and not self.busy:
            QTimer.singleShot(10,self._drain_tasks)

    def persist(self):
        self.settings.update({
            'stage': self.stage_path,
            'swap': self.swap_path,
            'projects': self.projects_path,
            'current_workspace': self.current_workspace,
            'output_pak': self.output_pak,
            'archive_label': self.archive_label,
            'batch_root': self.batch_root,
            'ui_density': self.ui_density,
            'reduce_motion': self.reduce_motion,
            'keep_original_snapshots': self.keep_original_snapshots,
            'show_activity_on_tasks': self.show_activity_on_tasks,
            'appearance_mode': self.appearance_mode,
            'appearance_colors': self.appearance_overrides,
            'theme_presets': self.saved_theme_presets,
        })
        save_settings(self.settings)

    def save_settings(self):
        self.stage_path = self.fields['stage'].text().strip()
        self.swap_path = self.fields['swap'].text().strip()
        self.projects_path = self.fields['projects'].text().strip()
        self.current_workspace = self.workspace_input.text().strip()
        self.output_pak = self.output_input.text().strip()
        try:
            self.persist()
            self.refresh_archives()
            self.refresh_swap_status()
            self.info('Saved', 'Manager folder settings saved.')
        except Exception as exc:
            self.failure(exc)

    def browse_folder(self, key):
        selected = QFileDialog.getExistingDirectory(self, 'Select ' + key, self.fields[key].text())
        if selected:
            self.fields[key].setText(selected)
            self.save_settings()

    def open_mod_profiles(self):
        """Open the profile library without changing the installed game."""
        try:
            if self.busy:
                raise RuntimeError('Finish the current operation before editing profiles.')
            from qt_mod_profiles import ModProfilesDialog
            suggested = Path(self.output_pak) if self.output_pak else None
            dialog = ModProfilesDialog(
                self, DATA_HOME / 'Profiles', self.stage_path,
                self.apply_mod_profile, suggested_pak=suggested)
            dialog.exec()
        except Exception as exc:
            self.failure(exc)

    def apply_mod_profile(self, profile_id):
        """Queue signed PAK staging in a worker, not the Qt thread."""
        if self.busy:
            raise RuntimeError('Wait for the current operation to finish.')
        if not self.flush_editors():
            raise RuntimeError('Save or close pending file edits first.')
        from launch_integration import state_status
        phase = state_status(Path(self.swap_path)).phase
        if phase not in ('missing', 'restored', 'prepared'):
            raise RuntimeError('Restore any active or interrupted modded play session first.')
        command = [sys.executable, '-u', str(ROOT / 'mod_profiles.py'),
                   'apply', '--root', str(DATA_HOME / 'Profiles'),
                   '--stage', str(Path(self.stage_path)),
                   '--swap', str(Path(self.swap_path)),
                   '--profile', profile_id if profile_id is not None else 'baseline']
        def complete():
            self.refresh_archives()
            self.refresh_swap_status()
            self.info('Mod profile staged',
                      'The selected profile was applied to the signing stage. '
                      'No installed game files were changed.')
        self.run_steps([('Applying signed Mod Profile', command)],
                       'Apply Mod Profile', complete)

    def export_model_folder(self):
        """Export native model files without blocking the application UI."""
        input_folder = QFileDialog.getExistingDirectory(
            self, 'Choose folder containing CryEngine models', self.projects_path)
        if not input_folder:
            return
        output_folder = QFileDialog.getExistingDirectory(
            self, 'Choose a separate Blender export folder', self.projects_path)
        if not output_folder:
            return
        from qt_background_exports import begin_file_operation, exports_busy
        if exports_busy(self):
            self.info('Export in progress', 'Finish or cancel the current model export first.')
            return
        from model_textured_export import export_extracted_folder
        source = Path(input_folder)
        destination = Path(output_folder)
        projects, stage = self.projects_path, self.stage_path

        def work(progress, cancelled):
            return export_extracted_folder(
                source, destination, projects_root=projects, stage_root=stage,
                progress=progress, cancelled=cancelled)

        def success(result):
            exported, skipped = result
            textured = sum(bool(info['texture_count']) for _, _, _, info in exported)
            rigged = sum(bool(info.get('bone_count')) for _, _, _, info in exported)
            if skipped:
                (destination / 'Blender_export_report.txt').write_text(
                    '\n'.join(name + ': ' + reason for name, reason in skipped),
                    encoding='utf-8')
            self.info(
                'Blender export finished',
                '%d models exported (%d textured, %d rigged), %d skipped.\\n%s' %
                (len(exported), textured, rigged, len(skipped), output_folder))

        def failed(error):
            self.failure('Blender export stopped: ' + error)

        begin_file_operation(
            self, 'Export Blender models', 'Preparing model folder export…',
            work, on_success=success, on_error=failed)

    def browse_external_pak(self):
        source, _ = QFileDialog.getOpenFileName(self, 'Choose custom-signed PAK',
                                                self.projects_path, 'PAK archives (*.pak)')
        if source:
            self.external_pak = source
            self.archive_label = ''
            self.info('External archive', 'Selected: ' + source)

    def _load_batch(self):
        self.batch_workspaces.clear()
        try:
            if self.batch_root:
                data = load_collection(Path(self.batch_root))
                if Path(data['stage']).resolve() == Path(self.stage_path).resolve():
                    self.batch_workspaces = {
                        entry['archive']: Path(self.batch_root)/entry['workspace']
                        for entry in data['archives']}
        except (ValueError, KeyError, TypeError, OSError):
            pass

    def _show_legacy_pak_editor(self, folder):
        """Keep historical ZIP PAK projects separate from signed Stage 2 mods."""
        from qt_legacy_pak_editor import LegacyPAKEditor
        folder = Path(folder).resolve(strict=True)
        for editor in tuple(self.editors):
            if isinstance(editor, LegacyPAKEditor) and editor.project['folder'] == folder:
                if editor.isVisible():
                    editor.raise_()
                    editor.activateWindow()
                    return editor
        editor = LegacyPAKEditor(self, folder)
        editor.setAttribute(Qt.WA_DeleteOnClose, False)
        self.editors.append(editor)
        editor.show()
        editor.raise_()
        return editor

    def open_legacy_pak(self):
        """Open unsigned ZIP-compatible 2012 PAK without game setup or injection."""
        source, _ = QFileDialog.getOpenFileName(
            self, 'Open Historical Evolve (2012) PAK',
            str(Path.home()), 'Legacy PAK (*.pak);;All files (*)')
        if not source:
            return
        try:
            from legacy_pak_workspace import create_project
            import hashlib
            path = Path(source).resolve(strict=True)
            folder = Path(self.projects_path).resolve() / 'Legacy_PAK_Projects'
            folder.mkdir(parents=True, exist_ok=True)
            safe = ''.join(ch if ch.isalnum() or ch in '-_' else '_'
                           for ch in path.stem)[:48] or 'Archive'
            label = hashlib.sha256(str(path).casefold().encode()).hexdigest()[:9]
            root = folder / (safe + '_' + label)
            if root.exists():
                from legacy_pak_workspace import load_project
                manifest = load_project(root)
                if manifest['source_path'] != path:
                    raise ValueError('Existing legacy project refers to a different PAK.')
            else:
                create_project(path, root)
            self._show_legacy_pak_editor(root)
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def open_legacy_project(self):
        directory = QFileDialog.getExistingDirectory(
            self, 'Open Existing Legacy PAK Project',
            str(Path(self.projects_path).resolve() / 'Legacy_PAK_Projects'))
        if not directory:
            return
        try:
            self._show_legacy_pak_editor(directory)
        except (OSError, ValueError, KeyError) as exc:
            self.failure(exc)

    def open_asset_library(self):
        from qt_asset_library import AssetLibraryDialog
        dialog = AssetLibraryDialog(
            self, self.asset_library_controller, self.stage_path,
            self.game_root or self._find_game_root())
        dialog.exec()

    def create_mod_project_from_asset_library(
            self, library_id, *, source_build_id=None, open_level=False):
        """Create an editable workspace from the custom-signed matching PAK.

        Reference assets remain untouched; the existing Build Mod action
        handles modified payload repacking, signing and verification.
        """
        try:
            if self.busy or self.asset_library_controller.busy:
                raise RuntimeError('Wait for the current task to finish.')
            from asset_library_bulk import project_command
            if not self.flush_editors():
                return
            folder = self._new_folder('AssetLibrary_Mod')
            game = self.game_root or self._find_game_root()
            relative, command = project_command(
                library_id, self.stage_path, folder, game, build_id=source_build_id)
            # Store explicit signed baseline ownership in the resulting project.
            self._pending_library_archive = library_id
            self._pending_library_build = source_build_id
            self._pending_library_map = bool(open_level)
            self.archive_label = relative
            self.run_steps(
                [('Creating editable project from verified staged PAK', command)],
                'Create editable mod project',
                lambda: self._after_asset_library_project(folder, relative))
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def _after_asset_library_project(self, folder, relative):
        from evolve_pak_workspace import load_workspace
        from project_selection import workspace_matches
        info = load_workspace(Path(folder))
        target = Path(self.stage_path) / 'paks' / Path(*relative.split('/'))
        if Path(info['source_pak']).resolve() != target.resolve():
            self.failure('Mod project source does not match the staged archive.')
            return
        self.current_workspace = str(folder)
        self.archive_label = relative
        self.workspace_input.setText(str(folder))
        self.output_pak = ''
        self.output_input.setText('')
        self.persist()
        try:
            from project_catalog import register_project
            register_project(self.projects_path, folder, self.stage_path,
                             source_build_id=getattr(self, '_pending_library_build', None),
                             archive_id=getattr(self, '_pending_library_archive', None))
            self.refresh_project_list()
        except (OSError, ValueError) as exc:
            self._log('Project catalog: ' + str(exc)[:200])
        self._log(
            'Editable project ready: ' + str(folder) +
            '\\nThe Asset Library is unchanged. Edit project copies and use Build Mod '
            'to sign modified entries using the existing custom key.')
        if getattr(self, '_pending_library_map', False):
            self._pending_library_map = False
            self.open_level_editor(folder)
        else:
            self._open_project_workspace(folder)

    def _open_project_workspace(self, folder):
        from qt_workspace_editor import QtWorkspaceEditor
        workspace = Path(folder).resolve(strict=True)
        for editor in self.editors:
            if (isinstance(editor, QtWorkspaceEditor) and editor.isVisible()
                    and editor.workspace == workspace):
                editor.raise_()
                editor.activateWindow()
                return
        editor = QtWorkspaceEditor(self, workspace)
        editor.setAttribute(Qt.WA_DeleteOnClose, False)
        self.editors.append(editor)
        editor.show()
        virtual = getattr(self, '_pending_library_virtual', None)
        self._pending_library_virtual = None
        if virtual and virtual in editor.records:
            editor.load(virtual)
        editor.raise_()

    def _warm_asset_library(self):
        from asset_library import installed_defaults, imported_archives
        game = self.game_root or self._find_game_root()
        if not game:
            return
        key = str(Path(game).resolve())
        if self._auto_library_scanned_game == key:
            return
        if self.asset_library_controller.busy:
            return
        files = installed_defaults(game)
        if not files:
            return
        if self.asset_library_controller.import_files(
                files, self.stage_path, automatic=True):
            self._auto_library_scanned_game = key
            self.shared_asset_label.setText('Asset Library: importing…')

    def _library_task_started(self, action):
        self._show_file_progress('Asset Library: ' + action)

    def _library_task_progress(self, label, completed, total):
        self._set_file_progress(label, completed, total)

    def _show_file_progress(self, label):
        self.operation_progress.setRange(0, 0)
        self.operation_progress.setToolTip(label)
        self.operation_progress.show()

    def _set_file_progress(self, label, completed, total):
        if total > 0:
            self.operation_progress.setRange(0, int(total))
            self.operation_progress.setValue(min(int(completed), int(total)))
        else:
            self.operation_progress.setRange(0, 0)
        self.operation_progress.setToolTip(
            '%s • %d / %d files' % (label, completed, total))
        self.operation_progress.show()
        self.status.setText(label + ' • %d / %d' % (completed, total))

    def _asset_library_finished(self, action, result, error):
        self.operation_progress.hide()
        self.refresh_library_table()
        if action == 'export-layout':
            if error:
                self.failure('Export layout failed: ' + str(error))
            elif result:
                output, count = result
                self.info('Asset Library export complete',
                          '%d extracted files copied with their original virtual paths to:\n%s' %
                          (count, output))
            return
        from asset_library import imported_archives, required_archive_status
        archives = imported_archives()
        coverage = required_archive_status()
        self.shared_asset_label.setText(
            'Shared: %d/6 • Animations: %d/3' %
            (coverage['shared'], coverage['animations']))
        details = self.asset_library_controller.status
        if coverage['missing']:
            details += '\nMissing required PAKs: ' + ', '.join(coverage['missing'])
        if coverage['warnings']:
            details += '\n%d skipped PAK entries; verify imported contents.' % coverage['warnings']
        self.shared_asset_label.setToolTip(details)
        if hasattr(self, 'asset_library_settings_status'):
            self.asset_library_settings_status.setText(
                '%d imported archives (required: %d/9) • %.1f MiB stored' %
                (len(archives), coverage['present'],
                 sum(item['bytes'] for item in archives) / 1048576))
        if error:
            self._log('Asset Library: ' + str(error)[:200])
            return
        if isinstance(result, list):
            failed = [row for row in result if row.get('status') == 'error']
            historical = [row for row in result
                          if row.get('status') == 'legacy-project']
            verified = len(result) - len(failed) - len(historical)
            self._log('Asset Library: %d signed archive imports, %d legacy ZIP '
                      'projects kept separate, %d failures' %
                      (verified, len(historical), len(failed)))
            if failed:
                self._log('First failed PAK: ' + failed[0]['name'] +
                          ': ' + failed[0]['error'][:180])
                if action == 'automatic-import':
                    # Retry on the next user-initiated archive Refresh.
                    self._auto_library_scanned_game = None
        if coverage['missing']:
            self._log('Asset Library missing required archives: ' +
                      ', '.join(coverage['missing']))
        # Loaded editor previews use a snapshot of the asset index. Refresh
        # model dependencies when the extracted library changes; do not
        # interrupt a playing animation or dirty text buffer.
        for editor in tuple(self.editors):
            try:
                if (editor.isVisible() and getattr(editor, 'mode', None) == 'models'
                        and editor.current and not
                        (editor.model_panel and editor.model_panel.anim_playing)):
                    # An imported shared PAK previously left this editor
                    # holding its old index + a 30-second model result cache.
                    # Re-resolve dependencies instead of replaying stale Kala.
                    from studio_polish_117 import clear_model_cache
                    clear_model_cache(editor.workspace)
                    editor.preview_index = None
                    editor.load(editor.current)
            except (OSError, ValueError, RuntimeError) as exc:
                self._log('Model reference refresh: ' + str(exc)[:120])

    def _warm_shared_assets(self):
        from shared_pak_cache import start_shared_catalog
        try:
            start_shared_catalog(self.stage_path, game_root=self._find_game_root())
        except (OSError, ValueError) as exc:
            self._log('Shared asset indexing unavailable: ' + str(exc))

    def _shared_catalog_tick(self):
        from shared_pak_cache import shared_catalog_status
        status = shared_catalog_status()
        if status == self._shared_catalog_last_status:
            return
        self._shared_catalog_last_status = status
        if hasattr(self, 'shared_asset_label'):
            # The permanent extracted Asset Library is now the primary source.
            # Transient shared PAK scans are only a fallback and must not
            # overwrite the Asset Library progress/status shown in the footer.
            self.shared_asset_label.setToolTip(
                self.asset_library_controller.status + '\nArchive fallback: ' + status)
        if status.startswith('Ready:'):
            self._log('Shared references: ' + status)
            # A model may have opened while indexing was still running.
            # Refresh its missing materials once without interrupting playback.
            for editor in tuple(self.editors):
                try:
                    if (getattr(editor, 'mode', None) == 'models'
                            and editor.isVisible()
                            and getattr(editor, 'mesh', None) is not None
                            and not (getattr(editor, 'model_panel', None) and
                                     editor.model_panel.anim_playing)):
                        editor.refresh_model_textures()
                except (AttributeError, RuntimeError, OSError, ValueError):
                    continue

    def refresh_archives(self):
        try:
            self.archive_entries = sorted(
                allowed_paks(Path(self.stage_path)).values(), key=str.casefold)
        except (OSError, ValueError) as exc:
            self.archive_entries = []
            self._log('Archive scan: ' + str(exc))
        self.refresh_table()
        self._warm_shared_assets()
        self._warm_asset_library()

    def refresh_table(self):
        if not hasattr(self, 'table'):
            return
        query = self.search.text()
        mode = self.file_filter.currentText()
        selected = set(self.selected_archives())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        from pak_dependencies import group_title
        records = ([
            rel for rel in matching_archives(self.archive_entries, query)
            if True] if mode != 'Game files' else [])
        if query and mode != 'Game files':
            groups = [rel for rel in self.archive_entries
                      if query.casefold() in group_title(rel).casefold()]
            records = sorted(set(records) | set(groups), key=str.casefold)
        for rel in records:
            name, folder = describe_archive(rel)
            ws = self.batch_workspaces.get(rel)
            status = 'Unpacked' if ws and Path(ws, '.evolve-pak-workspace.json').is_file() else 'PAK'
            try:
                size = (Path(self.stage_path)/'paks'/Path(*rel.split('/'))).stat().st_size
            except OSError:
                size = -1
            self._insert_row(rel, (group_title(rel), folder,
                                   archive_description(rel), name, status,
                                   format_size(size)))
        # Read-only loose-file discovery uses the existing bounds-checked scanner.
        loose_count = 0
        if mode != 'PAK archives':
            game = self.game_root or self._find_game_root()
            if game:
                from loose_game_files import scan_loose_files
                try:
                    for record in scan_loose_files(Path(game)):
                        if query.casefold() not in record.relative.casefold():
                            continue
                        name, folder = describe_archive(record.relative)
                        self._insert_row('loose-file:' + record.relative,
                                         ('Loose game files', folder, 'Game file', name,
                                          'Game file', format_size(record.size)))
                        loose_count += 1
                except (OSError, ValueError) as exc:
                    self._log('Loose file scan unavailable: ' + str(exc))
        self.table.setSortingEnabled(True)
        self.table.sortItems(0, Qt.AscendingOrder)
        self.archive_count.setText(
            f'{len(records) + loose_count} files ({len(records)} PAKs, {loose_count} game files)')
        if selected:
            from PySide6.QtCore import QItemSelectionModel
            sel = self.table.selectionModel()
            for row in range(self.table.rowCount()):
                meta = self.table.item(row, 0)
                if meta and meta.data(Qt.UserRole) in selected:
                    index = self.table.model().index(row, 0)
                    sel.select(index, QItemSelectionModel.Select | QItemSelectionModel.Rows)


    def _insert_row(self, identity, values):
        row = self.table.rowCount()
        self.table.insertRow(row)
        for col, value in enumerate(values):
            item = QTableWidgetItem(str(value))
            item.setFlags(item.flags() & ~Qt.ItemIsEditable)
            if col == 0:
                item.setData(Qt.UserRole, identity)
            self.table.setItem(row, col, item)

    def selected_archives(self):
        result = []
        for row in sorted({item.row() for item in self.table.selectedItems()}):
            cell = self.table.item(row, 0)
            if cell and cell.data(Qt.UserRole):
                result.append(cell.data(Qt.UserRole))
        return result

    def _select_archive(self):
        chosen = self.selected_archives()
        if len(chosen) != 1:
            return
        selected = chosen[0]
        if selected.startswith('loose-file:'):
            self.archive_label = ''
        else:
            self.archive_label = selected
            # Never carry a prior PAK's workspace into a different selection.
            from project_selection import workspace_for_archive
            matched = workspace_for_archive(
                self.stage_path, selected, self.current_workspace,
                self.batch_workspaces)
            self.current_workspace = str(matched) if matched else ''
            if hasattr(self,'workspace_input'):
                self.workspace_input.setText(self.current_workspace)
        self.external_pak = ''

    def _pak_locations(self, rel):
        if rel not in self.archive_entries or '..' in rel.split('/'):
            raise ValueError('Select a valid, verified staged PAK.')
        stage = Path(self.stage_path)/'paks'/Path(*rel.split('/'))
        game = self.game_root or self._find_game_root()
        original = Path(game)/Path(*rel.split('/')) if game else None
        return stage, original

    def _find_game_root(self):
        st = state_status(Path(self.swap_path))
        if st.game and Path(st.game).is_dir():
            self.game_root = Path(st.game)
        return self.game_root

    def archive_context_menu(self, where):
        selected = self.table.itemAt(where)
        if selected is None:
            return
        row = selected.row()
        # A right click on an already selected row must preserve the extended
        # multi-PAK selection needed for batch unpacking.
        selected_rows = {item.row() for item in self.table.selectedItems()}
        if row not in selected_rows:
            self.table.clearSelection()
            self.table.selectRow(row)
        ids = self.selected_archives()
        if not ids:
            return
        rel = ids[0]
        menu = QMenu(self.table)
        if rel.startswith('loose-file:'):
            menu.addAction('Edit Game File Copy', self.open_editor)
        else:
            from pak_dependencies import is_level_pak
            if len(ids) == 1 and is_level_pak(rel):
                submenu = menu.addMenu('Open With…')
                submenu.addAction('Level Editor 1.0',
                                  lambda: self.open_level_editor())
                submenu.addAction('Extracted File Editor',
                                  lambda: self.open_editor_or_unpack_as_files())
                menu.addSeparator()
            menu.addAction('Unpack Selected PAKs + Dependencies'
                           if len(ids) > 1 else 'Unpack PAK + Dependencies',
                           self.extract_batch if len(ids) > 1 else self.extract)
            menu.addAction('Information / Known Dependencies', self.inspect_pak)
            menu.addSeparator()
            menu.addAction('Show Original Game File in Explorer',
                           lambda: self.reveal_pak(original=True))
            menu.addAction('Show Staged PAK in Explorer',
                           lambda: self.reveal_pak(original=False))
            menu.addAction('Copy Original Game File Path',
                           lambda: self.copy_pak_path(original=True))
            menu.addAction('Copy Staged PAK Path',
                           lambda: self.copy_pak_path(original=False))
            menu.addSeparator()
            menu.addAction('Open Unpacked Files', self.open_unpacked)
        menu.exec(self.table.mapToGlobal(where))

    def _selected_rel(self):
        rels = [r for r in self.selected_archives() if not r.startswith('loose-file:')]
        return self.archive_label if self.archive_label in rels else (rels[0] if rels else self.archive_label)

    def open_editor_or_unpack_as_files(self):
        try:
            matching = self._workspace_for_selected_archive()
            if matching is not None:
                self.open_editor()
            else:
                self.extract()
        except (OSError, ValueError) as exc:
            self.failure(exc)

    def inspect_pak(self):
        rel = self._selected_rel()
        staged, original = self._pak_locations(rel)
        size = format_size(staged.stat().st_size) if staged.is_file() else 'Missing'
        from pak_dependencies import plan_dependencies, plan_report
        plan = plan_dependencies(rel, self.archive_entries)
        from asset_library import covered_stage_archives
        in_library = covered_stage_archives(plan.companions)
        report = plan_report(plan)
        report += '\n\nAsset Library dependencies: %d/%d reusable\n' % (
            len(in_library), len(plan.companions))
        report += '\n'.join(
            '  ✓ ' + item + ' (already extracted)' if item in in_library
            else '  • ' + item + ' (will extract on unpack)'
            for item in plan.companions)
        self.info('PAK details: ' + Path(rel).name,
                  f'Description (filename-derived): {archive_description(rel)}\n'
                  f'Archive: {rel}\nStaged size: {size}\nStaged: {staged}\n'
                  f'Installed original: {original or "Unknown"}\n'
                  f'Unpacked workspace: {self._workspace_for_selected_archive() or "Not unpacked"}\n\n'
                  + report)

    def reveal_pak(self, original=True):
        try:
            staged, game = self._pak_locations(self._selected_rel())
            target = game if original else staged
            if target is None or not target.is_file():
                raise FileNotFoundError('Selected PAK file does not exist: ' + str(target))
            if os.name == 'nt':
                subprocess.Popen(['explorer.exe', '/select,', str(target)])
            else:
                subprocess.Popen(['xdg-open', str(target.parent)])
        except Exception as error:
            self.failure(error)

    def copy_pak_path(self, original=True):
        try:
            staged, game = self._pak_locations(self._selected_rel())
            target = game if original else staged
            if target is None:
                raise ValueError('Original game folder is not configured.')
            QApplication.clipboard().setText(str(target))
        except Exception as error:
            self.failure(error)

    def open_unpacked(self):
        try:
            current = self._workspace_for_selected_archive()
        except (ValueError,OSError):
            current = None
        if current is None:
            self.failure('This selected PAK has no matching unpacked project. '
                         'Double-click it to unpack first.')
            return
        target = current/'files'
        if not target.is_dir():
            self.failure('Unpack the selected PAK first.')
        else:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def _pub_priv(self):
        folder = Path(self.stage_path)/'mykeys'
        pub, priv = folder/'public_key.bin', folder/'private_key.pem'
        if not pub.is_file():
            raise FileNotFoundError('Current signing public key is missing: ' + str(pub))
        return pub, priv

    def _selected_stage_pak(self):
        if self.external_pak:
            file = Path(self.external_pak)
            if not file.is_file():
                raise FileNotFoundError('External PAK is missing.')
            return file, None
        rel = self._selected_rel()
        if rel not in self.archive_entries:
            raise ValueError('Select a staged PAK before continuing.')
        pak, _ = self._pak_locations(rel)
        if not pak.is_file():
            raise FileNotFoundError(str(pak))
        return pak, rel

    def _new_folder(self, prefix):
        root = Path(self.projects_path)
        root.mkdir(parents=True, exist_ok=True)
        dest = root/prefix
        counter = 2
        while dest.exists():
            dest = root/(prefix+'_'+str(counter))
            counter += 1
        return dest

    def _workspace(self):
        record = self.selected_project_context()
        candidate = record['workspace'] if record else self.current_workspace
        path = Path(candidate)
        if not candidate or not (path/'.evolve-pak-workspace.json').is_file():
            raise ValueError('Select or create an editable project first.')
        return path

    def _start_batch_with_dependencies(self, selected, primary, auto_level=False):
        """Unpack exactly checked signed PAKs and their recognized companions."""
        from pak_dependencies import plan_dependencies, remaining_companions
        from asset_library import covered_stage_archives
        plans = [plan_dependencies(item, self.archive_entries) for item in selected]
        implicit = tuple(dict.fromkeys(
            name for plan in plans for name in plan.companions))
        covered = covered_stage_archives(implicit)
        union, reused = remaining_companions(
            selected, self.archive_entries, covered)
        if len(union) > 30:
            raise ValueError('Dependency selection exceeds the 30-PAK safe batch limit.')
        if self.filter_input.text().strip():
            raise ValueError('Clear the extraction filter to unpack dependencies completely.')
        if not self.confirm(
                'Extract PAKs with dependencies?',
                '%d signed PAK(s) will be extracted as separate editable '
                'projects (originals unchanged):\n\n%s\n\n'
                '%d dependencies are already fully extracted in the Asset Library '
                'and will be reused as read-only references:\n%s\n\n'
                'Only known PAK families are checked; other dynamic '
                'dependencies may remain. Proceed?' %
                (len(union), '\n'.join('  • ' + item for item in union),
                 len(reused), '\n'.join('  ✓ ' + name for name in reused)
                 or '  None')):
            return False
        pub, _ = self._pub_priv()
        dest = self._new_folder('PAK_Family')
        cmd = [sys.executable, '-u', str(ROOT/'multi_pak_assets.py'),
               '--stage', self.stage_path, '--public-key', str(pub),
               '--destination', str(dest)]
        for rel in union:
            cmd += ['--pak', rel]
        self._batch_primary = primary
        self._batch_open_level = bool(auto_level)
        self.run_steps([('Extracting PAKs and known companions', cmd)],
                       'Dependency batch unpack', lambda: self._after_batch(dest))
        return True

    def extract(self, auto_level=False):
        try:
            pak, rel = self._selected_stage_pak()
            from pak_dependencies import is_level_pak
            # Explicit Unpack on a known level PAK should open the Level
            # Editor when extraction completes, just like double-click.
            auto_level = bool(auto_level or (rel and is_level_pak(rel)))
            if rel in self.archive_entries:
                from pak_dependencies import plan_dependencies
                plan = plan_dependencies(rel, self.archive_entries)
                if plan.companions:
                    from asset_library import covered_stage_archives
                    imported = covered_stage_archives(plan.companions)
                    if any(p not in imported for p in plan.companions):
                        self._start_batch_with_dependencies(
                            [rel], rel, auto_level=auto_level)
                        return
            pub, _ = self._pub_priv()
            folder = self._new_folder(
                (rel or 'External/'+pak.name).replace('/','_').replace('.pak',''))
            cmd = [sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),
                   'extract','--pak',str(pak),'--public-key',str(pub),
                   '--workspace',str(folder),'--skip-unsupported']
            filter_text = self.filter_input.text().strip()
            if filter_text:
                cmd += ['--filter', filter_text]
            if auto_level and filter_text:
                raise ValueError('Clear the extraction filter for a complete level project.')
            self._single_open_level = bool(auto_level)
            self._extraction_timer = (time.perf_counter(), pak.stat().st_size, pak.name)
            self.run_steps([('Unpacking archive',cmd)], 'Unpack archive',
                           lambda: self._after_extract(folder))
        except Exception as exc:
            self.failure(exc)

    def _after_extract(self, folder):
        started, size, archive = getattr(self, '_extraction_timer',
                                         (time.perf_counter(), 0, 'unknown'))
        elapsed = max(.001, time.perf_counter() - started)
        self.settings['last_extraction_benchmark'] = {
            'archive': archive, 'duration_seconds': round(elapsed,3),
            'input_mib': round(size/1048576,3),
            'input_mib_per_second': round(size/1048576/elapsed,3),
            'note': 'Real signed PAK extraction wall-clock time; input archive bytes per second.'}
        self._extraction_timer = None
        self.current_workspace = str(folder)
        self.workspace_input.setText(str(folder))
        self.output_pak = ''
        self.output_input.setText('')
        self.persist()
        open_level = bool(getattr(self, '_single_open_level', False))
        self._single_open_level = False
        if open_level:
            self.open_level_editor(folder)
        else:
            self.open_editor()

    def extract_batch(self):
        try:
            chosen = [s for s in self.selected_archives()
                      if not s.startswith('loose-file:')]
            if not 2 <= len(chosen) <= 30:
                raise ValueError('Select 2–30 PAKs for a batch.')
            self._start_batch_with_dependencies(chosen, chosen[0])
        except Exception as exc:
            self.failure(exc)

    def _after_batch(self, folder):
        data = load_collection(folder)
        self.batch_root = str(folder)
        self._load_batch()
        pending = getattr(self, '_pending_map_members', None)
        self._pending_map_members = None
        if pending:
            from project_catalog import register_project
            source_build, members = pending
            for record in data['archives']:
                relative = record['archive']
                workspace = Path(folder) / record['workspace']
                try:
                    register_project(
                        self.projects_path, workspace, self.stage_path,
                        source_build_id=source_build,
                        archive_id=members.get(relative),
                        label=Path(relative).name)
                except (OSError, ValueError) as exc:
                    self._log('Map project ' + relative + ': ' + str(exc)[:180])
            self.refresh_project_list()
        preferred = getattr(self, '_batch_primary', '')
        archive_map = {row['archive']: Path(folder)/row['workspace']
                       for row in data['archives']}
        if preferred not in archive_map:
            preferred = data['archives'][0]['archive']
        self.archive_label = preferred
        self.current_workspace = str(archive_map[preferred])
        self.workspace_input.setText(self.current_workspace)
        self.persist()
        level = bool(getattr(self, '_batch_open_level', False))
        self._batch_primary = ''
        self._batch_open_level = False
        if level:
            from level_editor_core import LevelProject
            # Prefer editor .lyr data when provided in a companion PAK;
            # the selected runtime level.pak may instead use mission XML.
            available = [item for item in archive_map.items()
                         if LevelProject.contains_levels(item[1])]
            if available:
                with_layers = []
                for name, ws in available:
                    from evolve_pak_workspace import load_workspace
                    manifest = load_workspace(ws)
                    if any(entry['path'].casefold().endswith('.lyr')
                           for entry in manifest['entries']):
                        with_layers.append((name,ws))
                matching = with_layers or available
                from pak_dependencies import level_identity
                intended = level_identity(preferred)
                match = next((ws for name,ws in matching
                              if level_identity(name) == intended),
                             matching[0][1])
                self.open_level_editor(match)
            else:
                self.info('Level extracted, no recognized placement file',
                          'Companion PAKs were extracted, but no supported .lyr or '
                          'mission_*.xml + leveldata.xml files were found. '
                          'Open the extraction projects in Edit Files to inspect them.')
        else:
            self.open_editor()

    def _workspace_for_selected_archive(self):
        from project_selection import workspace_for_archive, workspace_matches
        if self.external_pak:
            return (Path(self.current_workspace).resolve()
                    if workspace_matches(self.current_workspace,self.external_pak)
                    else None)
        rel = self._selected_rel()
        if rel not in self.archive_entries:
            raise ValueError('Select a staged PAK before opening it.')
        return workspace_for_archive(
            self.stage_path, rel, self.current_workspace,
            self.batch_workspaces)

    def open_editor_or_unpack(self):
        try:
            from pak_dependencies import is_level_pak
            picked = [p for p in self.selected_archives()
                      if not p.startswith('loose-file:')]
            if len(picked) == 1 and is_level_pak(picked[0]):
                self.open_level_editor()
                return
            if any(p.startswith('loose-file:') for p in self.selected_archives()):
                self.open_editor()
                return
            matching = self._workspace_for_selected_archive()
            if matching is not None:
                self.current_workspace = str(matching)
                self.workspace_input.setText(self.current_workspace)
                self.persist()
                self.open_editor()
            else:
                # Selected PAK is new (or the prior extraction belongs to
                # another source). Always unpack it into a fresh project.
                self.extract()
        except (ValueError,OSError) as exc:
            self.failure(exc)

    def _editor_collection(self):
        mapping = {}
        if self.batch_root:
            try:
                data = load_collection(Path(self.batch_root))
                if Path(data['stage']).resolve() == Path(self.stage_path).resolve():
                    mapping = {row['archive']: Path(self.batch_root)/row['workspace']
                               for row in data['archives']}
            except (OSError, ValueError, KeyError, TypeError):
                pass
        return mapping

    def flush_editors(self):
        # Active text edits are never silently lost during build, signing,
        # stage installation, bundle export or original-file refresh.
        if hasattr(self, 'dev_memory_panel'):
            self.dev_memory_panel.stop()
        for editor in self.level_editors:
            if editor.isVisible() and editor.project.dirty_layers():
                self.info('Unsaved level edits',
                          'Save changes in the Level Editor or discard them before building a mod.')
                return False
        for editor in self.editors:
            if hasattr(editor, 'isVisible') and editor.isVisible() and hasattr(editor, '_confirm_buffer'):
                if not editor._confirm_buffer():
                    return False
        return True

    def open_level_editor(self, workspace=None):
        """Open a UE-style level project, extracting unprepared level families."""
        try:
            if self.busy:
                raise RuntimeError('Wait for the current extraction or build to finish.')
            from level_editor_core import LevelProject
            from qt_level_editor import UnrealStyleLevelEditor
            from pak_dependencies import is_level_pak
            folder = Path(workspace) if workspace is not None else None
            selected = self._selected_rel() if workspace is None else ''
            if workspace is None:
                try:
                    folder = self._workspace_for_selected_archive()
                except (OSError, ValueError):
                    folder = None
                # Never reuse a workspace from an unrelated selected PAK.
                if folder is None and selected in self.archive_entries and is_level_pak(selected):
                    self.extract(auto_level=True)
                    return
                if folder is not None and not LevelProject.contains_levels(folder):
                    if selected in self.archive_entries and is_level_pak(selected):
                        self.extract(auto_level=True)
                        return
            if folder is None or not LevelProject.contains_levels(folder):
                chosen = QFileDialog.getExistingDirectory(
                    self, 'Select an extracted level PAK project',
                    self.projects_path)
                if not chosen:
                    return
                folder = Path(chosen)
            if not LevelProject.contains_levels(folder):
                raise ValueError(
                    'No supported level placement data found in this project. '
                    'Select a PAK with .lyr layers or mission_*.xml and leveldata.xml.')
            folder = Path(folder).resolve()
            for editor in self.level_editors:
                if editor.isVisible() and editor.project.workspace == folder:
                    editor.raise_()
                    editor.activateWindow()
                    return
            editor = UnrealStyleLevelEditor(self, folder)
            editor.setAttribute(Qt.WA_DeleteOnClose, False)
            self.level_editors.append(editor)
            editor.show()
            editor.raise_()
        except (OSError, ValueError, RuntimeError) as exc:
            self.failure(exc)

    def open_editor(self):
        try:
            if self.busy:
                raise RuntimeError('Wait for the current task to finish.')
            from qt_workspace_editor import QtWorkspaceEditor, QtLooseFileEditor
            loose = next((rel[11:] for rel in self.selected_archives()
                          if rel.startswith('loose-file:')), None)
            if loose:
                game = self.game_root or self._find_game_root()
                if not game:
                    raise ValueError('Installed game location is unknown.')
                editor = QtLooseFileEditor(self, game, loose, self.projects_path)
            else:
                # Verify selection identity even on the explicit Edit Files
                # button, not just on double click.
                ws = self._workspace_for_selected_archive()
                if ws is None:
                    raise ValueError(
                        'The selected PAK has no matching extracted project. '
                        'Double-click it to unpack a fresh project.')
                self.current_workspace = str(ws)
                for editor in self.editors:
                    if (isinstance(editor, QtWorkspaceEditor) and editor.isVisible()
                            and editor.workspace == ws):
                        editor.raise_()
                        editor.activateWindow()
                        return
                from project_selection import editor_collection_for_workspace
                archive_map = editor_collection_for_workspace(
                    ws, self._editor_collection())
                editor = QtWorkspaceEditor(
                    self, ws, archive_map=archive_map)
            editor.setAttribute(Qt.WA_DeleteOnClose, False)
            self.editors.append(editor)
            editor.show()
            editor.raise_()
        except Exception as exc:
            self.failure(exc)

    def diff(self):
        try:
            if not self.flush_editors():
                return
            ws = self._workspace()
            self.run_steps([('Checking edits',[sys.executable,'-u',
                str(ROOT/'evolve_pak_workspace.py'),'diff','--workspace',str(ws)])],
                'Check edits')
        except Exception as exc:
            self.failure(exc)

    def build(self):
        try:
            if not self.flush_editors():
                return
            ws = self._workspace()
            from project_catalog import verified_target
            rel, pak = verified_target(ws, self.stage_path)
            pub, priv = self._pub_priv()
            if not priv.is_file():
                raise FileNotFoundError('Private signing key missing.')
            meta = json.loads((ws/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))
            if Path(meta['source_pak']).resolve() != pak.resolve():
                raise ValueError('Selected archive does not match the extracted workspace.')
            dest = Path(self.projects_path)/'Built'/(rel or pak.name).replace('/','_')/datetime.now().strftime('%Y%m%d_%H%M%S_%f')/pak.name
            dest.parent.mkdir(parents=True, exist_ok=False)
            cmd = [sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),
                   'build','--workspace',str(ws),'--public-key',str(pub),
                   '--private-key',str(priv),'--out',str(dest)]
            self.run_steps([('Rebuilding and signing PAK',cmd)], 'Build Mod',
                           lambda: self._after_build(dest))
        except Exception as exc:
            self.failure(exc)

    def build_offline_test(self):
        """Guided private/offline build, signed stage and isolated game launch.

        Restoration remains explicit until the client confirms game exit;
        never assume the launcher process lifetime is the game lifetime.
        """
        try:
            if self.busy or not self.flush_editors():
                return
            from asset_intelligence import validate_project
            workspace=self._workspace() if self.selected_project_context() else self._workspace_for_selected_archive()
            if workspace is None:
                raise ValueError('Select a valid editable project first.')
            st=self.refresh_swap_status()
            if st.phase not in ('missing','prepared','restored'):
                raise RuntimeError('Restore originals before an offline test.')
            changed,problems=validate_project(workspace)
            if problems:
                raise ValueError('Preflight found problems:\n'+'\n'.join(problems[:8]))
            if not changed:
                raise ValueError('There are no modified assets to test.')
            if self.selected_project_context():
                from project_catalog import verified_target
                relative,pak=verified_target(workspace,self.stage_path)
            else:
                pak,relative=self._selected_stage_pak()
            pub,priv=self._pub_priv()
            if not priv.is_file():
                raise FileNotFoundError('Signing private key not configured.')
            meta=json.loads((workspace/'.evolve-pak-workspace.json').read_text('utf-8'))
            if Path(meta['source_pak']).resolve()!=pak.resolve():
                raise ValueError('Selected PAK does not match this workspace.')
            if not self.confirm('Offline mod test',
                'Validate and build %d edited asset(s), sign the selected PAK, '
                'and stage it before launching in offline mode?\n\n'
                'Restore Game Files remains required after testing. '
                'No public multiplayer traffic will be enabled.'%len(changed)):
                return
            output=(Path(self.projects_path)/'Built'/
                relative.replace('/','_')/
                datetime.now().strftime('%Y%m%d_%H%M%S_%f')/pak.name)
            output.parent.mkdir(parents=True,exist_ok=False)
            cmd=[sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),
                 'build','--workspace',str(workspace),
                 '--public-key',str(pub),'--private-key',str(priv),
                 '--out',str(output)]
            self.run_steps([('Validate and sign offline test PAK',cmd)],
                'Build Offline Test',
                lambda:self._stage_offline_test(output,relative))
        except (ValueError,OSError,RuntimeError) as exc:
            self.failure(exc)

    def _stage_offline_test(self,pak,relative):
        self.output_pak=str(pak)
        self.output_input.setText(self.output_pak)
        self.persist()
        self._stage_install(pak,relative,'Stage Offline Test',
                            self._start_offline_test)

    def _start_offline_test(self):
        self.refresh_archives()
        self.refresh_swap_status()
        if self.confirm('Offline test staged',
            'The signed PAK is staged. Start a private/offline test session now? '
            'Your game will be network-blocked using the existing firewall '
            'safeguard. After testing, return to Play & Restore and restore '
            'your original files.'):
            self._developer_allow_online=False
            self.launch()

    def _after_build(self, result):
        self.output_pak = str(result)
        self.output_input.setText(str(result))
        self.persist()
        self.info('Mod built', 'Signed PAK ready: ' + str(result))

    def _stage_install(self, pak, rel, title, success):
        command = [sys.executable,'-u',str(ROOT/'universal_stage.py'),'install',
                   '--stage',self.stage_path,'--swap-dir',self.swap_path,
                   '--backup-root',str(Path(self.projects_path)/'Backups'),
                   '--relative',rel,'--mod',str(pak)]
        self.run_steps([('Verifying and adding modified PAK',command)], title, success)

    def install(self):
        try:
            if not self.flush_editors():
                return
            ws = self._workspace()
            if self.selected_project_context():
                from project_catalog import verified_target
                rel, pak = verified_target(ws, self.stage_path)
            else:
                pak, rel = self._selected_stage_pak()
            if not rel:
                raise ValueError('Select a verified target archive.')
            modified = Path(self.output_pak)
            if not modified.is_file() or modified.name.casefold() != pak.name.casefold():
                raise ValueError('Build the selected PAK first.')
            meta = json.loads((ws/'.evolve-pak-workspace.json').read_text(encoding='utf-8'))
            if Path(meta['source_pak']).resolve() != pak.resolve():
                raise ValueError('Build belongs to another PAK.')
            if not self.confirm('Add modified PAK?', f'Stage {rel}? Installed originals are not changed; rollback backup retained.'):
                return
            self._stage_install(modified, rel, 'Add PAK', self._after_install)
        except Exception as exc:
            self.failure(exc)

    def _after_install(self):
        self.refresh_swap_status()
        if self.confirm('PAK added', 'Ready to open Play & Restore?'):
            self.open_page(SECTIONS.index('Play & Restore'))

    def import_modified_pak(self):
        try:
            source, _ = QFileDialog.getOpenFileName(self, 'Import Modified PAK',
                                self.projects_path,'PAK (*.pak)')
            if not source:
                return
            pak = Path(source).resolve()
            self.archive_entries = sorted(allowed_paks(Path(self.stage_path)).values(), key=str.casefold)
            relative = import_target(pak.name, self.archive_entries, self._selected_rel())
            stage, _ = self._pak_locations(relative)
            if pak == stage.resolve():
                raise ValueError('Select a separate modified PAK, not the staged original.')
            if not self.confirm('Import modified PAK?', f'Verify signature/structure and stage {relative}? The installed game is unchanged.'):
                return
            self.archive_label = relative
            self._stage_install(pak, relative, 'Import Modified PAK',
                                self._after_import)
        except Exception as exc:
            self.failure(exc)

    def _after_import(self):
        self.current_workspace = ''
        self.output_pak = ''
        self.workspace_input.clear()
        self.output_input.clear()
        self.persist()
        self.refresh_archives()
        self._after_install()

    def rollback(self):
        try:
            root = Path(self.projects_path)/'Backups'
            folder = QFileDialog.getExistingDirectory(self,'Select backup containing metadata.json',
                                                     str(root))
            if not folder:
                return
            path = Path(folder)/'metadata.json'
            if not path.is_file():
                raise FileNotFoundError('Select a dated backup containing metadata.json.')
            meta = json.loads(path.read_text(encoding='utf-8'))
            if not self.confirm('Undo staged mod?', f'Restore previous signed {meta["relative"]}? Installed game files remain untouched.'):
                return
            self.run_steps([('Undoing staged PAK',[sys.executable,'-u',
                str(ROOT/'universal_stage.py'),'restore','--backup',folder])],
                'Undo staged mod', self.refresh_swap_status)
        except Exception as exc:
            self.failure(exc)

    def refresh_swap_status(self):
        st = state_status(Path(self.swap_path))
        try:
            game = Path(st.game) if st.game and Path(st.game).is_dir() else self.game_root
            if game and st.phase in ('missing','prepared','restored') and (plan_recovery(game) or inspect_injector(game).blocks_normal_launch):
                from launch_integration import SwapStatus
                st = SwapStatus('unsafe',
                                'Original game backups exist despite clean swap journal.')
                self.launch_status.setText('Original game or injector backup remains. Restore before launching.')
                return st
        except (OSError, ValueError):
            pass
        labels = {
            'missing':'Launcher not prepared yet.', 'restored':'Originals restored.',
            'prepared':'Mod files ready to launch.','swapped':'Mods installed — restore before normal play.',
            'swapping':'Swap interrupted: restoration needed.',
            'restoring':'Restoration in progress.','interrupted':'Restore original files.',
            'unsafe':'Unsafe state — inspect and restore originals.',
        }
        self.launch_status.setText(labels.get(st.phase, labels['unsafe']))
        if hasattr(self, 'preparation_status'):
            if st.phase == 'prepared':
                self.preparation_status.setText(
                    f'Ready for fast launch: {st.ready}/{st.count} prepared files are cached.')
            elif st.phase == 'restored':
                self.preparation_status.setText(
                    f'{st.ready}/{st.count} prepared files available. '
                    'Prepare ahead if files need rebuilding.')
            elif st.phase == 'missing':
                self.preparation_status.setText(
                    'Not prepared yet. Complete setup before the first launch.')
            else:
                self.preparation_status.setText(
                    'Restore or resolve the active swap before preparing more files.')
        return st

    def status_details(self):
        st = state_status(Path(self.swap_path))
        self.info('Swap status', st.message +
                  (f'\n\nPrepared files: {st.ready}/{st.count}' if st.count else ''))

    def setup_launcher(self):
        try:
            swap = Path(self.swap_path)
            if not swap.is_dir():
                raise FileNotFoundError('Launch helper folder is missing.')
            added = add_missing_scripts(swap)
            self.info('Launcher helper', 'Installed missing helpers: ' +
                      (', '.join(added) if added else 'None'))
        except Exception as exc:
            self.failure(exc)

    def launch(self):
        try:
            if self.busy:
                raise RuntimeError('Another task is running.')
            st = self.refresh_swap_status()
            if st.phase not in ('missing','prepared','restored'):
                raise RuntimeError('Restore the original game files before launching mods.')
            if st.stage and Path(st.stage).resolve() != Path(self.stage_path).resolve():
                raise ValueError('Swap state belongs to another stage.')
            commands = launcher_commands(Path(self.swap_path),Path(self.stage_path),st.phase)
            if self.confirm('Play with Mods?',
                             'Mods are intended for private/offline testing.\nClose game/launcher first, then wait for the launcher-ready message before pressing Play.'):
                set_offline(self._developer_game(), not self._developer_allow_online)
                self.run_steps(commands,'Play with Mods',self.refresh_swap_status,
                               cwd=Path(self.swap_path))
        except Exception as exc:
            self.failure(exc)

    def restore(self):
        try:
            if self.busy:
                raise RuntimeError('Wait for the current task to finish.')
            st = self.refresh_swap_status()
            game = Path(st.game) if st.game and Path(st.game).is_dir() else (
                self.game_root or self._find_game_root())
            if not game:
                choice = QFileDialog.getExistingDirectory(
                    self,'Choose EvolveGame to inspect originals')
                if not choice:
                    return
                game = Path(choice)
            pending = plan_recovery(game)
            if pending and st.phase not in ('swapped','swapping','restoring','interrupted'):
                if not self.confirm('Recover original game files?',
                                    f'Found {len(pending)} backups. Preserve modded versions and restore originals with a recovery manifest? Close Evolve and launcher first.'):
                    return
                command = [sys.executable,'-u',str(ROOT/'orphaned_swap_recovery.py'),
                           '--game',str(game),'--manifest-dir',
                           str(DATA_HOME/'RecoveryLogs'),'--apply']
                self.run_steps([('Restoring orphaned originals',command)],
                               'Restore originals',self._restore_network_after_game)
                return
            if not pending and st.phase in ('missing','prepared','restored'):
                self.info('Nothing to restore', 'No original backups remain in the selected game.')
                return
            if not pending and st.phase == 'unsafe':
                raise RuntimeError('Journal status is unsafe and no backups are visible; inspect before continuing.')
            if self.confirm('Restore original game files?', 'Close Evolve and client before returning modded PAKs to originals.'):
                self.run_steps([('Restoring original game files',
                               restore_command(Path(self.swap_path)))],
                               'Restore originals',self._restore_network_after_game,
                               cwd=Path(self.swap_path))
        except Exception as exc:
            self.failure(exc)

    def initial_setup(self):
        try:
            if not self.flush_editors():
                return
            if self.busy:
                raise RuntimeError('Wait for current operation.')
            if (APP_HOME/MARKER).is_file():
                self.configure_bundle()
                return
            current = self.refresh_swap_status()
            if current.phase not in ('missing','restored','prepared'):
                raise RuntimeError('Restore original game files before creating a new setup.')
            game = QFileDialog.getExistingDirectory(self,'Step 1: Choose installed EvolveGame folder')
            if not game:
                return
            launcher, _ = QFileDialog.getOpenFileName(self,'Step 2: Choose normal Modded Evolve client EXE','','EXE (*.exe)')
            if not launcher:
                return
            pending = scan_prepared(Path(game))
            if pending['backups'] or pending['partial']:
                raise RuntimeError('Unfinished original-file backups exist. Use Restore Game Files first.')
            preserve = True
            if pending['ready']:
                if not self.confirm('Existing prepared files',
                                    f'{len(pending["ready"])} earlier prepared files exist. Preserve them?'):
                    return
            storage = self.confirm_storage('Original PAK copies',
                               'Choose setup storage mode. The live launch still protects original game files.')
            if storage is None:
                return
            dest = DATA_HOME/'GameSets'/('Setup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not self.confirm('Confirm setup',
                f'Close game and launcher. Verify PAKs and prepare a private signed set in:\n{dest}\n\nContinue?'):
                return
            cmd = [sys.executable,'-u',str(ROOT/'first_run_setup.py'),
                   '--game',game,'--launcher',launcher,
                   '--destination',str(dest),'--seed-parent',str(DATA_HOME/'SetupSeeds')]
            if pending['ready'] and preserve:
                cmd.append('--preserve-old-prepared')
            if not storage:
                cmd.append('--skip-original-snapshots')
            self.run_steps([('Creating local mod setup',cmd)],'Set up from game',
                           lambda: self._after_setup(dest))
        except Exception as exc:
            self.failure(exc)

    def _after_setup(self, folder):
        result = json.loads((folder/'refresh_report.json').read_text(encoding='utf-8'))
        if result.get('status') != 'complete':
            raise RuntimeError('Setup did not complete; settings unchanged.')
        self.stage_path = result['stage']
        self.swap_path = result['swap']
        self.fields['stage'].setText(self.stage_path)
        self.fields['swap'].setText(self.swap_path)
        self.persist()
        self.refresh_archives()
        self.info('Setup finished', 'Your local mod signing setup is ready.')

    def refresh_originals(self):
        try:
            if not self.flush_editors():
                return
            if self.refresh_swap_status().phase not in ('missing','prepared','restored'):
                raise RuntimeError('Restore installed originals before refreshing signed PAKs.')
            game = QFileDialog.getExistingDirectory(self,'Select updated installed EvolveGame')
            if not game:
                return
            folder = QFileDialog.getExistingDirectory(self,'Choose new setup storage folder',
                                                       str(DATA_HOME/'GameSets'))
            if not folder:
                return
            keep = self.confirm_storage('Keep original snapshots?',
                                        'Choose whether the refreshed setup stores original PAK snapshots.')
            if keep is None:
                return
            dest = Path(folder)/('EvolveRefresh_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
            if not self.confirm('Refresh Original PAKs?',
                                'Keep old projects and backups. New signed PAKs must be rebuilt after normal client verification. Continue?'):
                return
            cmd = [sys.executable,'-u',str(ROOT/'refresh_originals.py'),
                   '--game',game,'--stage',self.stage_path,'--swap',self.swap_path,
                   '--destination',str(dest)]
            if not keep:
                cmd.append('--skip-original-snapshots')
            self.run_steps([('Refreshing originals',cmd)],'Refresh Original PAKs',
                           lambda: self._after_setup(dest))
        except Exception as exc:
            self.failure(exc)

    def configure_bundle(self):
        try:
            marker = APP_HOME/MARKER
            if not marker.is_file():
                self.info('Bundle setup', 'There is no complete exported bundle here.')
                return
            record = json.loads(marker.read_text(encoding='utf-8'))
            if record.get('configured'):
                self.info('Bundle setup', 'Already configured for: ' + record.get('game',''))
                return
            game = QFileDialog.getExistingDirectory(self,'Select installed EvolveGame')
            if not game:
                return
            exe,_ = QFileDialog.getOpenFileName(self,'Select normal Modded Evolve client EXE','','EXE (*.exe)')
            if not exe:
                return
            cmd = [sys.executable,'-u',str(ROOT/'portable_bundle.py'),
                   'configure','--home',str(APP_HOME),'--game',game,'--launcher',exe]
            self.run_steps([('Configure full portable bundle',cmd)],
                           'Configure complete bundle',self._after_bundle)
        except Exception as exc:
            self.failure(exc)

    def _after_bundle(self):
        settings = json.loads(settings_source().read_text(encoding='utf-8'))
        self.stage_path = settings.get('stage',self.stage_path)
        self.swap_path = settings.get('swap',self.swap_path)
        self.projects_path = settings.get('projects',self.projects_path)
        for key in ('stage','swap','projects'):
            self.fields[key].setText(getattr(self,key+'_path'))
        self.refresh_archives()
        self.refresh_swap_status()
        self.info('Bundle ready', 'Configured your existing exported complete setup.')

    def export_bundle(self):
        try:
            if not self.flush_editors():
                return
            if not getattr(sys,'frozen',False):
                raise RuntimeError('Use the packaged EXE to export the complete manager with runtime.')
            target,_ = QFileDialog.getSaveFileName(self,'Export complete manager ZIP',
                                  str(APP_HOME/'EvolveModManager-Complete.zip'),'ZIP (*.zip)')
            if not target:
                return
            if not self.confirm('Export complete manager?',
                                'Private setup export can contain signing keys. Only share with trusted recipients.'):
                return
            cmd = [sys.executable,'-u',str(ROOT/'portable_bundle.py'),
                   'export','--home',str(APP_HOME),
                   '--stage',self.stage_path,'--swap',self.swap_path,
                   '--projects',self.projects_path,'--output',target]
            self.run_steps([('Exporting complete manager bundle',cmd)],
                           'Export complete ZIP',
                           lambda: self.info('Export complete',str(target)))
        except Exception as exc:
            self.failure(exc)

    def check_setup(self):
        notes = package_status(Path(self.stage_path),Path(self.swap_path))
        st = self.refresh_swap_status()
        self.info('Setup check', ('Setup looks ready.' if not notes else
                  'Things requiring attention:\n' + '\n'.join('• '+x for x in notes)) +
                  '\n\n' + st.message)

    def open_project_dashboard(self):
        try:
            from qt_workflow_lab import ProjectDashboard
            ProjectDashboard(self).exec()
        except (ValueError,OSError) as exc:
            self.failure(exc)

    def open_task_manager(self):
        from qt_task_queue import TaskQueueDialog
        TaskQueueDialog(self).exec()

    def enqueue_task_action(self,name):
        allowed={'Review Changes':self.diff, 'Unpack PAK':self.extract,
                 'Build Mod':self.build}
        if name not in allowed:
            raise ValueError('Unsupported task category.')
        archive=self._selected_rel()
        if not archive or archive not in self.archive_entries:
            raise ValueError('Select a signed PAK before queuing work.')
        if len(self.task_queue)>=24:
            raise ValueError('Task queue is full.')
        self.task_queue.append({'name':name,'archive':archive,
                                'workspace':str(self.current_workspace)})
        if not self.busy:
            QTimer.singleShot(10,self._drain_tasks)

    def _drain_tasks(self):
        if self.busy or not self.task_queue:
            return
        task=self.task_queue[0]
        if (self._selected_rel()!=task['archive'] or
                str(self.current_workspace)!=task['workspace']):
            self.status.setText('Queue paused: select %s and its original '
                                'workspace, then reopen Tasks.'%task['archive'])
            return
        from project_selection import workspace_for_archive
        if task['name']!='Unpack PAK' and workspace_for_archive(
                self.stage_path,task['archive'],task['workspace'],
                self.batch_workspaces) is None:
            self.status.setText('Queue paused: PAK has no matching extracted project.')
            return
        self.task_queue.pop(0)
        self._queued_task_running = task
        {'Review Changes':self.diff,'Unpack PAK':self.extract,
         'Build Mod':self.build}[task['name']]()
        if not self.busy:
            self._queued_task_running = None

    def open_project_cleanup(self):
        if self.busy:
            self.info('Projects cleanup','Finish the current manager task first.')
            return
        try:
            from qt_asset_intelligence import ProjectCleanupDialog
            ProjectCleanupDialog(self).exec()
        except (ValueError,OSError) as exc:
            self.failure(exc)

    def open_pak_diff(self):
        try:
            from qt_asset_intelligence import PakDiffDialog
            PakDiffDialog(self).exec()
        except (ValueError,OSError) as exc:
            self.failure(exc)

    def open_projects(self):
        try:
            root = Path(self.projects_path)
            root.mkdir(parents=True,exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(root)))
        except Exception as exc:
            self.failure(exc)

    def create_shortcut(self):
        try:
            # Preview never changes the user's production shortcut target:
            # desktop links must keep pointing to the verified stable EXE.
            stable_exe = APP_HOME / 'EvolveModManager.exe'
            if not stable_exe.is_file():
                raise RuntimeError('Desktop shortcuts remain available from the stable release. This Qt preview does not replace your main executable.')
            link = create_desktop_shortcut(executable=stable_exe)
            self.info('Desktop shortcut ready','Created: ' + str(link))
        except Exception as exc:
            self.failure(exc)

    def check_updates(self, silent=False):
        """Compare Stable and Beta; offer one strictly newer verified release."""
        if self.busy or self.update_running:
            if not silent:
                self.info('Updates', 'Finish the current operation before updating.')
            return
        if (self.build_channel() not in ('main','beta') or
                not (APP_HOME/'EvolveModManager.exe').is_file()):
            self.update_status.setText('Updates require the packaged Windows release.')
            if not silent:
                self.info('Updates', 'Build the packaged Windows application to use verified updates.')
            return
        self.update_running = True
        self.update_status.setText('Checking Stable and Beta versions…')
        self.update_button.setEnabled(False)

        def background():
            candidate=None
            error=''
            try:
                candidate=discover_latest(installed_commit(APP_HOME),app_version().lstrip('v').split(' · Patch ', 1)[0], installed_revision(APP_HOME))
            except Exception as exc:
                error=str(exc)
            self.signals.update_checked.emit((candidate,None),error,silent)
        threading.Thread(target=background,daemon=True).start()

    def _on_update_checked(self, choices, error, silent):
        self.update_running = False
        self.update_button.setEnabled(True)
        candidate, _unused = choices
        self.available_update = candidate
        if error:
            self.update_status.setText('Could not check release channels')
            if not silent:
                QMessageBox.warning(self,'Updates','Could not check verified releases:\n'+error)
        elif candidate is None:
            self.update_status.setText('Newest published version already installed')
            if not silent:
                self.info('Updates','No newer Stable or Beta release is available.')
        else:
            title='Beta' if candidate.channel=='beta' else 'Stable'
            published = (candidate.version + (' · Patch ' + str(candidate.revision)
                         if candidate.revision else ''))
            self.update_status.setText(title+' update: '+published)
            note=('\n\nThis is an experimental Beta. It may contain unfinished features.'
                  if candidate.channel=='beta' else '')
            if self.confirm(title+' update available',
                            published+note+
                            '\n\nDownload, verify, install and restart? '
                            'Personal Data, keys, game files and projects remain unchanged.'):
                self._install_stable_update(candidate)

    def _install_stable_update(self,candidate):
        if self.busy or self.update_running or not self.flush_editors():
            return
        self.update_running = True
        self.update_button.setEnabled(False)
        self.update_progress_dialog = QProgressDialog(
            'Downloading verified update…', '', 0, 100, self)
        self.update_progress_dialog.setWindowTitle('Updating Evolve Mod Manager')
        self.update_progress_dialog.setCancelButton(None)
        self.update_progress_dialog.setValue(0)
        self.update_progress_dialog.setMinimumDuration(0)
        self.update_progress_dialog.show()
        def progress(stage,current,total):
            self.signals.progress.emit(stage,int(current),int(total))
        def work():
            try:
                staged = download_and_prepare(candidate,progress=progress,app_home=APP_HOME)
                self.signals.update_staged.emit(candidate,staged,'')
            except Exception as exc:
                self.signals.update_staged.emit(candidate,None,str(exc))
        threading.Thread(target=work,daemon=True).start()

    def _update_progress(self,stage,current,total):
        popup = self.update_progress_dialog
        if popup is None:
            return
        fraction = max(0.0,min(1.0,current/max(1,total)))
        if stage == 'Downloading':
            percent = round(65*fraction)
        elif stage == 'Verifying':
            percent = 68
        elif stage == 'Extracting':
            percent = 70+round(29*fraction)
        else:
            percent = 100
        popup.setLabelText(stage+'…')
        popup.setValue(percent)

    def _on_update_staged(self,candidate,staged,error):
        self.update_running = False
        self.update_button.setEnabled(True)
        if self.update_progress_dialog is not None:
            self.update_progress_dialog.close()
            self.update_progress_dialog = None
        if error:
            self.update_status.setText('Verified update download failed')
            QMessageBox.critical(self,'Update failed',
                error+'\n\nCurrent installation and Data folder were not changed.')
            return
        if self.busy or not self.flush_editors():
            self.info('Update postponed',
                      'Save edits and finish current tasks before applying the update.')
            return
        try:
            self.persist()
            launch_apply(candidate,staged,APP_HOME,os.getpid())
        except Exception as exc:
            self.failure(exc)
            return
        self.status.setText('Installer started; restarting the manager…')
        QApplication.instance().quit()

    def closeEvent(self, event):
        from qt_background_exports import exports_busy
        if exports_busy(self):
            self.info('Model export running',
                      'Wait for file export to finish or cancel it before closing.')
            event.ignore()
            return
        if self.asset_library_controller.busy:
            self.info('Asset Library import running',
                      'Finish or cancel extracted PAK import before closing the manager.')
            event.ignore()
            return
        if self.busy or self.update_running:
            self.info('Still running', 'Finish the current operation before closing; interrupted swaps are unsafe.')
            event.ignore()
            return
        if any(exports_busy(editor) for editor in
               list(self.editors) + list(self.library_previews)):
            self.info('Export still running',
                      'Finish or cancel the model export before closing the manager.')
            event.ignore()
            return
        if not self.flush_editors():
            event.ignore()
            return
        if any(
                viewer.isVisible()
                and getattr(viewer, '_model_pool', None) is not None
                and viewer._model_pool.activeThreadCount()
                for viewer in self.library_previews):
            self.info('Asset Library preview busy',
                      'Wait until an active library preview or export completes before closing.')
            event.ignore()
            return
        try:
            self.persist()
        except OSError:
            pass
        for editor in self.editors:
            if hasattr(editor, 'isVisible') and editor.isVisible():
                editor.close()
        for viewer in self.library_previews:
            if viewer.isVisible():
                viewer.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    capture_mode = next((flag for flag in
        ('--capture-settings-screenshot','--capture-library-screenshot',
         '--capture-play-screenshot','--capture-modding-screenshot','--capture-screenshot')
        if flag in sys.argv),None)
    family = ensure_readable_ui_font(app, strict=bool(capture_mode))
    if capture_mode:
        if not glyphs_readable(app.font()):
            raise RuntimeError('Qt screenshot would have unreadable font glyphs.')
        print('Real Qt font rasterization verified:', family, flush=True)
    main_window = QtManager()
    main_window.show()
    if capture_mode:
        capture_sections = {
            '--capture-modding-screenshot': 'Modding',
            '--capture-settings-screenshot': 'Settings',
            '--capture-library-screenshot': 'Asset Library',
            '--capture-play-screenshot': 'Play & Restore',
        }
        if capture_mode in capture_sections:
            main_window.open_page(SECTIONS.index(capture_sections[capture_mode]))
        try:
            destination = Path(sys.argv[sys.argv.index(capture_mode) + 1])
        except (IndexError, ValueError):
            raise SystemExit(capture_mode + ' requires a PNG path')
        def capture():
            destination.parent.mkdir(parents=True, exist_ok=True)
            image = main_window.grab()  # the live Qt Windows-rendered widget
            if image.isNull() or not image.save(str(destination), 'PNG'):
                raise RuntimeError('Qt could not capture its actual window.')
            app.quit()
        QTimer.singleShot(1800, capture)
    return app.exec()


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except BaseException:
        if _QT_STARTUP_LOG is not None:
            _QT_STARTUP_LOG.write(traceback.format_exc() + '\n')
        raise
