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
void main() {
    vec4 eye = gl_ModelViewMatrix * gl_Vertex;
    eye_position = eye.xyz;
    eye_normal = normalize(gl_NormalMatrix * gl_Normal);
    texcoord = gl_MultiTexCoord0.st;
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
uniform float specular_intensity;
uniform float illumination;
varying vec3 eye_normal;
varying vec3 eye_position;
varying vec2 texcoord;
void main() {
    vec4 surface = has_diffuse == 1 ? texture2D(diffuse_map, texcoord)
                                   : vec4(0.82, 0.80, 0.83, 1.0);
    float alpha = surface.a * opacity;
    if (alpha < 0.02) discard;
    vec3 n = normalize(eye_normal);
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
    vec3 lit = surface.rgb*(0.24+diffuse*0.76*illumination)
              + vec3(specular*specular_intensity*gloss*illumination);
    gl_FragColor = vec4(clamp(lit,0.0,1.0),alpha);
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
            'has_specular','opacity','specular_intensity','illumination')}
        for name,unit in (('diffuse_map',0),('normal_map',1),('specular_map',2)):
            glUniform1i(self.uniforms[name],unit)
        glUseProgram(0)

    def bind(self, *, opacity, gloss, illumination, diffuse, normal, specular):
        from OpenGL.GL import glUseProgram, glUniform1i, glUniform1f
        glUseProgram(self.program)
        for name,value in (('has_diffuse',diffuse),('has_normal',normal),
                           ('has_specular',specular)):
            glUniform1i(self.uniforms[name],int(bool(value)))
        for name,value in (('opacity',opacity),('specular_intensity',gloss),
                           ('illumination',illumination)):
            glUniform1f(self.uniforms[name],float(value))

    def unbind(self):
        from OpenGL.GL import glUseProgram
        glUseProgram(0)

    def close(self):
        from OpenGL.GL import glDeleteProgram
        if self.program:
            glDeleteProgram(self.program)
            self.program = 0
