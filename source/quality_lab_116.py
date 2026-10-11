"""v1.1.16 offline quality tools: project health, representative benchmarks and clip playlists.

All probes operate on verified unpacked projects, never on installed game files.
Benchmarks report what was actually measured; synthetic decompression is labelled
as a proxy and is not misrepresented as a signed-PAK extraction benchmark.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import zlib

from evolve_pak_workspace import load_workspace
from workspace_file_actions import checked_path

MAX_PROJECT_FILES = 150000
MAX_BENCHMARK_BYTES = 16 * 1024 * 1024


def project_health(workspace, *, file_limit=MAX_PROJECT_FILES):
    root = Path(workspace).resolve(strict=True)
    entries = load_workspace(root)['entries']
    if len(entries) > file_limit:
        raise ValueError('Project exceeds the health scanner file limit.')
    missing, unsafe, malformed = [], [], []
    total = 0
    xml_types = ('.xml', '.mtl', '.cdf', '.chrparams')
    for row in entries:
        relative = row.get('path')
        if not isinstance(relative, str):
            malformed.append(str(relative)[:100])
            continue
        try:
            from evolve_pak_workspace import normalized_name
            candidate = root / 'files' / normalized_name(relative)
            if candidate.is_symlink():
                unsafe.append(relative)
                continue
            if not candidate.is_file():
                missing.append(relative)
                continue
            target = checked_path(root, relative)
            total += target.stat().st_size
            if (relative.lower().endswith(xml_types) and
                    target.stat().st_size <= 2 * 1024 * 1024):
                raw = target.read_bytes()
                if (b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper()):
                    malformed.append(relative)
                elif raw[:1] == b'<':
                    try:
                        from xml.etree import ElementTree
                        ElementTree.fromstring(raw)
                    except ElementTree.ParseError:
                        malformed.append(relative)
        except (ValueError, OSError):
            unsafe.append(relative)
    issues = len(missing) + len(unsafe) + len(malformed)
    return {'project': str(root), 'files': len(entries), 'disk_bytes': total,
            'status': 'Healthy' if not issues else 'Needs attention',
            'missing_count': len(missing), 'unsafe_count': len(unsafe),
            'xml_issues_count': len(malformed),
            'missing_examples': missing[:30], 'unsafe_examples': unsafe[:30],
            'xml_examples': malformed[:30],
            'notes': ['Read-only scan; no backups or installed files were changed.',
                      'Checks file presence, workspace boundaries and parseable text XML.',
                      'A healthy result does not guarantee a valid rebuilt PAK signature.']}


def benchmark_workspace(workspace, *, sample_count=32):
    """Repeatable sampled IO/parse benchmark with explicit timing boundaries."""
    sample_count = max(1, min(100, int(sample_count)))
    root = Path(workspace).resolve(strict=True)
    started = time.perf_counter()
    rows = load_workspace(root)['entries']
    manifest_ms = (time.perf_counter() - started) * 1000
    samples = []
    skipped = 0
    for row in rows:
        if len(samples) >= sample_count:
            break
        try:
            target = checked_path(root, row['path'])
            if target.is_file() and not target.is_symlink() and 0 < target.stat().st_size <= MAX_BENCHMARK_BYTES:
                samples.append((row['path'], target))
        except (KeyError, OSError, ValueError):
            skipped += 1
    from model_asset import MODEL_EXTENSIONS
    model_candidates = [row for row in rows if Path(row.get('path','')).suffix.casefold() in MODEL_EXTENSIONS]
    if model_candidates and all(Path(name).suffix.casefold() not in MODEL_EXTENSIONS for name,_ in samples):
        try:
            candidate = checked_path(root,model_candidates[0]['path'])
            if candidate.is_file() and not candidate.is_symlink() and candidate.stat().st_size <= MAX_BENCHMARK_BYTES:
                samples.append((model_candidates[0]['path'],candidate))
        except (OSError,ValueError):
            pass
    start = time.perf_counter()
    byte_count = 0
    digests = []
    slowest = None
    slowest_time = -1
    for relative, target in samples:
        began = time.perf_counter()
        raw = target.read_bytes()
        byte_count += len(raw)
        digests.append(hashlib.sha256(raw).hexdigest())
        elapsed = (time.perf_counter() - began) * 1000
        if elapsed > slowest_time:
            slowest, slowest_time = relative, elapsed
    read_s = max(time.perf_counter() - start, 1e-9)
    proxy = b''.join(target.read_bytes()[:16384] for _, target in samples[:16])
    start = time.perf_counter()
    packed = zlib.compress(proxy) if proxy else b''
    if packed:
        assert zlib.decompress(packed) == proxy
    decompress_ms = (time.perf_counter()-start)*1000
    # Exercise geometry parsing when a supported model is available, without
    # claiming that it measured an OpenGL upload or GPU frame render.
    model_ms = None
    model_name = None
    texture_memory = 0
    dds_count = 0
    from dds_texture import parse_dds
    for rel,target in samples:
        if rel.lower().endswith('.dds') or rel.lower().endswith('.dds.0'):
            try:
                with target.open('rb') as stream:
                    header=stream.read(256)
                info=parse_dds(header)
                if 0 < info.width <= 16384 and 0 < info.height <= 16384:
                    texture_memory+=sum(max(1,info.width>>m)*max(1,info.height>>m)*4
                                        for m in range(min(16,info.mipmaps)))
                    dds_count+=1
            except (OSError,ValueError):
                continue
    for rel, target in samples:
        if rel.lower().endswith(('.cgf', '.chr', '.skin', '.cga')):
            try:
                from model_asset import inspect_model
                start = time.perf_counter()
                inspect_model(target.read_bytes())
                model_ms = round((time.perf_counter()-start)*1000, 3)
                model_name = rel
            except (ValueError, TypeError, OSError):
                pass
            break
    return {'project': str(root), 'manifest_ms': round(manifest_ms, 3),
            'read_mib_per_s': round(byte_count / 1048576 / read_s, 3),
            'read_total_bytes': byte_count, 'files_sampled': len(samples),
            'files_skipped': skipped, 'slowest_file': slowest,
            'slowest_file_ms': round(max(slowest_time,0),3),
            'model_parse_ms': model_ms, 'model_file': model_name,
            'texture_memory_estimated_mib': round(texture_memory / 1048576, 3),
            'texture_files_estimated': dds_count,
            'extraction_proxy_zlib_ms': round(decompress_ms, 3),
            'extraction_proxy_bytes': len(proxy),
            'checksum': hashlib.sha256(''.join(digests).encode()).hexdigest(),
            'disclaimer': 'Read-only sampled benchmark. Texture memory estimates assume RGBA8 GPU upload. zlib is a synthetic extraction proxy, '
                          'not signed-PAK unpacking time. OpenGL rendering/FPS is not measured.'}


def animation_playlist(clips, markers=(), *, loop=True, speed=1.0):
    if not isinstance(clips, (list, tuple)) or not 1 <= len(clips) <= 100:
        raise ValueError('Choose 1–100 animation clips.')
    values = []
    seen = set()
    for clip in clips:
        if not isinstance(clip, str) or not clip or len(clip) > 150:
            raise ValueError('Invalid animation clip label.')
        if clip not in seen:
            seen.add(clip)
            values.append(clip)
    if not 0.1 <= float(speed) <= 4:
        raise ValueError('Invalid playback speed.')
    fixed = []
    for record in markers:
        if not isinstance(record, (tuple, list)) or len(record) != 2:
            raise ValueError('Invalid timeline marker.')
        clip, seconds = record
        if clip not in seen or not 0 <= float(seconds) <= 100000:
            raise ValueError('Marker references an unknown clip or invalid time.')
        fixed.append([clip, float(seconds)])
    if len(fixed)>500:
        raise ValueError('Too many markers.')
    return {'format': 'evolve-animation-playlist', 'version': 1,
            'clips': values, 'markers': fixed, 'loop': bool(loop), 'speed': float(speed)}


def validate_animation_playlist(data, available):
    if not isinstance(data, dict) or data.get('format') != 'evolve-animation-playlist' or data.get('version') != 1:
        raise ValueError('Not an Evolve animation playlist.')
    result = animation_playlist(data.get('clips'), data.get('markers', []),
                                loop=data.get('loop', True), speed=data.get('speed', 1.0))
    allowed = set(available)
    matched = [clip for clip in result['clips'] if clip in allowed]
    return result, matched, [clip for clip in result['clips'] if clip not in allowed]


def compare_animation_clips(animations, first, second):
    if first not in animations or second not in animations:
        raise ValueError('Both clips must be decoded and available.')
    a, b = animations[first][0], animations[second][0]
    return {'first': first, 'first_seconds': round(float(a),4),
            'second': second, 'second_seconds': round(float(b),4),
            'duration_difference': round(float(a)-float(b),4),
            'note': 'Compares actual decoded clip durations. Pose/trajectory '
                    'comparison requires matching skeleton channels.'}
