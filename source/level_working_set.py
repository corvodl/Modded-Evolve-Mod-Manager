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

    def nearby(self, camera, radius, *, retain=(), margin=1.2):
        """Nearest squared distance per asset, including hysteresis residents."""
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
        candidates = (
            ((x, y, z), values)
            for (x, y, z), values in self.cells.items()
            if (low[0] <= x <= high[0] and low[1] <= y <= high[1]
                and low[2] <= z <= high[2])
        )
        distances = {}
        for _cell, entries in candidates:
            for key, point in entries:
                distance = sum((point[i] - center[i]) ** 2 for i in range(3))
                threshold = outer_sq if key in resident else radius_sq
                if distance <= threshold:
                    current = distances.get(key)
                    if current is None or distance < current:
                        distances[key] = distance
        return distances
