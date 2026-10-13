"""Verified CryEngine Stage 2 source Heightmap.dat geometry, not a flat proxy.

Validated against original Tutorial (512x512) and Desert Canyon (1024x1024)
decrypted source data: tagged uint16 samples, two exact length fields, an
explicit layer-ID successor, and source/world XY orientation checked against
placed map objects. Terrain textures/vegetation remain separate formats.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from xml.etree import ElementTree as ET

import numpy as np

MAX_DIMENSION = 4096
MAX_SOURCE_BYTES = 80 * 1024 * 1024
MAX_RENDER_GRID = 257
NAME = b'HeightmapData'
NEXT_SECTION = b'\x1bHeightmapLayerIdBitmap_ver2'


@dataclass(frozen=True)
class TerrainMesh:
    vertices: np.ndarray
    normals: np.ndarray
    indices: np.ndarray
    width: int
    height: int
    unit: float
    max_height: float
    decoded_samples: int
    stride: int
    source: str


def _samples(data):
    """Decode only an explicitly validated Source HeightmapData section."""
    if not 24 <= len(data) <= MAX_SOURCE_BYTES or data[0] != 0xff:
        raise ValueError('Unsupported or oversized source Heightmap.dat header.')
    xml_length = struct.unpack_from('<H',data,1)[0]
    if not 15 <= xml_length <= 65535 or 3+xml_length >= len(data):
        raise ValueError('Terrain XML length is invalid.')
    xml = data[3:3+xml_length]
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper():
        raise ValueError('Terrain XML contains forbidden entities.')
    try:
        meta = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError('Invalid terrain XML.') from error
    if meta.tag != 'Heightmap':
        raise ValueError('Not a CryEngine Heightmap document.')
    try:
        width = int(meta.attrib['Width'])
        height = int(meta.attrib['Height'])
        unit = float(meta.attrib['UnitSize'])
        maximum = float(meta.attrib['MaxHeight'])
    except (KeyError, ValueError, OverflowError) as error:
        raise ValueError('Terrain dimensions or world scale are malformed.') from error
    if (not 16 <= width <= MAX_DIMENSION or
        not 16 <= height <= MAX_DIMENSION or
        width & (width-1) or height & (height-1) or
        not math.isfinite(unit) or not 0 < unit <= 1000 or
        not math.isfinite(maximum) or not 0 < maximum <= 65536):
        raise ValueError('Unsupported terrain resolution or world scale.')
    count = width * height
    payload_bytes = 2 * count
    header = 3+xml_length
    # The leading u32 is the number of serialized data sections. Source
    # files verified for this layout start with a 14-byte section label,
    # followed by 'W', matching uint32 lengths and a zero flags field.
    expected = struct.pack('<I',4) + b'\x0e' + NAME + b'W'
    if data[header:header+len(expected)] != expected:
        raise ValueError('Unsupported HeightmapData section layout.')
    at = header + len(expected)
    if len(data) < at+12+payload_bytes+len(NEXT_SECTION):
        raise ValueError('Truncated terrain height payload.')
    first, second, flags = struct.unpack_from('<III',data,at)
    if first != payload_bytes or second != payload_bytes or flags != 0:
        raise ValueError('HeightmapData length or flags mismatch.')
    start = at + 12
    if data[start+payload_bytes:start+payload_bytes+len(NEXT_SECTION)] != NEXT_SECTION:
        raise ValueError('Terrain payload successor marker is missing.')
    grid = np.frombuffer(data,dtype='<u2',count=count,offset=start).reshape(width,height)
    return grid,width,height,unit,maximum


def decode_terrain(data, *, source='source Heightmap.dat',
                   max_grid=MAX_RENDER_GRID):
    """Return world-space terrain triangles with source elevations.

    The source's first dimension is world X, and the second world Y.
    Height is uint16 * authored MaxHeight / 65535. This was checked
    against placed road/decals in two different signed original maps.
    The render grid is decimated in source units, retaining the final
    boundary on each axis and never inventing missing samples.
    """
    grid,width,height,unit,maximum = _samples(data)
    if not 16 <= max_grid <= MAX_RENDER_GRID:
        raise ValueError('Invalid terrain grid limit.')
    stride = max(1,math.ceil((max(width,height)-1)/(max_grid-1)))
    x = np.arange(0,width,stride,dtype=np.int32)
    y = np.arange(0,height,stride,dtype=np.int32)
    if x[-1] != width-1:
        x = np.r_[x,width-1]
    if y[-1] != height-1:
        y = np.r_[y,height-1]
    xx,yy = np.meshgrid(x,y,indexing='ij')
    z = grid[np.ix_(x,y)].astype(np.float32)*(maximum/65535.)
    vertices = np.stack((xx*unit,yy*unit,z),axis=-1).reshape(-1,3).astype(np.float32)
    # Small finite differences in source world units, with Z-up normals.
    gx,gy = np.gradient(z,x.astype(np.float32)*unit,
                        y.astype(np.float32)*unit,edge_order=1)
    normals = np.stack((-gx,-gy,np.ones_like(z)),axis=-1).reshape(-1,3)
    normals /= np.maximum(np.linalg.norm(normals,axis=1,keepdims=True),1e-8)
    ny=len(y)
    ii = np.arange(len(x)-1,dtype=np.uint32)[:,None]
    jj = np.arange(len(y)-1,dtype=np.uint32)[None,:]
    a = (ii*ny+jj).reshape(-1)
    b = a+ny
    c = a+1
    d = b+1
    indices = np.stack((a,b,c,b,d,c),axis=-1).reshape(-1).astype(np.uint32)
    for array in (vertices,normals,indices):
        array.setflags(write=False)
    return TerrainMesh(vertices,normals,indices,width,height,unit,maximum,
                       width*height,stride,source)
