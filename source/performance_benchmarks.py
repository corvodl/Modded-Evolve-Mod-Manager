"""Opt-in, non-destructive Evolve manager microbenchmarks.

Run from source/ with Python 3.11. No installed game state is edited.
Outputs stage names and numeric timing summaries, not private file paths.
Real GL first-frame/final-quality data comes from EVOLVE_PERF_TRACE=1, not
from offscreen QT_QPA_PLATFORM tests.
"""
from __future__ import annotations
import argparse
import gc
import json
from pathlib import Path
import statistics
import time


def measure(task, *, repeats=5):
    if repeats < 5:
        raise ValueError('Use at least five comparable measurements.')
    samples = []
    for _ in range(repeats):
        gc.collect()
        begin = time.perf_counter()
        task()
        samples.append(round((time.perf_counter() - begin) * 1000, 3))
    return dict(samples_ms=samples, median_ms=round(statistics.median(samples), 3),
                min_ms=min(samples), max_ms=max(samples), count=len(samples))


def bench_spatial_index(repeats):
    from types import SimpleNamespace
    from level_working_set import PlacementGrid
    scene = []
    for i in range(25000):
        scene.append(SimpleNamespace(model='mesh', status='visible',
            world=SimpleNamespace(pos=(i % 250 * 100., i // 250 * 100., 0.)),
            material_id='variant-' + str(i // 3)))
    index = PlacementGrid(scene, lambda obj: obj.material_id)
    cameras = [(i * 23., i * 17., 0.) for i in range(60)]
    def traverse():
        results = []
        for point in cameras:
            results.append(index.nearby(point, 1100., margin=1.2))
        if not results or not all(isinstance(r, dict) for r in results):
            raise AssertionError('Spatial query failed')
    return {'build_ms': measure(lambda: PlacementGrid(
        scene, lambda obj: obj.material_id), repeats=repeats),
        '60_camera_queries_ms': measure(traverse, repeats=repeats),
        'placements': len(scene), 'unique_cells': len(index.cells)}


def bench_model(workspace, relative, repeats):
    from studio_polish_117 import prepare_model_cached, clear_model_cache
    workspace = Path(workspace).expanduser().resolve(strict=True)
    clear_model_cache(workspace)
    def open_model():
        result, _, _ = prepare_model_cached(workspace, relative)
        if result.get('mesh') is None:
            raise AssertionError('Model geometry is missing')
    first = measure(open_model, repeats=repeats)
    # A new process or an edited dependency is not equivalent to this in-memory
    # warm measurement. Disk geometry caches may already have been populated.
    return {'repeat_model_prepare_ms': first, 'reads': repeats,
            'cache_mode': 'first invocation plus in-memory warm repeats'}


def bench_map(workspace, repeats):
    from level_editor_core import LevelProject
    folder = Path(workspace).expanduser().resolve(strict=True)
    objects = []
    def parse():
        project = LevelProject(folder)
        objects.append(len(project.objects))
    timings = measure(parse, repeats=repeats)
    return {'level_project_parse_ms': timings,
            'object_counts': objects,
            'cache_mode': 'repeated parse; filesystem cache may be warm'}


def bench_crypto(size, repeats):
    import hashlib
    from evolve_video_pak_tool_fixed import twofish_ctr
    payload = bytes(i % 251 for i in range(size))
    key, iv = bytes(range(16)), bytes(range(15, -1, -1))
    encrypted = twofish_ctr(payload, key, iv)
    if twofish_ctr(encrypted, key, iv) != payload:
        raise ValueError('Twofish CTR reference round-trip mismatch')
    def encrypt():
        output = twofish_ctr(payload, key, iv)
        if hashlib.sha256(output).digest() != hashlib.sha256(encrypted).digest():
            raise ValueError('Non-deterministic CTR encryption result')
    return {'twofish_ctr_ms': measure(encrypt, repeats=repeats),
            'input_bytes': size, 'bytes_per_second_estimate': round(
                (size / (statistics.median(measure(encrypt, repeats=repeats)
                                            ['samples_ms']) / 1000)), 1)}
 

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('scenario', choices=('map', 'model', 'spatial', 'crypto'))
    parser.add_argument('--workspace', help='Extracted (unmodified) workspace')
    parser.add_argument('--relative', help='Virtual model path for model scenario')
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--bytes', type=int, default=1024*1024,
                        help='Synthetic crypto size, default 1 MiB')
    opts = parser.parse_args(argv)
    if opts.repeats < 5:
        parser.error('At least 5 repeats required')
    if opts.scenario in ('map', 'model') and not opts.workspace:
        parser.error('--workspace is required')
    if opts.scenario == 'model' and not opts.relative:
        parser.error('--relative is required for model')
    if opts.scenario == 'crypto' and not 1 <= opts.bytes <= 64*1024*1024:
        parser.error('--bytes must be 1 to 67108864')
    if opts.scenario == 'spatial':
        result = bench_spatial_index(opts.repeats)
    elif opts.scenario == 'map':
        result = bench_map(opts.workspace, opts.repeats)
    elif opts.scenario == 'model':
        result = bench_model(opts.workspace, opts.relative, opts.repeats)
    else:
        result = bench_crypto(opts.bytes, opts.repeats)
    print(json.dumps({'scenario': opts.scenario, 'repeats': opts.repeats,
                      'results': result}, indent=2))


if __name__ == '__main__':
    main()
