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
uniform sampler2D gloss_map;
uniform sampler2D detail_map;
uniform sampler2D detail_normal_map;
uniform sampler2D ao_map;
uniform sampler2D emissive_map;
uniform samplerCube environment_map;
uniform mat3 normal_uv;
uniform mat3 specular_uv;
uniform mat3 gloss_uv;
uniform mat3 detail_uv;
uniform mat3 detail_normal_uv;
uniform mat3 ao_uv;
uniform int has_diffuse;
uniform int has_emissive;
uniform float emissive_strength;
uniform int emission_enabled;
uniform int bloom_pass;
uniform int has_normal;
uniform int has_specular;
uniform int has_gloss;
uniform int has_detail;
uniform int has_detail_normal;
uniform int normal_y_flip;
uniform int has_ao;
uniform float ao_strength;
uniform int has_environment;
uniform float reflection_strength;
uniform int is_glass;
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
    if (bloom_pass == 1) {
        if (emission_enabled == 0 || has_emissive == 0) discard;
        vec3 emission = pow(texture2D(emissive_map, diffuse_coord).rgb,
                            vec3(2.2)) * max(0.0, emissive_strength);
        if (max(max(emission.r, emission.g), emission.b) < 0.002) discard;
        gl_FragColor = vec4(clamp(emission, 0.0, 1.0), 1.0);
        return;
    }
    // glTF/CryEngine double-sided materials must flip their backface normal.
    // Otherwise visible inside-facing parts of characters shade almost black.
    vec2 normal_coord = (normal_uv * vec3(texcoord,1.0)).xy;
    vec2 specular_coord = (specular_uv * vec3(texcoord,1.0)).xy;
    vec2 gloss_coord = (gloss_uv * vec3(texcoord,1.0)).xy;
    vec2 detail_coord = (detail_uv * vec3(texcoord,1.0)).xy;
    vec2 detail_normal_coord = (detail_normal_uv * vec3(texcoord,1.0)).xy;
    vec2 ao_coord = (ao_uv * vec3(texcoord,1.0)).xy;
    vec3 n = normalize(eye_normal);
    if (!gl_FrontFacing) n = -n;
    if (has_normal == 1 || has_detail_normal == 1) {
        vec2 bump_coord = has_normal == 1 ? normal_coord : detail_normal_coord;
        vec3 p_dx = dFdx(eye_position);
        vec3 p_dy = dFdy(eye_position);
        vec2 uv_dx = dFdx(bump_coord);
        vec2 uv_dy = dFdy(bump_coord);
        vec3 tangent = p_dx * uv_dy.y - p_dy * uv_dx.y;
        vec3 bitangent = p_dy * uv_dx.x - p_dx * uv_dy.x;
        if (dot(tangent,tangent) > 0.000001 &&
            dot(bitangent,bitangent) > 0.000001) {
            tangent = normalize(tangent - n * dot(n, tangent));
            bitangent = normalize(cross(n,tangent)) *
                        (dot(cross(n,tangent),bitangent) < 0.0 ? -1.0 : 1.0);
            vec3 mapped = has_normal == 1 ?
                  texture2D(normal_map,normal_coord).xyz * 2.0 - 1.0 :
                  vec3(0.0,0.0,1.0);
            // Viewer-only diagnostic: reverse tangent-space Y exactly once.
            // Do NOT alter the uploaded DDS/PNG or glTF exported pixels.
            if (normal_y_flip == 1) mapped.y = -mapped.y;
            if (has_detail_normal == 1) {
                vec3 fine = texture2D(detail_normal_map,detail_normal_coord).xyz * 2.0 - 1.0;
                if (normal_y_flip == 1) fine.y = -fine.y;
                // Conservative additive tangent-space detail; full CryEngine
                // material layering remains dependent on source shader rules.
                mapped = normalize(vec3(mapped.xy + fine.xy, mapped.z * fine.z));
            }
            n = normalize(mat3(tangent,bitangent,n) * mapped);
        }
    }
    vec3 light = normalize(vec3(0.45,0.75,0.85));
    vec3 view = normalize(-eye_position);
    float diffuse = max(0.0,dot(n,light));
    // Dedicated CryEngine gloss/smoothness maps define per-pixel
    // highlight sharpness. Unmapped slots retain the legacy exponent.
    float smoothness = has_gloss == 1 ?
          clamp(dot(texture2D(gloss_map,gloss_coord).rgb,
                    vec3(0.299,0.587,0.114)),0.0,1.0) : 0.2833333;
    float gloss = pow(max(0.0,dot(n,normalize(light+view))),
                      mix(8.0,128.0,smoothness));
    float specular = has_specular == 1 ?
                     dot(texture2D(specular_map,specular_coord).rgb,vec3(0.333333))
                     : 0.4;
    // Diffuse DDS colors are sRGB. Applying linear-space light directly to
    // encoded RGB makes dark outfits artificially black. Decode/encode around
    // diffuse lighting; unlit mode above still shows the untouched source DDS.
    vec3 albedoLinear = pow(surface.rgb, vec3(2.2));
    if (has_detail == 1) {
        vec3 fineAlbedo = pow(texture2D(detail_map,detail_coord).rgb, vec3(2.2));
        albedoLinear *= mix(vec3(1.0),fineAlbedo*2.0,0.5);
    }
    // A soft studio fill keeps back-facing dark armor legible without
    // replacing the original texture or lifting black pixels to invented colors.
    // The additional fill is strictly nonnegative and can be disabled.
    float fill = clamp(studio_fill,0.0,1.0);
    vec3 fillDirection = normalize(vec3(-0.65,-0.35,0.70));
    float bounce = max(0.0,dot(n,fillDirection));
    // Ambient occlusion attenuates indirect ambient/fill only: do not
    // darken direct diffuse light or invent AO from arbitrary DDS alpha.
    // AO is linear data; glTF occlusionTexture reads the red channel.
    float ao = has_ao == 1 ? clamp(texture2D(ao_map,ao_coord).r,0.0,1.0) : 1.0;
    float ambientOcclusion = mix(1.0,ao,clamp(ao_strength,0.0,1.0));
    float indirect = (0.24 + fill*(0.34+0.30*bounce)) * ambientOcclusion;
    float diffuseLight = clamp(indirect+diffuse*0.76*illumination,0.0,2.5);
    vec3 litLinear = albedoLinear*diffuseLight
                   + vec3(specular*specular_intensity*gloss*illumination);
    if (has_environment == 1) {
        vec3 viewNormal = normalize(n);
        vec3 direction = reflect(-view,viewNormal);
        float fresnel = pow(1.0-clamp(dot(viewNormal,view),0.0,1.0),5.0);
        vec3 reflected = pow(textureCube(environment_map,direction).rgb,vec3(2.2));
        litLinear += reflected*reflection_strength*
                     mix(0.08, 0.85, fresnel)*(is_glass == 1 ? 1.2 : 1.0);
    }
    if (has_emissive == 1 && emission_enabled == 1)
        litLinear += pow(texture2D(emissive_map,diffuse_coord).rgb,vec3(2.2)) * max(0.0,emissive_strength);
    vec3 lit = pow(clamp(litLinear,0.0,1.0),vec3(1.0/2.2));
    gl_FragColor = vec4(lit,alpha);
}
"""


# The same table owns both uniform discovery and texture unit binding.
# Registering a new material map in one place cannot omit its lookup.
SAMPLER_UNITS = (
    ('diffuse_map', 0), ('normal_map', 1), ('specular_map', 2),
    ('gloss_map', 3), ('detail_map', 4), ('detail_normal_map', 5),
    ('environment_map', 6), ('ao_map', 7), ('emissive_map', 8),
)
MATERIAL_UNIFORMS = (
    'normal_uv', 'specular_uv', 'gloss_uv', 'detail_uv', 'detail_normal_uv', 'ao_uv',
    'has_emissive', 'emissive_strength', 'emission_enabled', 'bloom_pass',
    'has_diffuse', 'has_normal', 'has_specular', 'has_gloss', 'has_detail',
    'has_detail_normal', 'normal_y_flip', 'has_environment', 'reflection_strength',
    'is_glass', 'has_ao', 'ao_strength', 'opacity', 'alpha_cutoff',
    'use_alpha_texture', 'specular_intensity', 'illumination', 'studio_fill',
    'unlit_diffuse',
)


class Renderer3:
    def __init__(self):
        from OpenGL.GL import (shaders, glGetUniformLocation, glUseProgram,
                               glUniform1i, glDeleteProgram, GL_VERTEX_SHADER,
                               GL_FRAGMENT_SHADER)
        self.program = shaders.compileProgram(
            shaders.compileShader(VERTEX, GL_VERTEX_SHADER),
            shaders.compileShader(FRAGMENT, GL_FRAGMENT_SHADER))
        try:
            glUseProgram(self.program)
            self.uniforms = {
                name: glGetUniformLocation(self.program, name)
                for name in (tuple(name for name, _ in SAMPLER_UNITS)
                             + MATERIAL_UNIFORMS)
            }
            for name, unit in SAMPLER_UNITS:
                glUniform1i(self.uniforms[name], unit)
        except Exception:
            try:
                glDeleteProgram(self.program)
            finally:
                self.program = 0
            raise
        finally:
            # Even an unexpected Python/GL exception must not leave a
            # partially configured shader bound in the fallback renderer.
            glUseProgram(0)

    def bind(self, *, opacity, gloss, illumination, diffuse, normal, specular,
             gloss_texture=False, detail=False, detail_normal=False, invert_normal_y=False, uv_matrices=None,
             environment=False, reflection_strength=0., glass=False,
             ao=False, ao_strength=1.,
             alpha_texture=False, unlit=False, fill=0.35, alpha_cutoff=.02,
             emissive=False, emissive_strength=0., emission_enabled=True,
             bloom_pass=False):
        from OpenGL.GL import glUseProgram, glUniform1i, glUniform1f, glUniformMatrix3fv, GL_FALSE
        glUseProgram(self.program)
        for name,value in (('has_emissive',emissive),('emission_enabled',emission_enabled),
                           ('bloom_pass',bloom_pass),
                           ('has_diffuse',diffuse),('has_normal',normal),
                           ('has_specular',specular), ('has_gloss',gloss_texture),
                           ('has_detail',detail), ('has_detail_normal',detail_normal),
                           ('normal_y_flip',invert_normal_y),
                           ('has_environment',environment), ('is_glass',glass), ('has_ao',ao),
                           ('use_alpha_texture',alpha_texture),
                           ('unlit_diffuse',unlit)):
            glUniform1i(self.uniforms[name],int(bool(value)))
        from texture_transform import gl_matrix3, IDENTITY
        matrices = uv_matrices or {}
        for role in ('normal','specular','gloss','detail','detail_normal','ao'):
            glUniformMatrix3fv(self.uniforms[role + '_uv'],1,GL_FALSE,
                               gl_matrix3(matrices.get(role,IDENTITY)))
        for name,value in (('ao_strength',ao_strength),('reflection_strength',reflection_strength),('opacity',opacity),('alpha_cutoff',alpha_cutoff),('specular_intensity',gloss),
                           ('illumination',illumination), ('studio_fill',fill),
                           ('emissive_strength',emissive_strength)):
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


BLOOM_VERTEX = """
#version 120
varying vec2 uv;
void main() {
    uv = gl_MultiTexCoord0.st;
    gl_Position = gl_Vertex;
}
"""

BLOOM_FRAGMENT = """
#version 120
uniform sampler2D mask_texture;
uniform vec2 pixel_step;
uniform float halo_strength;
varying vec2 uv;
void main() {
    // Quarter resolution offscreen glow, softened with a small
    // separable-style 13-tap radial kernel; blend additively with the scene.
    vec3 sum = texture2D(mask_texture,uv).rgb * 0.16;
    sum += (texture2D(mask_texture,uv+vec2(pixel_step.x,0.0)).rgb
          + texture2D(mask_texture,uv-vec2(pixel_step.x,0.0)).rgb
          + texture2D(mask_texture,uv+vec2(0.0,pixel_step.y)).rgb
          + texture2D(mask_texture,uv-vec2(0.0,pixel_step.y)).rgb) * 0.10;
    sum += (texture2D(mask_texture,uv+pixel_step).rgb
          + texture2D(mask_texture,uv-pixel_step).rgb
          + texture2D(mask_texture,uv+vec2(pixel_step.x,-pixel_step.y)).rgb
          + texture2D(mask_texture,uv+vec2(-pixel_step.x,pixel_step.y)).rgb) * 0.065;
    sum += (texture2D(mask_texture,uv+pixel_step*2.0).rgb
          + texture2D(mask_texture,uv-pixel_step*2.0).rgb
          + texture2D(mask_texture,uv+vec2(pixel_step.x,-pixel_step.y)*2.0).rgb
          + texture2D(mask_texture,uv+vec2(-pixel_step.x,pixel_step.y)*2.0).rgb) * 0.045;
    gl_FragColor = vec4(sum * halo_strength, 1.0);
}
"""


class EmissiveBloom:
    """Shader-only fullscreen composite; Qt owns the glow framebuffer."""
    def __init__(self):
        from OpenGL.GL import shaders, GL_VERTEX_SHADER, GL_FRAGMENT_SHADER
        from OpenGL.GL import glUseProgram, glUniform1i, glGetUniformLocation
        self.program = shaders.compileProgram(
            shaders.compileShader(BLOOM_VERTEX, GL_VERTEX_SHADER),
            shaders.compileShader(BLOOM_FRAGMENT, GL_FRAGMENT_SHADER))
        self.sampler = glGetUniformLocation(self.program, 'mask_texture')
        self.step = glGetUniformLocation(self.program, 'pixel_step')
        self.strength = glGetUniformLocation(self.program, 'halo_strength')
        glUseProgram(self.program)
        glUniform1i(self.sampler, 0)
        glUseProgram(0)

    def draw(self, width, height, strength=.55):
        from OpenGL.GL import (glUseProgram, glUniform2f, glUniform1f,
                               glBegin, glEnd, glTexCoord2f, glVertex2f,
                               GL_TRIANGLE_STRIP)
        glUseProgram(self.program)
        glUniform2f(self.step, 1.0/max(1,width), 1.0/max(1,height))
        glUniform1f(self.strength, float(strength))
        glBegin(GL_TRIANGLE_STRIP)
        for x,y,u,v in ((-1.,-1.,0.,0.),(1.,-1.,1.,0.),
                        (-1.,1.,0.,1.),(1.,1.,1.,1.)):
            glTexCoord2f(u,v)
            glVertex2f(x,y)
        glEnd()
        glUseProgram(0)

    def close(self):
        from OpenGL.GL import glDeleteProgram
        if self.program:
            glDeleteProgram(self.program)
            self.program = 0
