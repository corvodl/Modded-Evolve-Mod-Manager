#!/usr/bin/env python3
"""Capture an actual running, packaged EvolveModManager.exe on Windows CI.

This is a Windows window-handle / GDI PrintWindow capture, not a Tk
re-render, generated illustration, or synthetic screenshot. Requires Pillow.
"""
from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import time

from PIL import Image, ImageStat, PngImagePlugin

if sys.platform != 'win32':
    raise SystemExit('Real executable screenshot capture requires Windows.')

u32 = ctypes.WinDLL('user32', use_last_error=True)
gdi = ctypes.WinDLL('gdi32', use_last_error=True)
HWND = wintypes.HWND
HDC = wintypes.HDC
HBITMAP = wintypes.HBITMAP

class RECT(ctypes.Structure):
    _fields_ = [('left', wintypes.LONG), ('top', wintypes.LONG),
                ('right', wintypes.LONG), ('bottom', wintypes.LONG)]

class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [('biSize', wintypes.DWORD), ('biWidth', wintypes.LONG),
                ('biHeight', wintypes.LONG), ('biPlanes', wintypes.WORD),
                ('biBitCount', wintypes.WORD), ('biCompression', wintypes.DWORD),
                ('biSizeImage', wintypes.DWORD), ('biXPelsPerMeter', wintypes.LONG),
                ('biYPelsPerMeter', wintypes.LONG), ('biClrUsed', wintypes.DWORD),
                ('biClrImportant', wintypes.DWORD)]

u32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM),
                            wintypes.LPARAM]
u32.EnumWindows.restype = wintypes.BOOL
u32.GetWindowTextLengthW.argtypes = [HWND]
u32.GetWindowTextLengthW.restype = ctypes.c_int
u32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
u32.GetWindowTextW.restype = ctypes.c_int
u32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
u32.GetWindowThreadProcessId.restype = wintypes.DWORD
u32.IsWindowVisible.argtypes = [HWND]
u32.IsWindowVisible.restype = wintypes.BOOL
u32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(RECT)]
u32.GetWindowRect.restype = wintypes.BOOL
u32.ShowWindow.argtypes = [HWND, ctypes.c_int]
u32.SetForegroundWindow.argtypes = [HWND]
u32.GetWindowDC.argtypes = [HWND]
u32.GetWindowDC.restype = HDC
u32.ReleaseDC.argtypes = [HWND, HDC]
u32.PrintWindow.argtypes = [HWND, HDC, wintypes.UINT]
u32.PrintWindow.restype = wintypes.BOOL
gdi.CreateCompatibleDC.argtypes = [HDC]
gdi.CreateCompatibleDC.restype = HDC
gdi.CreateCompatibleBitmap.argtypes = [HDC, ctypes.c_int, ctypes.c_int]
gdi.CreateCompatibleBitmap.restype = HBITMAP
gdi.SelectObject.argtypes = [HDC, wintypes.HGDIOBJ]
gdi.SelectObject.restype = wintypes.HGDIOBJ
gdi.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi.DeleteDC.argtypes = [HDC]
gdi.GetDIBits.argtypes = [HDC, HBITMAP, wintypes.UINT, wintypes.UINT,
                          wintypes.LPVOID, ctypes.POINTER(BITMAPINFOHEADER),
                          wintypes.UINT]
gdi.GetDIBits.restype = ctypes.c_int

def find_manager_window(pid):
    found = []
    CALLBACK = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)

    @CALLBACK
    def callback(hwnd, unused):
        window_pid = wintypes.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value != pid or not u32.IsWindowVisible(hwnd):
            return True
        length = u32.GetWindowTextLengthW(hwnd)
        if length:
            name = ctypes.create_unicode_buffer(length + 1)
            u32.GetWindowTextW(hwnd, name, length + 1)
            if name.value.strip() == 'Evolve Mod Manager':
                found.append(hwnd)
        return True

    u32.EnumWindows(callback, 0)
    return found[0] if found else None

def print_window(hwnd):
    rect = RECT()
    if not u32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise OSError('Cannot read compiled manager window dimensions')
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if not (800 <= width <= 2600 and 600 <= height <= 1800):
        raise RuntimeError(f'Unexpected live manager window: {width}x{height}')
    window_dc = u32.GetWindowDC(hwnd)
    if not window_dc:
        raise RuntimeError('GetWindowDC failed')
    memory_dc = gdi.CreateCompatibleDC(window_dc)
    bitmap = gdi.CreateCompatibleBitmap(window_dc, width, height)
    try:
        old = gdi.SelectObject(memory_dc, bitmap)
        try:
            # PrintWindow reads the real Windows-rendered EXE window; the
            # PW_RENDERFULLCONTENT path also supports obscured windows.
            if not (u32.PrintWindow(hwnd, memory_dc, 2) or
                    u32.PrintWindow(hwnd, memory_dc, 0)):
                raise RuntimeError('Windows PrintWindow could not capture the EXE')
        finally:
            gdi.SelectObject(memory_dc, old)
        header = BITMAPINFOHEADER()
        header.biSize = ctypes.sizeof(header)
        header.biWidth = width
        header.biHeight = -height  # top-down RGB buffer
        header.biPlanes = 1
        header.biBitCount = 32
        buffer = ctypes.create_string_buffer(width * height * 4)
        lines = gdi.GetDIBits(memory_dc, bitmap, 0, height, buffer,
                             ctypes.byref(header), 0)
        if lines != height:
            raise RuntimeError(f'GetDIBits returned {lines} of {height} rows')
        return Image.frombytes('RGB', (width, height), buffer.raw, 'raw', 'BGRX')
    finally:
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory_dc)
        u32.ReleaseDC(hwnd, window_dc)

def validate(image):
    # A blank, desktop-less session can return a black PrintWindow image.
    # Do not upload that and pretend it is the actual application.
    thumbnail = image.copy()
    thumbnail.thumbnail((450, 340))
    pixels = list(thumbnail.getdata())
    dark = sum(max(p) < 90 for p in pixels)
    colored_red = sum(p[0] > 105 and p[0] > p[1] * 1.5 and
                      p[0] > p[2] * 1.25 for p in pixels)
    contrast = max(ImageStat.Stat(thumbnail).stddev)
    if dark < len(pixels) * 0.10 or colored_red < 20 or contrast < 14:
        raise RuntimeError(
            f'Capture is blank/unrecognizable; dark={dark}, '
            f'red={colored_red}, contrast={contrast:.1f}')
    print(f'Validated real screenshot: {image.width}x{image.height}; '
          f'{colored_red} red pixels on resized image.', flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    app = args.executable.resolve(strict=True)
    if app.name.lower() != 'evolvemodmanager.exe':
        raise ValueError('Launch only the compiled EvolveModManager.exe')
    proc = subprocess.Popen([str(app)], cwd=str(app.parent))
    try:
        start = time.monotonic()
        hwnd = None
        while time.monotonic() - start < 50:
            if proc.poll() is not None:
                raise RuntimeError(f'Compiled manager exited early: {proc.returncode}')
            hwnd = find_manager_window(proc.pid)
            if hwnd is not None:
                break
            time.sleep(0.4)
        if hwnd is None:
            raise RuntimeError('No visible Evolve Mod Manager window from the packaged EXE')
        u32.ShowWindow(hwnd, 9)
        u32.SetForegroundWindow(hwnd)
        # Allow the actual Windows event loop to paint the icon, tabs,
        # onboarding cards, and footer before capturing.
        time.sleep(3)
        screenshot = print_window(hwnd)
        validate(screenshot)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        meta = PngImagePlugin.PngInfo()
        meta.add_text('Source', str(app))
        meta.add_text('Capture', 'Win32 PrintWindow; running compiled EvolveModManager.exe')
        screenshot.save(args.output, pnginfo=meta)
        print(f'CAPTURED actual compiled Windows manager: {args.output}',
              flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=8)

if __name__ == '__main__':
    main()
