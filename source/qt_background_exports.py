"""Nonblocking, cancel-aware Qt export tasks with real progress.

Heavy model conversion MUST NOT run on the GUI thread. The progress bar is
determinate for completed files and shows an animated activity label while
an individual model is being decoded. Cancellation takes effect between
models, where it is safe to stop without modifying game references.
"""
from __future__ import annotations

from threading import Event
from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QProgressDialog


class _FileWorker(QThread):
    progress = Signal(str, int, int)
    completed = Signal(object, object)

    def __init__(self, action):
        super().__init__()
        self._action = action
        self._cancel = Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            if self._cancel.is_set():
                raise InterruptedError('Cancelled before export began.')
            result = self._action(self.report, self._cancel.is_set)
            self.completed.emit(result, None)
        except Exception as exc:
            detail = str(exc)
            if "Unsafe archive entry name: ''" in detail:
                # Report the caller, not only the shared path validator.
                # This distinguishes bad manifest, CDF and material lookups.
                import traceback
                frames = traceback.extract_tb(exc.__traceback__)
                caller = next((frame for frame in reversed(frames)
                               if frame.name != 'normalized_name'), None)
                if caller is not None:
                    from pathlib import Path
                    detail += ' [source: %s:%d %s]' % (
                        Path(caller.filename).name, caller.lineno, caller.name)
            self.completed.emit(None, detail)

    def report(self, label, completed, total):
        self.progress.emit(str(label), int(completed), int(total))


def begin_file_operation(parent, title, label, action, *, on_success=None,
                         on_error=None, cancel=True):
    """Schedule work without freezing the UI. Returns the started QThread.

    action(progress_callback, cancelled_callback) runs on a background thread.
    Callbacks and all Qt widget updates run only on the GUI thread.
    """
    jobs = getattr(parent, '_active_file_jobs', None)
    if jobs is None:
        jobs = []
        parent._active_file_jobs = jobs
    dialog = QProgressDialog(label, 'Cancel' if cancel else '', 0, 0, parent)
    dialog.setWindowTitle(title)
    dialog.setObjectName('fileExportProgress')
    dialog.setAutoClose(False)
    dialog.setAutoReset(False)
    dialog.setMinimumDuration(0)
    if not cancel:
        dialog.setCancelButton(None)

    thread = _FileWorker(action)
    jobs.append(thread)
    state = {'label': label, 'completed': 0, 'total': 0, 'elapsed': 0}
    timer = QTimer(dialog)
    timer.setInterval(600)

    def pulse():
        state['elapsed'] += 1
        dialog.setLabelText(
            '%s — %d / %d files (working %ds)' % (
                state['label'], state['completed'], state['total'],
                round(state['elapsed'] * 0.6)))

    def progress(task, completed, total):
        state.update(label=task, completed=completed, total=total)
        if total > 0:
            dialog.setRange(0, total)
            dialog.setValue(min(completed, total))
        else:
            dialog.setRange(0, 0)
        pulse()

    def finish(result, error):
        timer.stop()
        dialog.close()
        if error:
            if on_error is not None:
                on_error(error)
        elif on_success is not None:
            on_success(result)

    def cleanup():
        if thread in jobs:
            jobs.remove(thread)
        dialog.deleteLater()
        thread.deleteLater()

    thread.progress.connect(progress)
    thread.completed.connect(finish)
    thread.finished.connect(cleanup)
    if cancel:
        dialog.canceled.connect(thread.cancel)
        dialog.canceled.connect(lambda: dialog.setLabelText(
            'Cancelling after the current model finishes…'))
    timer.timeout.connect(pulse)
    dialog.show()
    timer.start()
    thread.start()
    return thread


def exports_busy(parent):
    return any(job.isRunning() for job in getattr(parent, '_active_file_jobs', ()))
