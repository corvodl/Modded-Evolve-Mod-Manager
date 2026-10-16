# Native Qt production candidate, built only after full Windows regression suite.
from pathlib import Path
from importlib.util import find_spec
from PyInstaller.utils.hooks import collect_all
import ast

root = Path(SPECPATH)
datas = [(str(p), '.') for p in root.glob('*.py')
         if not p.name.startswith(('test_', 'Test-', 'build_'))]
# The converter is redistributed as an independent GPLv2 executable,
# together with complete corresponding upstream source and license.
third_party = root / 'third_party' / 'Cryengine-Converter'
for name in ('cgf-converter.exe', 'Cryengine-Converter-source-v2.0.0.zip', 'NOTICE.txt'):
    asset = third_party / name
    if not asset.is_file():
        raise FileNotFoundError('Bundled Cryengine-Converter missing: ' + str(asset))
    datas.append((str(asset), 'third_party/Cryengine-Converter'))
datas += [(str(root/'launcher_support'), 'launcher_support'),
          (str(root/'assets'), 'assets'),
          (str(root/'reference'), 'reference'),
          (str(root/'ui_text.json'), '.'),
          (str(root/'VERSION.txt'), '.')]
binaries = []
# Optional for local source builds; mandatory for CI stable/installer builds.
# Keep LGPL licenses inside the same shipped app as their native DLLs.
import os
bulk_root = root / 'native'
bulk_dll = bulk_root / 'libgcrypt-20.dll'
if os.environ.get('EVOLVE_REQUIRE_NATIVE_TWOFISH') == '1' and not bulk_dll.is_file():
    raise FileNotFoundError('Required packaged native Twofish runtime missing')
if bulk_dll.is_file():
    for dll in sorted(bulk_root.glob('*.dll')):
        binaries.append((str(dll), '.'))
    for notice in sorted(bulk_root.glob('NOTICE.txt')):
        datas.append((str(notice), 'Licenses/Native-Twofish'))
    for license_file in sorted((bulk_root / 'Licenses').rglob('*')):
        if license_file.is_file():
            target = 'Licenses/Native-Twofish/' + license_file.relative_to(bulk_root / 'Licenses').parent.as_posix()
            datas.append((str(license_file), target))
hidden = [
    'qt_media_preview', 'PySide6.QtMultimedia', 'PySide6.QtMultimediaWidgets',
    'client_profile_details', 'qt_account_profile',
    'level_blender_export', 'blender_native_project', 'blender_texture_folder',
    'level_terrain_decode', 'level_dependency_loader', 'qt_level_scene_render',
    'database_animation', 'texture_selection', 'texture_transform', 'qt_texture_selection',
    'evolve_pak_workspace', 'evolve_pak_rekey', 'evolve_gameplay_editor',
    'evolve_video_pak_tool_fixed', 'dds_texture', 'dds_png_import',
    'dds_streaming', 'model_asset', 'model_preview', 'gpu_model_preview',
    'material_preview', 'model_material_resolve', 'multi_pak_assets', 'decode_cryxml',
    'universal_stage', 'refresh_originals', 'workspace_editor',
    'workspace_file_actions', 'pak_browser', 'portable_bundle',
    'first_run_setup', 'old_prepared', 'loose_game_files',
    'loose_file_editor', 'qt_manager', 'qt_workspace_editor', 'qt_easy_mod_installer', 'qt_quick_commands', 'easy_mod_install', 'game_storage', 'qt_game_storage', 'easy_mod_bundle', 'mod_install_receipts',
    'qt_asset_library', 'qt_asset_preview', 'asset_library_preview', 'legacy_pak_workspace', 'legacy_texture_preview', 'qt_legacy_pak_editor', 'legacy_pak_import', 'gfx_preview_backend', 'qt_gfx_preview', 'gfx_edit_backend', 'qt_gfx_editor', 'animation_converter_bridge', 'converted_motion', 'character_aliases',
    'qt_gpu_viewer', 'qt_asset_studio', 'studio_tools', 'qt_asset_intelligence', 'asset_intelligence', 'qt_workflow_lab', 'workflow_lab', 'qt_task_queue', 'asset_geometry_cache', 'operation_progress', 'qt_operation_progress', 'qt_background_exports', 'material_editor_v2', 'qt_developer_lab', 'developer_runtime_lab', 'developer_network_lab', 'developer_private_services', 'developer_support_lab', 'developer_write_policy', 'project_lifecycle', 'project_selection', 'qt_theme_116', 'quality_lab_116', 'studio_polish_117', 'renderer_3', 'developer_diagnostics_116', 'qt_font_health', 'desktop_shortcut', 'orphaned_swap_recovery',
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

entrypoints = ('qt_manager', 'worker_entry')
a = Analysis([str(root / (entry + '.py')) for entry in entrypoints],
             pathex=[str(root)], binaries=binaries, datas=datas,
             hiddenimports=sorted(set(hidden)), hookspath=[],
             runtime_hooks=['qt_streams_hook.py'], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
shared = [script for script in a.scripts if script[0] not in entrypoints]
executables = []
for entry, name, console in (
    ('qt_manager', 'EvolveModManager', False),
    ('worker_entry', 'EvolveModWorker', True),
):
    scripts = shared + [script for script in a.scripts if script[0] == entry]
    assert len(scripts) == len(shared) + 1
    executables.append(EXE(
        pyz, scripts, [], exclude_binaries=True, name=name,
        debug=False, strip=False, upx=False, console=console,
        icon=str(root/'assets'/'hunt.ico')))
coll = COLLECT(*executables, a.binaries, a.datas, strip=False,
               upx=False, name='EvolveModManager')
