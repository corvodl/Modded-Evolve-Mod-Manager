EVOLVE MOD MANAGER - PORTABLE APP
================================
Build with Build-Windows-EXE.cmd, then share dist\EvolveModManager-Windows.zip.
Each recipient extracts the whole folder and opens EvolveModManager.exe.
Click Set Up Manager, select EvolveGame and the normal Modded Evolve client EXE.
The manager generates the signing keys, inject-custom.dll and custom-signed PAKs
locally by reading the original game installation; original game files are not
changed by setup. Preparation requires significant free disk space (at least
2x the size of installed PAKs plus roughly 1 GiB for setup alone).
The release includes required executables/runtime libraries, not game PAKs.
Existing custom keys and projects must stay in the recipient's Data folder.
Do not send that Data folder in public releases.
Private/offline testing; normal online services may reject modified assets.

Texture editing (standalone DDS only):
- Unpack a PAK, open Edit Files, and select a .dds file.
- Export PNG saves the full-size top mip for editing in an image editor.
- To replace a texture, encode your edit as a DDS with matching format, dimensions, mip levels, and total file size; use Import DDS.
- Review Changes, Build Mod, and Add Mod to apply the new texture.
- PNG cannot be imported directly; .dds.N streaming texture parts are not supported.
