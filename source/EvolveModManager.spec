# Build on Windows with 64-bit Python 3.11.
from pathlib import Path
from importlib.util import find_spec
from PyInstaller.utils.hooks import collect_all
import ast

root = Path(SPECPATH)
datas = [(str(p), '.') for p in root.glob('*.py') if not p.name.startswith(('test_', 'Test-', 'build_'))]
datas += [(str(root/'launcher_support'), 'launcher_support')]
datas += [(str(root/'assets'), 'assets')]
datas += [(str(root/'reference'), 'reference')]
datas += [(str(root/'ui_text.json'), '.')] 
binaries = []
hidden = ['evolve_pak_workspace', 'evolve_pak_rekey', 'evolve_gameplay_editor',
          'evolve_video_pak_tool_fixed', 'dds_texture', 'dds_png_import', 'dds_streaming', 'model_asset', 'model_preview', 'multi_pak_assets', 'decode_cryxml', 'universal_stage', 'refresh_originals', 'workspace_editor', 'portable_bundle', 'first_run_setup', 'old_prepared']
for package in ('cryptography', 'frida', 'twofish', 'PIL'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hidden += h
# twofish loads its extension with ctypes; include it as a native library.
twofish_native = find_spec('_twofish')
if twofish_native and twofish_native.origin:
    binaries.append((twofish_native.origin, '.'))
# Existing swap scripts execute from the user's folder. Bundle their dependencies,
# but do not freeze controlled_swap: its __file__ locates the user's journal.
for script in (root/'launcher_support').glob('*.py'):
    for node in ast.walk(ast.parse(script.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            hidden += [n.name for n in node.names if n.name != 'controlled_swap']
        elif isinstance(node, ast.ImportFrom) and node.module and node.module != 'controlled_swap':
            hidden.append(node.module)
a = Analysis(['pak_manager_gui.py', 'worker_entry.py'], pathex=[str(root)],
             binaries=binaries, datas=datas, hiddenimports=sorted(set(hidden)),
             hookspath=[], runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
# Preserve PyInstaller runtime hooks in both executables.
common_scripts = [s for s in a.scripts if s[0] not in ('pak_manager_gui', 'worker_entry')]
gui_scripts = common_scripts + [s for s in a.scripts if s[0] == 'pak_manager_gui']
worker_scripts = common_scripts + [s for s in a.scripts if s[0] == 'worker_entry']
assert len(gui_scripts) == len(common_scripts) + 1
assert len(worker_scripts) == len(common_scripts) + 1
gui = EXE(pyz, gui_scripts, [], exclude_binaries=True, name='EvolveModManager',
          debug=False, strip=False, upx=False, console=False, icon=str(root/'assets'/'hunt.ico'))
worker = EXE(pyz, worker_scripts, [], exclude_binaries=True, name='EvolveModWorker',
             debug=False, strip=False, upx=False, console=True, icon=str(root/'assets'/'hunt.ico'))
coll = COLLECT(gui, worker, a.binaries, a.datas, strip=False, upx=False,
               name='EvolveModManager')
