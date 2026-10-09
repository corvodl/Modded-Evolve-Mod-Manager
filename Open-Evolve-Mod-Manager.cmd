@echo off
setlocal
cd /d "%~dp0"
for /f "delims=" %%D in ('dir /b /ad /o-d "dist\EvolveModManager-*" 2^>nul') do (
  if exist "dist\%%D\EvolveModManager.exe" (
    start "" "%CD%\dist\%%D\EvolveModManager.exe"
    exit /b 0
  )
)
echo No built Windows manager was found.
echo Run Build-Windows-EXE.cmd first, then launch EvolveModManager.exe.
pause
exit /b 1
