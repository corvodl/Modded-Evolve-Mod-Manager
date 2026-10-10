"""Run test modules in isolated processes with live output and timeouts.

Windows hosted runners sometimes block on Tk modal dialogs. This converts a
silent 60-minute build hang into a named failing/timeout module.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--timeout-seconds', type=int, default=90)
    parser.add_argument('modules', nargs='+')
    args = parser.parse_args(argv)
    if args.timeout_seconds <= 0:
        parser.error('--timeout-seconds must be positive')
    env = os.environ.copy()
    env['PYTHONUNBUFFERED'] = '1'
    started = time.monotonic()
    for index, module in enumerate(args.modules, 1):
        print(f'\n[CI TEST {index}/{len(args.modules)}] START {module}', flush=True)
        start = time.monotonic()
        try:
            result = subprocess.run([sys.executable, '-u', '-m', 'unittest', '-v', module],
                                    env=env, timeout=args.timeout_seconds, check=False)
        except subprocess.TimeoutExpired:
            print(f'[CI TEST {index}/{len(args.modules)}] TIMEOUT: {module} exceeded '
                  f'{args.timeout_seconds}s; likely a blocked Windows GUI/modal or slow test.',
                  file=sys.stderr, flush=True)
            return 124
        elapsed = time.monotonic() - start
        if result.returncode:
            print(f'[CI TEST {index}/{len(args.modules)}] FAIL {module}: '
                  f'exit {result.returncode} after {elapsed:.1f}s', file=sys.stderr, flush=True)
            return result.returncode
        print(f'[CI TEST {index}/{len(args.modules)}] PASS {module} ({elapsed:.1f}s)', flush=True)
    print(f'\nAll {len(args.modules)} unit-test modules passed in '
          f'{time.monotonic() - started:.1f}s.', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
