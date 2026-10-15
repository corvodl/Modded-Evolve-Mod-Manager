"""Optional native Blender .blend conversion of a verified level-scene GLB.

Requires a user-installed Blender executable. The GLB can be exported without
Blender; native projects are created only by Blender itself, in background mode.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

BLENDER_SCRIPT = r'''
import bpy
import sys
from pathlib import Path

argv = sys.argv[sys.argv.index("--")+1:]
source = Path(argv[0]).resolve(strict=True)
destination = Path(argv[1]).resolve()
if source.suffix.lower() != ".glb" or destination.suffix.lower() != ".blend":
    raise ValueError("Expected GLB input and Blender project output")
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(source))
root = bpy.data.collections.new("Evolve Map")
bpy.context.scene.collection.children.link(root)
terrain = bpy.data.collections.new("Terrain")
objects = bpy.data.collections.new("Placed Objects")
root.children.link(terrain)
root.children.link(objects)
layers = {}
for obj in list(bpy.context.scene.objects):
    if obj.name.lower().startswith("terrain"):
        parent = terrain
    else:
        layer = str(obj.get("source_layer", "")).replace("\\", "/")
        if layer and len(layers) < 1000:
            if layer not in layers:
                group = bpy.data.collections.new("Layer - " + Path(layer).stem[:55])
                objects.children.link(group)
                layers[layer] = group
            parent = layers[layer]
        else:
            parent = objects
    for collection in list(obj.users_collection):
        collection.objects.unlink(obj)
    parent.objects.link(obj)
bpy.context.view_layer.update()
if not any(x.type == "MESH" for x in bpy.context.scene.objects):
    raise RuntimeError("Imported GLB has no mesh objects")
bpy.ops.wm.save_as_mainfile(filepath=str(destination))
'''

def convert_glb_to_blend(source, destination, *, blender=None, timeout=1800):
    """Create native .blend in a subprocess; refuse partial/overwrite files."""
    source=Path(source).resolve(strict=True)
    destination=Path(destination).resolve()
    if source.suffix.casefold()!=".glb" or not source.is_file():
        raise ValueError("Source must be an existing .glb file.")
    if destination.suffix.casefold()!=".blend" or not destination.parent.is_dir():
        raise ValueError("Select an existing export directory and .blend filename.")
    if destination.exists():
        raise FileExistsError("Cannot overwrite an existing Blender project.")
    executable=Path(blender).resolve() if blender else shutil.which("blender")
    if executable is None or not Path(executable).is_file():
        raise FileNotFoundError(
            "Blender is not installed or could not be found. GLB export works without Blender.")
    if not 30 <= timeout <= 7200:
        raise ValueError("Invalid Blender conversion timeout.")
    with tempfile.TemporaryDirectory(prefix="evolve-blender-") as temp:
        script=Path(temp)/"convert_scene.py"
        script.write_text(BLENDER_SCRIPT,encoding="utf-8")
        partial=destination.with_name(destination.stem+".partial.blend")
        if partial.exists():
            raise FileExistsError("Previous partial Blender project exists.")
        try:
            process=subprocess.run(
                [str(executable),"-b","--factory-startup","--python",str(script),
                 "--",str(source),str(partial)],
                timeout=timeout,check=False,capture_output=True,text=True,
                errors="replace")
            if process.returncode:
                raise RuntimeError(
                    "Blender scene import failed: "+(process.stderr or process.stdout)[-1800:])
            if not partial.is_file() or partial.stat().st_size < 20:
                raise RuntimeError("Blender did not save an output project. stdout: " + (process.stdout or "")[-1400:] + " stderr: " + (process.stderr or "")[-1400:])
            if partial.open("rb").read(7)!=b"BLENDER":
                raise RuntimeError("Blender project signature was invalid.")
            partial.replace(destination)
        finally:
            if partial.exists():partial.unlink()
    return destination
