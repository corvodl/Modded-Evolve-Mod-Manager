@echo off
setlocal
cd /d "%~dp0"
set "PACKAGE_ROOT=%CD%"
set "PIP_CACHE_DIR=%CD%\.build\pip-cache"
set "PYINSTALLER_CONFIG_DIR=%CD%\.build\pyinstaller-cache"
echo Building the portable Evolve Mod Manager...
echo Build computer needs Python 3.11 x64. The finished app includes Python.
call source\Setup-Environment.cmd
if errorlevel 1 goto failed
set "TEMP=%CD%\.build\temp"
set "TMP=%CD%\.build\temp"
pushd source
"%PACKAGE_ROOT%\.build\venv\Scripts\python.exe" -m unittest test_unified_integration test_packaging test_preserve test_editor_refresh test_portable_bundle test_first_run_setup test_ui_copy test_branding test_dds_texture test_asset_streaming_models test_theme test_editor_tabs_png test_dds0_selection test_resizable_pak_list
if errorlevel 1 goto failed_source
"%PACKAGE_ROOT%\.build\venv\Scripts\python.exe" Test-CryXML-Namespace-Fix.py
if errorlevel 1 goto failed_source
"%PACKAGE_ROOT%\.build\venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath "%PACKAGE_ROOT%\.build\app-dist" --workpath "%PACKAGE_ROOT%\.build\pyinstaller" EvolveModManager.spec
if errorlevel 1 goto failed_source
popd
rem Older builds had an editable UI sidecar. Do not accidentally release it.
if exist ".build\app-dist\EvolveModManager\ui_text.json" del /q ".build\app-dist\EvolveModManager\ui_text.json"
if exist ".build\app-dist\EvolveModManager\ui_text.json" goto failed
".build\app-dist\EvolveModManager\EvolveModWorker.exe" "%PACKAGE_ROOT%\source\packaging_smoke.py"
if errorlevel 1 goto failed
if not exist ".build\app-dist\EvolveModManager\Docs" mkdir ".build\app-dist\EvolveModManager\Docs"
copy /y "Docs\*.txt" ".build\app-dist\EvolveModManager\Docs\" >nul
if errorlevel 1 goto failed
copy /y "Docs\README-PORTABLE-APP.txt" ".build\app-dist\EvolveModManager\START-HERE.txt" >nul
if errorlevel 1 goto failed
".build\venv\Scripts\python.exe" source\publish_release.py ".build\app-dist\EvolveModManager" "dist"
if errorlevel 1 goto failed
echo.
echo SUCCESS: Share dist\EvolveModManager-Windows.zip
echo Each recipient creates their own PAK setup from their installed game.
echo Your existing initialized app folders have not been replaced.
if /i "%GITHUB_ACTIONS%"=="true" exit /b 0
explorer "dist"
pause
exit /b 0
:failed_source
popd
:failed
echo.
echo BUILD FAILED. Copy the error above; no completed app is claimed.
echo If twofish needs a compiler, install Microsoft C++ Build Tools with
 echo Desktop development with C++, then retry.
if /i "%GITHUB_ACTIONS%"=="true" exit /b 1
pause
exit /b 1
