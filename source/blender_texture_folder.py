"""Write independently editable texture PNGs beside Blender model exports.

GLB remains self-contained for drag-and-drop Blender import. The companion
Textures/ folder exposes the *same exact* embedded image payloads so artists
can replace, inspect and relink textures without extracting binary glTF chunks.
No DDS recompression or downsizing occurs in this pass.
"""
from __future__ import annotations

import json
from pathlib import Path
import re
import tempfile

from model_textured_export import _unpack_glb

MAX_IMAGES = 256
MAX_IMAGE_BYTES = 64 * 1024 * 1024


def _safe_name(name):
    value = re.sub(r'[^a-zA-Z0-9_-]+', '_', str(name)).strip('_')[:65]
    return value or "Texture"


def texture_payloads(blob):
    doc, data = _unpack_glb(blob)
    images = doc.get("images", ())
    if len(images) > MAX_IMAGES:
        raise ValueError("Too many embedded textures for folder export.")
    output = []
    for i, item in enumerate(images):
        if "bufferView" not in item:
            raise ValueError("Texture image has an unsupported external URI.")
        mime = item.get("mimeType")
        if mime != "image/png":
            raise ValueError("Only embedded PNG image textures are supported.")
        view = doc["bufferViews"][item["bufferView"]]
        offset = view.get("byteOffset", 0)
        size = view["byteLength"]
        if (not 0 < size <= MAX_IMAGE_BYTES or offset < 0 or
                offset + size > len(data)):
            raise ValueError("Invalid or oversized embedded texture range.")
        payload = bytes(data[offset:offset + size])
        if not payload.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError("Embedded texture is not a valid PNG payload.")
        name = _safe_name(item.get("name") or ("Texture_%03d" % (i+1)))
        output.append((i, name, payload))
    return output


def write_texture_folder(blob, model_file):
    """Write <export directory>/Textures/<model>_<slot>.png, atomically.

    Existing textures are never silently replaced. Return the generated
    paths; names are prefixed by the model stem to avoid folder collisions.
    """
    model_file = Path(model_file)
    if not model_file.parent.is_dir():
        raise FileNotFoundError("Export destination directory is missing.")
    textures = texture_payloads(blob)
    destination = model_file.parent / "Textures"
    if destination.is_symlink():
        raise ValueError("Refusing symlinked texture output directory.")
    destination.mkdir(exist_ok=True)
    if not destination.is_dir():
        raise ValueError("Textures output path is not a directory.")
    prefix = _safe_name(model_file.stem)
    targets = [(destination / ("%s_%03d_%s.png" % (prefix,i+1,name)), raw)
               for i,name,raw in textures]
    manifest = destination / (prefix + "_texture_manifest.json")
    if any(path.exists() or path.is_symlink() for path,_ in targets) or manifest.exists():
        raise FileExistsError("Blender texture export already exists; choose a new destination.")
    details = []
    for path,raw in targets:
        # Embedded PNG IHDR stores exact source dimensions in big-endian order.
        if len(raw) < 24 or raw[12:16] != b'IHDR':
            raise ValueError("Embedded PNG is missing a valid IHDR dimension block.")
        width = int.from_bytes(raw[16:20], 'big')
        height = int.from_bytes(raw[20:24], 'big')
        if not (0 < width <= 16384 and 0 < height <= 16384):
            raise ValueError("Embedded texture dimensions are invalid.")
        details.append({"file":path.name,"width":width,"height":height})
    created = []
    try:
        for target,raw in targets:
            with target.open("xb") as stream:
                stream.write(raw)
            created.append(target)
        with manifest.open("x", encoding="utf-8") as stream:
            json.dump({
                "model":model_file.name,"embedded_texture_count":len(targets),
                "textures":details,
                "note":("No embedded PNG textures: check missing DDS references "
                        "and imported texture PAKs." if not details else
                        "PNG files are exact copies of embedded GLB images; "
                        "pixel resolution cannot exceed available source DDS.")
            },stream,indent=2)
        created.append(manifest)
    except BaseException:
        for target in created:
            target.unlink(missing_ok=True)
        raise
    return tuple(path for path,_ in targets)
