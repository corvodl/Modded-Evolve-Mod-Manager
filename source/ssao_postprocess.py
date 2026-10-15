"""Optional screen-space contact shading for the Qt OpenGL compatibility viewer.

This is deliberately a separate depth pass: it does not require game shaders,
PNGs, or access to CryEngine's renderer. Default is Off. Unsupported FBO or
GLSL contexts leave the primary model rendering intact and report a warning.
"""
from __future__ import annotations

# Number of full-screen depth lookups is bounded for hardware that only
# exposes OpenGL 2.1 compatibility contexts.
QUALITY = {'Off': (0, 0.), 'Low': (8, 4.), 'Medium': (12, 7.),
           'High': (16, 10.)}

VERTEX = """
#version 120
varying vec2 screen_uv;
void main() {
    screen_uv = gl_MultiTexCoord0.st;
    gl_Position = gl_Vertex;
}
"""

FRAGMENT = """
#version 120
uniform sampler2D scene_depth;
uniform vec2 texel_size;
uniform vec2 offsets[16];
uniform float sample_radius;
uniform float strength;
uniform int sample_count;
uniform int perspective_view;
varying vec2 screen_uv;

float eye_depth(float d) {
    if (perspective_view == 1) {
        float z = d * 2.0 - 1.0;
        return (2.0 * 1.0 * 100.0) / (101.0 - z * 99.0);
    }
    return -200.0 + d * 400.0;
}
void main() {
    float depth = texture2D(scene_depth, screen_uv).r;
    if (depth >= 0.99999) {
        gl_FragColor = vec4(1.0);
        return;
    }
    float center = eye_depth(depth);
    float occluded = 0.0;
    for (int i = 0; i < 16; ++i) {
        if (i >= sample_count) break;
        vec2 uv = screen_uv + offsets[i] * sample_radius * texel_size;
        float adjacent = texture2D(scene_depth, uv).r;
        if (adjacent >= 0.99999) continue;
        float delta = center - eye_depth(adjacent);
        // Neighbouring foreground surfaces shadow crevices; suppress
        // self-shadowing and distant depth discontinuity halos.
        occluded += smoothstep(0.018, 0.14, delta)
                    * (1.0 - smoothstep(0.35, 1.4, delta));
    }
    float ao = clamp(1.0 - strength * occluded / float(sample_count)
                     * 1.25, 0.48, 1.0);
    gl_FragColor = vec4(ao,ao,ao,1.0);
}
"""


class SsaoEffect:
    """Own an independent off-screen depth texture and fullscreen compositor."""

    def __init__(self):
        from OpenGL.GL import (
            GL_VERTEX_SHADER, GL_FRAGMENT_SHADER, glGenFramebuffers,
            glGenTextures, glGetUniformLocation, glUseProgram, glUniform1i)
        from OpenGL.GL import shaders
        self.program = shaders.compileProgram(
            shaders.compileShader(VERTEX, GL_VERTEX_SHADER),
            shaders.compileShader(FRAGMENT, GL_FRAGMENT_SHADER))
        self.framebuffer = int(glGenFramebuffers(1))
        self.texture = int(glGenTextures(1))
        self.size = (0, 0)
        self.uniforms = {name: glGetUniformLocation(self.program, name)
                         for name in ('scene_depth','texel_size','offsets',
                                      'sample_radius','strength','sample_count',
                                      'perspective_view')}
        glUseProgram(self.program)
        glUniform1i(self.uniforms['scene_depth'], 0)
        glUseProgram(0)

    def _resize(self, w, h):
        if self.size == (w,h):
            return
        from OpenGL.GL import (
            glBindFramebuffer, glBindTexture, glTexImage2D, glTexParameteri,
            glFramebufferTexture2D, glCheckFramebufferStatus,
            GL_FRAMEBUFFER, GL_FRAMEBUFFER_BINDING, GL_TEXTURE_2D,
            GL_DEPTH_COMPONENT, GL_DEPTH_COMPONENT24, GL_UNSIGNED_INT,
            GL_DEPTH_ATTACHMENT, GL_FRAMEBUFFER_COMPLETE, GL_TEXTURE_MIN_FILTER,
            GL_TEXTURE_MAG_FILTER, GL_NEAREST, GL_TEXTURE_WRAP_S,
            GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE, glGetIntegerv)
        old_fbo = int(glGetIntegerv(GL_FRAMEBUFFER_BINDING))
        glBindTexture(GL_TEXTURE_2D, self.texture)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_DEPTH_COMPONENT24,
                     w, h, 0, GL_DEPTH_COMPONENT, GL_UNSIGNED_INT, None)
        for key in (GL_TEXTURE_MIN_FILTER,GL_TEXTURE_MAG_FILTER):
            glTexParameteri(GL_TEXTURE_2D,key,GL_NEAREST)
        for key in (GL_TEXTURE_WRAP_S,GL_TEXTURE_WRAP_T):
            glTexParameteri(GL_TEXTURE_2D,key,GL_CLAMP_TO_EDGE)
        glBindFramebuffer(GL_FRAMEBUFFER,self.framebuffer)
        try:
            glFramebufferTexture2D(GL_FRAMEBUFFER,GL_DEPTH_ATTACHMENT,
                                   GL_TEXTURE_2D,self.texture,0)
            status = glCheckFramebufferStatus(GL_FRAMEBUFFER)
            if status != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError('SSAO depth framebuffer is unsupported (%s)' % status)
            self.size=(w,h)
        finally:
            glBindFramebuffer(GL_FRAMEBUFFER,old_fbo)
            glBindTexture(GL_TEXTURE_2D,0)

    def render(self, arrays, *, quality, perspective, visible_slot=None,
               hidden=(), wireframe=False):
        """Compute AO for the visible mesh, then multiply the already-lit image.

        Avoid a synchronous GPU->CPU depth readback. The depth pass has no
        material shader, PAK scanning or UV upload; it never edits input data.
        """
        if quality not in QUALITY:
            raise ValueError('Unknown SSAO quality: ' + str(quality))
        samples,radius = QUALITY[quality]
        if not samples or wireframe or arrays is None:
            return
        from math import pi, sin, cos
        from OpenGL.GL import (
            glGetIntegerv, glBindFramebuffer, glViewport, glClear, glDisable,
            glEnable, glDepthMask, glColorMask, glDrawBuffer, glReadBuffer,
            glEnableClientState, glDisableClientState, glVertexPointer,
            glDrawElements, glUseProgram, glActiveTexture, glBindTexture,
            glUniform2f, glUniform2fv, glUniform1f, glUniform1i,
            glBegin, glEnd, glVertex2f, glTexCoord2f, glBlendFunc,
            glPolygonMode, GL_VIEWPORT, GL_FRAMEBUFFER, GL_FRAMEBUFFER_BINDING,
            GL_NONE, GL_DEPTH_BUFFER_BIT, GL_DEPTH_TEST, GL_BLEND,
            GL_LIGHTING, GL_TEXTURE_2D, GL_TEXTURE0, GL_VERTEX_ARRAY,
            GL_FLOAT, GL_TRIANGLES, GL_UNSIGNED_INT, GL_ZERO, GL_SRC_COLOR,
            GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA, GL_QUADS, GL_FRONT_AND_BACK,
            GL_FILL, GL_TEXTURE_COORD_ARRAY, GL_NORMAL_ARRAY)
        viewport=glGetIntegerv(GL_VIEWPORT)
        w,h=int(viewport[2]),int(viewport[3])
        if w<2 or h<2:
            return
        self._resize(w,h)
        previous=int(glGetIntegerv(GL_FRAMEBUFFER_BINDING))
        try:
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D,0)
            glDisable(GL_TEXTURE_2D)
            glDisable(GL_BLEND)
            glDisable(GL_LIGHTING)
            glBindFramebuffer(GL_FRAMEBUFFER,self.framebuffer)
            glViewport(0,0,w,h)
            glDrawBuffer(GL_NONE)
            glReadBuffer(GL_NONE)
            glColorMask(False,False,False,False)
            glDepthMask(True)
            glEnable(GL_DEPTH_TEST)
            glPolygonMode(GL_FRONT_AND_BACK,GL_FILL)
            glClear(GL_DEPTH_BUFFER_BIT)
            glEnableClientState(GL_VERTEX_ARRAY)
            glVertexPointer(3,GL_FLOAT,0,arrays.vertices)
            for first,count,slot in arrays.draws:
                if slot < len(hidden) and hidden[slot]:
                    continue
                if visible_slot is not None and visible_slot != slot:
                    continue
                glDrawElements(GL_TRIANGLES,int(count),GL_UNSIGNED_INT,
                               arrays.indices[first:first+count])
            glDisableClientState(GL_VERTEX_ARRAY)
        finally:
            glBindFramebuffer(GL_FRAMEBUFFER,previous)
            glViewport(*[int(v) for v in viewport])
            glColorMask(True,True,True,True)
            glDepthMask(True)
            glDrawBuffer(0x0405)  # GL_BACK for standard default Qt GL FBO
        # OpenGL 2.1 fallback: one small compositing program, no full scene
        # material re-render. Quad vertices are already in clip space.
        glDisable(GL_DEPTH_TEST)
        glDepthMask(False)
        glDisable(GL_LIGHTING)
        glDisableClientState(GL_NORMAL_ARRAY)
        glDisableClientState(GL_TEXTURE_COORD_ARRAY)
        glEnable(GL_BLEND)
        glBlendFunc(GL_ZERO,GL_SRC_COLOR)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D,self.texture)
        glUseProgram(self.program)
        points=[(cos(2*pi*i/16)*(.5 if i % 2 else 1.0),
                 sin(2*pi*i/16)*(.5 if i % 2 else 1.0)) for i in range(16)]
        try:
            glUniform2f(self.uniforms['texel_size'],1.0/w,1.0/h)
            glUniform2fv(self.uniforms['offsets'],16,points)
            glUniform1f(self.uniforms['sample_radius'],radius)
            glUniform1f(self.uniforms['strength'],1.0)
            glUniform1i(self.uniforms['sample_count'],samples)
            glUniform1i(self.uniforms['perspective_view'],int(bool(perspective)))
            glBegin(GL_QUADS)
            for x,y,u,v in ((-1.,-1.,0.,0.),(1.,-1.,1.,0.),
                            (1.,1.,1.,1.),(-1.,1.,0.,1.)):
                glTexCoord2f(u,v)
                glVertex2f(x,y)
            glEnd()
        finally:
            glUseProgram(0)
            glBindTexture(GL_TEXTURE_2D,0)
            glDisable(GL_BLEND)
            glBlendFunc(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA)
            glEnable(GL_DEPTH_TEST)
            glEnable(GL_LIGHTING)
            glDepthMask(True)

    def close(self):
        from OpenGL.GL import glDeleteFramebuffers, glDeleteTextures, glDeleteProgram
        if self.framebuffer:
            glDeleteFramebuffers(1,[self.framebuffer])
            self.framebuffer=0
        if self.texture:
            glDeleteTextures([self.texture])
            self.texture=0
        if self.program:
            glDeleteProgram(self.program)
            self.program=0
