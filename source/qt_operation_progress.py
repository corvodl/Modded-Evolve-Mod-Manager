"""Reusable operation progress for interactive Qt file imports/exports.

Counts real bytes when copying a file. For a single decoder/GLB conversion,
use an indeterminate dialog rather than an invented percent.
"""
from __future__ import annotations

import os
from pathlib import Path
import tempfile

from PySide6.QtWidgets import QApplication, QProgressDialog

_BUFFER = 1024 * 1024


def progress_dialog(parent, title, label, total=0, *, cancel=True):
    dialog = QProgressDialog(label, 'Cancel' if cancel else '', 0, int(total), parent)
    dialog.setWindowTitle(title)
    dialog.setMinimumDuration(0)
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    if total <= 0:
        dialog.setRange(0, 0)
    if not cancel:
        dialog.setCancelButton(None)
    dialog.show()
    QApplication.processEvents()
    return dialog


def copy_file_progress(source, destination, *, parent=None, title='Export file'):
    """Copy with real byte progress and atomic replacement.

    Never intentionally alter input bytes. Interrupted copies are removed.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or not source.is_file() or not destination.parent.is_dir():
        raise ValueError('Choose distinct source and destination files.')
    total = source.stat().st_size
    ui = progress_dialog(parent, title, source.name, 1000)
    tempname = None
    try:
        with tempfile.NamedTemporaryFile(
                prefix='.evolve-export-', dir=destination.parent,
                delete=False) as tmp:
            tempname = tmp.name
            transferred = 0
            with source.open('rb') as inp:
                for block in iter(lambda: inp.read(_BUFFER), b''):
                    if ui.wasCanceled():
                        raise InterruptedError('File export cancelled; partial file removed.')
                    tmp.write(block)
                    transferred += len(block)
                    ui.setValue(min(1000, round(transferred * 1000 / max(1, total))))
                    ui.setLabelText('%s • %.1f / %.1f MiB' % (
                        source.name, transferred / 1048576, total / 1048576))
                    QApplication.processEvents()
            tmp.flush()
            os.fsync(tmp.fileno())
        if ui.wasCanceled():
            raise InterruptedError('File export cancelled; partial file removed.')
        os.replace(tempname, destination)
        tempname = None
        ui.setValue(1000)
        return destination
    finally:
        ui.close()
        if tempname:
            Path(tempname).unlink(missing_ok=True)


def run_busy(parent, title, label, action):
    """Indeterminate progress for opaque conversion work; never fake %."""
    ui = progress_dialog(parent, title, label, cancel=False)
    try:
        return action()
    finally:
        ui.close()
