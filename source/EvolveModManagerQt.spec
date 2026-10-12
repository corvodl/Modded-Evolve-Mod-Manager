# Experimental PySide6 build. Do not replace the verified Tk Main release.
from pathlib import Path
from importlib.util import find_spec
from PyInstaller.utils.hooks import collect_all
import ast

root = Path(SPECPATH)
datas = [(str(p), '.') for p in root.glob('*.py')
         if not p.name.startswith(('test_', 'Test-', 'build_'))]
datas += [(str(root/'launcher_support'), 'launcher_support'),
          (str(root/'assets'), 'assets'),
          (str(root/'reference'), 'reference'),
          (str(root/'ui_text.json'), '.'),
          (str(root/'VERSION.txt'), '.')]
binaries = []
hidden = [
    'database_animation', 'texture_selection', 'texture_transform', 'qt_texture_selection',
    'evolve_pak_workspace', 'evolve_pak_rekey', 'evolve_gameplay_editor',
    'evolve_video_pak_tool_fixed', 'dds_texture', 'dds_png_import',
    'dds_streaming', 'model_asset', 'model_preview', 'gpu_model_preview',
    'material_preview', 'multi_pak_assets', 'decode_cryxml',
    'universal_stage', 'refresh_originals', 'workspace_editor',
    'workspace_file_actions', 'pak_browser', 'portable_bundle',
    'first_run_setup', 'old_prepared', 'loose_game_files',
    'loose_file_editor', 'qt_manager', 'qt_workspace_editor',
    'qt_legacy_editor_bridge', 'qt_gpu_viewer', 'qt_font_health', 'desktop_shortcut', 'orphaned_swap_recovery',
]
for package in ('cryptography', 'frida', 'twofish', 'PIL'):
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hidden += h
hidden.append('numpy')
twofish_native = find_spec('_twofish')
if twofish_native and twofish_native.origin:
    binaries.append((twofish_native.origin, '.'))
for script in (root/'launcher_support').glob('*.py'):
    for node in ast.walk(ast.parse(script.read_text(encoding='utf-8'))):
        if isinstance(node, ast.Import):
            hidden += [n.name for n in node.names if n.name != 'controlled_swap']
        elif isinstance(node, ast.ImportFrom) and node.module and node.module != 'controlled_swap':
            hidden.append(node.module)

entrypoints = ('qt_manager', 'worker_entry', 'qt_legacy_editor_bridge')
a = Analysis([str(root / (entry + '.py')) for entry in entrypoints],
             pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=sorted(set(hidden)), hookspath=[],
             runtime_hooks=['qt_streams_hook.py'], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
shared = [script for script in a.scripts if script[0] not in entrypoints]
executables = []
for entry, name, console in (
    ('qt_manager', 'EvolveModManagerQt', True),
    ('worker_entry', 'EvolveModWorker', True),
    ('qt_legacy_editor_bridge', 'EvolveModLegacyEditor', False),
):
    scripts = shared + [script for script in a.scripts if script[0] == entry]
    assert len(scripts) == len(shared) + 1
    executables.append(EXE(
        pyz, scripts, [], exclude_binaries=True, name=name,
        debug=False, strip=False, upx=False, console=console,
        icon=str(root/'assets'/'hunt.ico')))
coll = COLLECT(*executables, a.binaries, a.datas, strip=False,
               upx=False, name='EvolveModManagerQt')
