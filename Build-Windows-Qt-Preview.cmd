@echo off
setlocal
cd /d "%~dp0"
set "PACKAGE_ROOT=%CD%"
set "PIP_CACHE_DIR=%CD%\.build\pip-cache"
set "PYINSTALLER_CONFIG_DIR=%CD%\.build\qt-pyinstaller-cache"
echo Building EXPERIMENTAL PySide6 Evolve Mod Manager.
echo Stable Main release and user Data folders will not be touched.
call source\Setup-Environment.cmd
if errorlevel 1 goto failed
".build\venv\Scripts\python.exe" -m pip install -r "source\requirements-qt.txt"
if errorlevel 1 goto failed
".build\venv\Scripts\python.exe" source\build_brand_icons.py
if errorlevel 1 goto failed
set "TEMP=%CD%\.build\temp"
set "TMP=%CD%\.build\temp"
pushd source
"%PACKAGE_ROOT%\.build\venv\Scripts\python.exe" -u -m unittest -v test_qt_migration
if errorlevel 1 goto failed_source
"%PACKAGE_ROOT%\.build\venv\Scripts\python.exe" -m PyInstaller --noconfirm --clean --distpath "%PACKAGE_ROOT%\.build\qt-preview-dist" --workpath "%PACKAGE_ROOT%\.build\qt-preview-work" EvolveModManagerQt.spec
if errorlevel 1 goto failed_source
popd
rem Store the exact experimental source revision. Never mark this as Main.
if not defined GITHUB_SHA set "GITHUB_SHA=0000000000000000000000000000000000000000"
> ".build\qt-preview-dist\EvolveModManagerQt\BUILD_COMMIT.txt" echo %GITHUB_SHA%
> ".build\qt-preview-dist\EvolveModManagerQt\BUILD_CHANNEL.txt" echo qt-preview
".build\venv\Scripts\python.exe" source\qt_preview_package.py ".build\qt-preview-dist\EvolveModManagerQt" "dist"
if errorlevel 1 goto failed
echo.
echo SUCCESS: Experimental Qt ZIP in dist. Stable Main update channel unchanged.
if /i "%GITHUB_ACTIONS%"=="true" exit /b 0
pause
exit /b 0
:failed_source
popd
:failed
echo.
echo Qt PREVIEW BUILD FAILED. Stable Tk app remains unchanged.
if /i "%GITHUB_ACTIONS%"=="true" exit /b 1
pause
exit /b 1
