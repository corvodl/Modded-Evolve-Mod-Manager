"""Rounded, accessible card surface for the Windows Tk/ttk interface.

Tk's native ttk Frame has square edges. Draw the corner silhouette on a Canvas
and keep ordinary ttk children on a real frame so keyboard focus, Windows
accessibility, and test selectors still work.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

BACKGROUND = '#0b0b0d'
SURFACE = '#18181e'
OUTLINE = '#333038'


class RoundedPanel(tk.Canvas):
    def __init__(self, master, *, padding=(17, 13), radius=16):
        self._inset_x, self._inset_y = padding
        self._radius = radius
        super().__init__(master, bg=BACKGROUND, bd=0, highlightthickness=0,
                         width=1, height=72, relief='flat')
        self.content = ttk.Frame(self, style='Card.TFrame', padding=(5, 2))
        self._window = self.create_window(
            (self._inset_x, self._inset_y), window=self.content, anchor='nw')
        self._shape = None
        self.bind('<Configure>', self._resize, add='+')
        self.content.bind('<Configure>', self._grow, add='+')

    def _grow(self, event):
        desired = event.height + 2 * self._inset_y
        if desired > 0 and self.winfo_height() != desired:
            self.configure(height=desired)

    def _resize(self, event):
        width = max(1, event.width)
        height = max(1, event.height)
        self.itemconfigure(self._window, width=max(1, width-2*self._inset_x))
        r = min(self._radius, width//2, height//2)
        if r <= 0:
            return
        # Smooth Bezier-like corners; the canvas is purely decorative.
        points = [
            r, 1, width-r, 1, width-r//3, 1, width-1, r,
            width-1, height-r, width-1, height-r//3,
            width-r, height-1, r, height-1, r//3, height-1,
            1, height-r, 1, r, 1, r//3,
        ]
        if self._shape is None:
            self._shape = self.create_polygon(
                points, smooth=True, splinesteps=20,
                fill=SURFACE, outline=OUTLINE, width=1)
            self.tag_lower(self._shape, self._window)
        else:
            self.coords(self._shape, *points)
