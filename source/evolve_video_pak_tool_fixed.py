#!/usr/bin/env python3
"""Inspect CryPak signed/encrypted videos.pak and patch a fixed-size file slot.

Requires: Python >=3.9 and `cryptography` plus `twofish` on Windows.
On Linux it can also use system libgcrypt for Twofish.
Never modifies the supplied original PAK; patch creates a new copy.

IMPORTANT: CRC32-matched padding may be rejected by the USM decoder.
This utility validates the archive cryptography, not video playback.
"""
import argparse
import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import zlib

from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import load_der_public_key

VIDEO_NAME = "Libs/UI/flashassets/videos/main_menu.usm"
EOCD = b"PK\x05\x06"
CDR_SIG = b"PK\x01\x02"
LOCAL_SIG = b"PK\x03\x04"


class Twofish:
    def __init__(self, key):
        try:
            from twofish import Twofish as PythonTwofish
            self.impl = PythonTwofish(key)
            self.batch = None
            self.lib = None
        except ImportError:
            # Linux: use libgcrypt's Twofish ECB to produce CTR key stream.
            if os.name == "nt":
                raise RuntimeError("Install the Twofish library: py -m pip install twofish")
            self.lib = ctypes.CDLL('libgcrypt.so.20')
            g = self.lib
            g.gcry_check_version.argtypes = [ctypes.c_char_p]
            g.gcry_check_version.restype = ctypes.c_char_p
            g.gcry_check_version(None)
            g.gcry_cipher_map_name.argtypes = [ctypes.c_char_p]
            g.gcry_cipher_map_name.restype = ctypes.c_int
            g.gcry_cipher_open.argtypes = [ctypes.POINTER(ctypes.c_void_p), ctypes.c_int, ctypes.c_int, ctypes.c_uint]
            g.gcry_cipher_setkey.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t]
            g.gcry_cipher_encrypt.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t]
            g.gcry_cipher_close.argtypes = [ctypes.c_void_p]
            self.handle = ctypes.c_void_p()
            alg = g.gcry_cipher_map_name(b'TWOFISH')
            if not alg or g.gcry_cipher_open(ctypes.byref(self.handle), alg, 1, 0):
                raise RuntimeError('libgcrypt Twofish unavailable')
            if g.gcry_cipher_setkey(self.handle, ctypes.c_char_p(key), len(key)):
                raise RuntimeError('libgcrypt rejected Twofish key')
            self.impl = None
            self.batch = True

    def encrypt_blocks(self, blocks):
        if self.impl:
            return b''.join(self.impl.encrypt(blocks[i:i+16]) for i in range(0, len(blocks), 16))
        out = ctypes.create_string_buffer(len(blocks))
        code = self.lib.gcry_cipher_encrypt(self.handle, out, len(blocks), ctypes.c_char_p(blocks), len(blocks))
        if code:
            raise RuntimeError('libgcrypt failed with code %s' % code)
        return out.raw

    def close(self):
        if self.lib:
            self.lib.gcry_cipher_close(self.handle)
            self.lib = None


def twofish_ctr(data, key, iv, block_offset=0):
    """CryEngine stream mode: 16-byte Twofish, little-endian counter."""
    if len(key) != 16 or len(iv) != 16:
        raise ValueError('expected 16-byte Twofish key and IV')
    tf = Twofish(key)
    iv_val = int.from_bytes(iv, 'little')
    out = bytearray()
    try:
        for start in range(0, len(data), 16 * 8192):
            part = data[start:start + 16 * 8192]
            count = (len(part) + 15) // 16
            ctrblocks = b''.join(((iv_val + block_offset + start // 16 + j) & ((1 << 128)-1)).to_bytes(16, 'little') for j in range(count))
            keystream = tf.encrypt_blocks(ctrblocks)
            out.extend(x ^ y for x, y in zip(part, keystream))
    finally:
        tf.close()
    return bytes(out)


def mgf1_sha256(seed, size):
    return b''.join(hashlib.sha256(seed + i.to_bytes(4, 'big')).digest() for i in range((size + 31) // 32))[:size]


def unwrap_crypto_block(block, pub):
    nums = pub.public_numbers()
    if len(block) != nums.n.bit_length() // 8:
        raise ValueError('wrapped block size is not RSA modulus size')
    em = pow(int.from_bytes(block, 'big'), nums.e, nums.n).to_bytes(len(block), 'big')
    if em[:1] != b'\x00':
        raise ValueError('incorrect OAEP first byte')
    masked_seed, masked_db = em[1:33], em[33:]
    seed = bytes(a ^ b for a, b in zip(masked_seed, mgf1_sha256(masked_db, 32)))
    db = bytes(a ^ b for a, b in zip(masked_db, mgf1_sha256(seed, len(masked_db))))
    if db[:32] != hashlib.sha256(b'').digest():
        raise ValueError('invalid OAEP-SHA256 label digest (wrong key?)')
    rest = db[32:]
    idx = rest.find(b'\x01')
    if idx < 0 or any(rest[:idx]):
        raise ValueError('invalid OAEP separator')
    value = rest[idx+1:]
    if len(value) != 16:
        raise ValueError('expected 16-byte OAEP key, got %s' % len(value))
    return value


def get_eocd_tail(f):
    f.seek(0, 2)
    length = f.tell()
    f.seek(max(0, length - 65557))
    data = f.read()
    for x in range(len(data) - 22, -1, -1):
        if data[x:x+4] == EOCD and x+22 <= len(data):
            disk, cd_disk, ondisk, count, cdsize, cdpos, clen = struct.unpack_from('<4H2IH', data, x+4)
            if x+22+clen == len(data):
                return length, (disk, cd_disk, ondisk, count, cdsize, cdpos, clen), data[x+22:]
    raise ValueError('no correctly terminated ZIP EOCD found')


def load_archive(path, keypath, tail_mode=False, basename='videos.pak'):
    pub = load_der_public_key(Path(keypath).read_bytes())
    if pub.key_size != 1024:
        raise ValueError('expected 1024-bit RSA key')
    with path.open('rb') as f:
        length, fields, comment = get_eocd_tail(f)
        disk, cd_disk, ondisk, count, cdsize, cdpos, clen = fields
        if disk or cd_disk or count != ondisk:
            raise ValueError('multidisk archive unsupported')
        if len(comment) != 2320 or comment[:6] != b'\x06\x00\x00\x00\x01\x03':
            raise ValueError('original signed+encrypted PAK extension not present')
        if struct.unpack_from('<I', comment, 6)[0] != 133 or struct.unpack_from('<I', comment, 139)[0] != 2181:
            raise ValueError('signature/encryption header size mismatch')
        # With the tail sample, CDR is positioned just before the EOCD;
        # full original PAK also validates the absolute offset.
        cdr_at = length - (clen + 22 + cdsize)
        if not tail_mode and cdpos != cdr_at:
            raise ValueError(f'Central-directory offset mismatch: CDR={cdpos}, expected={cdr_at}')
        f.seek(cdr_at)
        cipher_cdr = f.read(cdsize)
    wrapped = comment[139:]
    wrapped_iv = wrapped[4:132]
    wrapped_keys = [wrapped[133+i*128:133+(i+1)*128] for i in range(16)]
    iv = unwrap_crypto_block(wrapped_iv, pub)
    keys = [unwrap_crypto_block(x, pub) for x in wrapped_keys]
    # The CDR cipher key occupies the first key-table entry.
    plain_cdr = twofish_ctr(cipher_cdr, keys[0], iv)
    if plain_cdr[:4] != CDR_SIG:
        raise ValueError('wrong CDR key/IV or invalid central directory')
    sig = comment[11:139]
    pub.verify(sig, plain_cdr + basename.encode('ascii'), padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=0), hashes.SHA256())
    entries = {}
    p = 0
    for _ in range(count):
        if p + 46 > len(plain_cdr) or plain_cdr[p:p+4] != CDR_SIG:
            raise ValueError('invalid central-directory record at offset %d' % p)
        a = struct.unpack_from('<4s6H3I5H2I', plain_cdr, p)
        (_, ver_made, ver_need, flags, method, time, date, crc, csize, usize, nl, el, cl, diskstart, ia, ea, offset) = a
        filename = plain_cdr[p+46:p+46+nl].decode('utf-8')
        entries[filename] = dict(method=method, flags=flags, crc=crc, compressed_size=csize,
                                 original_size=usize, local_offset=offset, name_length=nl,
                                 extra_length=el, comment_length=cl)
        p += 46 + nl + el + cl
    if p != len(plain_cdr):
        raise ValueError('central directory trailing bytes')
    return dict(entries=entries, count=count, signature_valid=True, keys=keys, iv=iv,
                cdr_at=cdpos, cdr_size=cdsize, eocd_comment_size=clen, public_key=pub)


def local_data_start(f, entry, cdr_at):
    """CryEngine encrypted-header PAK: take data offsets from signed CDR.

    CryEngine ZipDirCacheFactory::InitDataOffset uses the encrypted CDR's
    nFileNameLength, not a local ZIP-header read, when headers are encrypted.
    The local header is not guaranteed to contain plaintext 'PK\x03\x04'.
    """
    header_at = entry['local_offset']
    start = header_at + 30 + entry['name_length']
    end = start + entry['compressed_size']
    f.seek(0, 2)
    file_size = f.tell()
    if header_at < 0 or start <= header_at or end > cdr_at or cdr_at > file_size:
        raise ValueError('signed CDR points to out-of-range encrypted file data')
    f.seek(header_at)
    header = f.read(4)
    if header != LOCAL_SIG and not getattr(local_data_start, '_reported_encrypted_header', False):
        print('INFO: encrypted local ZIP headers detected; '
              'using signed central-directory offsets (CryEngine behavior). '
              'Further identical messages suppressed.')
        local_data_start._reported_encrypted_header = True
    return start


def file_iv(entry):
    cs, us, crc = entry['compressed_size'], entry['original_size'], entry['crc']
    value = (((us ^ ((cs << 12) & 0xffffffff)) & 0xffffffff),
             0 if cs else 1,
             ((crc ^ (cs << 12)) & 0xffffffff),
             ((0 if us else 1) ^ cs) & 0xffffffff)
    return struct.pack('<4I', *value)


def patch_crc32_suffix(prefix_crc, target):
    """Find a 4-byte suffix with a specified zlib/ZIP CRC32."""
    baseline = zlib.crc32(b'\x00' * 4, prefix_crc)
    diff = target ^ baseline
    columns = [zlib.crc32((1 << bit).to_bytes(4, 'little'), prefix_crc) ^ baseline for bit in range(32)]
    rows = [sum(((columns[col] >> row) & 1) << col for col in range(32)) | (((diff >> row) & 1) << 32) for row in range(32)]
    for col in range(32):
        pivot = next((r for r in range(col, 32) if (rows[r] >> col) & 1), None)
        if pivot is None:
            raise ValueError('CRC correction linear system has no solution')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(32):
            if r != col and (rows[r] >> col) & 1:
                rows[r] ^= rows[col]
    x = sum(((rows[i] >> 32) & 1) << i for i in range(32))
    result = x.to_bytes(4, 'little')
    assert zlib.crc32(result, prefix_crc) == target
    return result


def patch_crc32_window(prefix_crc, trailing_bytes, target):
    """Find four bytes that achieve target CRC even with bytes following them."""
    trailing_bytes = bytes(trailing_bytes)
    baseline = zlib.crc32(b'\x00' * 4 + trailing_bytes, prefix_crc)
    diff = target ^ baseline
    columns = [
        zlib.crc32((1 << bit).to_bytes(4, 'little') + trailing_bytes, prefix_crc) ^ baseline
        for bit in range(32)
    ]
    rows = [
        sum(((columns[col] >> row) & 1) << col for col in range(32))
        | (((diff >> row) & 1) << 32)
        for row in range(32)
    ]
    for col in range(32):
        pivot = next((r for r in range(col, 32) if (rows[r] >> col) & 1), None)
        if pivot is None:
            raise ValueError('CRC correction linear system has no solution')
        rows[col], rows[pivot] = rows[pivot], rows[col]
        for r in range(32):
            if r != col and (rows[r] >> col) & 1:
                rows[r] ^= rows[col]
    return sum(((rows[i] >> 32) & 1) << i for i in range(32)).to_bytes(4, 'little')


def build_padded_replacement(user_video, size, required_crc):
    data = user_video.read_bytes()
    if len(data) > size:
        raise ValueError(f'candidate is too large: {len(data)} bytes, max {size}')
    if not data.startswith(b'CRID'):
        print('WARNING: replacement does not begin with CRID; check USM encoding', file=sys.stderr)

    # Exact-size USM: do not chop the trailer or extend the file.
    # The user observed 15 '=' bytes followed by a NUL; assume only the four
    # '=' bytes immediately before that NUL can be repurposed. This is
    # experimental and MUST be tested with the game decoder.
    if len(data) == size:
        if not data.endswith(b'=' * 15 + b'\x00'):
            raise ValueError('exact-size USM needs a confirmed four-byte filler area; '
                             'expected trailing 15 equals signs and NUL, aborting')
        patch_at = size - 5
        prefix_crc = zlib.crc32(data[:patch_at])
        trailer = data[patch_at+4:]
        correction = patch_crc32_window(prefix_crc, trailer, required_crc)
        result = data[:patch_at] + correction + trailer
        assert len(result) == size and result.endswith(b'\x00')
        if zlib.crc32(result) != required_crc:
            raise ValueError('internal CRC32 window-correction failure')
        print('EXPERIMENTAL: overwrote four trailing equals-sign bytes '
              'immediately before the final NUL; USM decoding is unverified.')
        return result, correction

    if len(data) > size - 4:
        raise ValueError(f'candidate is too large: {len(data)} bytes, max {size-4}; '
                         'cannot append CRC bytes and no safe in-place area identified')
    # Existing behavior for shorter files.
    head = data + bytes(size - 4 - len(data))
    crc_prefix = zlib.crc32(head)
    suffix = patch_crc32_suffix(crc_prefix, required_crc)
    result = head + suffix
    if zlib.crc32(result) != required_crc:
        raise ValueError('internal CRC32 correction failure')
    return result, suffix


def inspect(args):
    info = load_archive(args.archive, args.key, args.tail, args.name)
    print(f"Signature VALID; {info['count']} records; decrypted CDR {info['cdr_size']} bytes")
    if args.entry:
        e = info['entries'][args.entry]
        print(f"Entry: {args.entry}\nMethod: {e['method']}; size: {e['original_size']:,}; offset: {e['local_offset']:,}; CRC32: {e['crc']:08x}")
    else:
        for name, e in info['entries'].items():
            print(f"{e['original_size']:>12} bytes  method {e['method']:>2}  {name}")


def patch(args):
    # CryPak authenticates the exact archive basename as part of the signature.
    if args.archive.name != args.name or args.output.name != args.name:
        raise ValueError('Both archive paths must have the signed basename %r (put output in a different folder)' % args.name)
    if args.archive.resolve() == args.output.resolve():
        raise ValueError('output must be different from original PAK')
    if args.output.exists():
        raise ValueError('output already exists (refusing overwrite)')
    if args.archive.stat().st_size < 65536:
        raise ValueError('need COMPLETE original PAK, not a tail sample')
    info = load_archive(args.archive, args.key, basename=args.name)
    entry = info['entries'][args.entry]
    if entry['method'] != 13 or entry['compressed_size'] != entry['original_size']:
        raise ValueError('entry is not the expected method-13 stored video')
    # Confirm the target payload occupies its entire signed slot with no extra
    # local-header fields. For this original videos.pak, adjacent offsets are
    # contiguous and this is exact.
    following = sorted(e['local_offset'] for e in info['entries'].values()
                       if e['local_offset'] > entry['local_offset'])
    if following:
        signed_next = following[0]
        calculated_next = entry['local_offset'] + 30 + entry['name_length'] + entry['compressed_size']
        if signed_next != calculated_next:
            raise ValueError('signed entry layout has unexpected gap; not assuming local-header size')
    video, suffix = build_padded_replacement(args.video, entry['original_size'], entry['crc'])
    idx = (~(entry['crc'] >> 2)) & 0xf
    iv = file_iv(entry)
    with args.archive.open('rb') as f:
        start = local_data_start(f, entry, info['cdr_at'])
        f.seek(start)
        original_cipher = f.read(entry['original_size'])
    if len(original_cipher) != entry['original_size']:
        raise ValueError('truncated original video payload')
    original_plain = twofish_ctr(original_cipher, info['keys'][idx], iv)
    original_crc = zlib.crc32(original_plain)
    if original_crc != entry['crc']:
        raise ValueError(f'Original file CRC mismatch: got {original_crc:08x}, expected {entry["crc"]:08x}. Aborting.')
    print('Original encrypted video CRC verified; source prefix:', repr(original_plain[:16]))
    updated_cipher = twofish_ctr(video, info['keys'][idx], iv)
    assert twofish_ctr(updated_cipher, info['keys'][idx], iv) == video
    print('Video replacement encrypted; CRC correction bytes:', suffix.hex())
    print('Copying original PAK (may require >1 GB of free disk space)...')
    shutil.copyfile(args.archive, args.output)
    try:
        with args.output.open('r+b') as f:
            f.seek(start)
            f.write(updated_cipher)
        verify = load_archive(args.output, args.key, basename=args.name)
        with args.output.open('rb') as f:
            f.seek(start)
            saved = f.read(entry['original_size'])
        result_plain = twofish_ctr(saved, verify['keys'][idx], iv)
        if zlib.crc32(result_plain) != entry['crc'] or result_plain != video:
            raise ValueError('Post-write verification failed')
    except Exception:
        args.output.unlink(missing_ok=True)
        raise
    print('SUCCESS: signed central directory unchanged and original RSA signature still VALID.')
    print('Replacement file CRC is VALID. In-game USM decoding remains untested.')
    print('Output:', args.output)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='action', required=True)
    ip = sub.add_parser('inspect', help='verify CDR signature and list archived videos')
    ip.add_argument('archive', type=Path)
    ip.add_argument('--key', required=True, type=Path)
    ip.add_argument('--tail', action='store_true', help='input is the original 64 KiB archive tail')
    ip.add_argument('--name', default='videos.pak', help='signed PAK basename (default videos.pak)')
    ip.add_argument('--entry', default=VIDEO_NAME)
    pp = sub.add_parser('patch', help='create NEW full PAK with fixed-size replacement video')
    pp.add_argument('archive', type=Path)
    pp.add_argument('output', type=Path)
    pp.add_argument('--key', required=True, type=Path)
    pp.add_argument('--video', required=True, type=Path)
    pp.add_argument('--name', default='videos.pak')
    pp.add_argument('--entry', default=VIDEO_NAME)
    args = parser.parse_args()
    try:
        (inspect if args.action == 'inspect' else patch)(args)
    except Exception as e:
        print('ERROR:', e, file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    main()
