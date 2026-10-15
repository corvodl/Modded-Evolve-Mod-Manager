"""Spatial working-set index for large CryEngine level previews.

Retains every logical placement for editing/export; only selects which
material variants to request for the currently visible camera neighborhood.
The index is immutable and is rebuilt when the scene changes.
"""
from __future__ import annotations
from collections import defaultdict
from math import floor


class PlacementGrid:
    def __init__(self, scene, asset_key, *, cell_size=384.):
        if cell_size <= 0:
            raise ValueError('cell_size must be positive')
        self.cell_size = float(cell_size)
        self.cells = defaultdict(list)
        self.total = 0
        for obj in scene:
            if not obj.model or obj.status == 'hidden in editor':
                continue
            point = tuple(float(v) for v in obj.world.pos)
            cell = self._cell(point)
            self.cells[cell].append((asset_key(obj), point))
            self.total += 1

    def _cell(self, point):
        return tuple(floor(v / self.cell_size) for v in point)

    def nearby(self, camera, radius, *, eye=None, forward=None, retain=(), margin=1.2):
        """Nearest squared distance and front-facing flag per material key."""
        center = tuple(float(v) for v in camera)
        radius = max(1., float(radius))
        margin = max(1., float(margin))
        resident = set(retain)
        outer = radius * margin
        radius_sq, outer_sq = radius * radius, outer * outer
        low = self._cell(tuple(v - outer for v in center))
        high = self._cell(tuple(v + outer for v in center))
        # Keep the loop bounded even if the user zooms out far beyond the
        # map's own bounding extent: visit occupied cells once.
        dimensions = tuple(high[i] - low[i] + 1 for i in range(3))
        cell_volume = dimensions[0] * dimensions[1] * dimensions[2]
        if cell_volume <= max(64, 4 * len(self.cells)):
            candidates = (
                self.cells.get((x, y, z), ())
                for x in range(low[0], high[0] + 1)
                for y in range(low[1], high[1] + 1)
                for z in range(low[2], high[2] + 1)
            )
        else:
            # Huge zoom-out radius: avoid billions of empty cell probes.
            candidates = (
                values for cell, values in self.cells.items()
                if all(low[i] <= cell[i] <= high[i] for i in range(3))
            )
        distances = {}
        for entries in candidates:
            for key, point in entries:
                distance = sum((point[i] - center[i]) ** 2 for i in range(3))
                threshold = outer_sq if key in resident else radius_sq
                if distance > threshold:
                    continue
                front = (eye is not None and forward is not None and
                         sum((point[i] - eye[i]) * forward[i]
                             for i in range(3)) > 0)
                current = distances.get(key)
                distances[key] = (
                    min(distance, current[0]) if current else distance,
                    bool(front or (current and current[1])))
        return distances
