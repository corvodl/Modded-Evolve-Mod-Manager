#!/usr/bin/env python3
"""Edit Evolve Stage 2 custom-signed PAKs by exporting a workspace.

Only changed files are repacked; the *whole signed CDR* is rebuilt and RSA-PSS
signed once with the current private key. Method 13/14 Twofish CryPak entries.
No installed game files are modified by this program.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import sys
import xml.etree.ElementTree as ET
import zlib

from cryptography.hazmat.primitives.serialization import (Encoding, PublicFormat, load_pem_private_key, load_der_public_key)
from cryptography.hazmat.primitives.asymmetric import rsa
from evolve_pak_rekey import extract_plain_cdr, find_cdr_record, prepare_rekey_comment
from evolve_video_pak_tool_fixed import twofish_ctr, file_iv
from evolve_gameplay_editor import decrypt_entry, encode_cryxml, decode_cryxml, guarded_serialize, pick_entry, parse_cryxml_text

VERSION = 1
MANIFEST = '.evolve-pak-workspace.json'
WINDOWS_INVALID = re.compile(r'[<>:"|?*]')


def sha_bytes(b): return hashlib.sha256(b).hexdigest()


def sha_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(2*1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def normalized_name(name):
    # Archive uses Windows backslashes; workspace uses forward slashes.
    value=name.replace('\\','/')
    p=PurePosixPath(value)
    parts=p.parts
    if not value or value.startswith('/') or re.match('^[A-Za-z]:',value) or '\0' in value:
        raise ValueError(f'Unsafe archive entry name: {name!r}')
    if not parts or any(s in ('', '.', '..') for s in parts) or any(s.endswith(('.', ' ')) for s in parts):
        raise ValueError(f'Unsafe archive entry components: {name!r}')
    if any(WINDOWS_INVALID.search(s) or s.upper().split('.')[0] in {'CON','PRN','AUX','NUL','COM1','COM2','COM3','COM4','COM5','COM6','COM7','COM8','COM9','LPT1','LPT2','LPT3','LPT4','LPT5','LPT6','LPT7','LPT8','LPT9'} for s in parts):
        raise ValueError(f'Entry cannot be safely extracted on Windows: {name!r}')
    return '/'.join(parts)


def check_paths(wdir, records):
    seen=set()
    for rec in records:
        rel=normalized_name(rec['path'])
        if rel.casefold() in seen: raise ValueError(f'Duplicate case-insensitive path: {rel}')
        seen.add(rel.casefold())
        if rec['path']!=rel: raise ValueError('Invalid manifest normalized path')
        dest=(wdir/'files'/Path(*rel.split('/'))).resolve()
        if not dest.is_relative_to((wdir/'files').resolve()):
            raise ValueError(f'Unsafe workspace path: {rel}')
        yield rec, dest


def manifest_path(wdir):return wdir/MANIFEST


def load_workspace(wdir):
    data=json.loads(manifest_path(wdir).read_text(encoding='utf-8'))
    if data.get('version')!=VERSION:raise ValueError('Unsupported workspace manifest version')
    if not isinstance(data.get('entries'),list) or not data['entries']:
        raise ValueError('Missing workspace entries')
    list(check_paths(wdir,data['entries']))
    return data


def optional_filter(name, patterns):
    if not patterns:return True
    needle=name.casefold()
    return any(x.casefold() in needle for x in patterns)


def cmd_extract(args):
    src=args.pak.resolve();pub=args.public_key.resolve();workspace=args.workspace.resolve()
    if workspace.exists():raise FileExistsError(f'Workspace already exists: {workspace}; use a fresh folder to avoid erasing edits')
    source_hash_at_start = sha_file(src)
    info,_,_,signed_name=extract_plain_cdr(src,pub)
    if not info['entries']:raise ValueError('Empty PAK')
    records=[]; selected=0; other=0
    # Export may fail on a single malformed/unsupported CryXMLB entry.
    # Never clean up the partial workspace automatically: it may contain
    # valuable diagnostic output. The source PAK is read-only throughout.
    if src.is_relative_to(workspace) or workspace.is_relative_to(src):
        raise ValueError('Workspace and source archive paths must be separate')
    workspace.mkdir(parents=True)
    (workspace / '.EXTRACTION-INCOMPLETE.txt').write_text(
        'Extraction did not complete yet. Do not use this workspace to build a PAK.\n',
        encoding='utf-8')
    try:
        seen=set()
        for idx,(name,meta) in enumerate(info['entries'].items(),1):
            rel=normalized_name(name)
            if rel.casefold() in seen:raise ValueError(f'Case-insensitive duplicate archive path: {rel}')
            seen.add(rel.casefold())
            if meta['method'] not in (13,14) or meta['extra_length']:
                if args.skip_unsupported:
                    print(f'SKIP unsupported method: {name} (method {meta["method"]})')
                    continue
                raise ValueError(f'Unsupported method or central-directory extra bytes for {name!r}: {meta["method"]}, {meta["extra_length"]}')
            if not optional_filter(rel,args.filter):
                other+=1
                continue
            raw=decrypt_entry(src,info,name)
            typ='raw'
            if raw.startswith(b'CryXmlB\0'):
                root=decode_cryxml(raw)
                ET.indent(root,space='  ')
                data=ET.tostring(root,encoding='utf-8',xml_declaration=True)
                # XML export must preserve exact meaning after round-trip without editing.
                enc=guarded_serialize(parse_cryxml_text(data))
                if enc!=raw:
                    # Different binary bytes are normal due to string ordering; semantic check.
                    from evolve_gameplay_editor import semantic
                    if semantic(decode_cryxml(enc))!=semantic(decode_cryxml(raw)):
                        raise AssertionError('CryXmlB extraction roundtrip changed XML meaning: '+name)
                typ='cryxml'
            else:data=raw
            dest=workspace/'files'/Path(*rel.split('/'))
            dest.parent.mkdir(parents=True,exist_ok=True)
            dest.write_bytes(data)
            records.append({'path':rel,'archive_name':name,'format':typ,'sha256':sha_bytes(data),'original_sha256':sha_bytes(raw), 'method':meta['method']})
            selected+=1
            if selected%250==0:print(f'Exported {selected} files...')
        if not records:raise ValueError('No matching entries exported')
        report={'version':VERSION,'source_pak':str(src),'source_sha256':source_hash_at_start,
                'signed_basename':signed_name,'public_key_sha256':sha_file(pub),
                'archive_entry_count':info['count'],'entries':records}
        if not src.is_file():
            raise RuntimeError('SOURCE PAK DISAPPEARED DURING EXTRACTION; stop other Evolve tools and check staging: ' + str(src))
        current_hash = sha_file(src)
        if current_hash != report['source_sha256']:
            raise RuntimeError('SOURCE PAK CHANGED DURING EXTRACTION; stop other Evolve tools and check staging: ' + str(src))
        manifest_path(workspace).write_text(json.dumps(report,indent=2),encoding='utf-8')
        (workspace / '.EXTRACTION-INCOMPLETE.txt').unlink(missing_ok=True)
    except Exception:
        print('EXTRACTION FAILED; PARTIAL FILES PRESERVED FOR DIAGNOSIS:', workspace, file=sys.stderr)
        print('The source PAK was not intentionally changed by this command:', src, file=sys.stderr)
        raise
    print(f'EXPORTED {selected} editable files ({sum(r["format"]=="cryxml" for r in records)} CryXMLB converted to XML; {other} excluded by filters).')
    print('Workspace:',workspace)
    print('Edit files under:',workspace/'files')
    print('Next: run diff, then build. ONLY changed files will be repacked.')


def get_changes(wdir,data):
    changes=[]
    for rec,dest in check_paths(wdir,data['entries']):
        if not dest.is_file():raise FileNotFoundError(f'Missing exported file: {dest}')
        newhash=sha_file(dest)
        if newhash!=rec['sha256']:
            changes.append((rec,dest))
    return changes


def cmd_diff(args):
    wdir=args.workspace.resolve();data=load_workspace(wdir);changed=get_changes(wdir,data)
    if not changed:print('No modified files detected. Edit anything under workspace\\files and rerun.')
    else:
        print(f'{len(changed)} changed files:')
        for rec,_ in changed:print(' - '+rec['path']+' ['+rec['format']+']')
    print(f'Other exported files unchanged: {len(data["entries"])-len(changed)}')


def pack_changed_entry(plain, entry, keys, name, cdr_fields, local_offset):
    if entry['method'] not in (13,14) or entry['extra_length']!=0:
        raise ValueError(f'Unsupported replacement method/extra for {name}')
    if len(plain)>=2**32:raise ValueError('ZIP32 entry exceeds maximum size')
    crc=zlib.crc32(plain)&0xffffffff
    if entry['method']==14:
        compressor=zlib.compressobj(level=9,wbits=-15)
        packed=compressor.compress(plain)+compressor.flush()
    else:packed=plain
    if len(packed)>=2**32:raise ValueError('ZIP32 compressed size exceeded')
    meta=dict(entry,crc=crc,compressed_size=len(packed),original_size=len(plain),local_offset=local_offset)
    key_index=(~(crc>>2))&15
    crypt=twofish_ctr(packed,keys[key_index],file_iv(meta))
    filename=name.encode('utf-8')
    if len(filename)!=entry['name_length']:raise AssertionError('Signed directory name size mismatch')
    vneed,flags,method,tm,dt=cdr_fields[2:7]
    # GPBF data descriptor entries are not supported by this experimental writer.
    if flags & 8:raise ValueError(f'Data descriptor flag present, refusing rewrite: {name}')
    local=struct.pack('<4s5H3I2H',b'PK\x03\x04',vneed,flags,method,tm,dt,crc,len(packed),len(plain),len(filename),0)
    return local+filename+crypt,meta


def cmd_build(args):
    wdir=args.workspace.resolve();data=load_workspace(wdir)
    src=args.pak.resolve() if args.pak else Path(data['source_pak']).resolve()
    pub=args.public_key.resolve();priv=args.private_key.resolve();out=args.out.resolve()
    if not src.is_file():raise FileNotFoundError(f'Missing PAK source: {src}')
    if src==out:raise ValueError('Source and output must be different files')
    if src.name.casefold()!=out.name.casefold():raise ValueError('Output basename must match source PAK basename (e.g. libs.pak)')
    if out.exists():raise FileExistsError(f'Output already exists; refusing overwrite: {out}')
    if not out.parent.is_dir():raise FileNotFoundError(f'Output folder does not exist: {out.parent}')
    if sha_file(src)!=data['source_sha256']:
        raise ValueError('Workspace source PAK changed since export; re-extract before rebuilding')
    if sha_file(pub)!=data['public_key_sha256']:
        raise ValueError('Workspace was exported with a different public key')
    changes=get_changes(wdir,data)
    if not changes:raise ValueError('No changed files; make some edits before building')
    private=load_pem_private_key(priv.read_bytes(),password=None)
    if not isinstance(private,rsa.RSAPrivateKey) or private.key_size!=1024:raise ValueError('Expected custom 1024-bit RSA signing key')
    pubbytes=pub.read_bytes()
    if private.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1)!=pubbytes:
        raise ValueError('Public and private signing keys do not match')
    info,comment,cdr,signed_basename=extract_plain_cdr(src,pub)
    if signed_basename!=data['signed_basename'] or info['count']!=data['archive_entry_count']:
        raise ValueError('Workspace PAK structure mismatched')
    mods=[]
    for rec,file in changes:
        entry_name=pick_entry(info['entries'],rec['archive_name'])
        prior=decrypt_entry(src,info,entry_name)
        if sha_bytes(prior)!=rec['original_sha256']:
            raise ValueError(f'Original payload differs from export: {entry_name}')
        if rec['format']=='cryxml':
            edited=parse_cryxml_text(file.read_bytes())
            original_root=decode_cryxml(prior)
            if edited.tag!=original_root.tag:raise ValueError(f'CryXmlB root tag changed: {entry_name}')
            from cryxml_preserve import preserve_values
            replacement=preserve_values(prior, edited)
        elif rec['format']=='raw':replacement=file.read_bytes()
        else:raise ValueError(f'Unknown workspace format {rec["format"]}')
        if replacement==prior:
            print(f'SKIP: text changed but decoded asset has identical payload: {entry_name}')
            continue
        mods.append((entry_name,replacement))
    if not mods:raise ValueError('No actual payload changes to sign')
    print(f'Building a new signed {src.name} with {len(mods)} changed payloads; original PAK untouched.')
    new_cdr=bytearray(cdr)
    patches=[]
    current_pos=info['cdr_at']
    for name,replacement in mods:
        cdr_pos,fields=find_cdr_record(cdr,name)
        record,meta=pack_changed_entry(replacement,info['entries'][name],info['keys'],name,fields,current_pos)
        patches.append((name,record,replacement))
        struct.pack_into('<III',new_cdr,cdr_pos+16,meta['crc'],meta['compressed_size'],meta['original_size'])
        struct.pack_into('<I',new_cdr,cdr_pos+42,current_pos)
        current_pos+=len(record)
        if current_pos>=2**32:raise ValueError('PAK CDR offset exceeds ZIP32 limit')
    new_cdr_pos=current_pos
    enc_cdr=twofish_ctr(bytes(new_cdr),info['keys'][0],info['iv'])
    signed_comment=prepare_rekey_comment(comment,private,bytes(new_cdr),signed_basename,load_der_public_key(pubbytes))
    with src.open('rb') as stream:
        stream.seek(info['cdr_at']+info['cdr_size'])
        eocd=bytearray(stream.read(22))
    if len(eocd)!=22 or eocd[:4]!=b'PK\x05\x06':raise ValueError('EOCD missing at calculated location')
    if struct.unpack_from('<H',eocd,20)[0]!=len(comment):raise ValueError('EOCD comment length mismatch')
    struct.pack_into('<I',eocd,16,new_cdr_pos)
    try:
        with src.open('rb') as infile,out.open('xb') as outfile:
            remaining=info['cdr_at']
            while remaining:
                block=infile.read(min(2*1024*1024,remaining))
                if not block:raise EOFError('Source PAK truncated')
                outfile.write(block);remaining-=len(block)
            for _,record,_ in patches:outfile.write(record)
            outfile.write(enc_cdr);outfile.write(eocd);outfile.write(signed_comment)
        verify,_,_,got_name=extract_plain_cdr(out,pub)
        if verify['count']!=info['count'] or got_name!=signed_basename:
            raise AssertionError('Signed archive verification failed')
        changed_cdr=[n for n in info['entries'] if info['entries'][n]!=verify['entries'][n]]
        if set(changed_cdr)!={x[0] for x in patches}:
            raise AssertionError(f'Unexpected CDR metadata differences: {changed_cdr}')
        for name,_,replacement in patches:
            reloaded=decrypt_entry(out,verify,name)
            if reloaded!=replacement:raise AssertionError(f'Byte-for-byte payload verification FAILED: {name}')
        print(f'PASS: RSA signature, CDR, entry CRC32 and all {len(patches)} edited payloads verified.')
        for name,_,_ in patches:print(' CHANGED:',name)
        print('OUTPUT:',out)
        print('Installed Evolve files have NOT been changed.')
    except BaseException:
        out.unlink(missing_ok=True)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__,formatter_class=argparse.RawDescriptionHelpFormatter)
    sub=p.add_subparsers(dest='command',required=True)
    ex=sub.add_parser('extract',help='Export CryXMLB as editable XML and other PAK entries as raw files')
    ex.add_argument('--pak',required=True,type=Path);ex.add_argument('--public-key',required=True,type=Path)
    ex.add_argument('--workspace',required=True,type=Path);ex.add_argument('--filter',action='append',default=[],help='Optional case-insensitive substring filter (repeatable)')
    ex.add_argument('--skip-unsupported',action='store_true',help='Skip unsupported archive entry methods instead of aborting')
    df=sub.add_parser('diff',help='List changed extracted files')
    df.add_argument('--workspace',required=True,type=Path)
    b=sub.add_parser('build',help='Repack only changed entries and re-sign whole CryPak CDR')
    b.add_argument('--workspace',required=True,type=Path);b.add_argument('--pak',type=Path,help='Source PAK (defaults to original export path)')
    b.add_argument('--public-key',required=True,type=Path);b.add_argument('--private-key',required=True,type=Path)
    b.add_argument('--out',required=True,type=Path)
    args=p.parse_args()
    try:
        {'extract':cmd_extract,'diff':cmd_diff,'build':cmd_build}[args.command](args)
    except Exception as e:
        print(f'ERROR: {type(e).__name__}: {e}',file=sys.stderr)
        sys.exit(2)


if __name__=='__main__':main()
