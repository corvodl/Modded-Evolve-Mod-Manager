"""CPU residency policy independent of logical map membership and export."""
from collections import OrderedDict
from dataclasses import replace
import hashlib
import sys


def retained_resources(assets, seen=None):
    """Count shared meshes, arrays and all material images once by identity."""
    seen = set() if seen is None else set(seen)
    def measure(value):
        if value is None or id(value) in seen:
            return 0
        seen.add(id(value))
        if hasattr(value, 'nbytes'):
            return int(value.nbytes)
        if hasattr(value, 'getbands') and hasattr(value, 'size'):
            return value.width * value.height * len(value.getbands())
        if isinstance(value, dict):
            return sys.getsizeof(value) + sum(size(k)+size(v) for k,v in value.items())
        if isinstance(value, (tuple,list)):
            return sys.getsizeof(value) + sum(map(size,value))
        if hasattr(value, '__dict__'):
            return sys.getsizeof(value) + size(vars(value))
        return sys.getsizeof(value)
    size = measure
    total = sum(size(asset) for asset in assets)
    return total, seen


def retained_bytes(assets):
    return retained_resources(assets)[0]


class SceneResidency:
    def __init__(self, catalog, decode, budget):
        self.catalog = catalog
        self.decode = decode
        self.budget = budget
        self.assets = OrderedDict()
        self.failures = {}

    def load(self, keys, cancel, progress=None):
        """Worker-only update. Missing residency never removes logical objects.

        Keys are prioritized by the viewport. Rejected resources may be retried
        after camera movement; malformed resources are reported separately.
        """
        wanted = list(dict.fromkeys(k for k in keys if k in self.catalog))
        # Evict everything outside this visibility request before decoding.
        self.assets = OrderedDict((k,v) for k,v in self.assets.items() if k in wanted)
        selected = OrderedDict()
        geometry = {}
        images = {}
        for value in self.assets.values():
            geometry[value.model_file] = (value.mesh,value.geometry)
        used, retained = 0, set()
        for position,key in enumerate(wanted):
            if cancel.is_set():
                return None
            if progress:
                progress(position,len(wanted))
            if key in self.failures:
                continue
            try:
                asset = self.assets.pop(key, None)
                if asset is None:
                    asset = self.decode(self.catalog[key], geometry)
                if asset.materials:
                    fields = {}
                    for field in ('textures','normal_maps','specular_maps'):
                        values = []
                        for im in getattr(asset.materials,field,()):
                            if im is not None:
                                digest = (im.mode,im.size,hashlib.sha256(im.tobytes()).digest())
                                im = images.setdefault(digest,im)
                            values.append(im)
                        fields[field] = tuple(values)
                    asset = replace(asset,materials=replace(asset.materials,**fields))
                cost, identifiers = retained_resources((asset,), retained)
                if used + cost > self.budget:
                    # Stop at the first resource which would exceed the budget.
                    # A single oversize resource is diagnosed, not retried forever.
                    if not selected:
                        self.failures[key] = 'resource exceeds CPU residency budget'
                    break
                used += cost
                retained = identifiers
                selected[key] = asset
                geometry[asset.model_file] = (asset.mesh,asset.geometry)
            except (OSError,ValueError,TypeError) as exc:
                self.failures[key] = str(exc)[:180]
        self.assets = selected
        return dict(selected)
