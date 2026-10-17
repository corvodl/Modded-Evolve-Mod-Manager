"""Process memory and model-loader timing diagnostics with no game data.

These are observations, not GPU VRAM measurements. Fail closed if OS
interfaces are unsupported; the Performance Monitor should never delay Qt.
"""
from __future__ import annotations

import os
import time


def process_memory_mib():
    """Return (resident MiB, OS-observed peak MiB) or (None, None)."""
    try:
        import psutil
        info = psutil.Process(os.getpid()).memory_info()
        resident = getattr(info, 'rss', 0)
        peak = getattr(info, 'peak_wset', None)
        if peak is None:
            peak = getattr(info, 'peak_pagefile', None)
        return round(resident/1048576, 2), (
            round(peak/1048576, 2) if peak else None)
    except (ImportError, OSError, ValueError, AttributeError):
        pass
    if os.name == 'nt':
        try:
            import ctypes
            from ctypes import wintypes
            class _MemoryCounters(ctypes.Structure):
                _fields_ = [
                    ('cb', wintypes.DWORD),
                    ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t),
                ]
            data = _MemoryCounters()
            data.cb = ctypes.sizeof(data)
            ctypes.windll.kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            process = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(
                    process, ctypes.byref(data), data.cb):
                return (round(data.WorkingSetSize/1048576, 2),
                        round(data.PeakWorkingSetSize/1048576, 2))
        except (AttributeError, OSError, ValueError):
            pass
    try:
        if os.name == 'posix':
            import resource
            resident = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            # macOS reports bytes; Linux reports KiB.
            peak = resident/(1048576 if os.uname().sysname == 'Darwin' else 1024)
            return None, round(peak, 2)
    except (ImportError, OSError, ValueError):
        pass
    return None, None


def decoded_texture_megabytes(materials):
    """Estimate CPU/GPU uncompressed texture bytes without copying pixels."""
    if materials is None:
        return 0.
    total = 0
    for role in ('textures', 'normal_maps', 'specular_maps',
                 'gloss_maps', 'emissive_maps', 'detail_maps',
                 'detail_normal_maps', 'ao_maps', 'environment_maps'):
        for image in getattr(materials, role, ()):
            if image is not None:
                total += int(image.width)*int(image.height)*len(image.getbands())
    return round(total / 1048576, 2)


def format_stage_times(timings):
    if not timings:
        return 'No stage measurements yet'
    labels = {'reading_geometry':'Geometry / skeleton',
              'material_dds':'Materials / DDS',
              'animation_references':'Animation references'}
    return ', '.join('%s: %.1f ms' % (labels.get(k,k), float(v))
                     for k,v in timings.items())
