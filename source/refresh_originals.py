"""Build a verified signing stage after a game update.
Full-size original snapshots are optional. The installed game remains unchanged;
previous signing stages, projects, and swap recovery backups are always retained.
"""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import uuid
import zipfile
from universal_stage import digest, ensure_closed, read_json, save_json
from evolve_pak_workspace import normalized_name
from evolve_pak_rekey import cmd_resign, cmd_patch_shim, load_with_signed_basename
from evolve_video_pak_tool_fixed import get_eocd_tail
from cryptography.hazmat.primitives.serialization import load_pem_private_key, Encoding, PublicFormat


def inventory(game):
    found=[];seen=set()
    for path in sorted(game.rglob('*')):
        if path.is_symlink(): raise ValueError('Symlink in game folder; inspect before refresh: '+str(path))
        if not path.is_file() or path.suffix.lower()!='.pak':continue
        rel=normalized_name(path.relative_to(game).as_posix())
        if rel.casefold() in seen:raise ValueError('Duplicate archive path: '+rel)
        seen.add(rel.casefold());found.append((rel,path))
    if not found:raise ValueError('No PAKs found. Choose the EvolveGame folder.')
    return found


def preflight(game, stage, swap):
    ensure_closed()
    if not (game/'bin64_SteamRetail'/'Evolve.exe').is_file():
        raise ValueError('Choose EvolveGame, containing bin64_SteamRetail/Evolve.exe.')
    if game==stage or game in stage.parents or stage in game.parents:
        raise ValueError('Game and signing stage must be separate.')
    # Any recovery backup, including one missing from the journal, blocks refresh.
    for path in game.rglob('*.customkey-original'):
        raise ValueError('Restore originals before refreshing. Recovery backup found: '+str(path))
    jp=swap/'controlled_swap_state.json'
    old=read_json(jp) if jp.exists() else None
    if old:
        if old.get('phase') not in ('prepared','restored'):
            raise ValueError('An active/interrupted swap exists. Restore original game files first.')
        if Path(old['game']).resolve()!=game or Path(old['stage']).resolve()!=stage:
            raise ValueError('The current swap journal belongs to a different game or stage.')
    ready=[]
    for item in (old or {}).get('files',[]):
        target=Path(item['target']).resolve()
        expected=(game/normalized_name(item['name'].replace('\\','/'))).resolve()
        if target!=expected or not target.is_relative_to(game):raise ValueError('Unexpected target in journal.')
        rp=Path(item['ready']).resolve()
        if rp!=Path(str(target)+'.customkey-ready'):raise ValueError('Unexpected prepared path in journal.')
        if Path(item['backup']).exists():raise ValueError('Restore originals first.')
        if rp.exists():
            if digest(rp)!=item['sha256']:raise ValueError('Prepared file changed; inspect before refresh: '+str(rp))
            ready.append(rp)
    actual={p.resolve() for p in game.rglob('*.customkey-ready')}
    if actual!=set(ready):raise ValueError('Untracked prepared files exist; inspect the previous swap before refreshing.')
    return old,ready


def refresh(game, stage, swap, destination, keep_original_snapshots=True):
    game,stage,swap,destination=[Path(p).resolve() for p in (game,stage,swap,destination)]
    old,ready=preflight(game,stage,swap)
    if destination.exists():raise ValueError('Refresh output must be a new folder.')
    if game==destination or game in destination.parents or destination in game.parents:
        raise ValueError('Refresh output must be separate from the game.')
    for active in (stage,swap):
        if active==destination or active in destination.parents or destination in active.parents:
            raise ValueError('Refresh output must be separate from the current stage and swap folders.')
    files=inventory(game)
    old_key=stage.parent/'RSAKeyData.bin'
    pub=stage/'mykeys'/'public_key.bin'; private=stage/'mykeys'/'private_key.pem'
    key=old_key.read_bytes();custom=pub.read_bytes()
    signing=load_pem_private_key(private.read_bytes(),password=None)
    if signing.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1)!=custom:
        raise ValueError('Existing signing keys do not match.')
    shim=game/'bin64_SteamRetail'/'inject.dll'
    shim_bytes=shim.read_bytes()
    if len(key)!=140 or len(shim_bytes)!=4096 or shim_bytes[0x870:0x8fc]!=key:
        raise ValueError('Installed inject.dll no longer matches the supported original shim/key. Stop: an updated shim needs separate review.')
    # The signed stage is always required; copying a second archival snapshot is optional.
    required=sum(p.stat().st_size for _,p in files)*(2 if keep_original_snapshots else 1)+1024**3
    destination.parent.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(destination.parent).free<required:
        raise ValueError(f'Refresh needs up to {required/1024**3:.1f} GiB free for the signed stage' + (' and original snapshots.' if keep_original_snapshots else ' (without permanent original snapshots).'))
    destination.mkdir()
    report={'status':'building','game':str(game),'previous_stage':str(stage),'previous_swap':str(swap),
            'keep_original_snapshots':bool(keep_original_snapshots),'entries':[],'ready_moves':[]}
    save_json(destination/'refresh_report.json',report)
    new_stage=destination/'staged';new_swap=destination/'swap';originals=destination/'originals'
    new_stage.mkdir();new_swap.mkdir()
    if keep_original_snapshots: originals.mkdir()
    shutil.copytree(stage/'mykeys',new_stage/'mykeys')
    (destination/'RSAKeyData.bin').write_bytes(key)
    count=0
    for index,(rel,source) in enumerate(files,1):
        print(f'[{index}/{len(files)}] Checking and signing {rel}',flush=True)
        before=digest(source)
        original=source
        if keep_original_snapshots:
            original=originals/rel;original.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(source,original)
            if digest(original)!=before:raise ValueError('Original snapshot hash mismatch: '+rel)
        if digest(source)!=before:raise ValueError('Source changed during setup: '+rel)
        with original.open('rb') as stream:_,_,comment=get_eocd_tail(stream)
        if len(comment)==2320 and comment[:6]==b'\x06\x00\x00\x00\x01\x03':
            # Refuses custom-key/modded archives in the original snapshot.
            load_with_signed_basename(original,destination/'RSAKeyData.bin')
            out=new_stage/'paks'/rel;out.parent.mkdir(parents=True,exist_ok=True)
            cmd_resign(SimpleNamespace(original=original,output=out,original_public_key=destination/'RSAKeyData.bin',
                private_key=new_stage/'mykeys'/'private_key.pem',public_key=new_stage/'mykeys'/'public_key.bin'))
            kind='signed-encrypted';count+=1
        elif not comment and zipfile.is_zipfile(original):
            # Plain archives remain installed as-is and are not used by the swap.
            kind='plain-zip'
        else:raise ValueError('Unsupported archive trailer; previous setup retained: '+rel)
        if digest(source)!=before:raise ValueError('Installed PAK changed during signing: '+rel)
        report['entries'].append({'path':rel,'kind':kind,'sha256':before,'bytes':source.stat().st_size})
        save_json(destination/'refresh_report.json',report)
    if not count:raise ValueError('No supported signed archives found.')
    shim_input=shim
    if keep_original_snapshots:
        shim_copy=originals/'bin64_SteamRetail'/'inject.dll'
        shim_copy.parent.mkdir(parents=True,exist_ok=True);shim_copy.write_bytes(shim_bytes)
        shim_input=shim_copy
    cmd_patch_shim(SimpleNamespace(shim=shim_input,output=new_stage/'inject-custom.dll',
        original_public_key=destination/'RSAKeyData.bin',public_key=new_stage/'mykeys'/'public_key.bin',offset='0x870'))
    save_json(new_stage/'rekey_plan.json',{'source_root':str(game),'blocked':[],'entries':report['entries']})
    save_json(new_stage/'stage_status.json',{'status':'fully-staged-offline','count':count,'shim_sha256':digest(new_stage/'inject-custom.dll')})
    # Use this version's helpers in the new folder; retain all previous helpers.
    support=Path(__file__).parent/'launcher_support'
    for name in ('controlled_swap.py','reusable_restore.py','auto_launch_frida.py'):shutil.copy2(support/name,new_swap/name)
    # Preserve configured launcher path from previous helper/config without executing it.
    config={}
    previous_config=swap/'launcher_config.json'
    if previous_config.is_file():config=read_json(previous_config)
    elif (swap/'auto_launch_frida.py').is_file():
        import ast
        tree=ast.parse((swap/'auto_launch_frida.py').read_text(encoding='utf-8-sig'))
        for node in tree.body:
            if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='LAUNCHER' for t in node.targets):
                if isinstance(node.value,ast.Call) and node.value.args and isinstance(node.value.args[0],ast.Constant):
                    config['launcher']=node.value.args[0].value
    save_json(new_swap/'launcher_config.json',config)
    save_json(new_swap/'controlled_swap_state.json',{'version':1,'phase':'restored','game':str(game),'stage':str(new_stage),'files':[],'count':0})
    # Verify the live generation stayed stable through the whole operation.
    current_state,current_ready=preflight(game,stage,swap)
    if current_state!=old or current_ready!=ready:
        raise ValueError('Swap state changed during refresh. Previous setup retained.')
    if [r for r,_ in inventory(game)]!=[r for r,_ in files]:raise ValueError('Game archive inventory changed during refresh.')
    for row in report['entries']:
        if digest(game/row['path'])!=row['sha256']:raise ValueError('Game changed during refresh. Retry with launcher closed.')
    if shim.read_bytes()!=shim_bytes:raise ValueError('Installed shim changed during refresh.')
    ensure_closed()
    token=uuid.uuid4().hex
    moves=[(p,Path(str(p)+'.previous-'+token)) for p in ready]
    report['ready_moves']=[{'from':str(a),'to':str(b)} for a,b in moves]
    report['status']='activating';save_json(destination/'refresh_report.json',report)
    # Durable journal makes interrupted activation recoverable without guessing.
    marker=stage/'REFRESHED-TO.json'
    if marker.exists():raise ValueError('This stage was already superseded; select the latest stage in Settings.')
    moved=[]
    try:
        save_json(marker,{'destination':str(destination),'status':'activating'})
        for a,b in moves:
            os.replace(a,b);moved.append((a,b))
        if old:
            save_json(destination/'previous_swap_state.json',old)
            updated=dict(old,phase='restored');save_json(swap/'controlled_swap_state.json',updated)
        report.update(status='complete',stage=str(new_stage),swap=str(new_swap),signed_count=count)
        save_json(destination/'refresh_report.json',report)
        save_json(marker,{'destination':str(destination),'status':'complete'})
    except BaseException:
        try:
            for a,b in reversed(moved):os.replace(b,a)
            if old:save_json(swap/'controlled_swap_state.json',old)
            marker.unlink(missing_ok=True)
            report['status']='activation-failed-rolled-back';save_json(destination/'refresh_report.json',report)
        except Exception:
            print('Activation recovery needed. Preserve refresh_report.json and the previous setup.',file=sys.stderr)
        raise
    print('REFRESH COMPLETE:',destination,flush=True)
    print('Previous stage and edits retained. Unpack new archives and review/reapply edits before building.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('game','stage','swap','destination'):p.add_argument('--'+name,type=Path,required=True)
    p.add_argument('--skip-original-snapshots',action='store_true',
                   help='Save disk space: sign directly from installed game PAKs without keeping extra original copies.')
    a=p.parse_args()
    try:refresh(a.game,a.stage,a.swap,a.destination,keep_original_snapshots=not a.skip_original_snapshots)
    except Exception as e:
        print('REFRESH STOPPED:',e,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
