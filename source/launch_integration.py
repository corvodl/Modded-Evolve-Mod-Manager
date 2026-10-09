#!/usr/bin/env python3
"""Safe glue between the Evolve PAK editor and the user's EXISTING working v5 swap.

This module never patches Windows/game binaries. Setup adds missing launch helper
scripts, without overwriting an existing helper, state journal, or prepared files.
"""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import subprocess
import sys
from dataclasses import dataclass

ROOT = Path(__file__).resolve().parent
SUPPORT = ROOT / 'launcher_support'
STATE_FILE = 'controlled_swap_state.json'
REQUIRED_SCRIPTS = ('controlled_swap.py', 'reusable_restore.py', 'auto_launch_frida.py')


@dataclass(frozen=True)
class SwapStatus:
    phase: str
    message: str
    count: int = 0
    ready: int = 0
    backups: int = 0
    stage: str = ''
    game: str = ''


def state_status(swap_dir: Path) -> SwapStatus:
    journal = Path(swap_dir) / STATE_FILE
    if not journal.is_file():
        return SwapStatus('missing', 'Not prepared yet. Set up the launcher, then click Play with Mods.')
    try:
        state = json.loads(journal.read_text(encoding='utf-8'))
        files = state['files']
        phase = state['phase']
        if not isinstance(files, list) or not isinstance(phase, str):
            raise ValueError('Invalid journal fields')
        ready = sum(Path(item['ready']).is_file() for item in files)
        backups = sum(Path(item['backup']).is_file() for item in files)
        count = len(files)
        base = dict(count=count, ready=ready, backups=backups,
                    stage=state.get('stage',''), game=state.get('game',''))
        if phase == 'prepared':
            if backups:
                return SwapStatus('unsafe', 'Original backups exist despite prepared status. Stop and inspect.', **base)
            if ready != count or count < 1 or state.get('count', count) != count:
                return SwapStatus('unsafe', 'Prepared copies are incomplete. Do not launch; inspect the log.', **base)
            return SwapStatus('prepared', 'Mods are ready. Click Play with Mods.', **base)
        if phase == 'restored':
            if backups:
                return SwapStatus('unsafe', 'Some original backups still exist. Do not launch.', **base)
            return SwapStatus('restored', 'Original game files restored. Play with Mods will prepare your mods first.', **base)
        if phase in ('swapped', 'restoring', 'swapping', 'interrupted'):
            return SwapStatus(phase, 'Modified files may still be installed. Close both programs and restore originals.', **base)
        return SwapStatus('unsafe', 'Unknown swap state: '+phase, **base)
    except (OSError, ValueError, KeyError, TypeError) as e:
        return SwapStatus('unsafe', f'Cannot read swap journal: {e}')


def missing_scripts(swap_dir: Path):
    return [name for name in REQUIRED_SCRIPTS if not (Path(swap_dir) / name).is_file()]


def add_missing_scripts(swap_dir: Path):
    """Copy ONLY missing, trusted bundled files; never overwrite existing scripts/state."""
    target_dir = Path(swap_dir)
    if not target_dir.is_dir():
        raise FileNotFoundError('Swap folder does not exist: ' + str(target_dir))
    added = []
    for filename in missing_scripts(target_dir):
        src = SUPPORT / filename
        if not src.is_file():
            raise FileNotFoundError('Package is missing launcher support: ' + filename)
        dst = target_dir / filename
        # 'xb' protects state: no accidental overwrite even during races.
        with src.open('rb') as fsrc, dst.open('xb') as fdst:
            shutil.copyfileobj(fsrc, fdst)
        added.append(filename)
    return added


def launcher_commands(swap_dir: Path, stage_dir: Path, phase: str):
    """Return prelaunch commands, always using existing working swap files."""
    swap = Path(swap_dir)
    stage = Path(stage_dir)
    if missing_scripts(swap):
        raise FileNotFoundError('Launcher helper missing. Click Set Up Launcher first.')
    if phase in ('unsafe','swapped','swapping','restoring','interrupted'):
        raise RuntimeError('Original game files must be restored before launching again.')
    if not (stage / 'stage_status.json').is_file():
        raise FileNotFoundError('Custom signing stage is missing. Check Settings.')
    if (stage / 'REFRESHED-TO.json').exists():
        raise RuntimeError('This stage was superseded by Refresh Original PAKs. Select the new stage and swap folders from its refresh report.')
    commands = []
    if phase in ('missing','restored'):
        cmd = [sys.executable, '-u', str(swap / 'controlled_swap.py'), 'prepare',
               '--stage-dir', str(stage)]
        # Use exact installed game path when restoring an existing journal.
        if phase == 'restored':
            old = json.loads((swap / STATE_FILE).read_text(encoding='utf-8'))
            if old.get('game'):
                cmd += ['--game-root',old['game']]
        commands.append(('Preparing mod files',cmd))
    elif phase != 'prepared':
        raise RuntimeError('Unsupported swap state '+phase)
    commands.append(('Starting Evolve with Mods',[sys.executable, '-u', str(swap/'auto_launch_frida.py')]))
    return commands


def restore_command(swap_dir: Path):
    p=Path(swap_dir)/'reusable_restore.py'
    if not p.is_file():
        raise FileNotFoundError('Restore helper missing. Click Set Up Launcher first.')
    return [sys.executable, '-u', str(p)]


def package_status(stage_dir: Path, swap_dir: Path):
    stage = Path(stage_dir); swap=Path(swap_dir)
    notes = []
    if not (stage/'paks').is_dir(): notes.append('Custom-signed PAK folder missing')
    if not (stage/'mykeys'/'public_key.bin').is_file(): notes.append('Public signing key missing')
    if not (stage/'mykeys'/'private_key.pem').is_file(): notes.append('Private signing key missing')
    if not swap.is_dir(): notes.append('Launcher swap folder missing')
    elif missing_scripts(swap): notes.append('Launcher helper not set up')
    try:
        import frida
    except ImportError:
        notes.append('Frida is not installed for this Python')
    return notes
