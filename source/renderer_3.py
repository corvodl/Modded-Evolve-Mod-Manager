"""Opt-in OpenGL 2.1/GLSL 1.20 material renderer for Qt model previews.

Uses the existing verified geometry and submaterial batches. GPU shader failures
are intentionally recoverable: the caller keeps the fixed-function fallback.
This approximates CryEngine bump/specular/opacity; it is not shader parity.
"""
from __future__ import annotations

VERTEX = """
#version 120
varying vec3 eye_normal;
varying vec3 eye_position;
varying vec2 texcoord;
varying vec2 diffuse_coord;
void main() {
    vec4 eye = gl_ModelViewMatrix * gl_Vertex;
    eye_position = eye.xyz;
    eye_normal = normalize(gl_NormalMatrix * gl_Normal);
    texcoord = gl_MultiTexCoord0.st;
    diffuse_coord = (gl_TextureMatrix[0] * gl_MultiTexCoord0).st;
    gl_Position = gl_ProjectionMatrix * eye;
}
"""

FRAGMENT = """
#version 120
uniform sampler2D diffuse_map;
uniform sampler2D normal_map;
uniform sampler2D specular_map;
uniform int has_diffuse;
uniform int has_normal;
uniform int has_specular;
uniform float opacity;
uniform float alpha_cutoff;
uniform int use_alpha_texture;
uniform float specular_intensity;
uniform float illumination;
uniform float studio_fill;
uniform int unlit_diffuse;
varying vec3 eye_normal;
varying vec3 eye_position;
varying vec2 texcoord;
varying vec2 diffuse_coord;
void main() {
    vec4 surface = has_diffuse == 1 ? texture2D(diffuse_map, diffuse_coord)
                                   : vec4(0.82, 0.80, 0.83, 1.0);
    // Alpha may contain non-opacity data in standard CryEngine diffuse DDS.
    float alpha = (use_alpha_texture == 1 ? surface.a : 1.0) * opacity;
    if (alpha < alpha_cutoff) discard;
    // Diagnostic: compare actual diffuse DDS pixels without shader lighting,
    // normal-mapping or specular darkening. Intentionally dark source maps
    // should remain dark here; an unbound slot remains neutral gray.
    if (unlit_diffuse == 1) {
        gl_FragColor = vec4(surface.rgb,alpha);
        return;
    }
    // glTF/CryEngine double-sided materials must flip their backface normal.
    // Otherwise visible inside-facing parts of characters shade almost black.
    vec3 n = normalize(eye_normal);
    if (!gl_FrontFacing) n = -n;
    if (has_normal == 1) {
        vec3 p_dx = dFdx(eye_position);
        vec3 p_dy = dFdy(eye_position);
        vec2 uv_dx = dFdx(texcoord);
        vec2 uv_dy = dFdy(texcoord);
        vec3 tangent = p_dx * uv_dy.y - p_dy * uv_dx.y;
        vec3 bitangent = p_dy * uv_dx.x - p_dx * uv_dy.x;
        if (dot(tangent,tangent) > 0.000001 &&
            dot(bitangent,bitangent) > 0.000001) {
            tangent = normalize(tangent - n * dot(n, tangent));
            bitangent = normalize(cross(n,tangent)) *
                        (dot(cross(n,tangent),bitangent) < 0.0 ? -1.0 : 1.0);
            vec3 mapped = texture2D(normal_map,texcoord).xyz * 2.0 - 1.0;
            n = normalize(mat3(tangent,bitangent,n) * mapped);
        }
    }
    vec3 light = normalize(vec3(0.45,0.75,0.85));
    vec3 view = normalize(-eye_position);
    float diffuse = max(0.0,dot(n,light));
    float gloss = pow(max(0.0,dot(n,normalize(light+view))),42.0);
    float specular = has_specular == 1 ?
                     dot(texture2D(specular_map,texcoord).rgb,vec3(0.333333))
                     : 0.4;
    // Diffuse DDS colors are sRGB. Applying linear-space light directly to
    // encoded RGB makes dark outfits artificially black. Decode/encode around
    // diffuse lighting; unlit mode above still shows the untouched source DDS.
    vec3 albedoLinear = pow(surface.rgb, vec3(2.2));
    // A soft studio fill keeps back-facing dark armor legible without
    // replacing the original texture or lifting black pixels to invented colors.
    // The additional fill is strictly nonnegative and can be disabled.
    float fill = clamp(studio_fill,0.0,1.0);
    vec3 fillDirection = normalize(vec3(-0.65,-0.35,0.70));
    float bounce = max(0.0,dot(n,fillDirection));
    float diffuseLight = clamp(0.24+diffuse*0.76*illumination
                              + fill*(0.34+0.30*bounce),0.0,2.5);
    vec3 litLinear = albedoLinear*diffuseLight
                   + vec3(specular*specular_intensity*gloss*illumination);
    vec3 lit = pow(clamp(litLinear,0.0,1.0),vec3(1.0/2.2));
    gl_FragColor = vec4(lit,alpha);
}
"""


class Renderer3:
    def __init__(self):
        from OpenGL.GL import shaders, glGetUniformLocation, glUseProgram, glUniform1i
        from OpenGL.GL import GL_VERTEX_SHADER, GL_FRAGMENT_SHADER
        self.program = shaders.compileProgram(
            shaders.compileShader(VERTEX,GL_VERTEX_SHADER),
            shaders.compileShader(FRAGMENT,GL_FRAGMENT_SHADER))
        glUseProgram(self.program)
        self.uniforms={name:glGetUniformLocation(self.program,name) for name in (
            'diffuse_map','normal_map','specular_map','has_diffuse','has_normal',
            'has_specular','opacity','alpha_cutoff','use_alpha_texture','specular_intensity','illumination','studio_fill','unlit_diffuse')}
        for name,unit in (('diffuse_map',0),('normal_map',1),('specular_map',2)):
            glUniform1i(self.uniforms[name],unit)
        glUseProgram(0)

    def bind(self, *, opacity, gloss, illumination, diffuse, normal, specular,
             alpha_texture=False, unlit=False, fill=0.35, alpha_cutoff=.02):
        from OpenGL.GL import glUseProgram, glUniform1i, glUniform1f
        glUseProgram(self.program)
        for name,value in (('has_diffuse',diffuse),('has_normal',normal),
                           ('has_specular',specular), ('use_alpha_texture',alpha_texture),
                           ('unlit_diffuse',unlit)):
            glUniform1i(self.uniforms[name],int(bool(value)))
        for name,value in (('opacity',opacity),('alpha_cutoff',alpha_cutoff),('specular_intensity',gloss),
                           ('illumination',illumination), ('studio_fill',fill)):
            glUniform1f(self.uniforms[name],float(value))

    def unbind(self):
        from OpenGL.GL import glUseProgram
        glUseProgram(0)

    def close(self):
        from OpenGL.GL import glDeleteProgram
        if self.program:
            glDeleteProgram(self.program)
            self.program = 0


def ordered_transparency_batches(draws, indices, vertices, translucent_slots,
                                 rotation):
    """Opaque first; translucent subsets approximate back-to-front camera depth.

    Sorting is per submesh, not per triangle. It is deterministic and preserves
    valid original material slot/index ordering within opaque groups.
    """
    import math
    import numpy as np
    angle, tilt=(math.radians(float(rotation[0])),
                 math.radians(float(rotation[1])))
    axis=np.asarray((-math.sin(angle)*math.sin(tilt),
                     math.cos(angle)*math.sin(tilt),math.cos(tilt)),dtype=float)
    opaque, transparent=[],[]
    for first,count,slot in draws:
        row=(first,count,slot)
        if slot not in translucent_slots:
            opaque.append(row)
            continue
        selection=np.asarray(indices[first:first+count],dtype=np.int64)
        if not len(selection):
            depth=0.
        else:
            center=np.asarray(vertices,dtype=float)[selection].mean(axis=0)
            depth=float(np.dot(center,axis))
        transparent.append((depth,row))
    transparent.sort(key=lambda item:item[0])
    return opaque+[row for _,row in transparent]
