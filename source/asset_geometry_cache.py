"""Bounded, pickle-free GPU geometry cache for repeat model opens.

The first frame never waits for disk cache compression. Verified derived GPU
arrays may be persisted asynchronously while the Qt view paints.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import os
from pathlib import Path
from threading import BoundedSemaphore
import tempfile
import numpy as np

from gpu_model_preview import GPUArrays, prepare_geometry
from performance_trace import record, span

CACHE_REVISION=b'gpu-preview-v1-3-p3-uncompressed'
MAX_CACHE_BYTES=300*1024*1024
_write_slots=BoundedSemaphore(1)
_writer=None


def _fingerprint(mesh,shading):
    h=hashlib.sha256(CACHE_REVISION+str(shading).encode())
    for member,dtype in ((mesh.vertices,'<f4'),(mesh.triangles,'<i4'),
                         (mesh.uvs,'<f4'),(mesh.subsets,'<i4')):
        arr=np.asarray(member,dtype=dtype)
        h.update(str(arr.shape).encode())
        h.update(arr.tobytes())
    return h.hexdigest()


def _save_cache(cache,path,built):
    """Atomic uncompressed NPZ: no expensive deflate on first paint."""
    with span('model','geometry_cache_write'):
        temp=None
        try:
            cache.mkdir(parents=True,exist_ok=True)
            if len(list(cache.glob('*.npz')))>200:
                prune_cache(cache)
            fd,temp=tempfile.mkstemp(prefix='tmp-geo-',suffix='.npz',dir=cache)
            os.close(fd)
            np.savez(temp,v=built.vertices,n=built.normals,
                     u=built.uvs,i=built.indices,
                     d=np.asarray(built.draws,dtype='<i4'))
            if Path(temp).stat().st_size<=MAX_CACHE_BYTES:
                os.replace(temp,path)
                temp=None
        except (OSError,ValueError):
            pass
        finally:
            if temp is not None:
                Path(temp).unlink(missing_ok=True)


def _queue_cache_write(cache,path,built):
    """At most one in-flight snapshot, never an unbounded memory queue."""
    global _writer
    if not _write_slots.acquire(blocking=False):
        record('model','geometry_cache_write_skipped',busy=True)
        return
    try:
        if _writer is None:
            _writer=ThreadPoolExecutor(max_workers=1,thread_name_prefix='EvolveGeometryCache')
        def work():
            try:
                _save_cache(cache,path,built)
            finally:
                _write_slots.release()
        _writer.submit(work)
    except RuntimeError:
        _write_slots.release()


def cached_geometry(mesh,cache_root,*,shading='faceted',defer_write=False):
    with span('model','geometry_fingerprint'):
        key=_fingerprint(mesh,shading)
    cache=Path(cache_root)
    path=cache/(key+'.npz')
    if path.is_file() and path.stat().st_size<MAX_CACHE_BYTES:
        try:
            with span('model','geometry_cache_read'):
                with np.load(path,allow_pickle=False) as data:
                    vertices=data['v'].copy()
                    normals=data['n'].copy()
                    uvs=data['u'].copy()
                    indices=data['i'].copy()
                    draws=tuple(tuple(int(x) for x in row) for row in data['d'])
                    if (vertices.ndim==2 and vertices.shape[1]==3
                            and normals.shape==vertices.shape
                            and uvs.shape==(len(vertices),2)
                            and indices.ndim==1 and len(indices)<=1500000
                            and np.isfinite(vertices).all()
                            and np.isfinite(normals).all()
                            and np.isfinite(uvs).all()):
                        record('model','geometry_cache_hit',hit=True)
                        return GPUArrays(vertices,uvs,normals,indices,draws),True
        except (ValueError,OSError,KeyError):
            pass
    with span('model','geometry_build'):
        built=prepare_geometry(mesh,shading=shading)
    if defer_write:
        _queue_cache_write(cache,path,built)
    else:
        _save_cache(cache,path,built)
    return built,False


def prune_cache(root,*,max_bytes=900*1024*1024):
    folder=Path(root)
    if not folder.is_dir():
        return 0
    files=[(p,p.stat()) for p in folder.glob('*.npz')
           if p.is_file() and not p.is_symlink()]
    used=sum(st.st_size for _,st in files)
    deleted=0
    for path,stat in sorted(files,key=lambda entry:entry[1].st_mtime):
        if used<=max_bytes:
            break
        try:
            path.unlink()
            used-=stat.st_size
            deleted+=1
        except OSError:
            pass
    return deleted
