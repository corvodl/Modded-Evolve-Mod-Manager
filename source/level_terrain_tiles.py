"""Strict observed terrain tile envelope; color/world mapping is unverified."""
import re
import struct
from pathlib import Path
from PIL import Image
from level_asset_mount import resolve_scoped

MAX_TILE_DIMENSION = 4096
MAX_TILE_BYTES = 8 + MAX_TILE_DIMENSION ** 2 * 3
MAX_ATLAS_DIMENSION = 4096


def decode_tile(data, *, channel_order='RGB'):
    if len(data) < 8 or len(data) > MAX_TILE_BYTES:
        raise ValueError('Invalid terrain tile envelope size')
    width,height = struct.unpack_from('<II',data)
    if (not 1 <= width <= MAX_TILE_DIMENSION or not 1 <= height <= MAX_TILE_DIMENSION
            or len(data) != 8 + width*height*3):
        raise ValueError('Invalid terrain tile dimensions or payload length')
    if channel_order not in ('RGB','BGR'):
        raise ValueError('Unsupported terrain tile channel order')
    return Image.frombytes('RGB',(width,height),data[8:],'raw',channel_order)


def load_tile_atlas(index, workspace, *, cancel=None, channel_order='RGB'):
    """Bound atlas memory; never fill gaps with invented terrain pixels.

    Candidate mapping uses tileX_Y as columns/rows. This remains an opt-in
    diagnostic preview until a local tester verifies landmarks and seams.
    """
    tiles = {}
    for key in index:
        match = re.fullmatch(r'tile(\d+)_(\d+)\.raw',key)
        if match:
            xy = tuple(map(int,match.groups()))
            if max(xy) >= 16:
                raise ValueError('Terrain tile grid exceeds bounds')
            tiles[xy] = key
    if not tiles:
        return None
    nx,ny = max(x for x,y in tiles)+1, max(y for x,y in tiles)+1
    if len(tiles) != nx*ny:
        raise ValueError('Terrain tile grid is incomplete')
    atlas = None
    expected = None
    for (x,y),key in sorted(tiles.items()):
        if cancel is not None and cancel.is_set():
            raise InterruptedError('Terrain tiles cancelled')
        record,state = resolve_scoped(index,key,workspace)
        if record is None:
            raise ValueError('Terrain tile ' + state + ': ' + key)
        path = Path(record['file'])
        if not 8 <= path.stat().st_size <= MAX_TILE_BYTES:
            raise ValueError('Terrain tile file exceeds bounds')
        tile = decode_tile(path.read_bytes(),channel_order=channel_order)
        if expected is None:
            expected = tile.size
            scale = min(1., MAX_ATLAS_DIMENSION/(nx*tile.width), MAX_ATLAS_DIMENSION/(ny*tile.height))
            size = (max(1,int(tile.width*scale)),max(1,int(tile.height*scale)))
            atlas = Image.new('RGB',(nx*size[0],ny*size[1]))
        elif tile.size != expected:
            raise ValueError('Terrain tile dimensions disagree')
        if tile.size != size:
            tile = tile.resize(size,Image.Resampling.BOX)
        atlas.paste(tile,(x*size[0],y*size[1]))
    return atlas
