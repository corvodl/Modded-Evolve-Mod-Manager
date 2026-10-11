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
import model_blender_export
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
assert rsa.generate_private_key(public_exponent=65537, key_size=2048).key_size == 2048
assert Image and ImageTk and dds_texture.parse_dds and dds_png_import.encode_png_as_dds and dds_streaming.inspect_stream and model_asset.inspect_model and model_preview.read_preview_mesh
assert frida.__version__ and numpy.__version__ and material_preview.render_textured_mesh
assert gpu_model_preview.prepare_geometry and gpu_model_preview.Win32GPUPreview
assert model_blender_export.glb_bytes and model_blender_export.convert_folder
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
