"""Real headless Blender import/save/reopen integration check (no game files)."""
from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import numpy as np
from blender_native_project import convert_glb_to_blend
from level_blender_export import SceneAssembler
from level_scene_core import WorldTransform
from model_blender_export import glb_bytes
from types import SimpleNamespace

def main():
    mesh = SimpleNamespace(
        vertices=[(0.,0.,0.),(1.,0.,0.),(0.,1.,0.)],
        triangles=[(0,1,2)],uvs=[(0,0),(1,0),(0,1)],subsets=())
    assembler=SceneAssembler("Tutorial")
    identity=assembler.append_glb("rock",glb_bytes(mesh))
    assembler.add_instance(identity,"Rock A",world=WorldTransform(pos=(1,2,3)))
    assembler.add_instance(identity,"Rock B",world=WorldTransform(pos=(11,2,3)))
    with TemporaryDirectory() as root:
        path=Path(root)
        glb=path/"source.glb"
        blend=path/"scene.blend"
        glb.write_bytes(assembler.finish())
        convert_glb_to_blend(glb,blend,blender=sys.argv[1])
        assert blend.read_bytes()[:7] == b"BLENDER"
        print("BLENDER PROJECT CREATED:",blend.stat().st_size)
        import subprocess
        script=path/"validate.py"
        script.write_text(
            "import bpy\n"
            "ob={o.name:o for o in bpy.data.objects}\n"
            "assert 'Rock A' in ob and 'Rock B' in ob, list(ob)\n"
            "assert ob['Rock A'].type=='MESH'\n"
            "assert ob['Rock A'].data is ob['Rock B'].data\n"
            "a=ob['Rock A'].matrix_world.translation\n"
            "b=ob['Rock B'].matrix_world.translation\n"
            "assert abs((b-a).length-10)<0.01, (a,b)\n"
            "print('BLENDER IMPORT POSES AND INSTANCES PASS')\n",
            encoding="utf-8")
        out=subprocess.run([sys.argv[1],"-b",str(blend),"--python",
                            str(script)],capture_output=True,text=True,timeout=180)
        print(out.stdout[-2000:])
        if out.returncode:
            print(out.stderr[-3000:])
            raise RuntimeError("Reopening the created .blend did not verify.")
        print("BLENDER SCENE ROUNDTRIP PASS")

if __name__ == "__main__":
    main()
