"""Read-only preview of verified CryEngine CrChF v7 skinned render meshes.

This decoder currently understands the Goliath-family POSITION half4 stream
(type 0, stride 8) and 16-bit triangle-index stream (type 5, stride 2).
Unsupported layouts are never guessed; no source file is modified.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import struct
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

from model_asset import DATA_STREAM_CHUNK, inspect_model

MAX_VERTICES = 250_000
MAX_TRIANGLES = 500_000
MAX_DRAW_TRIANGLES = 22_000


@dataclass(frozen=True)
class PreviewMesh:
    vertices: tuple
    triangles: tuple
    center: tuple
    radius: float
    source_name: str = ''

    @property
    def description(self):
        return f'{len(self.vertices):,} vertices | {len(self.triangles):,} triangles'


def read_preview_mesh(data: bytes, source_name: str = '') -> PreviewMesh:
    """Decode known stream types, validating all mesh bounds before rendering."""
    info = inspect_model(data)
    if info.format_name != 'CrChF' or info.version != 7:
        raise ValueError('3D preview currently supports CrChF v7 geometry only.')
    streams = {}
    for i in range(info.chunks):
        kind, chunk_id, size, offset = struct.unpack_from('<4I', info.chunk_table, i * 16)
        if kind != DATA_STREAM_CHUNK:
            continue
        _flags, stream_type, count, stride, _a, _b = struct.unpack_from('<6I', data, offset)
        if stream_type in (0, 5):
            if stream_type in streams:
                raise ValueError('Multiple mesh position/index streams; grouped meshes are not yet supported.')
            streams[stream_type] = (count, stride, offset + 24)
    if 0 not in streams or 5 not in streams:
        raise ValueError('No supported render mesh: select its .skinm or .chrm companion, if available.')
    vertex_count, vertex_stride, vertex_at = streams[0]
    index_count, index_stride, index_at = streams[5]
    if not (3 <= vertex_count <= MAX_VERTICES and 3 <= index_count <= MAX_TRIANGLES * 3):
        raise ValueError('Mesh exceeds the preview safety limits.')
    if index_count % 3:
        raise ValueError('Index stream does not contain complete triangles.')
    if vertex_stride != 8:
        raise ValueError(f'Unsupported position encoding (stride {vertex_stride}). This model cannot yet be previewed.')
    if index_stride != 2:
        raise ValueError(f'Unsupported index encoding (stride {index_stride}). This model cannot yet be previewed.')
    positions = []
    xs = []; ys = []; zs = []
    for x, y, z, w in struct.iter_unpack('<4e', memoryview(data)[vertex_at:vertex_at + vertex_count * 8]):
        if not all(math.isfinite(v) for v in (x,y,z,w)) or abs(w - 1) > 0.01:
            raise ValueError('Invalid packed half-float vertex coordinates.')
        positions.append((x,y,z))
        xs.append(x);ys.append(y);zs.append(z)
    indices = struct.unpack_from('<' + str(index_count) + 'H', data, index_at)
    if max(indices) >= vertex_count:
        raise ValueError('Model triangle index is outside the vertex array.')
    center = ((min(xs)+max(xs))/2, (min(ys)+max(ys))/2, (min(zs)+max(zs))/2)
    radius = max(max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs)) / 2
    if radius <= 1e-7:
        raise ValueError('Mesh bounds are degenerate.')
    triangles = tuple(zip(indices[::3], indices[1::3], indices[2::3]))
    return PreviewMesh(tuple(positions), triangles, center, radius, source_name)


def find_preview_mesh(workspace, relative, raw):
    """Prefer the selected render mesh; fall back to its same-folder companion."""
    suffix = Path(relative).suffix.lower()
    if suffix in ('.skin', '.chr', '.cgf', '.cga'):
        companion_rel = relative + 'm'
        root = (Path(workspace) / 'files').resolve()
        file = (root / companion_rel).resolve()
        if file.is_relative_to(root) and file.is_file() and not file.is_symlink():
            return read_preview_mesh(file.read_bytes(), companion_rel)
        raise ValueError('This character file stores skeleton/metadata. A .skinm or .chrm render-mesh companion is needed for preview.')
    return read_preview_mesh(raw, relative)


def render_mesh(mesh: PreviewMesh, width=720, height=480, yaw=-0.5, elevation=0.24,
                zoom=1.0, wireframe=False) -> Image.Image:
    """Simple non-GPU shaded/outline render. No material/texture decoding."""
    width = max(128, min(int(width), 1600))
    height = max(128, min(int(height), 1200))
    zoom = max(.2, min(float(zoom), 6.0))
    picture = Image.new('RGB',(width, height), '#20212a')
    canvas = ImageDraw.Draw(picture)
    cx, cy, cz = mesh.center
    ca, sa = math.cos(yaw), math.sin(yaw)
    ce, se = math.cos(elevation), math.sin(elevation)
    scale = min(width, height) * .42 / mesh.radius * zoom
    points=[]; depths=[]
    for x,y,z in mesh.vertices:
        a,b,c=x-cx,y-cy,z-cz
        rx=a*ca - b*sa
        ry=a*sa + b*ca
        screen_x=width*.5 + rx*scale
        screen_y=height*.5 + (-c*ce + ry*se)*scale
        depth=ry*ce + c*se
        points.append((screen_x, screen_y))
        depths.append(depth)
    # Avoid huge draw times on dense models; evenly sample only for display.
    every = max(1, math.ceil(len(mesh.triangles)/MAX_DRAW_TRIANGLES))
    faces=[]
    for i in range(0,len(mesh.triangles),every):
        a,b,c=mesh.triangles[i]
        p,q,r=points[a],points[b],points[c]
        signed = (q[0]-p[0])*(r[1]-p[1]) - (q[1]-p[1])*(r[0]-p[0])
        if abs(signed)<.15:
            continue
        ax,ay,az=mesh.vertices[a]
        bx,by,bz=mesh.vertices[b]
        cx0,cy0,cz0=mesh.vertices[c]
        ux,uy,uz=bx-ax,by-ay,bz-az
        vx,vy,vz=cx0-ax,cy0-ay,cz0-az
        nx,ny,nz=uy*vz-uz*vy,uz*vx-ux*vz,ux*vy-uy*vx
        mag=math.sqrt(nx*nx+ny*ny+nz*nz)
        if mag<1e-12:continue
        # Two-sided diffuse approximation. This is geometry only; the
        # original vertex normals and CryEngine materials aren't recreated.
        light=abs((.45*nx-.37*ny+.8*nz)/mag)
        tone=int(100 + 125*light)
        faces.append(((depths[a]+depths[b]+depths[c])/3,(p,q,r), tone))
    # Painter's depth sort: correct for simple surfaces, imperfect for intersections.
    faces.sort(key=lambda row: row[0],reverse=True)
    for _depth, polygon, tone in faces:
        if wireframe:
            canvas.line((*polygon[0],*polygon[1],*polygon[2],*polygon[0]), fill=(80,175,184), width=1)
        else:
            canvas.polygon(polygon, fill=(int(tone*.63),int(tone*.75),tone))
    canvas.text((12,height-26), f'Untextured preview | {mesh.description}',fill='#cad2df')
    return picture


class ModelPreview:
    """Embedded Tk image canvas with mouse drag rotation and wheel zoom."""
    def __init__(self, parent):
        import tkinter as tk
        from tkinter import ttk
        self.frame=ttk.Frame(parent)
        toolbar=ttk.Frame(self.frame)
        toolbar.pack(fill='x',padx=5,pady=(4,2))
        self.details=tk.StringVar(value='Choose a supported mesh')
        ttk.Label(toolbar,textvariable=self.details).pack(side='left',fill='x',expand=True)
        self.wireframe=tk.BooleanVar(value=False)
        ttk.Checkbutton(toolbar,text='Wireframe',variable=self.wireframe,command=self.render).pack(side='right',padx=4)
        ttk.Button(toolbar,text='Reset view',command=self.reset).pack(side='right',padx=4)
        self.label=tk.Label(self.frame,background='#20212a',foreground='white',text='Select a model to preview')
        self.label.pack(fill='both',expand=True)
        self.label.bind('<ButtonPress-1>',self.press)
        self.label.bind('<B1-Motion>',self.drag)
        self.label.bind('<MouseWheel>',self.scroll)
        self.label.bind('<Button-4>',lambda _:self.change_zoom(1.15))
        self.label.bind('<Button-5>',lambda _:self.change_zoom(1/1.15))
        self.label.bind('<Configure>',self.configure)
        self._pending=None;self._last=None;self._photo=None;self.mesh=None
        self.yaw=-.5;self.elevation=.24;self.zoom=1.0

    def set_mesh(self,mesh):
        self.mesh=mesh
        self.details.set(f'{Path(mesh.source_name).name} | {mesh.description} | Drag to rotate, wheel to zoom')
        self.reset()

    def reset(self):
        self.yaw=-.5;self.elevation=.24;self.zoom=1.0
        self.render()

    def press(self,event):
        self._last=(event.x,event.y)

    def drag(self,event):
        if self._last is None or self.mesh is None:return
        dx=event.x-self._last[0];dy=event.y-self._last[1]
        self._last=(event.x,event.y)
        self.yaw+=dx*.012
        self.elevation=max(-1.45,min(1.45,self.elevation+dy*.008))
        self.schedule()

    def scroll(self,event):
        self.change_zoom(1.12 if event.delta>0 else 1/1.12)

    def change_zoom(self,factor):
        self.zoom=max(.2,min(6.,self.zoom*factor));self.schedule()

    def configure(self,event):
        if self.mesh is not None and event.width>150:self.schedule()

    def schedule(self):
        if self._pending is None:
            self._pending=self.label.after(65,self.render)

    def render(self):
        self._pending=None
        if self.mesh is None:return
        width=max(300,self.label.winfo_width());height=max(260,self.label.winfo_height())
        img=render_mesh(self.mesh,width,height,self.yaw,self.elevation,self.zoom,self.wireframe.get())
        self._photo=ImageTk.PhotoImage(img,master=self.label)
        self.label.configure(image=self._photo,text='')

    def clear(self):
        if self._pending is not None:
            self.label.after_cancel(self._pending);self._pending=None
        self.mesh=None;self._photo=None
        self.details.set('Choose a supported mesh')
        self.label.configure(image='',text='Select a model to preview')
