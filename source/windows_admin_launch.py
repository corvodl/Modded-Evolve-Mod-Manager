"""Windows UAC entry-point guard for the interactive manager only.

The GUI requests elevation to allow firewall configuration. Workers and
test invocations are deliberately never relaunched.
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys


def _quote_windows(arguments):
    return subprocess.list2cmdline([str(argument) for argument in arguments])


def ensure_manager_administrator():
    """Return True to start the GUI, False when a UAC relaunch was started.

    A frozen build requests this through its requireAdministrator manifest.
    A source invocation uses ShellExecuteW(runas) exactly once.
    """
    if os.name != 'nt':
        return True
    if getattr(sys, 'frozen', False):
        return True
    if any(arg.startswith('--capture-') for arg in sys.argv[1:]):
        return True
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return True
        args = _quote_windows([os.path.abspath(sys.argv[0]), *sys.argv[1:]])
        result = ctypes.windll.shell32.ShellExecuteW(
            None, 'runas', sys.executable, args, None, 1)
        if int(result) <= 32:
            raise OSError('Windows did not approve elevation (ShellExecuteW %s).' % result)
        return False
    except (OSError, AttributeError) as exc:
        raise RuntimeError('Administrator access is required to launch Evolve Mod Manager: %s' % exc) from exc
