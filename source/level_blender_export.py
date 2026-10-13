"""Read-only map-to-Blender GLB scene assembly.

Combines selected-map source terrain, compiled level placements and shared
model dependencies in *one* scene, while retaining separate editable objects.
Uses the same fenced, verified map mount as the live level preview.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np

from model_blender_export import glb_bytes
from model_textured_export import _pack_glb, _unpack_glb, embed_diffuse_textures

MAX_SCENE_NODES = 50000
MAX_GLTF_BINARY = 1500 * 1024 * 1024

# CryEngine local Z up -> glTF Y up, with no normalization of authored units.
_BASIS = np.array([[1,0,0,0],[0,0,1,0],[0,-1,0,0],[0,0,0,1]],dtype=np.float64)


def _world_matrix(world):
    q = np.asarray(world.rotate,dtype=np.float64)
    if not np.isfinite(q).all() or len(q)!=4 or np.linalg.norm(q)<1e-8:
        raise ValueError("Invalid map object quaternion.")
    w,x,y,z = q/np.linalg.norm(q)
    rot = np.array([
        [1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],
        [2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],
        [2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]],dtype=np.float64)
    scales=np.asarray(world.scale,dtype=np.float64)
    pos=np.asarray(world.pos,dtype=np.float64)
    if not np.isfinite(scales).all() or not np.isfinite(pos).all():
        raise ValueError("Non-finite map object transform.")
    local=np.eye(4,dtype=np.float64)
    local[:3,:3]=rot @ np.diag(scales)
    local[:3,3]=pos
    result=_BASIS @ local @ _BASIS.T
    return [float(n) for n in result.reshape(-1,order='F')]


class SceneAssembler:
    """Deduplicate mesh resources while retaining independent placed nodes."""
    def __init__(self, name):
        self.doc={
            "asset":{"version":"2.0","generator":"Evolve Mod Manager Level Scene Export"},
            "scene":0,
            "scenes":[{"name":name,"nodes":[]}],
            "nodes":[],"meshes":[],"materials":[],"textures":[],"images":[],
            "samplers":[],"accessors":[],"bufferViews":[],
            "buffers":[{"byteLength":0}],
            "extras":{"source":"Evolve Stage 2 map","supported_geometry_only":True}}
        self.binary=bytearray()
        self.mesh_ids={}

    def append_glb(self, key, source):
        if key in self.mesh_ids:
            return self.mesh_ids[key]
        doc, data=_unpack_glb(source)
        if len(doc.get("meshes",()))!=1:
            raise ValueError("Expected one decoded mesh resource.")
        if len(self.binary)+len(data)>MAX_GLTF_BINARY:
            raise ValueError("Combined map geometry exceeds memory budget.")
        self.binary.extend(b'\x00' * (-len(self.binary)%4))
        byte_offset=len(self.binary)
        self.binary.extend(data)
        offsets={k:len(self.doc[k]) for k in (
            "bufferViews","accessors","materials","textures","images","samplers")}
        for item in doc.get("bufferViews",()):
            value=dict(item)
            if value.get("buffer",0)!=0:
                raise ValueError("Unsupported external GLB buffer.")
            value["byteOffset"]=value.get("byteOffset",0)+byte_offset
            self.doc["bufferViews"].append(value)
        for item in doc.get("accessors",()):
            value=dict(item)
            if "bufferView" in value:
                value["bufferView"]+=offsets["bufferViews"]
            if value.get("sparse"):
                raise ValueError("Sparse glTF accessor not supported.")
            self.doc["accessors"].append(value)
        for item in doc.get("samplers",()):
            self.doc["samplers"].append(dict(item))
        for item in doc.get("images",()):
            value=dict(item)
            if "bufferView" in value:
                value["bufferView"]+=offsets["bufferViews"]
            elif "uri" in value:
                raise ValueError("Export must embed textures in GLB.")
            self.doc["images"].append(value)
        for item in doc.get("textures",()):
            value=dict(item)
            if "sampler" in value: value["sampler"]+=offsets["samplers"]
            if "source" in value: value["source"]+=offsets["images"]
            self.doc["textures"].append(value)
        for item in doc.get("materials",()):
            value=json.loads(json.dumps(item))
            pbr=value.get("pbrMetallicRoughness",{})
            if "baseColorTexture" in pbr:
                pbr["baseColorTexture"]["index"]+=offsets["textures"]
            for slot in ("normalTexture","emissiveTexture","occlusionTexture"):
                if slot in value: value[slot]["index"]+=offsets["textures"]
            self.doc["materials"].append(value)
        source_mesh=doc["meshes"][0]
        prims=[]
        for item in source_mesh.get("primitives",()):
            primitive=dict(item)
            primitive["attributes"]={
                name:accessor+offsets["accessors"]
                for name,accessor in item["attributes"].items()}
            if "indices" in primitive: primitive["indices"]+=offsets["accessors"]
            if "material" in primitive: primitive["material"]+=offsets["materials"]
            if "targets" in primitive:
                raise ValueError("Morph target mesh not supported in level scene.")
            prims.append(primitive)
        if not prims:
            raise ValueError("Model contains no drawable primitives.")
        index=len(self.doc["meshes"])
        self.doc["meshes"].append({"name":source_mesh.get("name",key),"primitives":prims})
        self.mesh_ids[key]=index
        return index

    def add_instance(self, mesh_id, name, *, world=None, metadata=None):
        if len(self.doc["nodes"])>=MAX_SCENE_NODES:
            raise ValueError("Map node count exceeds safety bound.")
        item={"mesh":mesh_id,"name":str(name)[:240]}
        if world is not None:
            item["matrix"]=_world_matrix(world)
        if metadata:
            item["extras"]=metadata
        index=len(self.doc["nodes"])
        self.doc["nodes"].append(item)
        self.doc["scenes"][0]["nodes"].append(index)
        return index

    def finish(self):
        if not self.doc["meshes"]:
            raise ValueError("No verified map geometry could be exported.")
        return _pack_glb(self.doc,self.binary)


def terrain_glb(terrain):
    """Convert a validated source heightmap into one independent editable mesh."""
    vertices=terrain.vertices
    triangles=[tuple(int(value) for value in face)
               for face in terrain.indices.reshape(-1,3)]
    uvs=np.column_stack((vertices[:,0]/max(terrain.unit*(terrain.width-1),1),
                          vertices[:,1]/max(terrain.unit*(terrain.height-1),1)))
    mesh=SimpleNamespace(vertices=vertices,triangles=triangles,uvs=uvs,subsets=())
    return glb_bytes(mesh,name="Terrain [source heightmap; untextured]")


def export_level_glb(project, destination, *, game=None, stage=None,
                     cache_root=None, cancel=None, progress=None,
                     selection=None):
    """Assemble multiple mapped PAK parts as ONE Blender-importable .glb.

    Selection is an optional set of scene GUIDs. Result includes explicit
    omissions; unsupported engine objects are never represented as geometry.
    """
    from qt_level_scene_render import prepare_scene, _asset_key
    destination=Path(destination)
    if destination.suffix.casefold()!=".glb" or not destination.parent.is_dir():
        raise ValueError("Select an existing output folder and .glb filename.")
    cancel=cancel or Event()
    prepared=prepare_scene(project,cancel,progress,game=game,stage=stage,
                           cache_root=cache_root)
    if prepared is None or cancel.is_set():
        raise InterruptedError("Level export cancelled.")
    assets,issues,details=prepared
    combined=SceneAssembler(str(project.level_name))
    report={"map":str(project.level_name),"objects":0,"mesh_resources":0,
            "terrain":False,"unresolved":dict(issues),"omitted":[]}
    terrain=details.get("terrain")
    if terrain is not None and selection is None:
        mesh=combined.append_glb("terrain",terrain_glb(terrain))
        combined.add_instance(mesh,"Terrain",metadata={
            "source":terrain.source,"decoded_samples":terrain.decoded_samples,
            "stride":terrain.stride})
        report["terrain"]=True
    for idx,obj in enumerate(project.scene):
        if cancel.is_set():
            raise InterruptedError("Level export cancelled.")
        if selection is not None and obj.id not in selection:
            continue
        key=_asset_key(obj)
        resource=assets.get(key)
        if resource is None:
            if obj.model: report["omitted"].append({
                "object":obj.name,"model":obj.model,"reason":obj.status})
            continue
        if key not in combined.mesh_ids:
            blob=glb_bytes(resource.mesh,name=obj.model)
            if resource.materials is not None:
                blob,_=embed_diffuse_textures(blob,resource.materials)
            mesh_id=combined.append_glb(key,blob)
        else:
            mesh_id=combined.mesh_ids[key]
        combined.add_instance(mesh_id,obj.name,world=obj.world,metadata={
            "guid":obj.id,"parent_guid":obj.parent_id,
            "model":obj.model,"material":obj.material,
            "source_layer":obj.source,"kind":obj.kind,"status":obj.status})
        report["objects"]+=1
    report["mesh_resources"]=len(combined.mesh_ids)
    blob=combined.finish()
    temp=destination.with_name(destination.name+".partial")
    if temp.exists():
        raise FileExistsError("A partial export already exists; inspect it first.")
    try:
        temp.write_bytes(blob)
        if cancel.is_set():
            raise InterruptedError("Level export cancelled.")
        temp.replace(destination)
    finally:
        if temp.exists():temp.unlink()
    return report
