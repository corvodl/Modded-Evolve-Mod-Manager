"""Bounded static Scaleform GFX inspection and FFDec 26.3.0 PNG rendering.

GFX/CFX are compiled Flash movies, NOT images. Only the installed, separately
configured JPEXS/FFDec renderer actually rasterizes timeline/sprite content.
ActionScript is never executed by this manager. Any runtime-created UI,
localization and engine callbacks cannot be simulated in a static preview.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import io
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import tempfile
import threading
import time
import zlib

from PIL import Image

MAX_SOURCE = 12 * 1024 * 1024
MAX_DECOMPRESSED = 32 * 1024 * 1024
MAX_TAGS = 80000
MAX_FRAMES = 2048
MAX_DIM = 8192
MAX_PIXELS = 36_000_000
MAX_IMPORTS = 128
MAX_OUTPUT = 70 * 1024 * 1024
TIMEOUT = 28
SUPPORTED_SCALE = (1, 2, 4)
IMAGE_TAGS = {6, 20, 21, 35, 36, 90}
SCRIPT_TAGS = {12, 59, 72, 82}
PLACE_TAGS = {4, 26, 70}
_IMPORT_TAGS = {57, 71}


class GfxError(ValueError):
    pass


@dataclass(frozen=True)
class GfxInfo:
    version: int
    compression: str
    width: int
    height: int
    fps: float
    frames: int
    sprites: tuple  # (id, frames)
    images: tuple  # embedded character IDs
    imports: tuple  # external movie paths
    scripts: bool
    root_placements: int
    tags: int
    exports: tuple = ()  # (character ID, readable exported symbol name)
    extension_tags: tuple = ()
    resources: tuple = ()  # (kind, characterId) resource tags with editable content

    @property
    def notes(self):
        warnings = ["Static Scaleform preview; in-game callbacks/localization are not executed."]
        if self.scripts:
            warnings.append("ActionScript present: runtime-created UI may be absent.")
        if self.scripts and not self.root_placements:
            warnings.append("Root timeline has no placed content; preview may be blank until the game invokes callbacks.")
        if self.imports:
            warnings.append("External font/movie imports require matching-build references: " +
                            ", ".join(self.imports[:6]))
        nonvisual = sum(1 for _, n in self.sprites if n == 0)
        if nonvisual:
            warnings.append(f"{nonvisual} nonvisual sprite/class definitions have no timeline to render.")
        if self.extension_tags:
            warnings.append("Scaleform extension tags " + repr(self.extension_tags) +
                            " are present; static output may differ from the game.")
        return tuple(warnings)


def _bits(buf, start, count):
    if count < 0 or count > 36 or start + count > len(buf) * 8:
        raise GfxError("Truncated GFX frame rectangle.")
    value = 0
    for i in range(count):
        p = start + i
        value = (value << 1) | ((buf[p // 8] >> (7 - p % 8)) & 1)
    return value


def _cstring(data):
    end = data.find(b"\x00", 0, 1024)
    if end < 0:
        raise GfxError("Invalid or oversized GFX imported movie name.")
    return data[:end].decode("utf-8", "replace")


def _decompress_limited(data, limit):
    unzip = zlib.decompressobj()
    payload = unzip.decompress(data, limit + 1)
    if (len(payload) > limit or unzip.unconsumed_tail or not unzip.eof
            or unzip.unused_data):
        raise GfxError("Compressed GFX payload is incomplete or exceeds safe limits.")
    return payload


def inspect_gfx(data):
    """Read GFX/CFX movie metadata and known tags without executing scripts."""
    if not isinstance(data, bytes) or len(data) < 16 or len(data) > MAX_SOURCE:
        raise GfxError("Missing or oversized GFX movie (limit 12 MiB).")
    signature = data[:3]
    if signature not in (b"GFX", b"CFX"):
        raise GfxError("Expected Scaleform GFX or compressed CFX movie signature.")
    version = data[3]
    total, = struct.unpack_from("<I", data, 4)
    if not 5 <= version <= 50 or total < 16 or total > MAX_DECOMPRESSED:
        raise GfxError("Unsupported GFX version or declared decompressed movie size.")
    if signature == b"CFX":
        body = _decompress_limited(data[8:], total - 8)
        uncompressed = data[:3].replace(b"C", b"G", 1) + data[3:8] + body
        compression = "CFX / zlib"
    else:
        uncompressed = data
        compression = "GFX / uncompressed"
    if len(uncompressed) != total:
        raise GfxError("GFX declared movie length does not match the payload.")
    body = uncompressed[8:]
    nbits = _bits(body, 0, 5)
    if nbits > 31 or nbits == 0:
        raise GfxError("Invalid GFX frame bounds.")
    values = []
    bit = 5
    for _ in range(4):
        unsigned = _bits(body, bit, nbits)
        values.append(unsigned - (1 << nbits) if unsigned & (1 << (nbits - 1))
                      else unsigned)
        bit += nbits
    offset = 8 + ((bit + 7) // 8)
    if offset + 4 > len(uncompressed):
        raise GfxError("Truncated GFX frame rate/count.")
    rate, frames = struct.unpack_from("<HH", uncompressed, offset)
    width = round((values[1] - values[0]) / 20)
    height = round((values[3] - values[2]) / 20)
    if (not 1 <= width <= MAX_DIM or not 1 <= height <= MAX_DIM
            or width * height > MAX_PIXELS or not 1 <= frames <= MAX_FRAMES):
        raise GfxError("GFX movie dimensions or frame count exceed preview limits.")
    pos = offset + 4
    sprites, images, imports = {}, set(), []
    resources = set()
    exports, extensions = [], set()
    script = False
    places = 0
    tags = 0
    while pos + 2 <= len(uncompressed):
        code_and_len, = struct.unpack_from("<H", uncompressed, pos)
        pos += 2
        code, size = code_and_len >> 6, code_and_len & 0x3f
        if size == 0x3f:
            if pos + 4 > len(uncompressed):
                raise GfxError("Truncated long GFX tag.")
            size, = struct.unpack_from("<I", uncompressed, pos)
            pos += 4
        if size > MAX_DECOMPRESSED or pos + size > len(uncompressed):
            raise GfxError("Invalid/truncated GFX tag payload.")
        block = uncompressed[pos:pos + size]
        pos += size
        tags += 1
        if tags > MAX_TAGS:
            raise GfxError("GFX tag count exceeds safe preview limit.")
        if code == 39 and len(block) >= 4:
            symbol, length = struct.unpack_from("<HH", block)
            # Valid Scaleform libraries may define 0-frame sprite shells for
            # ActionScript exports. They have no renderable timeline, but must
            # not invalidate their containing movie (main_menu.gfx uses many).
            if len(sprites) >= 1024 or length > MAX_FRAMES:
                raise GfxError("Too many GFX sprites or sprite frames.")
            sprites[symbol] = length
        if code == 56:  # ExportAssets: character ID -> viewer-friendly name
            if len(block) < 2:
                raise GfxError("Truncated exported GFX symbol list.")
            exported_count, = struct.unpack_from("<H", block)
            if exported_count > 2048 - len(exports):
                raise GfxError("GFX exported symbol count exceeds safe limit.")
            cursor = 2
            for _ in range(exported_count):
                if cursor + 2 > len(block):
                    raise GfxError("Truncated exported GFX symbol ID.")
                character_id, = struct.unpack_from("<H", block, cursor)
                cursor += 2
                name_bytes = block[cursor:]
                name = _cstring(name_bytes)
                cursor += name_bytes.index(b"\x00") + 1
                exports.append((character_id, name))
        if code >= 1000:
            extensions.add(code)
        if code in IMAGE_TAGS and len(block) >= 2:
            character_id = struct.unpack_from("<H", block)[0]
            images.add(character_id)
            resources.add(("image", character_id))
        if code in (2, 22, 32, 83) and len(block) >= 2:
            resources.add(("shape", struct.unpack_from("<H", block)[0]))
        if code in (11, 33, 37) and len(block) >= 2:
            resources.add(("text", struct.unpack_from("<H", block)[0]))
        if code in (14, 87) and len(block) >= 2:
            resources.add(("sound" if code == 14 else "binary", 
                           struct.unpack_from("<H", block)[0]))
        if code in (7, 34) and len(block) >= 2:
            resources.add(("button", struct.unpack_from("<H", block)[0]))
        if code in SCRIPT_TAGS:
            script = True
        if code in PLACE_TAGS:
            places += 1
        if code in _IMPORT_TAGS:
            imported = _cstring(block)
            if len(imports) >= MAX_IMPORTS:
                raise GfxError("Too many external movie imports.")
            imports.append(imported)
        if code == 0:
            break
    return GfxInfo(version, compression, width, height, rate / 256.0, frames,
                   tuple(sorted(sprites.items())), tuple(sorted(images)),
                   tuple(dict.fromkeys(imports)), script, places, tags,
                   tuple(exports), tuple(sorted(extensions)),
                   tuple(sorted(resources)))


_INVALID_COMPONENT = re.compile(r'[<>:"|?*]')
_RESERVED = frozenset(("CON", "PRN", "AUX", "NUL") +
                      tuple(f"COM{i}" for i in range(1, 10)) +
                      tuple(f"LPT{i}" for i in range(1, 10)))


def _virtual_path(value):
    """Pure, bounded CryEngine path sanitizer (no encryption imports needed).

    A GFX preview in the isolated renderer must not import private signing
    dependencies simply to normalize a virtual filename.
    """
    if (not isinstance(value, str) or not 1 <= len(value) <= 1024 or
            value.startswith(("/", "\\")) or "\\" in value or "\x00" in value):
        raise GfxError("Unsafe movie virtual path.")
    parts = value.split("/")
    if any(not p or p in (".", "..") or p.endswith((".", " ")) or
           _INVALID_COMPONENT.search(p) or
           p.split(".")[0].upper() in _RESERVED for p in parts):
        raise GfxError("Unsafe movie virtual path.")
    return "/".join(parts)


def safe_import_path(url, virtual):
    """Allow only a normalized relative movie from the same virtual folder."""
    if (not isinstance(url, str) or not url or
            url.startswith(("/", "\\")) or
            re.match(r"^[a-zA-Z][\w+.-]*:", url) or
            "\\" in url or "\x00" in url):
        raise GfxError("External URL/absolute GFX imports are never fetched: " + str(url)[:90])
    pieces = PurePosixPath(url).parts
    if any(x in (".", "..") for x in pieces):
        raise GfxError("Parent-relative GFX imports are disabled.")
    if not url.casefold().endswith((".gfx", ".swf")):
        raise GfxError("Unsupported external GFX import type.")
    name = _virtual_path(virtual)
    return _virtual_path(str(PurePosixPath(name).parent / url))


def _validate_imported_movie(content, relative):
    """Validate bounded Flash movie bytes; GFX and ordinary SWF differ.

    Scaleform ImportAssets2 can refer to .swf files (FWS/CWS) as well as
    .gfx files. JPEXS handles both, but GFX parsing alone must not reject
    otherwise valid Flash dependencies.
    """
    if not isinstance(content, bytes) or len(content) < 8 or len(content) > MAX_SOURCE:
        raise GfxError("External Flash movie is incomplete or exceeds size limit: " + relative)
    if relative.casefold().endswith(".gfx"):
        inspect_gfx(content)
        return
    if content[:3] not in (b"FWS", b"CWS"):
        raise GfxError("Imported SWF must have FWS or CWS signature: " + relative)
    size, = struct.unpack_from("<I", content, 4)
    if size < 12 or size > MAX_DECOMPRESSED:
        raise GfxError("Imported SWF declares unsafe decompressed size: " + relative)
    if content[:3] == b"FWS" and size != len(content):
        raise GfxError("Imported SWF length mismatch: " + relative)
    if content[:3] == b"CWS" and len(_decompress_limited(content[8:], size - 8)) != size - 8:
        raise GfxError("Imported SWF compression/length mismatch: " + relative)


def resolve_imports(info, virtual, resolver):
    """Resolve only unique, intact per-build/project providers supplied by UI.

    Resolver receives a full virtual path and returns bytes or None. Import
    candidates are never looked up across arbitrary independent builds.
    """
    resolved, failures = {}, []
    for url in info.imports:
        try:
            relative = safe_import_path(url, virtual)
            content = resolver(relative)
            if content is None:
                failures.append(f"Missing matching-context movie: {relative}")
                continue
            _validate_imported_movie(content, relative)
            resolved[relative] = content
        except (GfxError, ValueError, OSError) as exc:
            failures.append(str(exc))
    return resolved, tuple(failures)


def ffdec_command(configured=None):
    """Use a separately configured/pinned FFDec 26.3.0 installation.

    We intentionally do NOT download executables or bundle FFDec GPLv3 and a
    Java runtime without an explicit third-party redistribution review.
    ffdec-cli.exe is supported when present. A jar requires Java (or a private
    JVM executable) and missing runtime is reported to the user.
    """
    choice = configured or os.environ.get("EVOLVE_GFX_FFDEC", "")
    if not choice:
        raise GfxError("GFX renderer unavailable. Configure FFDec 26.3.0 (ffdec-cli.exe or ffdec.jar); Java may also be required.")
    file = Path(choice).expanduser().resolve()
    if file.is_dir():
        cli = file / "ffdec-cli.exe"
        jar = file / "ffdec.jar"
        file = cli if cli.is_file() else jar
    if not file.is_file() or file.is_symlink():
        raise GfxError("Configured FFDec renderer is missing or unsafe. Select the FFDec 26.3.0 installation again.")
    if file.name.casefold() == "ffdec-cli.exe":
        return (str(file),)
    if file.name.casefold() == "ffdec.jar":
        preferred_java = os.environ.get("EVOLVE_GFX_JAVA", "")
        java = (str(Path(preferred_java).resolve()) if preferred_java
                else shutil.which("java"))
        if not java or (preferred_java and not Path(java).is_file()):
            raise GfxError("FFDec JAR configured but Java is missing. Configure EVOLVE_GFX_JAVA or choose ffdec-cli.exe.")
        return (java, "-Djava.awt.headless=true", "-jar", str(file))
    raise GfxError("Select ffdec-cli.exe or ffdec.jar from a trusted FFDec 26.3.0 installation.")


def _execute(command, *, cwd, cancelled, timeout):
    """Kill on cancellation/timeout, drain bounded diagnostic text to avoid hangs."""
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, cwd=str(cwd),
                            creationflags=flags)
    buffer = bytearray()
    def drain():
        while True:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            if len(buffer) < 16000:
                buffer.extend(chunk[:16000 - len(buffer)])
    thread = threading.Thread(target=drain, daemon=True)
    thread.start()
    try:
        until = time.monotonic() + timeout
        while proc.poll() is None:
            if cancelled and cancelled():
                raise InterruptedError("Scaleform render cancelled.")
            if time.monotonic() >= until:
                raise GfxError("FFDec rendering timed out; try a smaller GFX frame/symbol.")
            time.sleep(0.05)
    except BaseException:
        proc.kill()
        proc.wait(timeout=3)
        raise
    finally:
        thread.join(timeout=2)
        if proc.stdout:
            proc.stdout.close()
    if proc.returncode != 0:
        raise GfxError("FFDec could not render this Scaleform selection: " +
                       bytes(buffer).decode("utf-8", "replace")[-700:])
    return bytes(buffer)


_CACHE = {}
_CACHE_LOCK = threading.RLock()


def render_gfx(data, *, virtual="movie.gfx", frame=1, symbol=0, scale=1,
               imports=None, build_context=None, backend=None, cancelled=None):
    """Return unscaled PNG raster bytes for the selected frame or sprite.

    Do not use a Qt screenshot. FFDec renders the Scaleform display-list.
    A cache key includes imported movie payload digests and build identity.
    """
    info = inspect_gfx(data)
    # FFDec itself understands ImportAssets2. Reject unsafe URLs before
    # invoking the third-party process, not merely in the UI resolver.
    for external in info.imports:
        safe_import_path(external, virtual)
    if (scale not in SUPPORTED_SCALE or
            info.width * scale > MAX_DIM or info.height * scale > MAX_DIM or
            info.width * info.height * scale * scale > MAX_PIXELS):
        raise GfxError("Requested raster resolution exceeds the 36-million-pixel limit.")
    maxframe = info.frames if symbol == 0 else dict(info.sprites).get(symbol, 0)
    if not isinstance(frame, int) or not 1 <= frame <= maxframe:
        raise GfxError("Selected timeline/sprite frame is unavailable.")
    command = ffdec_command(backend)
    name = _virtual_path(virtual)
    if not name.casefold().endswith(".gfx"):
        raise GfxError("Only supported GFX source paths can be rasterized.")
    dependencies = imports or {}
    if len(dependencies) > MAX_IMPORTS:
        raise GfxError("Too many staged external movies.")
    checked = {}
    for key, blob in dependencies.items():
        virtual_dep = _virtual_path(key)
        if not virtual_dep.casefold().endswith((".gfx", ".swf")):
            raise GfxError("Invalid external dependency type.")
        _validate_imported_movie(blob, virtual_dep)
        checked[virtual_dep] = blob
    executable = Path(command[-1] if command[-1].endswith(".jar") else command[0])
    stamp = (executable.stat().st_size, executable.stat().st_mtime_ns)
    digest = hashlib.sha256(data).hexdigest()
    dep_digests = tuple(sorted((n.casefold(), hashlib.sha256(b).hexdigest())
                               for n, b in checked.items()))
    key = (digest, name.casefold(), int(symbol), frame, scale,
           str(build_context), tuple(command), stamp, dep_digests)
    with _CACHE_LOCK:
        hit = _CACHE.get(key)
        if hit is not None:
            return hit
    with tempfile.TemporaryDirectory(prefix="evolve-gfx-") as temporary:
        root = Path(temporary)
        target = root.joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        # Never follow URL/absolute or parent traversal imports.
        for rel, blob in checked.items():
            dest = root.joinpath(*rel.split("/"))
            if dest == target or not dest.resolve().is_relative_to(root.resolve()):
                raise GfxError("Invalid or conflicting staged movie import.")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
        out = root / "_rendered"
        out.mkdir()
        args = [*command]
        if command[-1].endswith(".jar"):
            pass
        args += ["-cli", "-onerror", "abort", "-exportTimeout", str(TIMEOUT - 2),
                 "-zoom", str(scale), "-ignorebackground"]
        if symbol:
            # FFDec's "frame" exporter always targets the main timeline.
            # For a sprite, request the character explicitly with the sprite
            # exporter, then select the requested sprite-local frame.
            args.extend(["-selectid", str(symbol), "-select",
                         f"{symbol}:{frame}", "-format", "sprite:png"])
            output_kind = "sprite"
        else:
            args.extend(["-select", str(frame), "-format", "frame:png"])
            output_kind = "frame"
        # Explicitly prohibit remote HTTP(S) imports regardless of the
        # user's persisted FFDec preferences. Relative lookups see only the
        # isolated temporary virtual-path layout populated above.
        args.extend(["-importAssets", "yes,local"])
        args.extend(["-export", output_kind, str(out), str(target)])
        _execute(args, cwd=root, cancelled=cancelled, timeout=TIMEOUT)
        files = [p for p in out.rglob("*.png") if p.is_file() and not p.is_symlink()]
        if len(files) != 1:
            labels = [p.relative_to(out).as_posix() for p in files[:20]]
            raise GfxError("FFDec produced " + str(len(files)) +
                           " PNGs for the selected frame/sprite; outputs: " +
                           repr(labels))
        path = files[0]
        if path.stat().st_size > MAX_OUTPUT:
            raise GfxError("Rendered PNG exceeds the size limit.")
        rendered = path.read_bytes()
        with Image.open(io.BytesIO(rendered)) as image:
            if (image.width * image.height > MAX_PIXELS or
                    image.width > MAX_DIM or image.height > MAX_DIM):
                raise GfxError("Rendered image exceeds pixel limit.")
            image.load()
            if image.width < 1 or image.height < 1:
                raise GfxError("Renderer returned empty image.")
            with io.BytesIO() as stream:
                image.convert("RGBA").save(stream, "PNG")
                png = stream.getvalue()
    with _CACHE_LOCK:
        if len(_CACHE) >= 12:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = png
    return png


def export_gfx_png(png, destination):
    """Export rendered RGBA content unchanged, never a scaled viewer screenshot."""
    path = Path(destination).expanduser().resolve()
    if path.suffix.casefold() != ".png" or not path.parent.is_dir():
        raise GfxError("Choose a PNG filename in an existing directory.")
    if path.exists():
        raise FileExistsError("PNG already exists; choose a new name to avoid overwriting.")
    with Image.open(io.BytesIO(png)) as frame:
        if frame.format != "PNG":
            raise GfxError("No verified rendered PNG is available.")
        frame.load()
        if frame.width * frame.height > MAX_PIXELS:
            raise GfxError("Rendered image exceeds pixel limit.")
    with path.open("xb") as out:
        out.write(png)
    return path
