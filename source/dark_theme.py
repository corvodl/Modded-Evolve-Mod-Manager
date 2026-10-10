"""High-contrast black/charcoal UI with restrained Evolve-red accents."""
from tkinter import ttk

BG = '#0b0b0d'
CARD = '#18181c'
FIELD = '#101013'
TEXT = '#f2f2f4'
MUTED = '#a9a9b3'
RED = '#c32a3b'


def apply_theme(root):
    root.configure(background=BG)
    root.option_add('*Font', ('Segoe UI', 10))
    root.option_add('*Menu.font', ('Segoe UI', 10))
    for widget in ('Text', 'Listbox'):
        for key, value in {'background':FIELD, 'foreground':TEXT,
                           'selectBackground':RED, 'selectForeground':'#ffffff',
                           'highlightBackground':'#3b3b43', 'highlightColor':RED}.items():
            root.option_add('*'+widget+'.'+key, value)
    root.option_add('*Text.insertBackground', TEXT)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=BG, foreground=TEXT, bordercolor='#34343b',
                    lightcolor=CARD, darkcolor=BG, troughcolor=FIELD)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG)
    style.configure('Card.TFrame', background=CARD)
    style.configure('Card.TLabel', background=CARD, foreground=TEXT)
    style.configure('CardTitle.TLabel', background=CARD, foreground=TEXT, font=('Segoe UI Semibold', 13, 'bold'))
    style.configure('GuideHero.TLabel', background=BG, foreground=TEXT,
                    font=('Segoe UI Semibold', 22, 'bold'))
    style.configure('GuideKicker.TLabel', background=CARD, foreground='#ff6679',
                    font=('Segoe UI Semibold', 10, 'bold'))
    style.configure('GuideKickerBare.TLabel', background=BG, foreground='#ff6679',
                    font=('Segoe UI Semibold', 10, 'bold'))
    style.configure('GuideStep.TLabel', background=CARD, foreground=TEXT,
                    font=('Segoe UI Semibold', 12, 'bold'))
    style.configure('GuideBody.TLabel', background=CARD, foreground='#c5c4cd',
                    font=('Segoe UI', 10))
    style.configure('CardMuted.TLabel', background=CARD, foreground=MUTED, font=('Segoe UI', 10))
    style.configure('Brand.TLabel', background=BG, foreground='#fa4557', font=('Segoe UI Semibold', 19, 'bold'))
    style.configure('Section.TLabel', background=BG, foreground=TEXT, font=('Segoe UI Semibold', 15, 'bold'))
    style.configure('Muted.TLabel', background=BG, foreground=MUTED)
    style.configure('Status.TLabel', background=BG, foreground=TEXT, font=('Segoe UI', 9))
    style.configure('AccentText.TLabel', background=BG, foreground='#f06a78')
    style.configure('TLabelframe', background=BG, bordercolor='#383038', relief='solid')
    style.configure('TLabelframe.Label', foreground='#fa5566', background=BG,
                    font=('Segoe UI', 10, 'bold'))
    style.configure('TButton', background='#242328', foreground=TEXT, padding=(13, 9),
                    font=('Segoe UI Semibold', 10), bordercolor='#39353b',
                    focusthickness=1, focuscolor=RED, relief='flat')
    style.map('TButton', background=[('disabled','#202024'), ('pressed','#74202a'),
                                     ('active','#33232a')], foreground=[('disabled','#777780')])
    style.configure('Quiet.TButton', background='#1d1c20', foreground='#dedee2',
                    padding=(11, 7), bordercolor='#353139')
    style.map('Quiet.TButton', background=[('pressed','#3a272e'),('active','#292329')])
    style.configure('Accent.TButton', background=RED, foreground='#ffffff',
                    padding=(13, 9), bordercolor=RED, font=('Segoe UI', 10, 'bold'))
    style.map('Accent.TButton', background=[('disabled','#46222a'), ('pressed','#8a1829'),
                                            ('active','#e1384a')])
    style.configure('TEntry', fieldbackground=FIELD, foreground=TEXT, insertcolor=TEXT,
                    padding=(8, 8), bordercolor='#353139')
    style.map('TEntry', fieldbackground=[('readonly',CARD)], bordercolor=[('focus',RED)])
    # Taller, clear navigation tabs with a red active surface and consistent
    # Segoe typography. Clam avoids the dated native Windows notebook chrome.
    style.configure('TNotebook', background=BG, borderwidth=0,
                    tabmargins=(0, 5, 0, 0))
    style.configure('TNotebook.Tab', background='#1d1c22', foreground='#b7b5c0',
                    font=('Segoe UI Semibold', 10, 'bold'), padding=(22, 13),
                    borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected','#912537'),
                                           ('active','#33232b')],
              foreground=[('selected','#ffffff'), ('active','#ffffff')])
    # Treeview styling matches known high-contrast selection tests.
    tree_bg = '#1b1b20'
    tree_selected = '#40576d'
    style.configure('Treeview', background=tree_bg, fieldbackground=tree_bg,
                    foreground=TEXT, bordercolor='#34343b', rowheight=26,
                    font=('Segoe UI', 10))
    style.map('Treeview', background=[('selected', tree_selected)],
              foreground=[('disabled', '#8f8f99'), ('selected', '#ffffff')])
    style.configure('Archive.Treeview', background='#141417', fieldbackground='#141417',
                    foreground=TEXT, rowheight=32, font=('Segoe UI', 10),
                    bordercolor='#34343b', relief='flat')
    style.map('Archive.Treeview', background=[('selected', '#9c2635')],
              foreground=[('selected', '#ffffff')])
    style.configure('Archive.Treeview.Heading', background='#252328', foreground='#f2f2f4',
                    font=('Segoe UI Semibold', 10, 'bold'), padding=(10, 11))
    style.map('Archive.Treeview.Heading', background=[('active', '#40242c')])
    style.configure('Treeview.Heading', background=CARD, foreground=TEXT)
    style.map('Treeview.Heading', background=[('active', '#303039')],
              foreground=[('active', '#ffffff')])
    style.configure('Vertical.TScrollbar', background='#393940', arrowcolor=TEXT)
    style.configure('TSeparator', background='#472630')
