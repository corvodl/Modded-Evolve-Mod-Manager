@echo off
setlocal
cd /d "%~dp0.."
set "PIP_CACHE_DIR=%CD%\.build\pip-cache"
set "PYINSTALLER_CONFIG_DIR=%CD%\.build\pyinstaller-cache"
if not exist ".build\temp" mkdir ".build\temp"
set "TEMP=%CD%\.build\temp"
set "TMP=%CD%\.build\temp"
py -3.11 -c "import struct; assert struct.calcsize('P') == 8, 'Python 3.11 must be 64-bit'"
if errorlevel 1 exit /b 1
if not exist ".build\venv\Scripts\python.exe" py -3.11 -m venv .build\venv
if errorlevel 1 exit /b 1
rem A previous interrupted setup can leave python.exe present but pip missing.
".build\venv\Scripts\python.exe" -m pip --version >nul 2>&1
if errorlevel 1 (
 echo Repairing pip in the local build environment...
 ".build\venv\Scripts\python.exe" -m ensurepip --upgrade
 if errorlevel 1 goto pip_failed
)
".build\venv\Scripts\python.exe" -m pip --version
if errorlevel 1 goto pip_failed
".build\venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".build\venv\Scripts\python.exe" -m pip install -r source\requirements.txt "pyinstaller>=6.11,<7"
exit /b %errorlevel%

:pip_failed
echo.
echo Could not bootstrap pip in .build\venv.
echo Repair the Python 3.11 installation with pip/venv support, then retry.
echo Your game files, projects and signing keys were not changed.
exit /b 1
