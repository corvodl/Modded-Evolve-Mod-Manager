"""Explicit per-slot texture choices shared by preview and GLB export."""
from dataclasses import replace
from pathlib import Path
from PIL import Image
from material_preview import PreviewMaterials, _decode_texture, MAX_TEXTURE_BYTES


def texture_choices(index, folder=None):
    """Return exact source records; never merge different same-name textures."""
    choices = {}
    for virtual, records in index.items():
        if not virtual.lower().endswith(('.dds', '.dds.0', '.png', '.jpg', '.jpeg', '.tga')):
            continue
        for record in records:
            if '_shared_archive' in record:
                continue
            path = Path(record['file'])
            if path.is_file() and not path.is_symlink():
                choices[str(path.resolve())] = dict(record)
    if folder:
        root = Path(folder).resolve()
        for number, path in enumerate(root.rglob('*')):
            if number >= 60000:
                raise ValueError('Texture folder exceeds 60,000 entries; select a smaller folder.')
            if (path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root)
                    and path.name.lower().endswith(('.dds', '.dds.0', '.png', '.jpg', '.jpeg', '.tga'))):
                choices[str(path.resolve())] = {'file': path, 'relative': path.relative_to(root).as_posix(), 'workspace': None}
    return sorted(choices.values(), key=lambda r: (str(r['relative']).casefold(), str(r['file']).casefold()))


def load_chosen_texture(record, index):
    path = Path(record['file'])
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_TEXTURE_BYTES:
        raise ValueError('Choose a regular texture file smaller than 64 MiB.')
    if path.name.lower().endswith(('.dds', '.dds.0')):
        return _decode_texture(record, index=index)
    with Image.open(path) as source:
        if source.width * source.height > 16_777_216:
            raise ValueError('Texture exceeds 16 megapixels.')
        source.load()
        return source.convert('RGBA' if 'A' in source.getbands() else 'RGB')


def apply_texture_choices(materials, mesh, choices):
    """Choices are {slot: (decoded image, source label)}; input stays immutable."""
    if not choices:
        return materials
    slots = {slot for _, _, slot in mesh.subsets} or {0}
    count = max(max(slots) + 1, len(materials.textures) if materials else 0)
    if count > 256:
        raise ValueError('Too many material slots.')
    if materials is None:
        materials = PreviewMaterials((None,) * count, tuple('Material %d' % i for i in range(count)), 'Manual selection')
    images = list(materials.textures) + [None] * (count - len(materials.textures))
    names = list(materials.names) + ['Unresolved'] * (count - len(materials.names))
    for slot, (image, label) in choices.items():
        if slot not in slots:
            raise ValueError('Selected texture slot no longer belongs to this model.')
        images[slot] = image
        names[slot] = names[slot].split(' [selected:')[0] + ' [selected: ' + label + ']'
    return replace(materials, textures=tuple(images), names=tuple(names))
