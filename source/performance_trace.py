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
