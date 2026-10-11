"""Conservative filename-derived dependency families for signed Evolve CryPAKs.

A dependency plan is a KNOWN COMPANION group, not a claim that arbitrary
referenced models/textures or engine assets have been exhaustively resolved.
Only exact names from the current validated signing stage are extracted.
"""
from __future__ import annotations
from pathlib import PurePosixPath
from dataclasses import dataclass
import re


def _clean(path):
    return path.replace("\\", "/").strip("/")


def level_identity(relative):
    """Return case-folded level id for level.pak, terraintexture.pak or levels_X.pak."""
    path = _clean(relative)
    parts = path.split("/")
    if len(parts) >= 4 and parts[-3].casefold() == "levels" and parts[-1].casefold().endswith(".pak"):
        return parts[-2].casefold()
    stem = PurePosixPath(path).stem
    if len(parts) == 2 and parts[0].casefold() == "game" and stem.casefold().startswith("levels_"):
        return stem[7:].casefold()
    return None


def group_identity(relative):
    path = _clean(relative)
    level = level_identity(path)
    if level:
        return ("level", level)
    m = re.fullmatch(r"characters_(hunters|monsters|creatures)_(.+)_(data|ts)\.pak",
                     PurePosixPath(path).name, re.I)
    if m:
        return ("character", m.group(1).casefold(), m.group(2).casefold())
    m = re.fullmatch(r"(animationscaf|animationsconfig|animationsdba)\.pak",
                     PurePosixPath(path).name, re.I)
    if m:
        return ("animation",)
    return ("single", path.casefold())


def group_title(relative):
    family = group_identity(relative)
    if family[0] == "level":
        return "Level • " + family[1].replace("_", " ").title()
    if family[0] == "character":
        return family[1].title() + " • " + family[2].replace("_", " ").title()
    if family[0] == "animation":
        return "Global • Animations"
    return "Other • " + PurePosixPath(_clean(relative)).name


def is_level_pak(relative):
    return group_identity(relative)[0] == "level"


@dataclass(frozen=True)
class Plan:
    selected: str
    group: str
    companions: tuple[str, ...]
    missing: tuple[str, ...]
    descriptions: tuple[tuple[str, str], ...]

    @property
    def all_paks(self):
        return (self.selected,) + self.companions


def plan_dependencies(selected, available):
    """Select companion archives, never cross into a different map/character."""
    lookup = {_clean(item).casefold(): item for item in available}
    root = _clean(selected)
    if root.casefold() not in lookup:
        raise ValueError("Selected PAK is not in this signing stage.")
    selected = lookup[root.casefold()]
    kind = group_identity(selected)
    peers = []
    missing = []
    reasons = []
    if kind[0] == "level":
        map_name = kind[1]
        # The actual Level.PAK holds runtime mission placement data. The
        # levels_<map>.pak archive may hold editor-side .lyr and map resources.
        directory = "game/levels/" + map_name + "/"
        for path in available:
            low = _clean(path).casefold()
            if low.startswith(directory) and low.endswith(".pak") and low != root.casefold():
                peers.append(path)
                reasons.append((path, "Map folder companion"))
            elif (low == "game/levels_" + map_name + ".pak" and
                  low != root.casefold()):
                peers.append(path)
                reasons.append((path, "Map-level archive"))
        expected = ["game/levels/" + map_name + "/level.pak",
                    "game/levels/" + map_name + "/terraintexture.pak",
                    "game/levels_" + map_name + ".pak"]
        missing.extend(p for p in expected if p not in lookup)
    elif kind[0] == "character":
        typ, char = kind[1], kind[2]
        stem = "characters_" + typ + "_" + char + "_"
        for path in available:
            base = PurePosixPath(_clean(path)).name.casefold()
            if base in (stem + "data.pak", stem + "ts.pak") and path != selected:
                peers.append(path)
                reasons.append((path, "Character companion data/textures"))
        # Character references routinely depend on category-wide shared
        # geometry and texture PAKs plus the three animation archives.
        if char != "shared":
            for path in available:
                base = PurePosixPath(_clean(path)).name.casefold()
                if base in (f"characters_{typ}_shared_data.pak",
                            f"characters_{typ}_shared_ts.pak"):
                    peers.append(path)
                    reasons.append((path, "Shared " + typ + " assets"))
                elif base in ("animationscaf.pak", "animationsconfig.pak",
                              "animationsdba.pak"):
                    peers.append(path)
                    reasons.append((path, "Shared animation data"))
        expected = [stem + "data.pak", stem + "ts.pak"]
        missing.extend(name for name in expected if
                       not any(PurePosixPath(k).name.casefold() == name
                               for k in lookup))
    elif kind[0] == "animation":
        for path in available:
            if group_identity(path) == ("animation",) and path != selected:
                peers.append(path)
                reasons.append((path, "Animation archive companion"))
    seen = {selected.casefold()}
    unique = []
    for p in peers:
        if p.casefold() not in seen:
            seen.add(p.casefold())
            unique.append(p)
    return Plan(selected, group_title(selected), tuple(sorted(unique, key=str.casefold)),
                tuple(missing), tuple(reasons))


def plan_report(plan):
    text = [f"Dependency family: {plan.group}",
            f"Selected: {plan.selected}",
            f"Known companion PAKs ({len(plan.companions)}):"]
    desc = dict(plan.descriptions)
    text.extend("  • " + name + " — " + desc.get(name, "Companion")
                for name in plan.companions)
    if not plan.companions:
        text.append("  None recognized in this stage")
    if plan.missing:
        text += ["Expected but not found in stage:"] + [
            "  • " + item for item in plan.missing]
    text.append("Scope: filename-matched PAK families only. Other model, texture,")
    text.append("shader, game rules and runtime dependencies may remain unresolved.")
    return "\n".join(text)
