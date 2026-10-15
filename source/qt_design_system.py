"""Unified native Qt Widgets design language for Evolve Mod Manager.

Only valid Qt Style Sheet properties are used (no browser CSS or runtime
image dependencies). This stylesheet is generated on appearance changes,
never per widget or frame; no repeated SVG decoding or expensive blur.
"""
from __future__ import annotations

SPACING = (4, 8, 12, 16, 24, 32)
RADIUS = {"control": 8, "panel": 12, "dialog": 14}
MOTION_MS = 160


def _contrast(values):
    # Settings overrides remain respected. Derived neutral treatments avoid
    # imposing a dark-only overlay on light themes.
    bg = values["background"].lstrip("#")
    rgb = [int(bg[i:i+2], 16) / 255 for i in (0, 2, 4)]
    luminance = sum(c * w for c, w in zip(rgb, (0.2126, 0.7152, 0.0722)))
    return "light" if luminance > 0.55 else "dark"


def qt_modern_style(values, *, density="Comfortable"):
    """Return theme-aware, cross-window QWidget/QDialog/QMenu styles.

    Call only when the user changes palette/density, not on animation frames.
    Existing object-name selectors are maintained for backward compatibility.
    """
    tone = _contrast(values)
    light = tone == "light"
    v = dict(values)
    v.update({
        "hover": "#e4e8ee" if light else "#30333b",
        "pressed": "#d7dce5" if light else "#24262e",
        "sidebar": "#f0f2f5" if light else "#17191f",
        "selected": "#f0dfe3" if light else "#3d2831",
        "on_accent": "#ffffff",
        "raised": "#ffffff" if light else "#22252c",
        "separator": "#e6e9ed" if light else "#292c32",
        "scroll": "#bdc4ce" if light else "#4d525e",
    })
    compact = density == "Compact"
    v["pad_y"] = 5 if compact else 7
    v["pad_x"] = 9 if compact else 11
    v["rowpad"] = 2 if compact else 5
    v["tabpad"] = 7 if compact else 9
    # Styling one shared set of controls also catches standalone editor
    # windows, viewports, popovers, dialog buttons and floating inspectors.
    return """
    QMainWindow, QDialog, QWidget, QDockWidget {
        background: %(background)s; color: %(text)s;
    }
    QLabel { color: %(text)s; background: transparent; }
    QLabel#muted, QLabel#secondaryInfo, QLabel#viewHint {
        color: %(muted)s; background: transparent;
    }
    QLabel#sectionTitle { font-size: 18pt; font-weight: 600; }
    QLabel#cardTitle, QLabel#settingsHeading, QLabel#dockHeading,
    QLabel#fileExplorerHeading { font-size: 11pt; font-weight: 600; color: %(text)s; }
    QLabel#brand { color: %(text)s; font-weight: 650; font-size: 13pt; }
    QLabel#version { color: %(muted)s; font-size: 9pt; }
    QLabel#currentPageTitle { font-size: 11pt; font-weight: 600; }
    QLabel#currentPageContext { color: %(muted)s; font-size: 9pt; }
    QFrame#card, QGroupBox {
        background: %(surface)s;
        border: 1px solid %(separator)s;
        border-radius: 12px;
    }
    QFrame#drawer { background: %(sidebar)s; border: 0; border-right: 1px solid %(separator)s; }
    /* Permanent navigation rail: dark, quiet, outlined, keyboard accessible. */
    QFrame#homeNavigationRail { background: %(sidebar)s;
        border: 1px solid %(separator)s; border-radius: 12px; }
    QToolButton#railButton, QToolButton#railActive {
        background: transparent; border: 0; border-radius: 9px;
        padding: 8px; }
    QToolButton#railButton:hover { background: %(hover)s; }
    QToolButton#railActive { background: %(selected)s; border-left: 3px solid %(accent)s; }
    QToolButton#railButton:focus, QToolButton#railActive:focus {
        border: 1px solid %(accent)s; }
    QToolButton#homeBrandButton {
        background: transparent; border: none; color: %(text)s;
        font-size: 13pt; font-weight: 650; padding: 3px 10px;
    }
    QToolButton#homeBrandButton:hover { background: %(hover)s; border-radius: 9px; }
    QToolButton#homeBrandButton:focus { border: 1px solid %(accent)s; }
    QScrollArea#homeDashboard, QWidget#homeDashboardContent,
    QWidget#homeWorkspace, QWidget#homeStatusColumn {
        background: %(background)s; border: none; }
    QFrame#homeHero {
        background: %(raised)s;
        border: 1px solid %(separator)s;
        border-left: 4px solid %(accent)s;
        border-radius: 16px;
    }
    QLabel#homeEyebrow {
        font-size: 9pt; letter-spacing: 1px; font-weight: 600;
        color: %(accent)s; }
    QLabel#homeHeroTitle { font-size: 26pt; font-weight: 700; color: %(text)s; }
    QLabel#homeHeroDescription { color: %(muted)s; font-size: 11pt; }
    QLabel#homeSectionTitle { font-size: 15pt; color: %(text)s; font-weight: 650; }
    QPushButton#homeSecondaryAction {
        background: %(field)s; color: %(text)s; border: 1px solid %(separator)s;
        border-radius: 8px; }
    QPushButton#homeSecondaryAction:hover { background: %(hover)s; }
    QPushButton#homeTile {
        background: %(surface)s; border: 1px solid %(separator)s;
        border-radius: 12px; text-align: left; padding: 0;
    }
    QPushButton#homeTile:hover {
        background: %(hover)s; border-color: %(border)s; }
    QPushButton#homeTile:pressed { background: %(selected)s; border-color: %(accent)s; }
    QPushButton#homeTile:focus { border: 1px solid %(accent)s; }
    QLabel#homeTileTitle { font-weight: 650; font-size: 11pt; color: %(text)s; }
    QLabel#homeTileDescription { font-size: 9pt; color: %(muted)s; }
    QLabel#homeStatusValue { color: %(muted)s; font-size: 10pt; }
    QFrame#homeStatusCard { background: %(surface)s; border: 1px solid %(separator)s;
        border-radius: 10px; }

    QFrame#fileExplorerPane { background: %(sidebar)s; border: 0;
                              border-left: 1px solid %(separator)s; }
    QFrame#workspaceSurface, QFrame#workspaceEditorFrame {
        background: %(surface)s; border: 1px solid %(separator)s;
        border-radius: 12px;
    }
    QLabel#panelTitle { color: %(text)s; font-weight: 600; font-size: 10pt; }
    QWidget#workspaceHeader { background: transparent; }
    QWidget#disclosurePanel, QWidget#disclosureBody, QWidget#contextActions {
        background: transparent;
    }
    QPushButton#disclosureToggle {
        text-align: left; background: %(surface)s; color: %(muted)s;
        border: 0; border-radius: 8px; padding: 9px 12px; font-weight: 600;
    }
    QPushButton#disclosureToggle:hover, QPushButton#disclosureToggle:checked {
        background: %(hover)s; color: %(text)s;
    }
    QToolButton#contextOverflow { background: transparent; border: 1px solid %(separator)s; }
    QFrame#pageToolbar { background: %(surface)s; border: 1px solid %(separator)s;
                         border-radius: 9px; }

    QFrame#settingsDivider { background: %(separator)s; border: 0; }
    QGroupBox {
        margin-top: 15px;
        padding: 15px 10px 10px 10px;
        font-weight: 600;
    }
    QGroupBox::title { subcontrol-origin: margin; left: 12px;
                       padding: 0 4px; color: %(text)s; }
    QPushButton, QToolButton {
        background: %(button)s; color: %(text)s;
        border: 1px solid %(separator)s;
        border-radius: 8px;
        padding: %(pad_y)spx %(pad_x)spx;
        font-weight: 500;
        min-height: 21px;
    }
    QPushButton:hover, QToolButton:hover {
        background: %(hover)s; border-color: %(border)s;
    }
    QPushButton:pressed, QToolButton:pressed {
        background: %(pressed)s;
    }
    QPushButton:checked, QToolButton:checked {
        background: %(selected)s; border-color: %(accent)s;
    }
    QPushButton:disabled, QToolButton:disabled {
        background: %(field)s; color: %(muted)s;
        border-color: %(separator)s;
    }
    QPushButton#primary {
        background: %(accent)s; color: %(on_accent)s; border-color: %(accent)s;
    }
    QPushButton#primary:hover { background: %(accent)s; border-color: %(text)s; }
    QPushButton#nav, QPushButton#navActive {
        background: transparent; border: none; border-radius: 9px; padding: 0px;
    }
    QPushButton#nav:hover { background: %(hover)s; }
    QPushButton#navActive { background: %(selected)s; }
    QPushButton#navActive:hover { background: %(selected)s; }
    QPushButton:focus, QToolButton:focus, QLineEdit:focus, QComboBox:focus,
    QTreeView:focus, QTableWidget:focus, QPlainTextEdit:focus {
        border: 1px solid %(accent)s;
    }
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox,
    QTextEdit, QPlainTextEdit, QDateEdit {
        color: %(text)s; background: %(field)s;
        border: 1px solid %(border)s; border-radius: 8px;
        padding: %(pad_y)spx %(pad_x)spx;
        selection-color: %(text)s; selection-background-color: %(selection)s;
        min-height: 20px;
    }
    QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {
        color: %(muted)s; background: %(surface)s; border-color: %(separator)s;
    }
    QComboBox::drop-down { border: none; width: 24px; }
    QComboBox QAbstractItemView { background: %(surface)s; color: %(text)s;
        border: 1px solid %(border)s; selection-background-color: %(selected)s; }
    QTreeView, QTreeWidget, QTableView, QTableWidget, QListView, QListWidget {
        background: %(surface)s; alternate-background-color: %(field)s;
        color: %(text)s; border: 1px solid %(separator)s; border-radius: 8px;
        outline: 0; gridline-color: %(separator)s;
        selection-background-color: %(selected)s; selection-color: %(text)s;
    }
    QTreeView::item, QTreeWidget::item, QTableView::item,
    QTableWidget::item, QListWidget::item {
        padding: %(rowpad)spx 5px; border: 0;
    }
    QTreeView::item:hover, QTreeWidget::item:hover,
    QListView::item:hover, QTableView::item:hover {
        background: %(hover)s;
    }
    QTreeView::item:selected, QTreeWidget::item:selected,
    QTableView::item:selected, QTableWidget::item:selected,
    QListView::item:selected, QListWidget::item:selected {
        background: %(selected)s; color: %(text)s;
    }
    QHeaderView::section {
        background: %(surface)s; color: %(muted)s; border: 0;
        border-bottom: 1px solid %(separator)s;
        padding: 9px; font-weight: 600;
    }
    QHeaderView::section:hover { color: %(text)s; background: %(hover)s; }
    QTabWidget::pane { border: 0; background: %(background)s; }
    QTabBar::tab {
        background: transparent; color: %(muted)s;
        border: 0; border-bottom: 2px solid transparent;
        padding: %(tabpad)spx 13px; margin-right: 4px;
    }
    QTabBar::tab:hover { color: %(text)s; background: %(hover)s; }
    QTabBar::tab:selected {
        background: transparent; color: %(text)s;
        border-bottom: 2px solid %(accent)s; font-weight: 600;
    }
    QMenuBar, QMenu { background: %(surface)s; color: %(text)s;
                      border: 0; padding: 4px; }
    QMenu { border: 1px solid %(separator)s; }
    QMenuBar::item { padding: 7px 11px; border-radius: 6px; }
    QMenuBar::item:selected, QMenu::item:selected {
        background: %(hover)s; color: %(text)s; border-radius: 6px;
    }
    QMenu::item { padding: 7px 18px; }
    QMenu::separator { height: 1px; background: %(separator)s; margin: 4px 8px; }
    QScrollArea { background: transparent; border: 0; }
    QScrollBar:vertical { background: transparent; width: 9px; margin: 2px; }
    QScrollBar:horizontal { background: transparent; height: 9px; margin: 2px; }
    QScrollBar::handle:vertical, QScrollBar::handle:horizontal {
        background: %(scroll)s; border-radius: 4px; min-height: 22px; min-width: 22px;
    }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
        height: 0; background: transparent;
    }
    QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
        width: 0; background: transparent;
    }
    QSplitter::handle { background: %(separator)s; }
    QSplitter::handle:hover { background: %(border)s; }
    QToolTip { background: %(raised)s; color: %(text)s;
        border: 1px solid %(border)s; padding: 7px; }
    QCheckBox { spacing: 9px; color: %(text)s; }
    QCheckBox::indicator { width: 16px; height: 16px; }
    QProgressBar { border: 0; border-radius: 5px;
        text-align: center; background: %(field)s; color: %(text)s;
        min-height: 8px; }
    QProgressBar::chunk { border-radius: 5px; background: %(accent)s; }
    QStatusBar { color: %(muted)s; border-top: 1px solid %(separator)s; }
    QDockWidget::title { background: %(surface)s; color: %(text)s;
                         padding: 10px; font-weight: 600; }
    QFrame#viewportToolRail, QFrame#viewportAxisControls {
        background: %(raised)s; border: 1px solid %(border)s; border-radius: 9px;
    }
    QFrame#viewportTimeline { background: %(surface)s;
                              border-top: 1px solid %(separator)s; }
    """ % v


def style_dialog(dialog):
    """Inherit the shared app palette; never impose a hard-coded dark theme."""
    dialog.setProperty("evolveUnifiedDialog", True)
    dialog.setAutoFillBackground(False)


def configure_workspace_splitter(splitter, *, center=3, side=1):
    """Keep Qt native docking geometry and discoverable resizing affordances."""
    splitter.setChildrenCollapsible(False)
    splitter.setHandleWidth(5)
    if splitter.count() >= 2:
        splitter.setStretchFactor(0, center)
        splitter.setStretchFactor(1, side)
