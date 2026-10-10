"""Read-only GPU OpenGL 1.1 model preview using a Win32 WGL Tk child window.

No external OpenGL Python binding or game DLL injection is required. Modern video
GPU drivers accelerate the indexed vertex arrays, depth test and UV sampling.
If Windows only provides Microsoft's GDI software OpenGL renderer, initialization
refuses it so the existing Pillow preview remains the fallback.

All WGL/context calls MUST stay on the Tk main thread. Nothing modifies mesh,
textures, archive contents or the installed game.
"""
from __future__ import annotations

import ctypes as ct
import math
import sys
from dataclasses import dataclass


class GPUUnavailable(RuntimeError):
    """Hardware OpenGL is unavailable or the Tk Win32 surface was rejected."""


def gpu_supported_platform():
    return sys.platform == 'win32'


@dataclass(frozen=True)
class GPUArrays:
    vertices: object
    uvs: object
    normals: object
    indices: object
    draws: tuple   # (index offset, index count, submaterial slot)


def prepare_geometry(mesh):
    """Validate and prepare tightly packed indexed buffers once per model.

    The GPU receives all triangles, unlike the previous drag-time CPU sampler.
    Normal vectors are generated from the original triangles for approximate
    lighting because the supported CrChF input has no decoded normal stream.
    """
    import numpy as np
    vertices = np.ascontiguousarray(mesh.vertices, dtype=np.float32)
    faces = np.ascontiguousarray(mesh.triangles, dtype=np.int64)
    if vertices.ndim != 2 or vertices.shape[1] != 3 or not (3 <= len(vertices) <= 250_000):
        raise ValueError('Invalid vertex coordinates for GPU preview.')
    if faces.ndim != 2 or faces.shape[1] != 3 or not (1 <= len(faces) <= 500_000):
        raise ValueError('Invalid triangle topology for GPU preview.')
    if not np.isfinite(vertices).all() or faces.min() < 0 or faces.max() >= len(vertices):
        raise ValueError('Invalid GPU mesh bounds, non-finite vertex or index.')
    if len(mesh.uvs) == len(vertices):
        uvs = np.ascontiguousarray(mesh.uvs, dtype=np.float32)
        if uvs.shape != (len(vertices), 2) or not np.isfinite(uvs).all():
            raise ValueError('Invalid mesh UVs.')
    else:
        uvs = np.zeros((len(vertices), 2), dtype=np.float32)
    tris = vertices[faces]
    face_normals = np.cross(tris[:, 1] - tris[:, 0], tris[:, 2] - tris[:, 0])
    normals = np.zeros_like(vertices)
    for column in range(3):
        np.add.at(normals, faces[:, column], face_normals)
    sizes = np.linalg.norm(normals, axis=1)
    np.divide(normals, np.maximum(sizes[:, None], 1e-6), out=normals)
    normals = np.ascontiguousarray(normals, dtype=np.float32)
    indices = np.ascontiguousarray(faces.astype(np.uint32, copy=False).ravel())
    draws = tuple(mesh.subsets) if mesh.subsets else ((0, len(indices), 0),)
    at = 0
    for first, count, slot in draws:
        if first != at or first % 3 or count <= 0 or count % 3 or not (0 <= slot < 256):
            raise ValueError('Invalid GPU material subset table.')
        at += count
    if at != len(indices):
        raise ValueError('Model subsets do not cover all triangles.')
    return GPUArrays(vertices, uvs, normals, indices, draws)


# Classic OpenGL on Windows exposes these operations directly via opengl32.dll.
GL_RENDERER=0x1F01; GL_VENDOR=0x1F00; GL_VERSION=0x1F02
GL_COLOR_BUFFER_BIT=0x4000; GL_DEPTH_BUFFER_BIT=0x0100; GL_DEPTH_TEST=0x0B71
GL_LEQUAL=0x0203; GL_LIGHTING=0x0B50; GL_LIGHT0=0x4000
GL_LIGHT_MODEL_TWO_SIDE=0x0B52; GL_AMBIENT=0x1200; GL_DIFFUSE=0x1201
GL_POSITION=0x1203; GL_NORMALIZE=0x0BA1; GL_COLOR_MATERIAL=0x0B57
GL_FRONT_AND_BACK=0x0408; GL_AMBIENT_AND_DIFFUSE=0x1602
GL_PROJECTION=0x1701; GL_MODELVIEW=0x1700
GL_FLOAT=0x1406; GL_UNSIGNED_INT=0x1405; GL_UNSIGNED_BYTE=0x1401
GL_VERTEX_ARRAY=0x8074; GL_NORMAL_ARRAY=0x8075; GL_TEXTURE_COORD_ARRAY=0x8078
GL_TRIANGLES=4; GL_TEXTURE_2D=0x0DE1; GL_RGB=0x1907
GL_TEXTURE_MIN_FILTER=0x2801; GL_TEXTURE_MAG_FILTER=0x2800
GL_TEXTURE_WRAP_S=0x2802; GL_TEXTURE_WRAP_T=0x2803
GL_LINEAR=0x2601; GL_REPEAT=0x2901
GL_UNPACK_ALIGNMENT=0x0CF5; GL_FILL=0x1B02; GL_LINE=0x1B01


class PIXELFORMATDESCRIPTOR(ct.Structure):
    _fields_ = [
        ('nSize', ct.c_ushort), ('nVersion', ct.c_ushort), ('dwFlags', ct.c_uint32),
        ('iPixelType', ct.c_ubyte), ('cColorBits', ct.c_ubyte),
        ('cRedBits', ct.c_ubyte), ('cRedShift', ct.c_ubyte),
        ('cGreenBits', ct.c_ubyte), ('cGreenShift', ct.c_ubyte),
        ('cBlueBits', ct.c_ubyte), ('cBlueShift', ct.c_ubyte),
        ('cAlphaBits', ct.c_ubyte), ('cAlphaShift', ct.c_ubyte),
        ('cAccumBits', ct.c_ubyte), ('cAccumRedBits', ct.c_ubyte),
        ('cAccumGreenBits', ct.c_ubyte), ('cAccumBlueBits', ct.c_ubyte),
        ('cAccumAlphaBits', ct.c_ubyte), ('cDepthBits', ct.c_ubyte),
        ('cStencilBits', ct.c_ubyte), ('cAuxBuffers', ct.c_ubyte),
        ('iLayerType', ct.c_ubyte), ('bReserved', ct.c_ubyte),
        ('dwLayerMask', ct.c_uint32), ('dwVisibleMask', ct.c_uint32),
        ('dwDamageMask', ct.c_uint32),
    ]


def _winfn(dll, name, restype, *args):
    fn = getattr(dll, name)
    fn.restype = restype
    fn.argtypes = list(args)
    return fn


class Win32GPUPreview:
    """Owned WGL context for a Tk Frame (HWND), on the Tk event thread only."""
    def __init__(self, parent):
        if not gpu_supported_platform():
            raise GPUUnavailable('OpenGL embedded preview requires Windows.')
        import tkinter as tk
        self.frame = tk.Frame(parent, background='#20212a', highlightthickness=0)
        self.frame.pack(fill='both', expand=True)
        self.frame.update_idletasks()
        self.hwnd = int(self.frame.winfo_id())
        self.hdc = 0
        self.hglrc = 0
        self.renderer = ''
        self.buffers = None
        self.materials = None
        self.texture_ids = []
        self._loaded_materials = None
        self.texture_limit = 768
        self._closed = False
        self._create_context()

    def _create_context(self):
        self.user32 = ct.WinDLL('user32', use_last_error=True)
        self.gdi32 = ct.WinDLL('gdi32', use_last_error=True)
        self.opengl = ct.WinDLL('opengl32', use_last_error=True)
        self.get_dc = _winfn(self.user32, 'GetDC', ct.c_void_p, ct.c_void_p)
        self.release_dc = _winfn(self.user32, 'ReleaseDC', ct.c_int, ct.c_void_p, ct.c_void_p)
        choose = _winfn(self.gdi32, 'ChoosePixelFormat', ct.c_int, ct.c_void_p, ct.POINTER(PIXELFORMATDESCRIPTOR))
        set_format = _winfn(self.gdi32, 'SetPixelFormat', ct.c_int, ct.c_void_p, ct.c_int, ct.POINTER(PIXELFORMATDESCRIPTOR))
        self.swap = _winfn(self.gdi32, 'SwapBuffers', ct.c_int, ct.c_void_p)
        self.wgl_create = _winfn(self.opengl, 'wglCreateContext', ct.c_void_p, ct.c_void_p)
        self.wgl_make = _winfn(self.opengl, 'wglMakeCurrent', ct.c_int, ct.c_void_p, ct.c_void_p)
        self.wgl_delete = _winfn(self.opengl, 'wglDeleteContext', ct.c_int, ct.c_void_p)
        try:
            self.hdc = self.get_dc(self.hwnd)
            if not self.hdc: raise GPUUnavailable('GetDC failed on Tk preview window.')
            pfd = PIXELFORMATDESCRIPTOR()
            pfd.nSize = ct.sizeof(PIXELFORMATDESCRIPTOR)
            pfd.nVersion = 1
            pfd.dwFlags = 0x4 | 0x20 | 0x1  # DRAW_TO_WINDOW, SUPPORT_OPENGL, DOUBLEBUFFER
            pfd.iPixelType = 0
            pfd.cColorBits = 32
            pfd.cDepthBits = 24
            pfd.cStencilBits = 8
            pf = choose(self.hdc, ct.byref(pfd))
            if not pf or not set_format(self.hdc, pf, ct.byref(pfd)):
                raise GPUUnavailable('Windows could not assign an accelerated OpenGL pixel format.')
            self.hglrc = self.wgl_create(self.hdc)
            if not self.hglrc or not self.wgl_make(self.hdc, self.hglrc):
                raise GPUUnavailable('Could not create an OpenGL rendering context.')
            glstr = _winfn(self.opengl, 'glGetString', ct.c_char_p, ct.c_uint)
            self.renderer = (glstr(GL_RENDERER) or b'').decode('utf-8', 'replace')
            version = (glstr(GL_VERSION) or b'').decode('utf-8', 'replace')
            # Win32 GDI Generic / Basic Render Driver are CPU emulations.
            if not self.renderer or 'gdi generic' in self.renderer.lower() or 'basic render' in self.renderer.lower():
                raise GPUUnavailable('No accelerated GPU OpenGL driver (' + (self.renderer or 'unknown') + ').')
            if not version:
                raise GPUUnavailable('Unable to identify OpenGL version.')
            self._bind_functions()
            self._init_gl()
            self.wgl_make(None, None)
        except BaseException:
            self.close()
            raise

    def _bind_functions(self):
        gl = self.opengl
        funcs = {
            'glEnable':(None,ct.c_uint),'glDisable':(None,ct.c_uint),
            'glClearColor':(None,ct.c_float,ct.c_float,ct.c_float,ct.c_float),
            'glClear':(None,ct.c_uint), 'glDepthFunc':(None,ct.c_uint),
            'glViewport':(None,ct.c_int,ct.c_int,ct.c_int,ct.c_int),
            'glMatrixMode':(None,ct.c_uint),'glLoadIdentity':(None,),
            'glOrtho':(None,ct.c_double,ct.c_double,ct.c_double,ct.c_double,ct.c_double,ct.c_double),
            'glLoadMatrixf':(None,ct.c_void_p),'glTranslatef':(None,ct.c_float,ct.c_float,ct.c_float),
            'glEnableClientState':(None,ct.c_uint),'glDisableClientState':(None,ct.c_uint),
            'glVertexPointer':(None,ct.c_int,ct.c_uint,ct.c_int,ct.c_void_p),
            'glNormalPointer':(None,ct.c_uint,ct.c_int,ct.c_void_p),
            'glTexCoordPointer':(None,ct.c_int,ct.c_uint,ct.c_int,ct.c_void_p),
            'glDrawElements':(None,ct.c_uint,ct.c_int,ct.c_uint,ct.c_void_p),
            'glColor4f':(None,ct.c_float,ct.c_float,ct.c_float,ct.c_float),
            'glColorMaterial':(None,ct.c_uint,ct.c_uint),'glPolygonMode':(None,ct.c_uint,ct.c_uint),
            'glLightfv':(None,ct.c_uint,ct.c_uint,ct.c_void_p),
            'glLightModeli':(None,ct.c_uint,ct.c_int),
            'glGenTextures':(None,ct.c_int,ct.c_void_p),
            'glDeleteTextures':(None,ct.c_int,ct.c_void_p),
            'glBindTexture':(None,ct.c_uint,ct.c_uint),
            'glTexParameteri':(None,ct.c_uint,ct.c_uint,ct.c_int),
            'glPixelStorei':(None,ct.c_uint,ct.c_int),
            'glTexImage2D':(None,ct.c_uint,ct.c_int,ct.c_int,ct.c_int,ct.c_int,ct.c_int,ct.c_uint,ct.c_uint,ct.c_void_p),
        }
        self.gl = {name:_winfn(gl,name,*signature) for name,signature in funcs.items()}

    def _call(self,name,*args):
        return self.gl[name](*args)

    def _init_gl(self):
        self._call('glClearColor',32/255,33/255,42/255,1)
        self._call('glEnable',GL_DEPTH_TEST)
        self._call('glDepthFunc',GL_LEQUAL)
        self._call('glEnable',GL_NORMALIZE)
        self._call('glEnable',GL_LIGHTING)
        self._call('glEnable',GL_LIGHT0)
        self._call('glEnable',GL_COLOR_MATERIAL)
        self._call('glColorMaterial',GL_FRONT_AND_BACK,GL_AMBIENT_AND_DIFFUSE)
        self._call('glLightModeli',GL_LIGHT_MODEL_TWO_SIDE,1)
        self._call('glLightfv',GL_LIGHT0,GL_AMBIENT,ct.cast((ct.c_float*4)(.42,.42,.42,1),ct.c_void_p))
        self._call('glLightfv',GL_LIGHT0,GL_DIFFUSE,ct.cast((ct.c_float*4)(.80,.80,.80,1),ct.c_void_p))
        self._call('glLightfv',GL_LIGHT0,GL_POSITION,ct.cast((ct.c_float*4)(-.5,.65,.8,0),ct.c_void_p))

    def set_mesh(self, mesh):
        self.buffers = prepare_geometry(mesh) if mesh is not None else None

    def set_materials(self, materials):
        self.materials = materials
        self._loaded_materials = None

    def set_quality(self, quality):
        limit = {'Fast':384, 'Balanced':768, 'Detailed':1024}.get(quality,768)
        if self.texture_limit != limit:
            self.texture_limit = limit
            self._loaded_materials = None

    def _upload_materials(self):
        # Context must already be current. Reuse GPU textures until materials change.
        if self._loaded_materials is self.materials:
            return
        if self.texture_ids:
            ids = (ct.c_uint*len(self.texture_ids))(*self.texture_ids)
            self._call('glDeleteTextures',len(ids),ct.cast(ids,ct.c_void_p))
        self.texture_ids = []
        if self.materials is not None:
            import numpy as np
            for image in self.materials.textures:
                if image is None:
                    self.texture_ids.append(0)
                    continue
                rgb = image.convert('RGB')
                from PIL import Image
                rgb.thumbnail((self.texture_limit,self.texture_limit), resample=Image.Resampling.BILINEAR)
                pixels = np.ascontiguousarray(rgb, dtype=np.uint8)
                out = ct.c_uint()
                self._call('glGenTextures',1,ct.byref(out))
                if out.value == 0:
                    raise GPUUnavailable('Could not allocate a GPU texture.')
                self.texture_ids.append(out.value)
                self._call('glBindTexture',GL_TEXTURE_2D,out.value)
                self._call('glTexParameteri',GL_TEXTURE_2D,GL_TEXTURE_MIN_FILTER,GL_LINEAR)
                self._call('glTexParameteri',GL_TEXTURE_2D,GL_TEXTURE_MAG_FILTER,GL_LINEAR)
                self._call('glTexParameteri',GL_TEXTURE_2D,GL_TEXTURE_WRAP_S,GL_REPEAT)
                self._call('glTexParameteri',GL_TEXTURE_2D,GL_TEXTURE_WRAP_T,GL_REPEAT)
                self._call('glPixelStorei',GL_UNPACK_ALIGNMENT,1)
                self._call('glTexImage2D',GL_TEXTURE_2D,0,GL_RGB,rgb.width,rgb.height,0,GL_RGB,GL_UNSIGNED_BYTE,ct.c_void_p(pixels.ctypes.data))
        self._loaded_materials = self.materials

    def draw(self, mesh, materials, *, yaw, elevation, zoom, wireframe, textured):
        import numpy as np
        if self._closed:raise GPUUnavailable('GPU preview context was closed.')
        if self.buffers is None:
            self.set_mesh(mesh)
        if materials is not self.materials:
            self.set_materials(materials)
        if not self.wgl_make(self.hdc,self.hglrc):
            raise GPUUnavailable('GPU rendering context could not be activated.')
        try:
            self._upload_materials()
            w,h=max(1,self.frame.winfo_width()),max(1,self.frame.winfo_height())
            self._call('glViewport',0,0,w,h)
            self._call('glClear',GL_COLOR_BUFFER_BIT | GL_DEPTH_BUFFER_BIT)
            self._call('glMatrixMode',GL_PROJECTION)
            self._call('glLoadIdentity')
            halfheight=mesh.radius/(.84*max(.2,min(6.,zoom)))
            halfwidth=halfheight*w/h
            self._call('glOrtho',-halfwidth,halfwidth,-halfheight,halfheight,-mesh.radius*16,mesh.radius*16)
            self._call('glMatrixMode',GL_MODELVIEW)
            self._call('glLoadIdentity')
            ca,sa=math.cos(yaw),math.sin(yaw)
            ce,se=math.cos(elevation),math.sin(elevation)
            rot=np.asarray([ca,-sa*se,-sa*ce,0,
                            -sa,-ca*se,-ca*ce,0,
                            0,ce,-se,0,
                            0,0,0,1],dtype=np.float32)
            self._call('glLoadMatrixf',ct.c_void_p(rot.ctypes.data))
            cx,cy,cz=mesh.center
            self._call('glTranslatef',-cx,-cy,-cz)
            self._call('glPolygonMode',GL_FRONT_AND_BACK,GL_LINE if wireframe else GL_FILL)
            self._call('glEnableClientState',GL_VERTEX_ARRAY)
            self._call('glEnableClientState',GL_NORMAL_ARRAY)
            self._call('glEnableClientState',GL_TEXTURE_COORD_ARRAY)
            buf=self.buffers
            self._call('glVertexPointer',3,GL_FLOAT,0,ct.c_void_p(buf.vertices.ctypes.data))
            self._call('glNormalPointer',GL_FLOAT,0,ct.c_void_p(buf.normals.ctypes.data))
            self._call('glTexCoordPointer',2,GL_FLOAT,0,ct.c_void_p(buf.uvs.ctypes.data))
            self._call('glColor4f',.75,.83,.95,1)
            fallback=next((x for x in self.texture_ids if x),0)
            for start,count,slot in buf.draws:
                tid=self.texture_ids[slot] if slot<len(self.texture_ids) else 0
                if not tid and len(buf.draws)==1:
                    tid=fallback
                if textured and not wireframe and tid and len(mesh.uvs)==len(mesh.vertices):
                    self._call('glEnable',GL_TEXTURE_2D)
                    self._call('glBindTexture',GL_TEXTURE_2D,tid)
                    self._call('glColor4f',1,1,1,1)
                else:
                    self._call('glDisable',GL_TEXTURE_2D)
                    self._call('glColor4f',.65,.76,.91,1)
                self._call('glDrawElements',GL_TRIANGLES,count,GL_UNSIGNED_INT,
                           ct.c_void_p(buf.indices.ctypes.data + start*4))
            self._call('glDisable',GL_TEXTURE_2D)
            for key in (GL_VERTEX_ARRAY,GL_NORMAL_ARRAY,GL_TEXTURE_COORD_ARRAY):
                self._call('glDisableClientState',key)
            self._call('glPolygonMode',GL_FRONT_AND_BACK,GL_FILL)
            if not self.swap(self.hdc):
                raise GPUUnavailable('GPU could not display the rendered frame.')
        finally:
            self.wgl_make(None,None)

    def close(self):
        if self._closed:return
        self._closed=True
        try:
            if self.hglrc:
                self.wgl_make(self.hdc,self.hglrc)
                if self.texture_ids and hasattr(self,'gl'):
                    ids=(ct.c_uint*len(self.texture_ids))(*self.texture_ids)
                    self._call('glDeleteTextures',len(ids),ct.cast(ids,ct.c_void_p))
                self.wgl_make(None,None)
                self.wgl_delete(self.hglrc)
                self.hglrc=0
        finally:
            if self.hdc:
                self.release_dc(self.hwnd,self.hdc)
                self.hdc=0
            try:
                self.frame.destroy()
            except Exception:
                pass
