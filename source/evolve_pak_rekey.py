#!/usr/bin/env python3
"""Experimental Evolve Stage 2 RSA-PSS/Twofish-CTR archive re-keying.

Supports the observed 2320-byte CryPak encrypted+signed ZIP comment format.
Does not rebuild PAK entries or alter the original file; outputs in another dir.
Requires Python 3.11, cryptography, twofish; depends on included prior parser.
This is third-party game mod research; use on copies of your own installation.
"""
import argparse
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import struct
import sys
import zlib

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.serialization import (
    Encoding, PrivateFormat, PublicFormat, NoEncryption,
    load_der_public_key, load_pem_private_key,
)

from evolve_video_pak_tool_fixed import get_eocd_tail, load_archive, unwrap_crypto_block

COMMENT_SIZE = 2320
SIG_START, SIG_END = 11, 139
WRAP_START = 139
RSA_SIZE = 128


def mgf1(seed: bytes, length: int) -> bytes:
    return b''.join(hashlib.sha256(seed + i.to_bytes(4, 'big')).digest()
                    for i in range((length + 31) // 32))[:length]


def rsa_private_oaep_wrap(secret: bytes, priv) -> bytes:
    """Reverse the observed CryPak RSA operation.

    The original archive unwraps the OAEP block with public exponent `e`,
    therefore producing a replacement ciphertext uses private exponent `d`.
    This is *not* standard RSA public-key OAEP encryption.
    """
    if len(secret) != 16:
        raise ValueError('expected 16-byte Twofish key / IV')
    k = (priv.key_size + 7) // 8
    hlen = 32
    if k != 128:
        raise ValueError('observed PAK format requires RSA-1024 (128-byte blocks)')
    db_len = k - 1 - hlen
    ps = bytes(db_len - hlen - 1 - len(secret))
    db = hashlib.sha256(b'').digest() + ps + b'\x01' + secret
    seed = secrets.token_bytes(hlen)
    masked_db = bytes(x ^ y for x, y in zip(db, mgf1(seed, db_len)))
    masked_seed = bytes(x ^ y for x, y in zip(seed, mgf1(masked_db, hlen)))
    encoded = b'\x00' + masked_seed + masked_db
    n = priv.private_numbers().public_numbers.n
    d = priv.private_numbers().d
    wrapped = pow(int.from_bytes(encoded, 'big'), d, n).to_bytes(k, 'big')
    assert unwrap_crypto_block(wrapped, priv.public_key()) == secret
    return wrapped


def ensure_new(path: Path):
    if path.exists():
        raise FileExistsError(f'Refusing to overwrite: {path}')
    if not path.parent.is_dir():
        raise FileNotFoundError(f'Output folder does not exist: {path.parent}')


def cmd_keygen(args):
    folder = args.output_dir.resolve()
    folder.mkdir(parents=True, exist_ok=True)
    private_path = folder / 'private_key.pem'
    public_path = folder / 'public_key.bin'
    for p in [private_path, public_path]:
        ensure_new(p)
    priv = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    priv_bytes = priv.private_bytes(Encoding.PEM, PrivateFormat.TraditionalOpenSSL, NoEncryption())
    pub_bytes = priv.public_key().public_bytes(Encoding.DER, PublicFormat.PKCS1)
    assert len(pub_bytes) == 140, len(pub_bytes)
    try:
        with private_path.open('xb') as f:
            f.write(priv_bytes)
        # Unix permissions if available; on Windows secure your files manually.
        if os.name != 'nt':
            os.chmod(private_path, 0o600)
        with public_path.open('xb') as f:
            f.write(pub_bytes)
    except Exception:
        # Do not leave a half-written private/public pair behind.
        for p in [private_path, public_path]:
            if p.exists():
                p.unlink()
        raise
    print(f'Private key (KEEP PRIVATE): {private_path}')
    print(f'Public key  (PKCS#1 DER): {public_path}')
    print('Public key SHA-256:', hashlib.sha256(pub_bytes).hexdigest())
    print('Generated a LOCAL research key, not a production-safe RSA key size.')


def read_keys_from_comment(comment, original_pub):
    # Fixed header positions confirmed from the original signed videos.pak tail.
    enc = comment[WRAP_START:]
    blocks = [enc[4:4+RSA_SIZE]]
    blocks.extend(enc[133+i*RSA_SIZE:133+(i+1)*RSA_SIZE] for i in range(16))
    if len(blocks) != 17 or any(len(x) != RSA_SIZE for x in blocks):
        raise ValueError('invalid wrapped-key layout')
    return [unwrap_crypto_block(block, original_pub) for block in blocks]


def prepare_rekey_comment(comment: bytes, new_private, plain_cdr: bytes, basename: str, original_pub):
    if len(comment) != COMMENT_SIZE or comment[:6] != b'\x06\x00\x00\x00\x01\x03':
        raise ValueError('unsupported PAK trailer format')
    key_secrets = read_keys_from_comment(comment, original_pub)
    changed = bytearray(comment)
    signature = new_private.sign(
        plain_cdr + basename.encode('ascii'),
        padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=0),
        hashes.SHA256(),
    )
    if len(signature) != RSA_SIZE:
        raise AssertionError('wrong signature size')
    changed[SIG_START:SIG_END] = signature
    changed[143:271] = rsa_private_oaep_wrap(key_secrets[0], new_private)
    for i, secret in enumerate(key_secrets[1:]):
        a = 272 + i * RSA_SIZE
        changed[a:a+RSA_SIZE] = rsa_private_oaep_wrap(secret, new_private)
    if len(changed) != COMMENT_SIZE:
        raise AssertionError('comment size changed')
    # Verify each re-wrapped secret before touching an archive.
    new_pub = new_private.public_key()
    if read_keys_from_comment(changed, new_pub) != key_secrets:
        raise AssertionError('key rewrapping self-check failed')
    new_pub.verify(signature, plain_cdr + basename.encode('ascii'),
                   padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=0), hashes.SHA256())
    return bytes(changed)


def logical_pak_basename(path: Path) -> str:
    """Use the signed PAK filename when reading a preserved .pak.modbackup copy.

    The .modbackup suffix belongs to the local backup, not to CryPak's
    basename authenticated by RSA-PSS. Do not strip arbitrary extensions.
    """
    name = path.name
    if name.lower().endswith('.pak.modbackup'):
        name = name[:-len('.modbackup')]
    return name


def load_with_signed_basename(path: Path, key: Path):
    """Resolve the actual signed basename, including a .pak.modbackup source."""
    name = logical_pak_basename(path)
    variants = list(dict.fromkeys((name, name.lower())))
    last_error = None
    for name in variants:
        try:
            return load_archive(path, str(key), basename=name), name
        except InvalidSignature as error:
            last_error = error
    raise ValueError(f'Invalid signature with filename variants: {variants}') from last_error


def extract_plain_cdr(original: Path, original_key: Path):
    """Use verified analyzer and discover signed archive basename."""
    import evolve_video_pak_tool_fixed as cry
    parsed, signed_basename = load_with_signed_basename(original, original_key)
    with original.open('rb') as f:
        size, _, comment = get_eocd_tail(f)
        cdpos = parsed['cdr_at']
        f.seek(cdpos)
        encrypted_cdr = f.read(parsed['cdr_size'])
    plain = cry.twofish_ctr(encrypted_cdr, parsed['keys'][0], parsed['iv'])
    return parsed, comment, plain, signed_basename


def cmd_inspect(args):
    pak = args.pak.resolve()
    parsed, signed_basename = load_with_signed_basename(pak, args.public_key)
    print(f'PASS: {pak.name} (signed as {signed_basename}): {parsed["count"]} signed+encrypted entries; original signature valid')
    print('CDR size:', parsed['cdr_size'], 'comment size:', parsed['eocd_comment_size'])


def cmd_resign(args):
    src = args.original.resolve()
    dst = args.output.resolve()
    if src == dst:
        raise ValueError('Source and destination must differ')
    if src.name != dst.name:
        raise ValueError('Output must retain original archive basename (signature covers filename)')
    ensure_new(dst)
    priv = load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(priv, rsa.RSAPrivateKey) or priv.key_size != 1024:
        raise ValueError('requires a 1024-bit RSA PEM private key')
    original_pub = load_der_public_key(args.original_public_key.read_bytes())
    parsed, original_comment, plain, signed_basename = extract_plain_cdr(src, args.original_public_key)
    new_comment = prepare_rekey_comment(original_comment, priv, plain, signed_basename, original_pub)
    print(f'Original signature verified. {parsed["count"]} entries; CDR {len(plain)} bytes.')
    print('Rewrapped 17 RSA-OAEP Twofish secrets and generated fresh RSA-PSS signature.')
    try:
        # Streaming copy works for the 1GB+ archives without loading them into RAM.
        with src.open('rb') as infile, dst.open('xb') as outfile:
            shutil.copyfileobj(infile, outfile, length=2 * 1024 * 1024)
        with dst.open('r+b') as fout:
            fout.seek(-COMMENT_SIZE, os.SEEK_END)
            assert fout.read(COMMENT_SIZE) == original_comment
            fout.seek(-COMMENT_SIZE, os.SEEK_END)
            fout.write(new_comment)
        # Full re-parse, decrypt and verify using the fresh public key.
        new_pub_path = args.public_key.resolve()
        if new_pub_path.read_bytes() != priv.public_key().public_bytes(Encoding.DER, PublicFormat.PKCS1):
            raise ValueError('The provided new public key does not match private key')
        result = load_archive(dst, str(new_pub_path), basename=signed_basename)
        if result['count'] != parsed['count']:
            raise AssertionError('entry count changed')
        print(f'PASS: new signature verified with new public key; decrypted {result["count"]} entries.')
        print('RESULT:', dst)
        print('NOTE: File payloads and central directory are unchanged. This does NOT add a larger video.')
    except Exception:
        if dst.exists():
            dst.unlink()
        raise



def find_cdr_record(plain_cdr: bytes, filename: str):
    """Return (record start, 46-byte header tuple) for matching filename."""
    pos = 0
    while pos + 46 <= len(plain_cdr):
        fields = struct.unpack_from('<4s6H3I5H2I', plain_cdr, pos)
        if fields[0] != b'PK\x01\x02':
            raise ValueError('invalid central directory record')
        nlen, elen, clen = fields[10:13]
        name = plain_cdr[pos+46:pos+46+nlen].decode('utf-8')
        if name == filename:
            return pos, fields
        pos += 46 + nlen + elen + clen
    raise ValueError(f'Entry not found in signed CDR: {filename}')


def cmd_replace_video(args):
    """Append an enlarged replacement entry and rebuild just its CDR record.

    EXPERIMENTAL. Evolve's in-game acceptance has not been tested.
    The original video bytes remain as unused archive space, while the CDR
    points the same filename at a new appended encrypted copy.
    """
    import evolve_video_pak_tool_fixed as cry
    original = args.original.resolve()
    dest = args.output.resolve()
    if original == dest or logical_pak_basename(original) != dest.name:
        raise ValueError('Input and output must be distinct; source may have .modbackup suffix but signed PAK basename must match output')
    ensure_new(dest)
    video = args.video.resolve()
    size = video.stat().st_size
    if size >= 2**32:
        raise ValueError('new file is too large for ZIP32')
    with video.open('rb') as f:
        if f.read(4) != b'CRID':
            raise ValueError('replacement video does not begin with CRID; this is not a recognized CRI USM')
    priv = load_pem_private_key(args.private_key.read_bytes(), password=None)
    if not isinstance(priv, rsa.RSAPrivateKey) or priv.key_size != 1024:
        raise ValueError('expected 1024-bit RSA private key')
    expected_public = priv.public_key().public_bytes(Encoding.DER, PublicFormat.PKCS1)
    if expected_public != args.public_key.read_bytes():
        raise ValueError('new public/private keys do not match')
    old_pub = load_der_public_key(args.original_public_key.read_bytes())
    info, old_comment, plain_cdr, signed_basename = extract_plain_cdr(original, args.original_public_key)
    pos, fields = find_cdr_record(plain_cdr, args.entry)
    entry = info['entries'][args.entry]
    if entry['method'] != 13 or entry['extra_length'] != 0:
        raise ValueError('only method 13 with zero entry extra bytes supported')
    name_bytes = args.entry.encode('utf-8')
    if len(name_bytes) != entry['name_length']:
        raise ValueError('CDR name-length mismatch')
    orig_end = info['cdr_at']
    if orig_end >= 2**32:
        raise ValueError('archive exceeds ZIP32 limit')
    print('Original PAK verified:', info['count'], 'entries; original CDR at', orig_end)
    print('Replacement USM:', size, 'bytes, old allocated slot:', entry['compressed_size'])
    print('Computing CRC32 and encrypting replacement with original Twofish keys...')
    with video.open('rb') as src:
        plain_video = src.read()
    crc = zlib.crc32(plain_video) & 0xffffffff
    modified = dict(entry)
    modified.update(crc=crc, original_size=size, compressed_size=size,
                    local_offset=orig_end)
    key_index = (~(crc >> 2)) & 0xf
    cipher = cry.twofish_ctr(plain_video, info['keys'][key_index], cry.file_iv(modified))
    del plain_video

    # Build standard 30-byte ZIP local header; the encrypted-header CryPak path
    # is expected to derive location from signed CDR without reading this header.
    # Whether Evolve accepts this mix is an open in-game question.
    version_needed, flags, method, timestamp, date = fields[2:7]
    header = struct.pack('<4s5H3I2H', b'PK\x03\x04', version_needed,
                         flags, method, timestamp, date, crc, size, size,
                         len(name_bytes), 0)
    assert len(header) == 30
    new_directory = bytearray(plain_cdr)
    struct.pack_into('<III', new_directory, pos + 16, crc, size, size)
    struct.pack_into('<I', new_directory, pos + 42, orig_end)
    cdr_cipher = cry.twofish_ctr(bytes(new_directory), info['keys'][0], info['iv'])
    new_cdr_pos = orig_end + len(header) + len(name_bytes) + size
    if new_cdr_pos + len(cdr_cipher) >= 2**32:
        raise ValueError('new central directory too far for ZIP32')
    new_comment = prepare_rekey_comment(old_comment, priv, bytes(new_directory),
                                        signed_basename, old_pub)
    with original.open('rb') as f:
        f.seek(-COMMENT_SIZE-22, os.SEEK_END)
        eocd = bytearray(f.read(22))
    if len(eocd) != 22 or eocd[:4] != b'PK\x05\x06':
        raise ValueError('EOCD not in expected final position')
    struct.pack_into('<I', eocd, 16, new_cdr_pos)
    print('New signed directory position:', new_cdr_pos)
    print('Output archive will be larger, and original video bytes stay unused.')
    try:
        with original.open('rb') as infile, dest.open('xb') as outfile:
            remaining = orig_end
            while remaining:
                block = infile.read(min(2 * 1024 * 1024, remaining))
                if not block:
                    raise EOFError('source PAK truncated')
                outfile.write(block)
                remaining -= len(block)
            outfile.write(header)
            outfile.write(name_bytes)
            outfile.write(cipher)
            outfile.write(cdr_cipher)
            outfile.write(eocd)
            outfile.write(new_comment)
        result = load_archive(dest, str(args.public_key), basename=signed_basename)
        if result['entries'][args.entry]['compressed_size'] != size or result['entries'][args.entry]['crc'] != crc:
            raise AssertionError('reparsed entry not correct')
        with dest.open('rb') as f:
            f.seek(orig_end + 30 + len(name_bytes))
            enc = f.read(size)
        dec = cry.twofish_ctr(enc, result['keys'][key_index], cry.file_iv(modified))
        if dec != video.read_bytes():
            raise AssertionError('encrypted payload round-trip mismatch')
        print('PASS: RSA-PSS verified under new public key, CDR re-decrypts, and replacement USM round-trips.')
        print('Output:', dest)
        print('EXPERIMENTAL: Retail Evolve PAK mounting and USM playback NOT verified.')
    except Exception:
        dest.unlink(missing_ok=True)
        raise


def cmd_patch_shim(args):
    """Offline, guarded public-key swap in an existing *user-supplied* shim.

    Refuses to patch an offset without the exact original 140-byte DER key.
    Does not modify the input shim.
    """
    old = args.original_public_key.read_bytes()
    new = args.public_key.read_bytes()
    if len(old) != 140 or len(new) != 140:
        raise ValueError('Expected equal 140-byte PKCS#1 DER public keys')
    for value in [old, new]:
        pub = load_der_public_key(value)
        if pub.key_size != 1024 or pub.public_bytes(Encoding.DER, PublicFormat.PKCS1) != value:
            raise ValueError('not a canonical RSA-1024 PKCS#1 public key')
    ensure_new(args.output)
    data = args.shim.read_bytes()
    offset = int(args.offset, 0)
    if offset < 0 or offset + len(old) > len(data):
        raise ValueError('offset outside shim file')
    current = data[offset:offset+len(old)]
    if current != old:
        raise ValueError('Public key at requested offset does not match original key. Refusing to patch.')
    patched = data[:offset] + new + data[offset+len(old):]
    args.output.write_bytes(patched)
    print('Patched ONLY the verified public-key bytes in a COPY of the provided shim.')
    print('Output:', args.output)
    print('This does not establish whether the shim works in Evolve; test separately.')


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    subs = parser.add_subparsers(dest='command', required=True)
    p = subs.add_parser('keygen', help='generate 1024-bit test RSA keys locally')
    p.add_argument('output_dir', type=Path)
    p.set_defaults(func=cmd_keygen)
    p = subs.add_parser('inspect', help='verify existing encrypted+signed PAK')
    p.add_argument('pak', type=Path)
    p.add_argument('--public-key', type=Path, required=True)
    p.set_defaults(func=cmd_inspect)
    p = subs.add_parser('resign', help='re-sign/re-wrap existing signed+encrypted PAK to a new key')
    p.add_argument('original', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--original-public-key', type=Path, required=True)
    p.add_argument('--private-key', type=Path, required=True)
    p.add_argument('--public-key', type=Path, required=True)
    p.set_defaults(func=cmd_resign)
    p = subs.add_parser('replace-video', help='EXPERIMENTAL: append larger encrypted USM and re-sign modified archive')
    p.add_argument('original', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--video', type=Path, required=True)
    p.add_argument('--entry', default='Libs/UI/flashassets/videos/main_menu.usm')
    p.add_argument('--original-public-key', type=Path, required=True)
    p.add_argument('--private-key', type=Path, required=True)
    p.add_argument('--public-key', type=Path, required=True)
    p.set_defaults(func=cmd_replace_video)
    p = subs.add_parser('patch-shim' , help='replace known 140-byte public key at a verified file offset in a copy of a shim')
    p.add_argument('shim', type=Path)
    p.add_argument('output', type=Path)
    p.add_argument('--original-public-key', type=Path, required=True)
    p.add_argument('--public-key', type=Path, required=True)
    p.add_argument('--offset', default='0x870')
    p.set_defaults(func=cmd_patch_shim)
    args = parser.parse_args()
    try:
        args.func(args)
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
