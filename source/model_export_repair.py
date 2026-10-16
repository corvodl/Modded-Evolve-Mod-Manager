"""Conservative, read-only preparation and diagnostics for Blender model export.

Never reorder/remove source vertices: rig weight streams and animation palettes use
those vertex IDs. Repair only provably degenerate faces and very small 3-edge
topological holes; report detached components for manual review instead of
deleting intentional floating equipment or closing character openings.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class RepairReport:
    degenerate_faces: int = 0
    unused_vertices: int = 0
    tiny_holes_closed: int = 0
    boundary_edges: int = 0
    disconnected_triangles: int = 0
    uv_coordinates_snapped: int = 0
    notes: tuple = ()


def _length_sq(a, b):
    return sum((float(x) - float(y)) ** 2 for x, y in zip(a, b))


def prepare_export_mesh(mesh, *, close_tiny_holes=True):
    """Return (prepared mesh, diagnostics); retain exact vertex/bone indices.

    UV welding snaps only near-identical coordinates; it never merges vertex
    indices or UV seams. The normals/tangents exporter computes one smooth
    per-vertex basis (glTF does not serialize legacy smoothing-group IDs).
    """
    verts = mesh.vertices
    radius = max(float(mesh.radius), 1e-8)
    tiny = (radius * 1e-7) ** 2
    tris = list(mesh.triangles)
    slots = mesh.subsets or ((0, len(tris) * 3, 0),)
    if len(verts) > 250000 or len(tris) > 500000:
        raise ValueError('Model repair exceeds geometry safety limits.')
    if sum(n for _, n, _ in slots) != len(tris) * 3:
        raise ValueError('Invalid render-mesh subset table.')
    used = set()
    cleaned = []
    cleaned_slots = []
    removed = 0
    for first, count, slot in slots:
        if first % 3 or count % 3 or first + count > len(tris) * 3:
            raise ValueError('Invalid subset alignment.')
        begin = len(cleaned) * 3
        for tri in tris[first // 3:(first + count) // 3]:
            if len(tri) != 3 or any(not isinstance(k, int) or k < 0 or k >= len(verts) for k in tri):
                raise ValueError('Out-of-bounds triangle index.')
            a, b, c = tri
            if len({a,b,c}) != 3:
                removed += 1
                continue
            ab = tuple(float(verts[b][k]) - float(verts[a][k]) for k in range(3))
            ac = tuple(float(verts[c][k]) - float(verts[a][k]) for k in range(3))
            cross = (ab[1]*ac[2]-ab[2]*ac[1],
                     ab[2]*ac[0]-ab[0]*ac[2],
                     ab[0]*ac[1]-ab[1]*ac[0])
            if sum(x*x for x in cross) <= tiny * tiny:
                removed += 1
                continue
            cleaned.append(tri)
            used.update(tri)
        if len(cleaned) * 3 > begin:
            cleaned_slots.append((begin, len(cleaned)*3-begin, slot))
    if not cleaned:
        raise ValueError('Mesh has no nondegenerate triangles.')
    holes = 0
    boundary_count = 0
    isolated = 0
    notes = []
    # Limit high-memory topological analysis for huge level geometry.
    if len(cleaned) <= 150000:
        edge_faces = defaultdict(list)
        face_slots = []
        for first, count, slot in cleaned_slots:
            face_slots.extend([slot] * (count // 3))
        for i, (a,b,c) in enumerate(cleaned):
            for x,y in ((a,b),(b,c),(c,a)):
                edge_faces[(min(x,y),max(x,y))].append((x,y,face_slots[i]))
        boundary = [items[0] for items in edge_faces.values() if len(items)==1]
        boundary_count = len(boundary)
        # Classify small disconnected mesh islands, but do not delete them:
        # glasses, fingers, tubes and attached equipment often look 'floating'.
        parents = {v:v for v in used}
        def root(v):
            while parents[v] != v:
                parents[v] = parents[parents[v]]
                v = parents[v]
            return v
        for a,b,c in cleaned:
            aa,bb,cc = root(a),root(b),root(c)
            parents[bb]=aa
            parents[cc]=aa
        component_counts = Counter(root(a) for a,b,c in cleaned)
        isolated = sum(n for n in component_counts.values() if n <= 2)
        if isolated:
            notes.append('Tiny disconnected parts retained; verify against original character attachments.')
        if close_tiny_holes and boundary_count and boundary_count < 30000:
            outgoing = defaultdict(list)
            for a,b,slot in boundary:
                outgoing[a].append((b,slot))
            candidate_faces = defaultdict(list)
            visited = set()
            for a,b,slot in boundary:
                if (a,b) in visited:
                    continue
                # Exactly three boundary edges in one submaterial, with
                # no branching, are the only safely triangulatable holes.
                nxt = outgoing.get(b, ())
                if len(outgoing[a]) != 1 or len(nxt) != 1:
                    continue
                c, slot2 = nxt[0]
                if c in (a,b) or len(outgoing[c]) != 1:
                    continue
                end, slot3 = outgoing[c][0]
                if end != a or slot != slot2 or slot != slot3:
                    continue
                if max(_length_sq(verts[a],verts[b]),
                       _length_sq(verts[b],verts[c]),
                       _length_sq(verts[c],verts[a])) > (radius*0.002)**2:
                    continue
                # Inverse winding seals the hole with no new vertices.
                candidate_faces[slot].append((c,b,a))
                visited.update(((a,b),(b,c),(c,a)))
            if candidate_faces:
                updated = []
                updated_slots = []
                for first, count, slot in cleaned_slots:
                    begin = len(updated)*3
                    updated.extend(cleaned[first//3:(first+count)//3])
                    # Attach to last primitive of this slot to avoid duplicate faces.
                    last = next((j for j, entry in enumerate(cleaned_slots)
                                 if entry[2] == slot and j > cleaned_slots.index((first,count,slot))), None)
                    if last is None:
                        added = candidate_faces.pop(slot, [])
                        updated.extend(added)
                        holes += len(added)
                    updated_slots.append((begin,len(updated)*3-begin,slot))
                cleaned = updated
                cleaned_slots = updated_slots
                boundary_count = max(0,boundary_count - holes*3)
    else:
        notes.append('Detailed connectivity scan skipped for a very large mesh.')
    if boundary_count:
        notes.append('Open edges retained unless a tiny unambiguous triangular hole was verified.')
    # Preserve all UV islands: near-equal numeric coordinates only are snapped.
    uvs = mesh.uvs
    snapped = 0
    if len(uvs) == len(verts):
        snap_map = {}
        out = []
        for pair in uvs:
            u, v = map(float, pair)
            if not math.isfinite(u) or not math.isfinite(v):
                raise ValueError('Non-finite UV.')
            key = (round(u, 7), round(v, 7))
            saved = snap_map.setdefault(key,(u,v))
            if (u,v) != saved:
                snapped += 1
            out.append(saved)
        uvs = tuple(out)
    return replace(mesh, triangles=tuple(cleaned), subsets=tuple(cleaned_slots),
                   uvs=uvs), RepairReport(
        degenerate_faces=removed, unused_vertices=len(verts)-len(used),
        tiny_holes_closed=holes, boundary_edges=boundary_count,
        disconnected_triangles=isolated, uv_coordinates_snapped=snapped,
        notes=tuple(notes))
