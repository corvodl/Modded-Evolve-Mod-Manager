"""v1.1.16 semantic Qt palette with customizable light/dark colors."""
from __future__ import annotations
import re

from qt_design_system import qt_modern_style

ROLES = ('background', 'surface', 'field', 'text', 'muted',
         'border', 'accent', 'button', 'selection')
PALETTES = {
    'Dark': dict(background='#111318', surface='#1c1e24', field='#24262e',
                 text='#f2f3f5', muted='#adb1bd', border='#393e49',
                 accent='#c9384a', button='#292c34', selection='#48303b'),
    'Light': dict(background='#f5f6f8', surface='#ffffff', field='#eff1f5',
                  text='#242731', muted='#606775', border='#d6dbe3',
                  accent='#b72b41', button='#e9ecf1', selection='#f1dce2'),
}
HEX = re.compile(r'^#[0-9a-fA-F]{6}$')
TOKEN = re.compile(r'#[0-9a-fA-F]{3,8}\b')
ORIGINAL = {
    'background': '#0b0b0d #141319',
    'surface': '#17171d #16151b #201d24 #202027 #29262e #1e1c23 #222026',
    'field': '#18171d #201e23 #201920 #261e26',
    'text': '#f5f3f6 #f7f4f6 #ffffff #fff #f4f2f4 #eeeaf0 #eee #f7f4f7 #d9d5dd #d6d3db',
    'muted': '#a6a3ae #736e79',
    'border': '#353139 #45323a #49404a #463741 #41313a #3a353f #39353d #28262c #38343d #4b434d #35252c #403943',
    'accent': '#fa4557 #fa586c #c73649 #cf4858 #dc475b',
    'button': '#26242c #38313c #211d25',
    'selection': '#48303a #74313f #69434b #35262f #682734 #87293b #864052 #55303c #693647 #702c3c',
}


def palette(mode='Dark', overrides=None):
    if mode not in PALETTES:
        raise ValueError('Appearance must be Light or Dark.')
    result = dict(PALETTES[mode])
    for key, value in (overrides or {}).items():
        if key not in ROLES or not isinstance(value,str) or not HEX.fullmatch(value):
            raise ValueError('Invalid appearance color: '+str(key))
        result[key] = value.lower()
    return result


def qt_palette_style(base, mode='Dark', overrides=None, *, density='Comfortable'):
    values = palette(mode, overrides)
    lookup = {}
    for role, names in ORIGINAL.items():
        for name in names.split():
            lookup[name.lower()] = values[role]
    style = TOKEN.sub(lambda match: lookup.get(match.group().lower(), match.group()), base)
    style += """
    QDialog, QTreeWidget, QTreeView, QListWidget, QTabWidget, QGroupBox {
        background:%(surface)s; color:%(text)s; }
    QTreeWidget, QTreeView, QListWidget { border:1px solid %(border)s;
        selection-background-color:%(selection)s; selection-color:%(text)s; }
    QTabBar::tab { background:%(button)s; color:%(text)s;
        border:1px solid %(border)s; padding:7px 13px; }
    QTabBar::tab:selected { border-bottom:2px solid %(accent)s; background:%(surface)s; }
    QSlider::groove:horizontal { height:5px; background:%(border)s; }
    QSlider::handle:horizontal { background:%(accent)s; width:14px;
        margin:-5px 0; border-radius:7px; }
    QProgressBar { background:%(field)s; color:%(text)s; border:1px solid %(border)s; }
    QProgressBar::chunk { background:%(accent)s; }
    QDialog QPushButton, QToolButton { min-height:26px; }
    QDialog QPushButton:focus, QToolButton:focus,
    QTabBar::tab:focus, QCheckBox:focus {
        border:2px solid %(accent)s; }
    QTreeWidget::item:selected, QTreeView::item:selected,
    QListWidget::item:selected, QTableWidget::item:selected {
        background:%(selection)s; color:%(text)s; }
    QTreeWidget::item, QTreeView::item, QListWidget::item {
        padding:3px 5px; }
    QLineEdit:disabled, QComboBox:disabled, QPushButton:disabled {
        color:%(muted)s; border-color:%(border)s; }
    QToolTip { background:%(surface)s; color:%(text)s;
        border:1px solid %(border)s; padding:5px; }
    """ % values
    style += qt_modern_style(values, density=density)
    return style
