"""Experimental PySide6 Evolve Mod Manager frontend.

Do not publish this as the default updater channel until full editor parity is
confirmed. The stable Tk application remains the production release.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import webbrowser
import traceback

# PyInstaller's windowed bootloader does not provide sys.stdout/stderr.
# Third-party imports or Windows native callbacks may still write there.
# Record preview errors privately rather than exiting without diagnostics.
_QT_STARTUP_LOG = None
if os.name == 'nt' and getattr(sys, 'frozen', False):
    try:
        _QT_LOG_DIR = Path(os.environ.get('LOCALAPPDATA') or os.environ.get('TEMP') or '.') / 'EvolveModManagerQtPreview'
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


from PySide6.QtCore import Qt, QEasingCurve, QPropertyAnimation, QRect, Signal, QObject, QTimer, QProcess, QUrl
from PySide6.QtGui import QAction, QIcon, QPixmap, QFont, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QFrame, QLabel, QVBoxLayout, QHBoxLayout,
    QStackedWidget, QScrollArea, QPushButton, QLineEdit, QComboBox, QTableWidget,
    QTableWidgetItem, QHeaderView, QSplitter, QPlainTextEdit, QMessageBox,
    QFileDialog, QDialog, QDialogButtonBox, QFormLayout, QProgressDialog,
    QCheckBox, QMenu, QAbstractItemView, QSizePolicy, QProgressBar,
)

from app_runtime import APP_HOME, DATA_HOME, SETTINGS_FILE, settings_source, save_settings, worker_command
from app_updater import discover, download_and_prepare, launch_apply, installed_commit
from version_info import app_version
from pak_browser import archive_description, describe_archive, matching_archives, format_size, import_target
from universal_stage import allowed_paks
from launch_integration import state_status, launcher_commands, restore_command, package_status, add_missing_scripts
from orphaned_swap_recovery import plan_recovery
from multi_pak_assets import load_collection
from old_prepared import scan_prepared
from desktop_shortcut import create_desktop_shortcut
from portable_bundle import MARKER

ROOT = Path(__file__).resolve().parent
COLORS = {'bg':'#0b0b0d','surface':'#17171d','panel':'#202027',
          'text':'#f5f3f6','muted':'#a6a3ae','accent':'#cf394b','outline':'#353139'}
SECTIONS = ('Instructions','Modding','Play & Restore','Settings','Credits')


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
    b.clicked.connect(callback)
    return b


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
    lay.setContentsMargins(20, 16, 20, 18)
    lay.setSpacing(10)
    if title:
        heading = _label(title)
        heading.setObjectName('cardTitle')
        lay.addWidget(heading)
    if note:
        lay.addWidget(_label(note, muted=True))
    return frame, lay


def _scroll_page():
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    wrapper = QWidget()
    layout = QVBoxLayout(wrapper)
    layout.setContentsMargins(14, 14, 14, 18)
    layout.setSpacing(14)
    scroll.setWidget(wrapper)
    return scroll, layout


def qt_style():
    return """
    QMainWindow, QWidget { background:#0b0b0d; color:#f5f3f6;
        font-family:'Segoe UI'; font-size:10pt; }
    QFrame#card { background:#17171d; border:1px solid #353139; border-radius:16px; }
    QFrame#drawer { background:#16151b; border-right:1px solid #45323a; }
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
    QPushButton#nav { background:transparent; text-align:left; color:#d9d5dd;
         border:none; border-radius:9px; padding:9px 12px; }
    QPushButton#nav:hover { background:#35262f; }
    QPushButton#navActive { background:#682734; color:white; border:none;
         border-radius:9px; text-align:left; padding:9px 12px; }
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
    QMenu { background:#1e1c23; color:#eee; border:1px solid #403943; padding:5px; }
    QMenu::item { padding:7px 18px; border-radius:7px; }
    QMenu::item:selected { background:#702c3c; }
    """


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
        self.setWindowTitle('Evolve Mod Manager — Qt Preview')
        icon = ROOT / 'assets' / 'hunt.ico'
        if icon.is_file():
            self.setWindowIcon(QIcon(str(icon)))
        self.resize(1150, 785)
        self.setMinimumSize(880, 620)
        self.setStyleSheet(qt_style())

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
        self.external_pak = ''
        self.game_root = None
        self.archive_entries = []
        self.batch_workspaces = {}
        self.busy = False
        self.process = None
        self.editors = []
        self.available_update = None
        self.update_running = False
        self.signals = TaskSignals()
        self.signals.completed.connect(self._task_finished)
        self.signals.output.connect(self._log)
        self._success = None
        self._drawer_open = False

        base = QWidget()
        self.setCentralWidget(base)
        body = QVBoxLayout(base)
        body.setContentsMargins(20, 14, 20, 12)
        body.setSpacing(12)

        top = QHBoxLayout()
        self.menu_button = _button('☰  Menu', self.toggle_menu, primary=True)
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
        navlay.addWidget(_label('SECTIONS', muted=True))
        self.nav_buttons = []
        for index, title in enumerate(SECTIONS):
            btn = _button(title, lambda _checked=False, idx=index: self.open_page(idx))
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
        self._modding_page()
        self._play_page()
        self._settings_page()
        self._credits_page()
        self.stack.setCurrentIndex(0)
        self._log_dialog = None
        self.log_lines = ['Qt preview started. No installed game files were changed.']
        self.status = _label('Ready — read Instructions before connecting an installed game.', muted=True)
        footer = QHBoxLayout()
        footer.addWidget(self.status, 1)
        self.version_label = _label(app_version())
        self.version_label.setObjectName('version')
        footer.addWidget(self.version_label)
        body.addLayout(footer)
        self._load_batch()
        self.refresh_archives()
        self.refresh_swap_status()
        QTimer.singleShot(0, self._position_drawer)

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

    def open_page(self, index):
        self.stack.setCurrentIndex(index)
        for i, button in enumerate(self.nav_buttons):
            button.setObjectName('navActive' if i == index else 'nav')
            button.style().unpolish(button)
            button.style().polish(button)
        self.toggle_menu(False)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape and self._drawer_open:
            self.toggle_menu(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def _instructions_page(self):
        scroll, lay = _scroll_page()
        heading = _label('WELCOME TO EVOLVE MOD MANAGER')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        lay.addWidget(_label('Install Stage 2 through the normal Modded Evolve client, then configure your modding workspace.', muted=True))
        card, row = _card('STEP 01 / INSTALL EVOLVE STAGE 2',
            'The manager does not include or download Evolve. Install the game from modded-evolve.com before selecting any folders. The Steam/Legacy game is not a replacement.')
        lay.addWidget(card)
        buttons = QWidget()
        buttons.setLayout(_row(_button('Get Evolve Stage 2 ↗',
                            lambda: webbrowser.open('https://modded-evolve.com/'), primary=True),
                               _button('Set Up Manager', self.initial_setup)))
        row.addWidget(buttons)
        for title, content in (
            ('02 / CONNECT YOUR GAME', 'Close Evolve and the client; choose the installed EvolveGame folder and the normal Modded Evolve launcher. Setup generates private signing resources.'),
            ('03 / UNPACK AND EDIT', 'Search PAK names or readable descriptions; inspect, unpack, edit XML/DDS/models, review changes, build and add mods.'),
            ('04 / PLAY OFFLINE', 'Use Play & Restore to prepare modded files and launch. Modified files are intended for private/offline testing.'),
            ('05 / RESTORE ORIGINALS', 'Close Evolve and its launcher. Restore original PAKs, or use backup recovery if a prior swap lost its journal.'),
        ):
            c, l = _card(title, content)
            lay.addWidget(c)
        lay.addWidget(_label('Keep the entire portable folder together. Never discard Data, signing keys, projects, original backups or recovery manifests.', muted=True))
        lay.addWidget(_button('Go to Modding →', lambda: self.open_page(1)))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['instructions'] = scroll

    def _modding_page(self):
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)
        title = _label('Modding')
        title.setObjectName('sectionTitle')
        root.addWidget(title)
        root.addWidget(_label('Browse signed PAKs and project copies. Readable names are inferred from filenames; signed paths are unchanged.', muted=True))
        actions = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText('Search PAK filename or description…')
        self.search.textChanged.connect(self.refresh_table)
        actions.addWidget(self.search, 1)
        self.file_filter = QComboBox()
        self.file_filter.addItems(('All files', 'PAK archives', 'Game files'))
        self.file_filter.currentTextChanged.connect(self.refresh_table)
        actions.addWidget(self.file_filter)
        actions.addWidget(_button('Refresh', self.refresh_archives))
        actions.addWidget(_button('External PAK…', self.browse_external_pak))
        root.addLayout(actions)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(('Folder', 'Description', 'File', 'Type / Project', 'Size'))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setColumnWidth(0, 170)
        self.table.itemSelectionChanged.connect(self._select_archive)
        self.table.itemDoubleClicked.connect(lambda _: self.open_editor_or_unpack())
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.archive_context_menu)
        root.addWidget(self.table, 1)
        self.archive_count = _label('No PAKs loaded', muted=True)
        root.addWidget(self.archive_count)
        for group in (
            (('Unpack PAK', self.extract, False),
             ('Unpack Selected PAKs', self.extract_batch, False),
             ('Edit Files', self.open_editor, True),
             ('Review Changes', self.diff, False)),
            (('Build Mod', self.build, True),
             ('Add Mod', self.install, False),
             ('Import Modified PAK', self.import_modified_pak, False),
             ('Undo Staged Mod', self.rollback, False)),
        ):
            bar = QHBoxLayout()
            for title, func, primary in group:
                bar.addWidget(_button(title, func, primary=primary), 1)
            root.addLayout(bar)
        self.stack.addWidget(page)
        self.pages['modding'] = page

    def _play_page(self):
        scroll, lay = _scroll_page()
        heading = _label('Play & Restore')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        lay.addWidget(_label('Offline mod testing. The live game files must be restored before normal multiplayer.', muted=True))
        card, panel = _card('MOD LAUNCH STATUS', 'Launcher and restoration status are read from the real swap journal and original backups.')
        lay.addWidget(card)
        self.launch_status = _label('Checking current game state…')
        panel.addWidget(self.launch_status)
        box = QWidget()
        box.setLayout(_row(_button('Play With Mods', self.launch, primary=True),
                           _button('Restore Game Files', self.restore)))
        panel.addWidget(box)
        other = QWidget()
        other.setLayout(_row(_button('Refresh Status', self.refresh_swap_status),
                             _button('Status Details', self.status_details),
                             _button('Set Up Launcher', self.setup_launcher),
                             _button('Refresh Original PAKs', self.refresh_originals)))
        panel.addWidget(other)
        lay.addWidget(_button('How Play & Restore works', lambda: self.show_help(2)))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['play'] = scroll

    def _settings_page(self):
        scroll, lay = _scroll_page()
        heading = _label('Settings')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        card, inside = _card('FOLDERS', 'Paths are stored inside your local Data/Settings folder. They are not moved when updated.')
        lay.addWidget(card)
        self.fields = {}
        for label, name in (('Signing stage', 'stage'), ('Launch/swap folder', 'swap'), ('Projects', 'projects')):
            line = QHBoxLayout()
            line.addWidget(_label(label))
            inp = QLineEdit()
            inp.setText(getattr(self, name + '_path'))
            self.fields[name] = inp
            line.addWidget(inp, 1)
            line.addWidget(_button('Browse…', lambda _=False, k=name: self.browse_folder(k)))
            inside.addLayout(line)
        btns = QWidget()
        btns.setLayout(_row(_button('Set Up Manager', self.initial_setup, primary=True),
                             _button('Check Setup', self.check_setup),
                             _button('Open Projects', self.open_projects),
                             _button('Save Settings', self.save_settings)))
        inside.addWidget(btns)
        update_card, update_row = _card('UPDATES', 'Main-channel builds check GitHub for a verified release; installation is optional.')
        lay.addWidget(update_card)
        self.update_status = _label('Updates: not checked', muted=True)
        update_row.addWidget(self.update_status)
        self.update_button = _button('Check for Updates', self.check_updates)
        update_row.addWidget(self.update_button)
        shortcut_card, shortcut_row = _card('DESKTOP SHORTCUT',
                                            'Create a Windows shortcut for this portable manager using the matching red-and-black icon.')
        lay.addWidget(shortcut_card)
        shortcut_row.addWidget(_button('Create Desktop Shortcut', self.create_shortcut))
        advanced, advanced_row = _card('ADVANCED OPTIONS', 'All advanced features from the Tk interface remain available here.')
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
                                _button('Settings Help', lambda: self.show_help(3))))
        advanced_row.addWidget(controls)
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['settings'] = scroll

    def _credits_page(self):
        scroll, lay = _scroll_page()
        heading = _label('Credits')
        heading.setObjectName('sectionTitle')
        lay.addWidget(heading)
        card, panel = _card('EVOLVE MOD MANAGER', 'Project by @CorvoDL. A portable modding utility for the restored Evolve Stage 2 client.')
        lay.addWidget(card)
        panel.addWidget(_button('Project on GitHub ↗',
                         lambda: webbrowser.open('https://github.com/corvodl/Modded-Evolve-Mod-Manager')))
        panel.addWidget(_button('Required Modded Evolve client ↗',
                         lambda: webbrowser.open('https://modded-evolve.com/')))
        panel.addWidget(_label('Qt migration preview: the stable Tkinter release remains the primary executable until editor feature parity is verified.', muted=True))
        lay.addStretch()
        self.stack.addWidget(scroll)
        self.pages['credits'] = scroll

    def show_help(self, section=None):
        detail = (
            'Instructions: install Evolve Stage 2 from modded-evolve.com, then connect the game and client.',
            'Modding: select a signed PAK, unpack, inspect files, edit resources, review changes, build and stage your mod.',
            'Play & Restore: launch mods for private/offline testing. Close Evolve and its launcher before restoring originals.',
            'Settings: check stage/swap/project locations, update, create shortcuts, export complete bundles and refresh signed resources.',
            'Credits: project, backend and safety information.',
        )
        if section is None:
            section = self.stack.currentIndex()
        self.info('Help — ' + SECTIONS[section], detail[section])

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
        box.exec()
        if box.clickedButton() == yes:
            return True
        if box.clickedButton() == no:
            return False
        return None

    def failure(self, error):
        self._log('ERROR: ' + str(error))
        self.status.setText('Could not continue: ' + str(error))
        QMessageBox.critical(self, 'Could not continue', str(error))

    def toggle_activity(self):
        if self._log_dialog is None:
            box = QDialog(self)
            box.setWindowTitle('Activity | Evolve Mod Manager')
            box.resize(920, 460)
            box.setStyleSheet(qt_style())
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
        self._success = success
        self._steps = list(steps)
        self._task_label = label
        self._task_cwd = Path(cwd or ROOT)
        if self._log_dialog is None:
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
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        self.process.readyReadStandardOutput.connect(self._process_output)
        self.process.finished.connect(self._process_finished)
        self.process.errorOccurred.connect(
            lambda error: self._log('Process error: ' + self.process.errorString()))
        self.process.start()

    def _process_output(self):
        output = bytes(self.process.readAllStandardOutput()).decode('utf-8', errors='replace')
        if output:
            self._log(output)
            if 'HOOK READY' in output or 'Automatic CreateProcessW hook ready' in output:
                self.status.setText('Launcher ready — press Play in the Evolve client.')
            if 'SWAP COMPLETE' in output:
                self.status.setText('Modded files swapped. Evolve is starting…')

    def _process_finished(self, code, status):
        if code:
            self._task_finished(self._task_label, code)
        else:
            self._run_next_step()

    def _task_finished(self, name, code):
        self.busy = False
        self.process = None
        if code:
            self.status.setText(f'FAILED: {name} (exit {code}). Review Activity.')
            self._log('FAILED — backups and journals have not been deleted.')
            self.refresh_swap_status()
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

    def persist(self):
        self.settings.update({
            'stage': self.stage_path,
            'swap': self.swap_path,
            'projects': self.projects_path,
            'current_workspace': self.current_workspace,
            'output_pak': self.output_pak,
            'archive_label': self.archive_label,
            'batch_root': self.batch_root,
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

    def refresh_archives(self):
        try:
            self.archive_entries = sorted(
                allowed_paks(Path(self.stage_path)).values(), key=str.casefold)
        except (OSError, ValueError) as exc:
            self.archive_entries = []
            self._log('Archive scan: ' + str(exc))
        self.refresh_table()

    def refresh_table(self):
        if not hasattr(self, 'table'):
            return
        query = self.search.text()
        mode = self.file_filter.currentText()
        selected = set(self.selected_archives())
        self.table.setSortingEnabled(False)
        self.table.setRowCount(0)
        records = matching_archives(self.archive_entries, query) if mode != 'Game files' else []
        for rel in records:
            name, folder = describe_archive(rel)
            ws = self.batch_workspaces.get(rel)
            status = 'Unpacked' if ws and Path(ws, '.evolve-pak-workspace.json').is_file() else 'PAK'
            try:
                size = (Path(self.stage_path)/'paks'/Path(*rel.split('/'))).stat().st_size
            except OSError:
                size = -1
            self._insert_row(rel, (folder, archive_description(rel), name, status, format_size(size)))
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
                                         (folder, 'Game file', name, 'Game file',
                                          format_size(record.size)))
                        loose_count += 1
                except (OSError, ValueError) as exc:
                    self._log('Loose file scan unavailable: ' + str(exc))
        self.table.setSortingEnabled(True)
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
            if selected in self.batch_workspaces:
                self.current_workspace = str(self.batch_workspaces[selected])
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
            menu.addAction('Unpack Selected PAKs' if len(ids) > 1 else 'Unpack PAK',
                           self.extract_batch if len(ids) > 1 else self.extract)
            menu.addAction('Inspect PAK Details', self.inspect_pak)
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

    def inspect_pak(self):
        rel = self._selected_rel()
        staged, original = self._pak_locations(rel)
        size = format_size(staged.stat().st_size) if staged.is_file() else 'Missing'
        self.info('PAK details: ' + Path(rel).name,
                  f'Description (filename-derived): {archive_description(rel)}\n'
                  f'Archive: {rel}\nStaged size: {size}\nStaged: {staged}\n'
                  f'Installed original: {original or "Unknown"}\n'
                  f'Unpacked workspace: {self.current_workspace or "Not unpacked"}')

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
        target = Path(self.current_workspace)/'files'
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
        path = Path(self.current_workspace)
        if not self.current_workspace or not (path/'.evolve-pak-workspace.json').is_file():
            raise ValueError('Unpack a signed PAK first.')
        return path

    def extract(self):
        try:
            pak, rel = self._selected_stage_pak()
            pub, _ = self._pub_priv()
            folder = self._new_folder((rel or 'External/'+pak.name).replace('/','_').replace('.pak',''))
            cmd = [sys.executable,'-u',str(ROOT/'evolve_pak_workspace.py'),
                   'extract','--pak',str(pak),'--public-key',str(pub),
                   '--workspace',str(folder),'--skip-unsupported']
            filter_text = self.filter_input.text().strip()
            if filter_text:
                cmd += ['--filter', filter_text]
            self.run_steps([('Unpacking archive',cmd)], 'Unpack archive',
                           lambda: self._after_extract(folder))
        except Exception as exc:
            self.failure(exc)

    def _after_extract(self, folder):
        self.current_workspace = str(folder)
        self.workspace_input.setText(str(folder))
        self.output_pak = ''
        self.output_input.setText('')
        self.persist()
        self.open_editor()

    def extract_batch(self):
        try:
            chosen = [s for s in self.selected_archives() if not s.startswith('loose-file:')]
            if not 2 <= len(chosen) <= 30:
                raise ValueError('Select 2–30 PAKs for a batch.')
            if self.filter_input.text().strip():
                raise ValueError('Clear the extraction filter before batch extraction.')
            if not self.confirm('Unpack selected PAKs?',
                                f'Unpack {len(chosen)} archives into separate projects? This may use considerable space.'):
                return
            pub, _ = self._pub_priv()
            dest = self._new_folder('Batch_Assets')
            cmd = [sys.executable,'-u',str(ROOT/'multi_pak_assets.py'),
                   '--stage',self.stage_path,'--public-key',str(pub),
                   '--destination',str(dest)]
            for rel in chosen:
                cmd += ['--pak',rel]
            self.run_steps([('Unpacking selected PAKs',cmd)], 'Batch unpack',
                           lambda: self._after_batch(dest))
        except Exception as exc:
            self.failure(exc)

    def _after_batch(self, folder):
        data = load_collection(folder)
        self.batch_root = str(folder)
        self._load_batch()
        self.archive_label = data['archives'][0]['archive']
        self.current_workspace = str(self.batch_workspaces[self.archive_label])
        self.workspace_input.setText(self.current_workspace)
        self.persist()
        self.open_editor()

    def open_editor_or_unpack(self):
        if self.current_workspace and (Path(self.current_workspace)/'files').is_dir():
            self.open_editor()
        else:
            self.extract()

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
        for editor in self.editors:
            if hasattr(editor, 'isVisible') and editor.isVisible() and hasattr(editor, '_confirm_buffer'):
                if not editor._confirm_buffer():
                    return False
        return True

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
                ws = self._workspace().resolve()
                for editor in self.editors:
                    if (isinstance(editor, QtWorkspaceEditor) and editor.isVisible()
                            and editor.workspace == ws):
                        editor.raise_()
                        editor.activateWindow()
                        return
                editor = QtWorkspaceEditor(
                    self, ws, archive_map=self._editor_collection(),
                    advanced=self.open_advanced_editor)
            editor.setAttribute(Qt.WA_DeleteOnClose, False)
            self.editors.append(editor)
            editor.show()
            editor.raise_()
        except Exception as exc:
            self.failure(exc)

    def open_advanced_editor(self):
        # Full GPU model preview and experimental CryEngine features remain
        # accessible via an isolated compatibility process until native Qt
        # editor feature tests cover them.
        try:
            if self.busy:
                raise RuntimeError('Wait until current operation finishes.')
            loose = next((name[11:] for name in self.selected_archives()
                          if name.startswith('loose-file:')), None)
            frozen = getattr(sys, 'frozen', False)
            if frozen:
                editor_exe = APP_HOME / 'EvolveModLegacyEditor.exe'
                if not editor_exe.is_file():
                    raise FileNotFoundError('Qt preview is missing EvolveModLegacyEditor.exe.')
                args = [str(editor_exe)]
            else:
                args = [sys.executable, str(ROOT/'qt_legacy_editor_bridge.py')]
            if loose:
                game = self.game_root or self._find_game_root()
                if not game:
                    raise ValueError('Installed game folder not known.')
                args += ['--loose-file', loose, '--game', str(game),
                         '--projects', self.projects_path]
            else:
                ws = self._workspace()
                args += ['--workspace', str(ws), '--stage', self.stage_path,
                         '--projects', self.projects_path]
                if self.batch_root:
                    args += ['--batch-root', self.batch_root]
            self._log('Opening advanced editor with compatibility bridge.')
            subprocess.Popen(args, cwd=str(APP_HOME if frozen else ROOT))
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
            pak, rel = self._selected_stage_pak()
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
            pak, rel = self._selected_stage_pak()
            if not rel:
                raise ValueError('Select a PAK from the verified list.')
            modified = Path(self.output_pak)
            if not modified.is_file() or modified.name.casefold() != pak.name.casefold():
                raise ValueError('Build the selected PAK first.')
            ws = self._workspace()
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
            self.open_page(2)

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
            if game and st.phase in ('missing','prepared','restored') and plan_recovery(game):
                from launch_integration import SwapStatus
                st = SwapStatus('unsafe',
                                'Original game backups exist despite clean swap journal.')
                self.launch_status.setText('Original backups remain. Restore before launching.')
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
                               'Restore originals',self.refresh_swap_status)
                return
            if not pending and st.phase in ('missing','prepared','restored'):
                self.info('Nothing to restore', 'No original backups remain in the selected game.')
                return
            if not pending and st.phase == 'unsafe':
                raise RuntimeError('Journal status is unsafe and no backups are visible; inspect before continuing.')
            if self.confirm('Restore original game files?', 'Close Evolve and client before returning modded PAKs to originals.'):
                self.run_steps([('Restoring original game files',
                               restore_command(Path(self.swap_path)))],
                               'Restore originals',self.refresh_swap_status,
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
        if self.busy or self.update_running:
            if not silent:
                self.info('Updates','Finish the current operation first.')
            return
        if not (APP_HOME/'EvolveModManagerQt.exe').is_file():
            self.update_status.setText(
                'Qt preview builds do not install over the stable main release.')
            if not silent:
                self.info('Updates', 'Use the stable main release to install verified updates. Qt preview does not replace it.')
            return
        # Do not offer a Tk update manifest for the unrelated experimental
        # executable. Publishing requires a separately verified Qt manifest.
        self.update_status.setText('Qt preview update channel not enabled yet.')

    def closeEvent(self, event):
        if self.busy or self.update_running:
            self.info('Still running', 'Finish the current operation before closing; interrupted swaps are unsafe.')
            event.ignore()
            return
        if not self.flush_editors():
            event.ignore()
            return
        try:
            self.persist()
        except OSError:
            pass
        for editor in self.editors:
            if hasattr(editor, 'isVisible') and editor.isVisible():
                editor.close()
        event.accept()


def main():
    app = QApplication(sys.argv)
    app.setStyle('Fusion')
    app.setFont(QFont('Segoe UI', 10))
    main_window = QtManager()
    main_window.show()
    if '--capture-screenshot' in sys.argv:
        try:
            destination = Path(sys.argv[sys.argv.index('--capture-screenshot') + 1])
        except (IndexError, ValueError):
            raise SystemExit('--capture-screenshot requires a PNG path')
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
