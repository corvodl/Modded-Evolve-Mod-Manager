"""Redistributable synthetic GFX movies for parser and real FFDec tests.

No Evolve or proprietary movie bytes are used. The tiny DefineShape SWF
contains a red vector rectangle, a second blank timeline frame and a nested
sprite, plus optional ImportAssets2 and script tags for diagnostics.
"""
import struct
import zlib


def _bitpack(bits):
    bits = list(bits)
    bits += [0] * (-len(bits) % 8)
    return bytes(sum((bit & 1) << (7 - i) for i, bit in enumerate(bits[n:n+8]))
                 for n in range(0, len(bits), 8))


def _uint(value, length):
    return [(value >> (length - 1 - n)) & 1 for n in range(length)]


def _signed(value, length):
    return _uint(value & ((1 << length) - 1), length)


def _rect(width, height):
    x, y = width * 20, height * 20
    count = max(x.bit_length() + 1, y.bit_length() + 1, 2)
    return _bitpack(_uint(count, 5) + _signed(0, count) +
                    _signed(x, count) + _signed(0, count) +
                    _signed(y, count))


def _tag(code, content=b""):
    if len(content) < 63:
        return struct.pack("<H", (code << 6) | len(content)) + content
    return struct.pack("<HI", (code << 6) | 63, len(content)) + content


def synthetic_gfx(*, scripts=False, imports=False, sprites=True,
                  nonvisual_sprite=False, exported_names=False,
                  action_message=None, embedded_image=False,
                  width=64, height=48, compressed=False):
    shape = [0, 0, 0, 1, 0, 1]  # Change style: move + FillStyle1
    shape += _uint(1, 5) + [0, 0] + [1]  # moveBits=1, x=0,y=0, fill=1
    for horizontal, delta in ((True, width*20), (False, height*20),
                              (True, -width*20), (False, -height*20)):
        n = max(abs(delta).bit_length() + 1, 2)
        shape += [1, 1] + _uint(n-2, 4) + [0, 0 if horizontal else 1]
        shape += _signed(delta, n)
    shape += [0, 0, 0, 0, 0, 0]
    # one red solid fill, no line styles; 1 fill bit / 0 line bits
    fill = bytes([1, 0, 255, 0, 0, 0])
    shape_payload = struct.pack("<H", 1) + _rect(width, height)
    shape_payload += fill + _bitpack(_uint(1, 4) + _uint(0, 4) + shape)
    place_shape = struct.pack("<HH", 1, 1) + b"\x00"  # identity matrix
    place_sprite = struct.pack("<HH", 2, 1) + b"\x00"
    body = _tag(2, shape_payload)
    if embedded_image:
        from io import BytesIO
        from PIL import Image
        encoded = BytesIO()
        Image.new("RGB", (16, 16), (245, 0, 0)).save(encoded, "JPEG")
        body += _tag(21, struct.pack("<H", 4) + encoded.getvalue())
    if sprites:
        nested = _tag(4, place_shape) + _tag(1) + _tag(0)
        body += _tag(39, struct.pack("<HH", 2, 1) + nested)
    if nonvisual_sprite:
        # Library class symbols can have no frames; main_menu.gfx uses many.
        body += _tag(39, struct.pack("<HH", 3, 0) + _tag(0))
    if exported_names:
        body += _tag(56, struct.pack("<HH", 1, 2) + b"mcButtonMainMenu\x00")
    if imports:
        body += _tag(71, b"gfxfontlib_locfont.gfx\x00\x01\x00\x00\x00")
    if scripts or action_message is not None:
        action = b"\x00"
        if action_message is not None:
            payload = b"\x00" + action_message.encode("ascii") + b"\x00"
            action = (b"\x96" + struct.pack("<H", len(payload)) +
                      payload + b"\x26\x00")
        body += _tag(12, action)
    body += _tag(4, place_sprite if sprites else place_shape)
    body += _tag(1)
    body += _tag(28, struct.pack("<H", 1))
    body += _tag(1) + _tag(0)
    payload = _rect(width, height) + struct.pack("<HH", 24 << 8, 2) + body
    fullsize = len(payload) + 8
    return ((b"CFX" if compressed else b"GFX") + bytes([15]) +
            struct.pack("<I", fullsize) +
            (zlib.compress(payload) if compressed else payload))
