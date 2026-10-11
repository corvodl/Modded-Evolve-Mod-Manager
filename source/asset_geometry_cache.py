"""Bounded, pickle-free GPU geometry cache for repeat model opens.

Only derived vertex/normal/UV data is cached. Cache is invalidated on model
geometry, topology, UV, shading, renderer version or material subset change.
"""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
import numpy as np

from gpu_model_preview import GPUArrays, prepare_geometry

CACHE_REVISION=b'gpu-preview-v1-1-15'
MAX_CACHE_BYTES=300*1024*1024


def _fingerprint(mesh,shading):
    h=hashlib.sha256(CACHE_REVISION+str(shading).encode())
    for member,dtype in ((mesh.vertices,'<f4'),(mesh.triangles,'<i4'),
                         (mesh.uvs,'<f4'),(mesh.subsets,'<i4')):
        arr=np.asarray(member,dtype=dtype)
        h.update(str(arr.shape).encode())
        h.update(arr.tobytes())
    return h.hexdigest()


def cached_geometry(mesh,cache_root,*,shading='faceted'):
    key=_fingerprint(mesh,shading)
    cache=Path(cache_root)
    path=cache/(key+'.npz')
    if path.is_file() and path.stat().st_size<MAX_CACHE_BYTES:
        try:
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
                    return GPUArrays(vertices,uvs,normals,indices,draws),True
        except (ValueError,OSError,KeyError):
            pass
    built=prepare_geometry(mesh,shading=shading)
    try:
        cache.mkdir(parents=True,exist_ok=True)
        if len(list(cache.glob('*.npz')))>200:
            prune_cache(cache)
        fd,temp=tempfile.mkstemp(prefix='tmp-geo-',suffix='.npz',dir=cache)
        os.close(fd)
        try:
            np.savez_compressed(temp,v=built.vertices,n=built.normals,
                u=built.uvs,i=built.indices,
                d=np.asarray(built.draws,dtype='<i4'))
            if Path(temp).stat().st_size<=MAX_CACHE_BYTES:
                os.replace(temp,path)
        finally:
            Path(temp).unlink(missing_ok=True)
    except (OSError,ValueError):
        pass
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
