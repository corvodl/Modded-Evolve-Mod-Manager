"""Export the installed manager plus local mod data, without the game/client.
Portable metadata is rebound to its extraction folder and recipient game paths.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import zipfile
from universal_stage import ensure_closed, digest, read_json, save_json, allowed_paks
from launch_integration import REQUIRED_SCRIPTS

MARKER='portable_bundle.json'
STAGE='Data/Setup/staged'
SWAP='Data/Setup/swap'
PROJECTS='Data/Projects'

def mapped(value, mappings):
    if isinstance(value,dict):return {k:mapped(v,mappings) for k,v in value.items()}
    if isinstance(value,list):return [mapped(v,mappings) for v in value]
    if not isinstance(value,str):return value
    normalized=value.replace('\\','/')
    for old,new in sorted(mappings,key=lambda x:len(x[0]),reverse=True):
        old=old.replace('\\','/').rstrip('/')
        if normalized.casefold()==old.casefold():return new
        if normalized.casefold().startswith(old.casefold()+'/'):return new+normalized[len(old):]
    return value

def tree(root):
    root=Path(root).resolve()
    for p in sorted(root.rglob('*')):
        if p.is_symlink():raise ValueError('Cannot export a linked file/folder: '+str(p))
        if p.is_file() and '__pycache__' not in p.parts:yield p,p.relative_to(root).as_posix()

def safe_member(name):
    p=Path(name)
    if p.is_absolute() or '..' in p.parts or ':' in name or '\\' in name:
        raise ValueError('Unsafe portable metadata path: '+name)
    return p

def portable_export(home, stage, swap, projects, output):
    ensure_closed()
    home,stage,swap,projects,output=[Path(p).resolve() for p in (home,stage,swap,projects,output)]
    if output.exists():raise ValueError('Choose a new ZIP filename; existing exports are not overwritten.')
    for root in (stage,swap,projects,home/'_internal'):
        if output.is_relative_to(root):raise ValueError('Save the export outside the folders being collected.')
    for name in ('EvolveModManager.exe','EvolveModWorker.exe','_internal'):
        if not (home/name).exists():raise ValueError('Export from the built Windows app, with both EXEs and _internal present.')
    if (stage/'REFRESHED-TO.json').exists():raise ValueError('Select the current stage, not a superseded set.')
    plan=read_json(stage/'rekey_plan.json');status=read_json(stage/'stage_status.json')
    if status.get('status')!='fully-staged-offline':raise ValueError('Signing stage is incomplete.')
    rels=list(allowed_paks(stage).values())
    if len(rels)!=status.get('count'):raise ValueError('Stage archive count mismatch.')
    state_path=swap/'controlled_swap_state.json'
    state=read_json(state_path) if state_path.is_file() else None
    if state and Path(state['stage']).resolve()!=stage:raise ValueError('Selected stage differs from the swap journal.')
    if state and state.get('phase') not in ('prepared','restored'):raise ValueError('Restore Original Game Files before exporting.')
    if state and any(Path(f['backup']).exists() for f in state.get('files',[])):raise ValueError('Recovery backups exist. Restore first.')
    game=Path((state or {}).get('game') or plan['source_root']).resolve()
    if any(game.rglob('*.customkey-original')):raise ValueError('Unrestored game backups exist. Restore first.')
    key=stage.parent/'RSAKeyData.bin'
    for file in (key,stage/'mykeys'/'public_key.bin',stage/'mykeys'/'private_key.pem',stage/'inject-custom.dll'):
        if not file.is_file():raise ValueError('Required setup file missing: '+str(file))
    if projects.exists() and not projects.is_dir():raise ValueError('Projects path must be a folder.')
    if projects==game or projects in game.parents or game in projects.parents:
        raise ValueError('Keep editing projects separate from the installed game before exporting.')
    source_shim=game/'bin64_SteamRetail'/'inject.dll'
    original=source_shim.read_bytes(); original_key=key.read_bytes()
    if len(original)!=4096 or original[0x870:0x8fc]!=original_key:raise ValueError('Installed original injector is not restored or does not match the signing setup.')
    if digest(stage/'inject-custom.dll')!=status['shim_sha256']:raise ValueError('Staged injector does not match the stage report.')
    from cryptography.hazmat.primitives.serialization import load_pem_private_key,Encoding,PublicFormat
    private=load_pem_private_key((stage/'mykeys'/'private_key.pem').read_bytes(),password=None)
    if private.public_key().public_bytes(Encoding.DER,PublicFormat.PKCS1)!=(stage/'mykeys'/'public_key.bin').read_bytes():raise ValueError('Signing keys do not match.')
    sources={}; transforms={}; mappings=[(str(stage),'{APP}/'+STAGE),(str(swap),'{APP}/'+SWAP),(str(projects),'{APP}/'+PROJECTS),(str(game),'{GAME}')]
    seen=set()
    def add(path,member):
        safe_member(member)
        if member.casefold() in seen:raise ValueError('Duplicate export path: '+member)
        seen.add(member.casefold());sources[member]=Path(path)
    for name in ('EvolveModManager.exe','EvolveModWorker.exe'):add(home/name,name)
    for p,rel in tree(home/'_internal'):add(p,'_internal/'+rel)
    for p,rel in tree(home/'Docs'):add(p,'Docs/'+rel)
    for p,rel in tree(stage):add(p,STAGE+'/'+rel)
    add(key,'Data/Setup/RSAKeyData.bin')
    for p,rel in tree(projects):add(p,PROJECTS+'/'+rel)
    # A workspace can refer to an older/external PAK; include that exact source too.
    dependencies={}
    for p in projects.rglob('.evolve-pak-workspace.json'):
        data=read_json(p);source=Path(data['source_pak']).resolve()
        if not source.is_file():raise ValueError('Project source PAK missing: '+str(source))
        if not source.is_relative_to(stage) and not source.is_relative_to(projects):
            key_name=str(source)
            if key_name not in dependencies:
                member='Data/ProjectSources/'+hashlib.sha256(key_name.encode()).hexdigest()[:20]+'/'+source.name
                add(source,member);dependencies[key_name]=member;mappings.append((key_name,'{APP}/'+member))
    # Save previous customized helpers as reference; use the bundled relocatable helpers.
    for p,rel in tree(swap):
        if p.suffix.lower() in ('.py','.ps1','.cmd','.bat'):
            add(p,'Data/PreviousHelpers/'+rel)
    support=Path(__file__).parent/'launcher_support'
    for name in REQUIRED_SCRIPTS:add(support/name,SWAP+'/'+name)
    originals=stage.parent/'originals'
    if originals.is_dir():
        for p,rel in tree(originals):add(p,'Data/Originals/'+rel)
        mappings.append((str(originals),'{APP}/Data/Originals'))
    for member,p in sources.items():
        if member==STAGE+'/rekey_plan.json' or p.name=='.evolve-pak-workspace.json' or (p.name=='metadata.json' and '/Backups/' in member):
            transforms[member]=mapped(read_json(p),mappings)
    transforms[STAGE+'/rekey_plan.json']['source_root']='{GAME}'
    transforms[SWAP+'/controlled_swap_state.json']={'version':1,'phase':'restored','game':'{GAME}','stage':'{APP}/'+STAGE,'files':[],'count':0}
    transforms[SWAP+'/launcher_config.json']={'launcher':'{LAUNCHER}'}
    transforms['Data/Settings/manager_settings.json']={'stage':'{APP}/'+STAGE,'swap':'{APP}/'+SWAP,'projects':'{APP}/'+PROJECTS,'current_workspace':'','output_pak':'','archive_label':''}
    # Fingerprints establish which installed game assets this bundle expects.
    fingerprints={}
    for i,rel in enumerate(rels,1):
        safe_member(rel)
        installed=game/rel;staged=stage/'paks'/rel
        if not staged.is_file() or not installed.is_file():raise ValueError('Required PAK missing: '+rel)
        print(f'Checking original {i}/{len(rels)}: {rel}',flush=True)
        fingerprints[rel]=digest(installed)
    fingerprints['bin64_SteamRetail/inject.dll']=digest(source_shim)
    marker={'version':1,'configured':False,'bound_home':'','metadata':list(transforms),'fingerprints':fingerprints}
    output.parent.mkdir(parents=True,exist_ok=True)
    temporary=output.with_name(output.name+'.partial-'+uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(temporary,'x',compression=zipfile.ZIP_STORED,allowZip64=True) as z:
            for i,(member,path) in enumerate(sources.items(),1):
                if member in transforms:continue
                before=path.stat()
                print(f'Packing {i}/{len(sources)}: {member}',flush=True)
                z.write(path,'EvolveModManager/'+member)
                after=path.stat()
                if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Source changed during export: '+str(path))
            for member,data in transforms.items():z.writestr('EvolveModManager/'+member,json.dumps(data,indent=2),compress_type=zipfile.ZIP_DEFLATED)
            z.writestr('EvolveModManager/'+MARKER,json.dumps(marker,indent=2))
            if not any(m.startswith(PROJECTS+'/') for m in sources):
                z.writestr('EvolveModManager/'+PROJECTS+'/README.txt','New editing projects are stored in this folder.\n')
            z.writestr('EvolveModManager/START-HERE.txt','COMPLETE MOD MANAGER BUNDLE\nExtract the entire folder, then open EvolveModManager.exe.\nSelect your installed EvolveGame folder and normal client EXE when prompted.\nThe ZIP contains runtime dependencies, signing keys, staged PAKs and editing projects.\nIt does not contain the installed game or normal client.\nKeep the whole folder together. Restore game originals before moving it.\n')
        ensure_closed()
        if state_path.is_file() and read_json(state_path)!=state:raise ValueError('Swap state changed during export. Retry with other managers closed.')
        # Verify CRCs before publishing the final name, including ZIP64 entries.
        print('Verifying complete ZIP. Large exports may take several minutes.',flush=True)
        with zipfile.ZipFile(temporary) as z:
            bad=z.testzip()
            if bad:raise ValueError('ZIP integrity check failed: '+bad)
        os.replace(temporary,output)
    except BaseException:
        temporary.unlink(missing_ok=True);raise
    print('COMPLETE BUNDLE:',output,flush=True)
    return output


def bind_home(home):
    home=Path(home).resolve();path=home/MARKER
    if not path.is_file():return None
    marker=read_json(path)
    if marker.get('version')!=1:raise ValueError('Unsupported complete-bundle version.')
    old=marker.get('bound_home','')
    if old==str(home):return marker
    journal=home/SWAP/'controlled_swap_state.json'
    if old and journal.is_file():
        state=read_json(journal)
        if state.get('phase') != 'restored' or any(Path(f['ready']).exists() for f in state.get('files',[])) or any(Path(f['backup']).exists() for f in state.get('files',[])):
            raise ValueError('This bundle was moved during a game swap. Restore using the original folder before moving it.')
    mappings=[('{APP}',str(home))]
    if old:mappings.append((old,str(home)))
    for member in marker['metadata']:
        file=home/safe_member(member)
        if not file.resolve().is_relative_to(home):raise ValueError('Portable metadata resolves outside app folder.')
        save_json(file,mapped(read_json(file),mappings))
    marker['bound_home']=str(home)
    if old:
        # Moving the app requires choosing/validating the installation again.
        marker['configured']=False
        save_json(journal,{'version':1,'phase':'restored','game':'{GAME}','stage':str(home/STAGE),'files':[],'count':0})
    save_json(path,marker)
    return marker


def configure(home,game,launcher):
    ensure_closed()
    home,game,launcher=[Path(p).resolve() for p in (home,game,launcher)]
    marker=bind_home(home)
    if not marker:raise ValueError('This is not a complete exported bundle.')
    if not (game/'bin64_SteamRetail'/'Evolve.exe').is_file() or not launcher.is_file():raise ValueError('Select the installed EvolveGame folder and client EXE.')
    if home==game or home in game.parents or game in home.parents:raise ValueError('Keep the manager separate from the installed game.')
    if any(game.rglob('*.customkey-original')):raise ValueError('This game has unrestored swap backups. Restore with its existing manager first.')
    if any(game.rglob('*.customkey-ready')):raise ValueError('This game already has prepared mod files. Restore/retire its existing setup before configuring this bundle; do not delete recovery files.')
    for i,(rel,expected) in enumerate(marker['fingerprints'].items(),1):
        p=game/safe_member(rel)
        print(f'Verifying installed game {i}/{len(marker["fingerprints"])}: {rel}',flush=True)
        if not p.is_file() or digest(p)!=expected:raise ValueError('Installed game differs from the bundle originals: '+rel+'. Update/repair the game to the matching version, or export a refreshed bundle.')
    for member in marker['metadata']:
        p=home/safe_member(member)
        save_json(p,mapped(read_json(p),[('{GAME}',str(game)),('{LAUNCHER}',str(launcher))]))
    plan=read_json(home/STAGE/'rekey_plan.json');plan['source_root']=str(game);save_json(home/STAGE/'rekey_plan.json',plan)
    save_json(home/SWAP/'controlled_swap_state.json',{'version':1,'phase':'restored','game':str(game),'stage':str(home/STAGE),'files':[],'count':0})
    save_json(home/SWAP/'launcher_config.json',{'launcher':str(launcher)})
    marker.update(configured=True,game=str(game),launcher=str(launcher));save_json(home/MARKER,marker)
    print('Complete bundle configured. All manager resources are inside its folder.')


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='action',required=True)
    e=sub.add_parser('export')
    for name in ('home','stage','swap','projects','output'):e.add_argument('--'+name,type=Path,required=True)
    c=sub.add_parser('configure')
    for name in ('home','game','launcher'):c.add_argument('--'+name,type=Path,required=True)
    a=p.parse_args()
    try:
        if a.action=='export':portable_export(a.home,a.stage,a.swap,a.projects,a.output)
        else:configure(a.home,a.game,a.launcher)
    except Exception as exc:print('BUNDLE STOPPED:',exc,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
