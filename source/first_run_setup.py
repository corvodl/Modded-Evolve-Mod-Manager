"""Create a local signing workspace from an installed, restored Evolve game.
The distributed application carries tools only; users create their own PAK sets.
"""
import argparse
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace
import uuid
from cryptography.hazmat.primitives.serialization import load_der_public_key, Encoding, PublicFormat
from evolve_pak_rekey import cmd_keygen
from refresh_originals import refresh, preflight
from old_prepared import preserve_ready
from universal_stage import ensure_closed, save_json


def public_key_from_shim(path):
    data=Path(path).read_bytes()
    if len(data)!=4096:
        raise ValueError('This installed inject.dll layout is not supported. Expected the known 4096-byte shim; no game files changed.')
    key=data[0x870:0x8fc]
    try:
        parsed=load_der_public_key(key)
        valid=parsed.key_size==1024 and parsed.public_bytes(Encoding.DER,PublicFormat.PKCS1)==key
    except Exception:valid=False
    if not valid:raise ValueError('No supported original RSA public key at the expected injector location. No game files changed.')
    # The shipped reference is for checking known layouts, NOT a replacement
    # for a missing or different game injector. Derive the real key from the
    # recipient's installed game so rebuilt PAKs match that exact game.
    reference = Path(__file__).resolve().parent / 'reference'
    shim_ref = reference / 'inject.dll'
    key_ref = reference / 'RSAKeyData.bin'
    if shim_ref.is_file() and key_ref.is_file():
        if len(shim_ref.read_bytes()) != 4096 or shim_ref.read_bytes()[0x870:0x8fc] != key_ref.read_bytes():
            raise ValueError('Bundled injector/key reference is damaged. Re-extract the build kit.')
        if key != key_ref.read_bytes():
            print('NOTE: Installed injector uses a different valid public key than the known reference. The installed game key will be used.', flush=True)
    return key


def setup_from_game(game, launcher, destination, seed_parent, archive_ready=False):
    game,launcher,destination,seed_parent=[Path(p).resolve() for p in (game,launcher,destination,seed_parent)]
    ensure_closed()
    if not (game/'bin64_SteamRetail'/'Evolve.exe').is_file():raise ValueError('Choose EvolveGame, containing bin64_SteamRetail/Evolve.exe.')
    if not launcher.is_file() or launcher.suffix.lower()!='.exe':raise ValueError('Choose the normal Modded Evolve client EXE.')
    if launcher.name.casefold()=='evolve.exe':raise ValueError('Choose the normal client EXE, not the game executable.')
    for root in (destination,seed_parent):
        if root==game or root in game.parents or game in root.parents:raise ValueError('Manager data must be stored separately from the installed game.')
    if destination.exists():raise ValueError('Setup output must be a new folder. Existing keys and projects are never replaced.')
    # Validate the installed injector before touching any prepared copies.
    # Previously prepared files are never removed automatically. The user can
    # explicitly choose to preserve/rename them in-place first.
    key=public_key_from_shim(game/'bin64_SteamRetail'/'inject.dll')
    preserved = {'count': 0, 'manifest': ''}
    if archive_ready:
        preserved = preserve_ready(game, seed_parent.parent/'ArchivedPrepared')
    preflight(game,seed_parent/'uncreated-stage',seed_parent/'uncreated-swap')
    seed=seed_parent/('setup-'+uuid.uuid4().hex)
    stage=seed/'staged';swap=seed/'swap'
    stage.mkdir(parents=True,exist_ok=False);swap.mkdir()
    (seed/'RSAKeyData.bin').write_bytes(key)
    save_json(swap/'launcher_config.json',{'launcher':str(launcher)})
    print('Generating local signing keys. The installed game remains unchanged.',flush=True)
    cmd_keygen(SimpleNamespace(output_dir=stage/'mykeys'))
    # Copies originals, verifies their signatures, creates a custom stage and
    # prepares fresh local helper/config files using the same verified workflow.
    report=refresh(game,stage,swap,destination)
    report.pop('previous_stage',None);report.pop('previous_swap',None)
    report['initial_setup']=True;report['launcher']=str(launcher)
    report['preserved_old_prepared']=preserved
    save_json(destination/'refresh_report.json',report)
    # This seed belongs exclusively to this invocation; its keys now exist in
    # the completed stage. No existing user setup is removed.
    shutil.rmtree(seed)
    print('INITIAL SETUP COMPLETE:',destination,flush=True)
    print('Original snapshots, signing keys, staged PAKs and helpers were generated locally.')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('game','launcher','destination','seed-parent'):p.add_argument('--'+name,required=True,type=Path)
    p.add_argument('--preserve-old-prepared',action='store_true',help='User approved renaming prepared copies in place, without changing original PAKs.')
    a=p.parse_args()
    try:setup_from_game(a.game,a.launcher,a.destination,a.seed_parent,archive_ready=a.preserve_old_prepared)
    except Exception as e:print('INITIAL SETUP STOPPED:',e,file=sys.stderr);return 1
    return 0

if __name__=='__main__':sys.exit(main())
