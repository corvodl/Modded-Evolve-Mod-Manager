"""Safe, bounded helpers for visual gameplay editing, asset search and batch export.

All game modifications stay inside extracted project workspaces. Source PAKs
and installed game files remain untouched. GUI owns confirmations and backups.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from xml.parsers import expat

from workspace_editor import validate_xml
from workspace_file_actions import checked_path
from model_asset import is_model
from dds_texture import is_dds, is_split_dds

NUMBER = re.compile(r'^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$')
OPEN_TAG = re.compile(r'<([A-Za-z_][\w.:-]*)(?:\s+[^<>]*?)?\s*/?>', re.S)
ATTR = re.compile(r'([A-Za-z_][\w.:-]*)\s*=\s*(["\x27])([^"\x27]*?)\2', re.S)
MAX_XML = 5 * 1024 * 1024
MAX_ROWS = 4000
MAX_BATCH = 3000


@dataclass(frozen=True)
class NumericField:
    id: int
    tag: str
    attribute: str
    value: str
    start: int
    end: int
    element: str


def numeric_fields(xml_text):
    """Find editable attribute spans without reserializing CryXML namespaces.

    Ignore XML comments, CDATA, processing instructions and XML declarations.
    Use an XML parser for final validation; the text scanner only locates spans.
    """
    if not isinstance(xml_text, str) or len(xml_text.encode('utf-8')) > MAX_XML:
        raise ValueError('Choose a text XML file smaller than 5 MiB.')
    raw = xml_text.encode('utf-8')
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
        raise ValueError('DTD/entity declarations are unsupported.')
    validate_xml(raw)
    # Mask XML comments and CDATA without shifting offsets.
    masked = list(xml_text)
    for match in re.finditer(r'<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>', xml_text, re.S):
        masked[match.start():match.end()] = ' ' * (match.end()-match.start())
    clean = ''.join(masked)
    fields = []
    for node in OPEN_TAG.finditer(clean):
        tag = node.group(1)
        snippet = clean[node.start():node.end()]
        for match in ATTR.finditer(snippet):
            key, value = match.group(1), match.group(3).strip()
            if not NUMBER.fullmatch(value):
                continue
            start = node.start()+match.start(3)
            end = node.start()+match.end(3)
            fields.append(NumericField(
                len(fields), tag, key, value, start, end,
                tag + '[' + str(node.start()) + ']'))
            if len(fields) > MAX_ROWS:
                raise ValueError('This XML contains too many numeric controls.')
    return tuple(fields)


def apply_numeric_edits(xml_text, replacements):
    """Update selected original attribute values; preserve other byte content."""
    fields = numeric_fields(xml_text)
    if len(replacements) > len(fields):
        raise ValueError('Too many edited gameplay fields.')
    by_id = {f.id:f for f in fields}
    changes = []
    for idx, new in replacements.items():
        if not isinstance(idx, int) or idx not in by_id:
            raise ValueError('Unknown gameplay field ID.')
        if not isinstance(new, str) or not NUMBER.fullmatch(new.strip()):
            raise ValueError('Gameplay values must be finite decimal numbers.')
        number = float(new)
        if not math.isfinite(number) or abs(number) > 1e12:
            raise ValueError('Value exceeds safe numeric editing bounds.')
        field = by_id[idx]
        if xml_text[field.start:field.end] != field.value:
            raise ValueError('Gameplay file changed; reload before saving.')
        changes.append((field.start, field.end, new.strip()))
    new_text = xml_text
    for start, end, value in sorted(changes,reverse=True):
        new_text = new_text[:start]+value+new_text[end:]
    # Existing topology and all attribute names must remain unchanged.
    validate_xml(new_text.encode('utf-8'), xml_text.encode('utf-8'))
    return new_text


def asset_search(workspaces, query='', *, suffix='', limit=2000):
    """Read workspace manifests only, not entire PAK files or asset bytes."""
    query = query.casefold().strip()
    extension = suffix.casefold().strip()
    results = []
    seen = set()
    if not 1 <= limit <= MAX_BATCH:
        raise ValueError('Unsupported asset search limit')
    for title, root in workspaces.items():
        root = Path(root).resolve()
        manifest = root / '.evolve-pak-workspace.json'
        if not manifest.is_file():
            continue
        data = json.loads(manifest.read_text('utf-8'))
        entries = data.get('entries')
        if not isinstance(entries, list):
            raise ValueError('Invalid extracted workspace inventory.')
        if len(entries) > 150000:
            raise ValueError('Workspace inventory exceeds search safety limit.')
        for entry in entries:
            rel = entry.get('path', '')
            if not isinstance(rel,str) or not rel:
                continue
            # checked_path is also used before opening/copying actual bytes.
            if query and query not in rel.casefold():
                continue
            if extension and not rel.casefold().endswith(extension):
                continue
            key = (str(root),rel.casefold())
            if key in seen:
                continue
            seen.add(key)
            results.append((str(title), root, rel, entry.get('size',0)))
            if len(results) >= limit:
                return tuple(results)
    return tuple(results)


def _safe_export_target(directory, rel):
    from evolve_pak_workspace import normalized_name
    safe = normalized_name(rel)
    base = Path(directory).resolve()
    output = (base/safe).resolve()
    if not output.is_relative_to(base):
        raise ValueError('Unsafe export destination')
    return output


def batch_export(workspaces, selections, destination, *, kind='original', max_assets=1000,
                 projects_root=None):
    """Batch export project-owned assets, refusing to overwrite existing output.

    selections: (label, root, relative) tuples, typically from asset_search.
    kind: original (safe copy), png (DDS->PNG), or glb (render model->GLB).
    Output is grouped per archive; every error is recorded in the report.
    """
    import shutil
    from dds_texture import export_png
    from model_textured_export import export_extracted_model
    if kind not in ('original','png','glb'):
        raise ValueError('Unsupported batch export format.')
    if len(selections) > max_assets or max_assets > MAX_BATCH:
        raise ValueError('Reduce the number of assets before exporting.')
    dest = Path(destination).resolve()
    extracted_roots=[(Path(ws).resolve()/'files').resolve()
                     for ws in workspaces.values()]
    if any(dest.is_relative_to(root) or root.is_relative_to(dest)
           for root in extracted_roots):
        raise ValueError('Export outside extracted workspace folders to avoid'
                         ' copying over project or installed assets.')
    if not dest.is_dir():
        raise FileNotFoundError('Create or select an export destination directory.')
    report = []
    for label, workspace, rel in selections:
        if (not isinstance(label,str) or len(label)>120 or
                label in ('.','..') or '/' in label or '\\' in label or ':' in label):
            raise ValueError('Unsafe archive label for batch export.')
        root = Path(workspace).resolve()
        if str(root) not in {str(Path(x).resolve()) for x in workspaces.values()}:
            raise ValueError('Batch contains an unrecognized workspace.')
        try:
            source = checked_path(root, rel)
            if kind == 'png':
                if is_split_dds(rel):
                    raise ValueError('Split DDS needs merging; use the texture editor.')
                if not is_dds(rel):
                    raise ValueError('Not a complete DDS texture.')
                target = _safe_export_target(dest / str(label), rel+'.png')
            elif kind == 'glb':
                if not is_model(rel):
                    raise ValueError('Not a CryEngine model.')
                target = _safe_export_target(dest / str(label), rel+'.glb')
            else:
                target = _safe_export_target(dest / str(label), rel)
            if target.exists():
                raise FileExistsError('Destination exists; no overwrite.')
            target.parent.mkdir(parents=True,exist_ok=True)
            if kind == 'original':
                shutil.copyfile(source,target)
            elif kind == 'png':
                export_png(source.read_bytes(),target)
            else:
                export_extracted_model(source,target,projects_root=projects_root)
            report.append({'asset':rel,'archive':str(label),
                           'status':'exported','destination':str(target)})
        except (OSError,ValueError,TypeError) as exc:
            report.append({'asset':rel,'archive':str(label),
                           'status':'skipped','reason':str(exc)[:220]})
    return tuple(report)
