"""Machine-readable, line-buffered progress for extraction subprocesses.

PROGRESS lines are separate from human-readable Activity output, and only
report actual processed entries (not guessed elapsed-time percentages).
"""
from __future__ import annotations

import re

_PATTERN = re.compile(r'^PROGRESS\|([^|\r\n]{1,150})\|(\d+)\|(\d+)$')


def progress_line(label, completed, total):
    title = re.sub(r'[\r\n|]', '_', str(label))[:150]
    count = max(0, int(total))
    done = min(count, max(0, int(completed)))
    return 'PROGRESS|%s|%d|%d' % (title, done, count)


def parse_progress_line(line):
    match = _PATTERN.fullmatch(line.strip())
    if match is None:
        return None
    label, done, total = match.group(1), int(match.group(2)), int(match.group(3))
    if done > total:
        return None
    return label, done, total


class ProgressStream:
    def __init__(self):
        self._pending = ''

    def feed(self, chunk):
        """Return (progress_events, visible_log) for arbitrarily split output."""
        self._pending += chunk
        events, lines = [], []
        while '\n' in self._pending:
            part, self._pending = self._pending.split('\n', 1)
            event = parse_progress_line(part)
            if event is not None:
                events.append(event)
            else:
                lines.append(part + '\n')
        if len(self._pending) > 65536:
            lines.append(self._pending)
            self._pending = ''
        return events, ''.join(lines)

    def flush(self):
        content, self._pending = self._pending, ''
        event = parse_progress_line(content)
        return ((event,) if event is not None else ()), ('' if event else content)
