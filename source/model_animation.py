"""Read-only glTF skeletal animation preview for imported CryEngine clips.

Supports one skinned mesh, linear/step/spherical interpolation and any number
of named animation clips. Original CAF/DBA controllers must first be converted
by an external compatible CryEngine animation converter. This module never
executes external programs or modifies original game data.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
from pathlib import Path
import struct

import numpy as np

from model_preview import PreviewMesh
from model_textured_export import _unpack_glb

MAX_GLB = 192 * 1024 * 1024
MAX_KEYS = 250_000
MAX_ACCESSOR_ELEMENTS = 1_500_000
MAX_BONES = 1024
COMPONENT = {5120: ('i1',1),5121: ('u1',1),5122: ('<i2',2),
             5123: ('<u2',2),5125: ('<u4',4),5126: ('<f4',4)}
WIDTH = {'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT4':16}


def _accessor(doc, binary, ident):
    acc = doc['accessors'][ident]
    if acc.get('sparse'):
        raise ValueError('Sparse glTF animation accessors are not supported.')
    n = int(acc['count'])
    width = WIDTH[acc['type']]
    dtype, byte_size = COMPONENT[acc['componentType']]
    if not 1 <= n <= MAX_ACCESSOR_ELEMENTS:
        raise ValueError('Animation accessor exceeds the safety limit.')
    view = doc['bufferViews'][acc['bufferView']]
    start = int(view.get('byteOffset',0)) + int(acc.get('byteOffset',0))
    size = byte_size*width
    stride = int(view.get('byteStride',size))
    view_start = int(view.get('byteOffset',0))
    view_end = view_start + int(view['byteLength'])
    if (stride < size or stride > 256 or start < view_start or
            start+(n-1)*stride+size > view_end or view_end > len(binary)):
        raise ValueError('Animation accessor points outside its binary buffer.')
    data = np.ndarray((n,width), dtype=dtype, buffer=binary,
                      offset=start, strides=(stride,byte_size)).copy()
    if acc.get('normalized'):
        if acc['componentType'] == 5121:
            data = data.astype(np.float32)/255.
        elif acc['componentType'] == 5123:
            data = data.astype(np.float32)/65535.
    if not np.isfinite(data).all():
        raise ValueError('Non-finite glTF animation accessor.')
    return data


def _quaternion_matrix(q):
    x,y,z,w = q
    n = x*x+y*y+z*z+w*w
    if n < 1e-12:
        return np.eye(4, dtype=np.float64)
    x,y,z,w = np.array((x,y,z,w), dtype=np.float64) * math.sqrt(2/n)
    xx,xy,xz,xw,yy,yz,yw,zz,zw = (
        x*x,x*y,x*z,x*w,y*y,y*z,y*w,z*z,z*w)
    result = np.eye(4)
    result[:3,:3] = [[1-yy-zz,xy-zw,xz+yw],
                     [xy+zw,1-xx-zz,yz-xw],
                     [xz-yw,yz+xw,1-xx-yy]]
    return result


def _local_matrix(node, changes=None):
    changes = changes or {}
    if 'matrix' in node and not changes:
        return np.asarray(node['matrix'],dtype=np.float64).reshape(4,4,order='F')
    if 'matrix' in node:
        raise ValueError('Animated matrix-only glTF joints require TRS conversion.')
    translation = np.asarray(changes.get('translation',node.get('translation',[0,0,0])),dtype=np.float64)
    rotation = np.asarray(changes.get('rotation',node.get('rotation',[0,0,0,1])),dtype=np.float64)
    scale = np.asarray(changes.get('scale',node.get('scale',[1,1,1])),dtype=np.float64)
    if not (translation.shape == (3,) and rotation.shape == (4,) and scale.shape == (3,)):
        raise ValueError('Malformed glTF joint transform.')
    mat = _quaternion_matrix(rotation)
    mat[:3,:3] *= scale[None,:]
    mat[:3,3] = translation
    return mat


def _slerp(first, last, fraction):
    a,b = np.asarray(first,dtype=np.float64),np.asarray(last,dtype=np.float64)
    dot = float(np.dot(a,b))
    if dot < 0.:
        b=-b;dot=-dot
    if dot > .9995:
        value = a + fraction*(b-a)
    else:
        theta = math.acos(np.clip(dot,-1,1))
        sin = math.sin(theta)
        value = math.sin((1-fraction)*theta)/sin*a + math.sin(fraction*theta)/sin*b
    return value/max(1e-12,np.linalg.norm(value))


@dataclass
class AnimationTrack:
    node: int
    path: str
    times: np.ndarray
    values: np.ndarray
    mode: str

    def sample(self, seconds):
        if len(self.times)==1 or seconds <= self.times[0]:
            return self.values[0]
        if seconds >= self.times[-1]:
            return self.values[-1]
        right = int(np.searchsorted(self.times,seconds,side='right'))
        left = right-1
        if self.mode == 'STEP':
            return self.values[left]
        fraction = (seconds-self.times[left])/max(1e-10,self.times[right]-self.times[left])
        if self.path=='rotation':
            return _slerp(self.values[left],self.values[right],fraction)
        return self.values[left]*(1-fraction)+self.values[right]*fraction


class AnimatedGLB:
    def __init__(self, blob, name='Animation'):
        if len(blob) > MAX_GLB:
            raise ValueError('Animated model exceeds 192 MiB.')
        self.doc,self.binary=_unpack_glb(blob)
        doc=self.doc
        self.nodes=doc['nodes']
        if len(self.nodes)>4096:
            raise ValueError('Too many model nodes.')
        skin_nodes=[(i,node) for i,node in enumerate(self.nodes) if 'mesh' in node and 'skin' in node]
        if len(skin_nodes)!=1:
            raise ValueError('Choose an animated GLB containing exactly one skinned mesh.')
        self.mesh_node,instance=skin_nodes[0]
        self.skin=doc['skins'][instance['skin']]
        self.joints=[int(x) for x in self.skin['joints']]
        if not 1<=len(self.joints)<=MAX_BONES:
            raise ValueError('Invalid rig joint count.')
        self.parents={}
        for i,node in enumerate(self.nodes):
            for child in node.get('children',[]):
                if child in self.parents:
                    raise ValueError('Ambiguous glTF node hierarchy.')
                self.parents[child]=i
        self.mesh_global=self._global_matrices({})[self.mesh_node]
        self.inverse_mesh=np.linalg.inv(self.mesh_global)
        inverse=_accessor(doc,self.binary,self.skin['inverseBindMatrices'])
        self.inverse_binds=inverse.reshape(-1,4,4).transpose(0,2,1).astype(np.float64)
        if len(inverse)!=len(self.joints):
            raise ValueError('Skin inverse-bind count mismatch.')
        meshes=doc['meshes'][instance['mesh']]
        pos_list=[];uv_list=[];face_list=[];joint_list=[];weight_list=[];groups=[]
        for primitive in meshes.get('primitives',[]):
            if primitive.get('mode',4)!=4:
                continue
            attributes=primitive['attributes']
            if 'JOINTS_0' not in attributes or 'WEIGHTS_0' not in attributes:
                raise ValueError('Skinned glTF primitive lacks joint or weight data.')
            coords=_accessor(doc,self.binary,attributes['POSITION']).astype(np.float64)
            joint=_accessor(doc,self.binary,attributes['JOINTS_0']).astype(np.int32)
            weights=_accessor(doc,self.binary,attributes['WEIGHTS_0']).astype(np.float64)
            if joint.shape != (len(coords),4) or weights.shape!=(len(coords),4):
                raise ValueError('Malformed joint or weight attribute.')
            if joint.min()<0 or joint.max()>=len(self.joints):
                raise ValueError('GLB skin joint index out of range.')
            sums=weights.sum(axis=1)
            if np.any(sums<.95) or np.any(sums>1.05):
                raise ValueError('GLB skin weights are not normalized.')
            weights=weights/sums[:,None]
            idx=_accessor(doc,self.binary,primitive['indices']).reshape(-1).astype(np.int64)
            if len(idx)%3 or len(idx)>1_500_000 or idx.min()<0 or idx.max()>=len(coords):
                raise ValueError('Invalid animated mesh triangle indices.')
            previous=sum(len(x) for x in pos_list)
            previous_indices=sum(len(x) for x in face_list)
            face_list.append(idx.reshape(-1,3)+previous)
            groups.append((previous_indices,len(idx),min(254,primitive.get('material',0))))
            pos_list.append(coords)
            joint_list.append(joint)
            weight_list.append(weights)
            if 'TEXCOORD_0' in attributes:
                uv_list.append(_accessor(doc,self.binary,attributes['TEXCOORD_0']))
            else:
                uv_list.append(np.zeros((len(coords),2)))
        if not pos_list:
            raise ValueError('Animated GLB has no supported triangle primitives.')
        self.positions=np.vstack(pos_list)
        self.skin_ids=np.vstack(joint_list)
        self.weights=np.vstack(weight_list)
        self.faces=np.vstack(face_list)
        self.groups=tuple(groups)
        self.uvs=np.vstack(uv_list)
        if len(self.positions)>250_000 or len(self.faces)>500_000:
            raise ValueError('Animated mesh exceeds render limits.')
        self.clips={}
        for i,animation in enumerate(doc.get('animations',[])):
            tracks=[];duration=0.
            for channel in animation.get('channels',[]):
                target=channel.get('target',{})
                node=int(target.get('node',-1))
                path=target.get('path')
                if node<0 or node>=len(self.nodes) or path not in ('translation','rotation','scale'):
                    continue
                sampler=animation['samplers'][channel['sampler']]
                mode=sampler.get('interpolation','LINEAR')
                if mode not in ('LINEAR','STEP'):
                    # Cubic-spline requires tangent handling; reject explicitly.
                    raise ValueError('Unsupported animation interpolation: '+mode)
                times=_accessor(doc,self.binary,sampler['input']).reshape(-1)
                vals=_accessor(doc,self.binary,sampler['output'])
                if (len(times)!=len(vals) or not 1 <= len(times) <= MAX_KEYS or
                        np.any(np.diff(times)<=0) or times[0]<0):
                    raise ValueError('Invalid clip timestamps.')
                tracks.append(AnimationTrack(node,path,times,vals,mode))
                duration=max(duration,float(times[-1]))
            if tracks and duration<=3600:
                name=animation.get('name') or 'Clip %d'%(i+1)
                while name in self.clips:
                    name+=' (copy)'
                self.clips[name]=(duration,tracks)
        if not self.clips:
            raise ValueError('No supported skeletal animation tracks in this GLB.')
        self.basename=name
        self.base_vertices=self._transform_pose({})
        self.preview_mesh=self._make_mesh(self.base_vertices)
        self.materials=self._embedded_materials()

    def _embedded_materials(self):
        """Decode PNG/JPEG images embedded within this local GLB; no URL loading."""
        from io import BytesIO
        from PIL import Image
        from material_preview import PreviewMaterials
        materials = self.doc.get('materials', [])
        embedded = self.doc.get('images', [])
        textures = self.doc.get('textures', [])
        used = []
        for material in materials[:64]:
            index = material.get('pbrMetallicRoughness',{}).get(
                'baseColorTexture',{}).get('index', -1)
            image = None
            if isinstance(index,int) and 0<=index<len(textures):
                image_id = textures[index].get('source',-1)
                if isinstance(image_id,int) and 0<=image_id<len(embedded):
                    item = embedded[image_id]
                    if item.get('mimeType') in ('image/png','image/jpeg') and 'bufferView' in item:
                        view = self.doc['bufferViews'][item['bufferView']]
                        start=int(view.get('byteOffset',0))
                        length=int(view['byteLength'])
                        if 0 < length <= 16*1024*1024 and start>=0 and start+length<=len(self.binary):
                            try:
                                with Image.open(BytesIO(self.binary[start:start+length])) as im:
                                    if im.width*im.height<=8_000_000:
                                        im.load()
                                        image=im.convert('RGB')
                            except (OSError,ValueError):
                                pass
            used.append(image)
        return PreviewMaterials(tuple(used),
            tuple(m.get('name','Material %d'%i) for i,m in enumerate(materials[:64])),
            'Embedded GLB textures')

    @classmethod
    def from_file(cls,path):
        file=Path(path)
        if file.suffix.lower()!='.glb' or not file.is_file() or file.stat().st_size>MAX_GLB:
            raise ValueError('Select a GLB animation (max 192 MiB).')
        return cls(file.read_bytes(),file.stem)

    def _global_matrices(self,changes):
        result={}
        active=set()
        def world(n):
            if n in result:return result[n]
            if n in active:raise ValueError('Cycle in animated skeleton hierarchy.')
            active.add(n)
            mat=_local_matrix(self.nodes[n],changes.get(n))
            if n in self.parents:
                mat=world(self.parents[n])@mat
            active.remove(n)
            result[n]=mat
            return mat
        for joint in range(len(self.nodes)):
            world(joint)
        return result

    def _transform_pose(self,changes):
        world=self._global_matrices(changes)
        matrices=np.stack([self.inverse_mesh @ world[j] @ ib
                           for j,ib in zip(self.joints,self.inverse_binds)])
        transform=matrices[self.skin_ids]
        hom=np.concatenate([self.positions,np.ones((len(self.positions),1))],axis=1)
        moved=np.einsum('nvij,nj->nvi',transform,hom)[:,:,:3]
        return np.einsum('nvc,nv->nc',moved,self.weights)

    def _make_mesh(self,vertices):
        # Convert glTF Y-up back to the CryEngine Z-up camera convention.
        cr=np.stack((vertices[:,0],-vertices[:,2],vertices[:,1]),axis=1)
        mn,mx=cr.min(axis=0),cr.max(axis=0)
        center=(mn+mx)*.5
        radius=max(float((mx-mn).max())*.5,1e-6)
        return PreviewMesh(
            tuple(map(tuple,cr)),tuple(map(tuple,self.faces)),
            tuple(center),radius,self.basename,tuple(map(tuple,self.uvs)),
            tuple(self._draw_groups()))

    def _draw_groups(self):
        return self.groups if hasattr(self,'groups') else (
            (0,len(self.faces)*3,0),)

    def sample(self,clip,seconds):
        if clip not in self.clips:
            raise ValueError('Select an available animation clip.')
        duration,tracks=self.clips[clip]
        seconds=float(seconds)%max(.001,duration)
        changes={}
        for track in tracks:
            changes.setdefault(track.node,{})[track.path]=track.sample(seconds)
        pose=self._transform_pose(changes)
        return np.stack((pose[:,0],-pose[:,2],pose[:,1]),axis=1).astype(np.float32)
