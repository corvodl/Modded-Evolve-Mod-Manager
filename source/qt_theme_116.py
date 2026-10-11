"""v1.1.16 semantic Qt palette with customizable light/dark colors."""
from __future__ import annotations
import re

ROLES = ('background', 'surface', 'field', 'text', 'muted',
         'border', 'accent', 'button', 'selection')
PALETTES = {
    'Dark': dict(background='#0b0b0d', surface='#17171d', field='#18171d',
                 text='#f5f3f6', muted='#a6a3ae', border='#39353d',
                 accent='#c73649', button='#26242c', selection='#87293b'),
    'Light': dict(background='#f5f6f8', surface='#ffffff', field='#f9fafc',
                  text='#202129', muted='#5a5e6c', border='#c8cbd3',
                  accent='#b21f36', button='#e6e8ee', selection='#e9bac4'),
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


def qt_palette_style(base, mode='Dark', overrides=None):
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
    """ % values
    return style
