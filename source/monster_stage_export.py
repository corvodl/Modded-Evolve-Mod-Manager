"""Select three independently verified monster evolution-stage assemblies.

The model-export caller passes GLBs already assembled and animated against
their own exact CryEngine CDF/skeleton. A stage is not inferred from ranking,
GLB order or a third-person camera suffix; a CDF must explicitly identify it.
"""
from __future__ import annotations

from pathlib import PurePosixPath
import re


_STAGE_MARKER = re.compile(
    r'(?:stage|stg|evolution|evo|level|lvl|tier)[_. -]*([123])(?=$|[^0-9])',
    re.IGNORECASE)
_SHORT_MARKER = re.compile(r'(?:^|[_ .-])(?:s|l)[_. -]*([123])(?=$|[_. -])', re.IGNORECASE)


def is_monster_character(character_folder):
    parts = PurePosixPath(str(character_folder).replace('\\', '/')).parts
    return (len(parts) == 3 and parts[0].casefold() == 'characters'
            and parts[1].casefold() == 'monsters')


def stage_from_cdf(cdf, character_folder):
    """Return exactly one explicitly identified stage number, else None.

    This intentionally excludes *_3p, LOD, equipment, and miscellaneous CDF
    variants. A stage in a nested folder also counts; conflicting markers do not.
    """
    root = PurePosixPath(str(character_folder).replace('\\', '/'))
    name = PurePosixPath(str(cdf).replace('\\', '/'))
    if name.suffix.casefold() != '.cdf' or not name.is_relative_to(root):
        return None
    parts = name.relative_to(root).parts
    if not parts:
        return None
    markers = set()
    for part in parts[:-1] + (name.stem,):
        matches = list(_STAGE_MARKER.finditer(part)) + list(_SHORT_MARKER.finditer(part))
        markers.update(int(match.group(1)) for match in matches)
    return next(iter(markers)) if len(markers) == 1 else None


def select_monster_stage_models(assemblies, character_folder):
    """Return (stage -> (GLB, original CDF report), selected paths).

    Every stage must have an exact verified CDF assembly. Never substitute the
    same generic body for three stages, or relabel a stage-3 character as stage 1.
    When multiple same-stage CDFs exist, prefer an actual multi-part rigged
    assembly over an accessory; ties remain ambiguous and are rejected.
    """
    if not is_monster_character(character_folder):
        raise ValueError('Stage-root model selection is only valid for monsters.')
    options = {1: [], 2: [], 3: []}
    for location, pair in assemblies.items():
        if not isinstance(pair, (tuple, list)) or len(pair) != 2:
            continue
        blob, info = pair
        if not isinstance(info, dict) or not isinstance(blob, bytes) or not blob.startswith(b'glTF'):
            continue
        cdf = info.get('cdf', '')
        stage = stage_from_cdf(cdf, character_folder)
        if stage is None:
            continue
        # The builder must have independently checked this CDF and not
        # inferred it from a similarly named asset.
        if str(cdf).casefold() not in str(location).casefold():
            # Paths under Assemblies/ use .glb instead of .cdf.
            from pathlib import PurePosixPath as P
            expected = P(str(cdf).replace('\\','/')).stem.casefold()
            if P(location).stem.casefold() != expected:
                continue
        score = (bool(info.get('preferred_full_character')),
                 int(info.get('part_count') or 0) > 1,
                 int(info.get('bone_count') or 0) > 0,
                 int(info.get('part_count') or 0),
                 int(info.get('bone_count') or 0),
                 int(info.get('embedded_animations') or 0))
        options[stage].append((score, location, pair))
    missing = [str(stage) for stage in (1, 2, 3) if not options[stage]]
    if missing:
        raise ValueError(
            'Monster root export requires independently verified CDF models for '
            'Stages 1, 2 and 3; missing stage(s): ' + ', '.join(missing)
            + '. Import the correct monster DATA/shared/animation PAKs and retry.')
    chosen, selected = {}, set()
    for stage, candidates in options.items():
        candidates.sort(key=lambda item: (item[0], item[1].casefold()), reverse=True)
        if len(candidates) > 1 and candidates[0][0] == candidates[1][0]:
            raise ValueError('Ambiguous verified monster Stage %d assemblies; '
                             'select a single appropriate stage CDF.' % stage)
        _, path, pair = candidates[0]
        selected.add(path)
        chosen[stage] = pair
    if len(selected) != 3:
        raise ValueError('Monster stage CDFs must identify three distinct models.')
    return chosen, frozenset(selected)
