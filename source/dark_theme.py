"""Black, charcoal and red Evolve-inspired Tk theme."""
from tkinter import ttk

def apply_theme(root):
    bg, panel, field = '#0d0d0f', '#18181c', '#101013'
    text, muted, red = '#f2f2f4', '#b5b5bf', '#bd2030'
    root.configure(background=bg)
    root.option_add('*Font', ('Segoe UI', 10))
    for widget in ('Text', 'Listbox'):
        for key, value in {'background':field, 'foreground':text,
                           'selectBackground':red, 'selectForeground':'#ffffff',
                           'highlightBackground':'#3b3b43', 'highlightColor':red}.items():
            root.option_add('*'+widget+'.'+key, value)
    root.option_add('*Text.insertBackground', text)
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', background=bg, foreground=text, bordercolor='#34343b',
                    lightcolor=panel, darkcolor=bg, troughcolor=field)
    style.configure('TFrame', background=bg)
    style.configure('TLabel', background=bg)
    style.configure('TLabelframe', background=bg, bordercolor='#3b3035')
    style.configure('TLabelframe.Label', foreground='#ff6370', background=bg)
    style.configure('TButton', background=panel, foreground=text, padding=(12,7),
                    bordercolor='#49414a', focusthickness=2, focuscolor='#ff6370')
    style.map('TButton', background=[('disabled','#202024'),('pressed','#891725'),('active','#44232c')],
              foreground=[('disabled','#777780')])
    style.configure('Accent.TButton', background=red, foreground='#ffffff')
    style.map('Accent.TButton', background=[('disabled','#46222a'),('pressed','#891725'),('active','#df3042')])
    style.configure('TEntry', fieldbackground=field, foreground=text, insertcolor=text, padding=5)
    style.map('TEntry', fieldbackground=[('readonly',panel)], bordercolor=[('focus',red)])
    style.configure('TNotebook', background=bg, borderwidth=0)
    style.configure('TNotebook.Tab', background=panel, foreground=muted, padding=(10,9))
    style.map('TNotebook.Tab', background=[('selected',red),('active','#352027')],
              foreground=[('selected','#ffffff'),('active','#ffffff')])
    style.configure('Vertical.TScrollbar', background='#393940', arrowcolor=text)
    style.configure('TSeparator', background='#472630')
