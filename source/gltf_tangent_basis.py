"""Compute a deterministic glTF tangent basis for read-only CryEngine mesh exports.

Source meshes may omit a decoded tangent stream. Generate smooth vertex
normals and UV derivatives so Blender has a stable tangent-space normal basis.
"""
from __future__ import annotations

import math
import struct


def _sub(a, b):
    return tuple(a[i]-b[i] for i in range(3))


def _cross(a, b):
    return (a[1]*b[2]-a[2]*b[1], a[2]*b[0]-a[0]*b[2],
            a[0]*b[1]-a[1]*b[0])


def _dot(a, b):
    return sum(a[i]*b[i] for i in range(3))


def _unit(v, fallback):
    length = math.sqrt(_dot(v, v))
    return tuple(x/length for x in v) if length > 1e-12 else fallback


def gltf_vertex_basis(mesh):
    """Return packed float32 NORMAL and optional TANGENT vertex buffers.

    Coordinates match model_blender_export._pack_vertices (x, z, -y).
    TANGENT.w encodes bitangent handedness. Invalid UV derivatives produce a
    stable perpendicular fallback, never NaN or an unbound normal-map basis.
    """
    count = len(mesh.vertices)
    pos = [(float(x), float(z), -float(y)) for x, y, z in mesh.vertices]
    use_uv = len(mesh.uvs) == count
    nacc = [[0.0, 0.0, 0.0] for _ in range(count)]
    tacc = [[0.0, 0.0, 0.0] for _ in range(count)]
    bacc = [[0.0, 0.0, 0.0] for _ in range(count)]

    for tri in mesh.triangles:
        i0, i1, i2 = tri
        edge1 = _sub(pos[i1], pos[i0])
        edge2 = _sub(pos[i2], pos[i0])
        face_n = _cross(edge1, edge2)
        if use_uv:
            u0, v0 = mesh.uvs[i0]
            u1, v1 = mesh.uvs[i1]
            u2, v2 = mesh.uvs[i2]
            du1, dv1, du2, dv2 = u1-u0, v1-v0, u2-u0, v2-v0
            det = du1*dv2-du2*dv1
            if abs(det) > 1e-12:
                tangent = tuple((edge1[i]*dv2-edge2[i]*dv1)/det for i in range(3))
                bitangent = tuple((edge2[i]*du1-edge1[i]*du2)/det for i in range(3))
            else:
                tangent = bitangent = (0.0, 0.0, 0.0)
        for index in tri:
            for j in range(3):
                nacc[index][j] += face_n[j]
                if use_uv:
                    tacc[index][j] += tangent[j]
                    bacc[index][j] += bitangent[j]

    authored_normals = getattr(mesh, 'vertex_normals', ())
    authored_tangents = getattr(mesh, 'vertex_tangents', ())
    has_authored_normals = len(authored_normals) == count
    has_authored_tangents = use_uv and has_authored_normals and len(authored_tangents) == count
    normals = bytearray()
    tangents = bytearray()
    for index in range(count):
        if has_authored_normals:
            sx, sy, sz = authored_normals[index]
            n = _unit((sx, sz, -sy), (0.0, 1.0, 0.0))
        else:
            n = _unit(nacc[index], (0.0, 1.0, 0.0))
        normals.extend(struct.pack("<3f", *n))
        if not use_uv:
            continue
        if has_authored_tangents:
            sx, sy, sz, authored_sign = authored_tangents[index]
            tangent = (sx, sz, -sy)
        else:
            tangent = tuple(tacc[index][j]-n[j]*_dot(tacc[index], n)
                            for j in range(3))
        if _dot(tangent, tangent) < 1e-20:
            axis = (0.0, 0.0, 1.0) if abs(n[2]) < 0.9 else (0.0, 1.0, 0.0)
            tangent = _cross(axis, n)
        tangent = _unit(tangent, (1.0, 0.0, 0.0))
        sign = (float(authored_sign) if has_authored_tangents else
                (-1.0 if _dot(_cross(n, tangent), bacc[index]) < 0 else 1.0))
        tangents.extend(struct.pack("<4f", *tangent, sign))
    return bytes(normals), bytes(tangents) if use_uv else None
