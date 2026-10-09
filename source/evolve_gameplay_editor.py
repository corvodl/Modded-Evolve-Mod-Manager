#!/usr/bin/env python3
"""CryXmlB editor for encrypted/signed Evolve Stage 2 libs.pak.

Reads + verifies input against a supplied RSA PKCS#1 public key, decodes the
chosen CryXmlB entry, applies an XML attribute edit or a replacement XML,
appends an encrypted method-13 entry to a *new* PAK, regenerates/re-signs the
CDR with the same private key, and verifies readback. Never writes to source.
For offline/private testing only; no effective gameplay balance is guaranteed.
"""
import argparse
import hashlib
import os
from pathlib import Path
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat, load_pem_private_key
from cryptography.hazmat.primitives.asymmetric import rsa
from evolve_pak_rekey import extract_plain_cdr, find_cdr_record, prepare_rekey_comment
from evolve_video_pak_tool_fixed import load_archive, local_data_start, twofish_ctr, file_iv, get_eocd_tail


def strip_pretty_indentation(node):
    """Remove indentation introduced by pretty XML rendering, not nonblank text."""
    for e in node.iter():
        if e.text is not None and not e.text.strip():
            e.text = None
        if e.tail is not None and not e.tail.strip():
            e.tail = None


def encode_cryxml(root):
    """CryXmlB with node/attribute/child/string tables (CryEngine 3 layout)."""
    nodes = []
    parents = []
    def walk(e, parent):
        index = len(nodes)
        nodes.append(e)
        parents.append(parent)
        for child in e:
            walk(child, index)
    walk(root, 0xffffffff)
    if len(nodes) >= (1 << 32):
        raise ValueError('too many XML nodes')
    ids = {id(e): i for i, e in enumerate(nodes)}
    strings = bytearray(b'\0')
    interned = {'': 0}
    def add(s):
        if not isinstance(s, str):
            raise TypeError('all strings must be text')
        if '\0' in s:
            raise ValueError('embedded NUL string cannot be encoded')
        if s not in interned:
            val = s.encode('utf-8')
            interned[s] = len(strings)
            strings.extend(val + b'\0')
        return interned[s]
    node_data = bytearray()
    attr_data = bytearray()
    child_data = bytearray()
    count_attr = 0
    count_child = 0
    for i, e in enumerate(nodes):
        tag = add(e.tag)
        content = add(e.text or '')
        attrs = list(e.attrib.items())
        if len(attrs) >= (1 << 16) or len(e) >= (1 << 16):
            raise ValueError('too many attributes/children in single element')
        attr_at = count_attr
        for key, value in attrs:
            attr_data.extend(struct.pack('<II', add(key), add(value)))
            count_attr += 1
        child_at = count_child
        for child in e:
            child_data.extend(struct.pack('<I', ids[id(child)]))
            count_child += 1
        node_data.extend(struct.pack('<IIHHIIII', tag, content, len(attrs), len(e), parents[i], attr_at, child_at, 0))
    header_size = 44
    nodes_at = header_size
    attrs_at = nodes_at + len(node_data)
    children_at = attrs_at + len(attr_data)
    strings_at = children_at + len(child_data)
    total = strings_at + len(strings)
    if total >= (1 << 32):
        raise ValueError('CryXmlB file too large')
    hdr = b'CryXmlB\0' + struct.pack('<9I', total, nodes_at, len(nodes), attrs_at, count_attr,
                                     children_at, count_child, strings_at, len(strings))
    result = hdr + node_data + attr_data + child_data + strings
    assert len(result) == total
    return result


def decode_cryxml(data):
    from decode_cryxml import decode
    if not data.startswith(b'CryXmlB\0'):
        raise ValueError('Entry is not CryXmlB; refuses to modify non-CryXmlB payload')
    return decode(data)


def semantic(e):
    return (e.tag, e.text or '', tuple(e.attrib.items()), tuple(semantic(x) for x in e))


def parse_cryxml_text(xml_bytes):
    """Read editable XML without expanding qualified names or xmlns attributes.

    CryXMLB stores raw tag names and explicit xmlns declarations as normal XML
    attributes. ElementTree.fromstring()/parse() expand names into {URI}tags,
    dropping those attributes. That silently changes CryXMLB data for assets
    such as libs/DynamicSpeech/Source/BanterScript.xml (Excel-style XML).

    Expat with namespace processing *disabled* preserves exact qualified names,
    prefix spelling, namespace declaration attributes and attribute order.
    """
    from xml.parsers import expat
    parser = expat.ParserCreate()   # No namespace separator => preserve names.
    builder = ET.TreeBuilder()
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    parser.Parse(xml_bytes, True)
    return builder.close()


def guarded_serialize(xml_root):
    strip_pretty_indentation(xml_root)
    out = encode_cryxml(xml_root)
    if semantic(decode_cryxml(out)) != semantic(xml_root):
        raise ValueError('CryXmlB encode/decode semantic roundtrip mismatch')
    return out


def key_index(entry):
    return (~(entry['crc'] >> 2)) & 15


def pick_entry(entries, name):
    needle = name.replace('\\','/').casefold()
    found = [k for k in entries if k.replace('\\','/').casefold() == needle]
    if len(found) != 1:
        raise ValueError(f'Expected one entry {name!r}, found {len(found)}')
    return found[0]


def decrypt_entry(pak, info, entryname):
    meta = info['entries'][entryname]
    if meta['method'] not in (13, 14) or meta['extra_length'] != 0:
        raise ValueError(f'Entry method {meta["method"]}, extra bytes {meta["extra_length"]}; only encrypted method 13 (stored) and 14 (deflate) supported')
    if meta['method'] == 13 and meta['compressed_size'] != meta['original_size']:
        raise ValueError('Unexpected stored/original size mismatch in method 13')
    with pak.open('rb') as f:
        at = local_data_start(f, meta, info['cdr_at'])
        f.seek(at)
        cipher = f.read(meta['compressed_size'])
    if len(cipher) != meta['compressed_size']:
        raise EOFError('incomplete encrypted entry data')
    dec = twofish_ctr(cipher, info['keys'][key_index(meta)], file_iv(meta))
    if meta['method'] == 14:
        dec = zlib.decompress(dec, -15)
    if len(dec) != meta['original_size']:
        raise ValueError('Decompressed size does not match signed directory')
    if (zlib.crc32(dec) & 0xffffffff) != meta['crc']:
        raise ValueError('Entry decryption CRC32 FAILED; refusing edit')
    return dec


def get_parsed(pak, public):
    info, comment, cdr, signed_name = extract_plain_cdr(pak, public)
    return info, comment, cdr, signed_name


def find_nodes(root, xpath):
    if xpath in ('.','/',''):
        return [root]
    return root.findall(xpath)


def show_tree(root, filter_text, limit):
    needle = filter_text.casefold()
    index = 0
    for e in root.iter():
        if not e.attrib:
            continue
        name = e.attrib.get('name', '')
        value = e.attrib.get('value', '')
        description = f'{e.tag} name={name!r} value={value!r}'
        if needle and needle not in description.casefold():
            continue
        print(description)
        index += 1
        if index >= limit:
            print(f'(Limited to {limit} lines)')
            return
    if not index:
        print('No matching attributes. Try --filter with another term, or export XML.')


def cmd_list(args):
    info, *_ = get_parsed(args.pak, args.public_key)
    entries = [e for e in info['entries'] if args.filter.lower() in e.lower()]
    for path in entries[:args.limit]:
        m = info['entries'][path]
        print(f'{path}  (method={m["method"]}, bytes={m["original_size"]})')
    print(f'Matched {len(entries)} archive entries; listing {min(len(entries), args.limit)}')


def cmd_show(args):
    info, *_ = get_parsed(args.pak, args.public_key)
    name = pick_entry(info['entries'],args.entry)
    root = decode_cryxml(decrypt_entry(args.pak, info, name))
    print('Entry:', name)
    show_tree(root,args.filter,args.limit)
    if args.export:
        if args.export.exists():
            raise FileExistsError('Refusing to overwrite exported XML')
        args.export.parent.mkdir(parents=True,exist_ok=True)
        ET.indent(root)
        args.export.write_bytes(ET.tostring(root,encoding='utf-8',xml_declaration=True))
        print('Exported:', args.export)


def edit_xml(args, current):
    old_root = decode_cryxml(current)
    if args.xml:
        new_root = parse_cryxml_text(args.xml.read_bytes())
        if new_root.tag != old_root.tag:
            raise ValueError(f'Root tag mismatch: {old_root.tag} vs {new_root.tag}')
        print('Replacing with edited XML:', args.xml)
    else:
        new_root = old_root
        targets = find_nodes(new_root,args.xpath)
        if len(targets) != 1:
            raise ValueError(f'XPath found {len(targets)} nodes (requires exactly one): {args.xpath}')
        node = targets[0]
        if args.attribute not in node.attrib:
            raise ValueError(f'Attribute {args.attribute!r} not present on matched element')
        before = node.attrib[args.attribute]
        node.attrib[args.attribute] = args.value
        print(f'Editing {args.xpath} attribute {args.attribute}: {before!r} -> {args.value!r}')
    from cryxml_preserve import preserve_values
    encoded = preserve_values(current, new_root)
    return encoded


def build_new_pak(src, dst, pubkey, privkey, requested_entry, replacement):
    if not src.is_file():
        raise FileNotFoundError(f'Source PAK missing: {src}')
    if src.resolve() == dst.resolve():
        raise ValueError('Output and input must be different files')
    if dst.exists():
        raise FileExistsError(f'Refusing to overwrite output: {dst}')
    if src.name.casefold() != dst.name.casefold():
        raise ValueError('Output basename must be the same as original signed PAK name; use separate directories')
    if not dst.parent.is_dir():
        raise FileNotFoundError(f'Create output folder first: {dst.parent}')
    info, comment, old_cdr, signed_name = get_parsed(src,pubkey)
    name = pick_entry(info['entries'],requested_entry)
    entry = info['entries'][name]
    if entry['method'] not in (13, 14) or entry['extra_length'] != 0:
        raise ValueError('Replacement currently supports encrypted method 13/14 entries with zero extra bytes')
    if entry['method'] == 13 and entry['compressed_size'] != entry['original_size']:
        raise ValueError('Unexpected method-13 size mismatch')
    priv = load_pem_private_key(privkey.read_bytes(), password=None)
    if not isinstance(priv,rsa.RSAPrivateKey) or priv.key_size != 1024:
        raise ValueError('Expected RSA-1024 private key for this archive version')
    supplied_pub = pubkey.read_bytes()
    if priv.public_key().public_bytes(Encoding.DER, PublicFormat.PKCS1) != supplied_pub:
        raise ValueError('Public key does not match private signing key')
    newsize = len(replacement)
    if newsize >= 2**32:
        raise ValueError('Replacement entry exceeds ZIP32')
    new_crc = zlib.crc32(replacement) & 0xffffffff
    if decrypt_entry(src,info,name) == replacement:
        raise ValueError('Replacement is identical to existing entry; no change required')
    pos,fields = find_cdr_record(old_cdr,name)
    localpos = info['cdr_at']
    name_bytes = name.encode('utf-8')
    if len(name_bytes) != entry['name_length']:
        raise ValueError('CDR name byte length mismatch')
    if entry['method'] == 14:
        compressor = zlib.compressobj(level=9, wbits=-15)
        compressed = compressor.compress(replacement) + compressor.flush()
    else:
        compressed = replacement
    packed_size = len(compressed)
    if packed_size >= 2**32:
        raise ValueError('Compressed entry exceeds ZIP32')
    meta = dict(entry, crc=new_crc,compressed_size=packed_size,original_size=newsize,local_offset=localpos)
    cipher = twofish_ctr(compressed,info['keys'][key_index(meta)],file_iv(meta))
    vneed,flags,method,tm,dt=fields[2:7]
    loc_hdr = struct.pack('<4s5H3I2H', b'PK\x03\x04',vneed, flags, method,tm,dt, new_crc,packed_size,newsize,len(name_bytes),0)
    new_cdr = bytearray(old_cdr)
    struct.pack_into('<III',new_cdr,pos+16,new_crc,packed_size,newsize)
    struct.pack_into('<I',new_cdr,pos+42,localpos)
    encrypted_cdr = twofish_ctr(bytes(new_cdr),info['keys'][0],info['iv'])
    new_cdr_pos = localpos + len(loc_hdr) + len(name_bytes) + len(cipher)
    if new_cdr_pos + len(encrypted_cdr) >= 2**32:
        raise ValueError('Archive too large for ZIP32')
    from cryptography.hazmat.primitives.serialization import load_der_public_key
    current_pub = load_der_public_key(supplied_pub)
    updated_comment = prepare_rekey_comment(comment,priv,bytes(new_cdr),signed_name,current_pub)
    with src.open('rb') as f:
        _,eocd_data, _ = get_eocd_tail(f)
        f.seek(-2342,os.SEEK_END)
        eocd = bytearray(f.read(22))
    if len(eocd)!=22 or eocd[:4]!=b'PK\x05\x06':
        raise ValueError('Expected EOCD immediately before signed comment')
    struct.pack_into('<I', eocd, 16, new_cdr_pos)
    try:
        with src.open('rb') as infile, dst.open('xb') as outfile:
            remain = info['cdr_at']
            while remain:
                block = infile.read(min(2*1024*1024,remain))
                if not block:
                    raise EOFError('Original archive truncated')
                outfile.write(block)
                remain -= len(block)
            outfile.write(loc_hdr)
            outfile.write(name_bytes)
            outfile.write(cipher)
            outfile.write(encrypted_cdr)
            outfile.write(eocd)
            outfile.write(updated_comment)
        reloaded, *_ = get_parsed(dst,pubkey)
        if reloaded['count'] != info['count'] or reloaded['entries'][name]['crc'] != new_crc:
            raise AssertionError('PAK reparse failed')
        if decrypt_entry(dst,reloaded,name) != replacement:
            raise AssertionError('Replacement decryption/CRC or CryPak offset validation failed')
        if not decrypt_entry(dst,reloaded,name).startswith(b'CryXmlB\0'):
            raise AssertionError('Replaced payload lacks CryXmlB header')
        print('PASS: New PAK signature verified, entry CRC32 correct, binary XML round-trip confirmed.')
        print('Output:', dst)
        print('Original unchanged:', src)
        print('CHANGED ENTRY:', name)
        print('NOTE: New PAK is custom-key signed; game behavior requires a private/offline test.')
    except Exception:
        dst.unlink(missing_ok=True)
        raise


def cmd_build(args):
    info, *_ = get_parsed(args.pak,args.public_key)
    realname = pick_entry(info['entries'],args.entry)
    original = decrypt_entry(args.pak,info,realname)
    replaced = edit_xml(args,original)
    print('CryXmlB byte size:',len(original),'->',len(replaced))
    build_new_pak(args.pak,args.out,args.public_key,args.private_key,realname,replaced)


def cli():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=p.add_subparsers(dest='cmd',required=True)
    for name in ('list','show','build'):
        s=sub.add_parser(name)
        s.add_argument('--pak',type=Path,required=True,help='Custom-signed staging Game/libs.pak')
        s.add_argument('--public-key',type=Path,required=True,help='Existing staged mykeys/public_key.bin')
        if name!='list':
            s.add_argument('--entry',required=True,help='Exact archive path, e.g. libs/Items/Mercs/Torvald/BurstShotgunAmmo.xml')
        if name in ('list','show'):
            s.add_argument('--filter',default='')
            s.add_argument('--limit',type=int,default=50)
        if name=='show':
            s.add_argument('--export',type=Path,help='Output readable XML file (new path only)')
        if name=='build':
            s.add_argument('--private-key',type=Path,required=True)
            s.add_argument('--out',type=Path,required=True)
            selector=s.add_mutually_exclusive_group(required=True)
            selector.add_argument('--xpath',help='XPath identifying exactly one XML element')
            selector.add_argument('--xml',type=Path,help='Rebuild from edited readable XML')
            s.add_argument('--attribute',default='value')
            s.add_argument('--value',help='New attribute value (use with --xpath)')
        s.set_defaults(func={'list':cmd_list,'show':cmd_show,'build':cmd_build}[name])
    args=p.parse_args()
    if args.cmd=='build' and args.xpath is not None and args.value is None:
        p.error('--xpath requires --value')
    try:
        args.func(args)
    except Exception as ex:
        print(f'ERROR: {type(ex).__name__}: {ex}',file=sys.stderr)
        sys.exit(2)

if __name__=='__main__':
    cli()
