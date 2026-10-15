"""Dependency smoke check run by the bundled worker after building."""
from twofish import Twofish
from cryptography.hazmat.primitives.asymmetric import rsa
import frida
import evolve_pak_workspace
import refresh_originals
import portable_bundle
import first_run_setup
import old_prepared
import workspace_editor
import modern_surfaces
import desktop_shortcut
import dds_texture
import dds_png_import
import dds_streaming
import model_asset
import model_preview
import gfx_preview_backend
import legacy_pak_workspace
import legacy_texture_preview
import qt_legacy_pak_editor
import qt_gfx_preview
import gfx_edit_backend
import qt_gfx_editor
import model_blender_export
import model_textured_export
import model_rigging
import model_animation
import animation_converter_bridge
import qt_cpu_model_viewer
import gpu_model_preview
import material_preview
import numpy
import multi_pak_assets
from PIL import Image, ImageTk
from app_runtime import BUNDLE
assert (BUNDLE/"assets"/"hunt.ico").is_file()
assert (BUNDLE/"assets"/"hunt.png").is_file()
assert (BUNDLE/"VERSION.txt").is_file()
from version_info import app_version
from release_notes import RELEASE_NOTES
assert app_version() == "v" + RELEASE_NOTES[0][0]
assert (BUNDLE/"reference"/"RSAKeyData.bin").is_file()
assert (BUNDLE/"reference"/"inject.dll").is_file()
assert (BUNDLE/"reference"/"inject.dll").read_bytes()[0x870:0x8fc] == (BUNDLE/"reference"/"RSAKeyData.bin").read_bytes()
assert Twofish(bytes(16)).decrypt(Twofish(bytes(16)).encrypt(bytes(16))) == bytes(16)
# Test actual packaged DLL loading, not only source-tree crypto fallback.
import os
from evolve_video_pak_tool_fixed import Twofish as CryTwofish
cipher = CryTwofish(bytes(16))
try:
    assert cipher.encrypt_blocks(bytes(16)).hex() == '9f589f5cf6122c32b6bfec2f2ae8c35a'
    if os.environ.get('EVOLVE_REQUIRE_NATIVE_TWOFISH') == '1':
        assert cipher.bulk is not None, 'Native bulk Twofish not loaded in packaged worker'
        license_root = BUNDLE / 'Licenses' / 'Native-Twofish'
        assert (license_root / 'NOTICE.txt').is_file(), 'Native crypto notice missing'
        for provider in ('libgcrypt', 'libgpg-error'):
            for notice in ('COPYING', 'COPYING.LIB'):
                assert (license_root / provider / notice).is_file(), (
                    'Missing LGPL/GPL redistribution notice: ' + provider + '/' + notice)
finally:
    cipher.close()
assert rsa.generate_private_key(public_exponent=65537, key_size=2048).key_size == 2048
assert Image and ImageTk and dds_texture.parse_dds and dds_png_import.encode_png_as_dds and dds_streaming.inspect_stream and model_asset.inspect_model and model_preview.read_preview_mesh
assert frida.__version__ and numpy.__version__ and material_preview.render_textured_mesh
assert gpu_model_preview.prepare_geometry and gpu_model_preview.Win32GPUPreview
assert gfx_preview_backend.inspect_gfx and gfx_preview_backend.render_gfx
assert legacy_pak_workspace.create_project and legacy_pak_workspace.rebuild_pak
assert legacy_texture_preview.decode_legacy_texture and qt_legacy_pak_editor.LegacyPAKEditor
assert qt_gfx_preview.GfxPreviewWidget
assert gfx_edit_backend.create_gfx_session and gfx_edit_backend.apply_gfx_session
assert qt_gfx_editor.GfxEditorDialog
assert model_blender_export.glb_bytes and model_blender_export.convert_folder
assert model_textured_export.textured_model_from_workspace
assert model_rigging.skin_glb and model_rigging.parse_compiled_bones
assert model_animation.AnimatedGLB and animation_converter_bridge.validate_conversion
print('Bundled crypto, Twofish, Frida, GPU preview, Pillow DDS and PAK workspace imports passed.')

# File context-menu helpers must be bundled for all three editor tabs.
import workspace_file_actions
assert workspace_file_actions.checked_path
assert modern_surfaces.RoundedPanel
assert desktop_shortcut.create_desktop_shortcut

import app_updater
assert app_updater.discover and app_updater.download_and_prepare and app_updater.launch_apply

import pak_browser
assert pak_browser.import_target('libs.pak', ['Game/libs.pak']) == 'Game/libs.pak'

import loose_game_files, loose_file_editor
assert loose_game_files.scan_loose_files and loose_file_editor.LooseFileEditor

from qt_manager import QtManager, SECTIONS
from qt_workspace_editor import QtWorkspaceEditor, QtLooseFileEditor
from qt_gpu_viewer import QtGPUModelDialog, QtGLModelCanvas
from qt_font_health import ensure_readable_ui_font, glyphs_readable
from OpenGL import GL
assert QtManager and QtWorkspaceEditor and QtLooseFileEditor and QtGPUModelDialog and QtGLModelCanvas and GL
print('Bundled native Qt Widgets and OpenGL model rendering imports passed.')
