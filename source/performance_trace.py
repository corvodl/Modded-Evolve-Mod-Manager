"""Opt-in, path-free performance spans for reproducible manager profiling.

Set EVOLVE_PERF_TRACE=1 before launching the manager. Only logical operation
names, durations and numeric counters are written to
DATA_HOME/Diagnostics/performance-spans.jsonl. No archive path, user profile
data, account token, or source content is included.
"""
from __future__ import annotations

from contextlib import contextmanager
from itertools import count
import json
import os
from pathlib import Path
from threading import Lock, get_ident
import time

_lock = Lock()
_ids = count(1)


def _trace_path():
    from app_runtime import DATA_HOME
    return Path(DATA_HOME) / 'Diagnostics' / 'performance-spans.jsonl'


def enabled():
    return os.environ.get('EVOLVE_PERF_TRACE') == '1'


def operation_id():
    """Monotonically distinguish overlapping requests within the process."""
    with _lock:
        return next(_ids)


def record(operation, stage, *, elapsed_ms=None, outcome='ok', **counters):
    """Append an event; tracing failures are always nonfatal to asset work."""
    if not enabled():
        return
    # Deliberately avoid arbitrary string values that may contain filenames.
    allowed = {
        str(key): value for key, value in counters.items()
        if isinstance(value, (bool, int, float)) and
        isinstance(key, str) and key.isidentifier()
    }
    entry = {
        't_monotonic_ns': time.perf_counter_ns(),
        'operation': str(operation)[:40],
        'stage': str(stage)[:60],
        'outcome': str(outcome)[:24],
        'thread': get_ident(),
        **allowed,
    }
    if elapsed_ms is not None:
        entry['elapsed_ms'] = round(float(elapsed_ms), 3)
    try:
        with _lock:
            path = _trace_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(entry, separators=(',', ':')) + '\n')
    except (OSError, ValueError, TypeError):
        pass


@contextmanager
def span(operation, stage, **counters):
    """Time one bounded stage without modifying its exception or return."""
    if not enabled():
        yield
        return
    start = time.perf_counter()
    try:
        yield
    except BaseException:
        record(operation, stage,
               elapsed_ms=(time.perf_counter() - start) * 1000,
               outcome='error', **counters)
        raise
    else:
        record(operation, stage,
               elapsed_ms=(time.perf_counter() - start) * 1000,
               **counters)


def start_qt_heartbeat(owner, interval_ms=100):
    """Watch large main-thread stalls without adding a timer when disabled."""
    if not enabled():
        return None
    from PySide6.QtCore import QTimer
    timer = QTimer(owner)
    timer.setInterval(interval_ms)
    previous = [time.perf_counter()]

    def sample():
        now = time.perf_counter()
        delay_ms = max(0., (now - previous[0]) * 1000 - interval_ms)
        previous[0] = now
        if delay_ms >= 50.:
            record('ui', 'event_loop_delay', delay_ms=round(delay_ms, 3))

    timer.timeout.connect(sample)
    timer.start()
    return timer


def install_qt_startup_events(owner):
    """Observe GUI first paint and first user action without changing events.

    Call after main window construction; installing is opt-in only. The returned
    QObject must be held by the owner and explicitly disposed on shutdown.
    """
    if not enabled():
        return None
    from PySide6.QtCore import QObject, QEvent
    from PySide6.QtWidgets import QApplication
    class _StartupObserver(QObject):
        def __init__(self, window):
            super().__init__(window)
            self.window = window
            self.created = time.perf_counter()
            self.painted = False
            self.interacted = False

        def eventFilter(self, watched, event):
            kind = event.type()
            if not self.painted and watched is self.window and kind == QEvent.Paint:
                self.painted = True
                record('startup', 'main_first_paint',
                       elapsed_ms=(time.perf_counter()-self.created)*1000)
            if (self.painted and not self.interacted and
                    kind in (QEvent.MouseButtonPress, QEvent.KeyPress,
                             QEvent.TouchBegin)):
                try:
                    belongs = (watched is self.window or
                               watched.window() is self.window)
                except (AttributeError, RuntimeError):
                    belongs = False
                if belongs:
                    self.interacted = True
                    record('startup', 'first_accepted_interaction',
                           elapsed_ms=(time.perf_counter()-self.created)*1000)
            return False

    observer = _StartupObserver(owner)
    app = QApplication.instance()
    if app is not None:
        app.installEventFilter(observer)
    return observer
