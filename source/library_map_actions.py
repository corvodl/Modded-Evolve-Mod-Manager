"""Source-path-grounded Asset Library map selection.

Never classify a bare `level.pak` or `terraintexture.pak` from the name
alone: a verified source path must establish map ownership first.
"""
from pathlib import PurePosixPath
from level_asset_mount import level_from_source


def library_map_identity(record):
    """Identify one map from an imported PAK's original location."""
    if not isinstance(record, dict) or not str(record.get("name", "")).casefold().endswith(".pak"):
        return None
    source = str(record.get("source") or "")
    if not source.replace("\\", "/").casefold().endswith(".pak"):
        return None
    name = PurePosixPath(source.replace("\\", "/")).name.casefold()
    if name != str(record["name"]).casefold():
        return None
    return level_from_source(source)


def validated_map_selection(records):
    """Return (preferred primary record, map ID) or reject mixed-map rows.

    Existing build/ownership and project staging checks remain mandatory.
    """
    if not records:
        raise ValueError("Select at least one imported map PAK.")
    if len(records) > 30:
        raise ValueError("A map selection cannot exceed 30 PAKs.")
    map_names = [library_map_identity(record) for record in records]
    if any(name is None for name in map_names):
        raise ValueError(
            "One selected PAK is not in a recognized map location. "
            "Choose Level, terrain, and other PAKs from the same level folder.")
    if len(set(map_names)) != 1:
        raise ValueError("Selected PAKs belong to different maps.")
    level = map_names[0]
    primary = next((record for record in records
                    if record["name"].casefold() == "level.pak"), None)
    if primary is None:
        primary = next((record for record in records
                        if record["name"].casefold() == "levels_" + level + ".pak"),
                       records[0])
    return primary, level
