"""Friendly one-time FFDec prerequisite for Scaleform GFX previews and editing."""
from __future__ import annotations

from PySide6.QtCore import QUrl, Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox

from gfx_preview_backend import GfxError, ffdec_command

FFDEC_DOWNLOAD = "https://github.com/jindrapetrik/jpexs-decompiler/releases"


def ffdec_is_ready(manager):
    """Honor the manager setting and the backend's environment override."""
    try:
        ffdec_command(manager.settings.get("gfx_ffdec_path"))
        return True
    except (GfxError, OSError, ValueError):
        return False


def show_ffdec_requirement(parent):
    """Tell users the external dependency, without downloading or installing it."""
    # Selection-change signals also fire in automated Qt tests and on project
    # restoration. Do not block their event loop with a synchronous exec().
    existing = getattr(parent, "_ffdec_requirement_dialog", None)
    if existing is not None and existing.isVisible():
        existing.raise_()
        return existing
    message = QMessageBox(parent)
    message.setWindowModality(Qt.WindowModal)
    message.setObjectName("gfxFfdecRequired")
    message.setWindowTitle("FFDec required for Scaleform GFX")
    message.setIcon(QMessageBox.Information)
    message.setText("JPEXS Free Flash Decompiler (FFDec) is needed to preview or edit GFX movies.")
    message.setInformativeText(
        "FFDec must be installed separately. Download FFDec from its official "
        "GitHub releases page, then use Configure FFDec to choose "
        "ffdec-cli.exe or ffdec.jar (Java may also be needed). "
        "You can continue browsing and exporting raw files without FFDec.\n\n"
        + FFDEC_DOWNLOAD)
    download = message.addButton("Open FFDec download page", QMessageBox.ActionRole)
    message.addButton("Continue browsing", QMessageBox.RejectRole)
    message.buttonClicked.connect(
        lambda clicked: QDesktopServices.openUrl(QUrl(FFDEC_DOWNLOAD))
        if clicked is download else None)
    if parent is not None:
        parent._ffdec_requirement_dialog = message
        message.finished.connect(
            lambda _result: setattr(parent, "_ffdec_requirement_dialog", None))
    message.open()  # Asynchronous popup: user can act; UI tests keep running.
    return message
