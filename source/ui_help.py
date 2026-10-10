"""On-demand help window: detailed instructions never crowd primary workflows."""
from __future__ import annotations
import tkinter as tk
from tkinter import ttk


def open_help_window(parent, title, sections, selected=0):
    if not sections:
        raise ValueError('Help window requires at least one section')
    popup = tk.Toplevel(parent)
    popup.title(title)
    popup.configure(background='#0b0b0d')
    popup.geometry('690x480')
    popup.minsize(480, 350)
    popup.transient(parent)
    content = ttk.Frame(popup, padding=16)
    content.pack(fill='both', expand=True)
    ttk.Label(content, text=title, style='Section.TLabel').pack(anchor='w', pady=(0, 14))
    book = ttk.Notebook(content)
    book.pack(fill='both', expand=True)
    for heading, body in sections:
        page = ttk.Frame(book, padding=16)
        book.add(page, text=heading)
        label = ttk.Label(page, text=body, justify='left', anchor='nw', wraplength=570)
        label.pack(anchor='nw', fill='x')
        page.bind('<Configure>', lambda event, node=label: node.configure(wraplength=max(250, event.width - 30)))
    book.select(max(0, min(len(sections) - 1, selected)))
    ttk.Button(content, text='Close', command=popup.destroy).pack(anchor='e', pady=(13, 0))
    popup.bind('<Escape>', lambda _: popup.destroy())
    return popup
