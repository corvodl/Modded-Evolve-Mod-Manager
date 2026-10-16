# Stage GNU libgcrypt's maintained native bulk Twofish implementation for
# PyInstaller. Input packages come from signed MSYS2/pacman packages installed
# by GitHub Actions. Distribute the libraries with their actual LGPL licenses.
param(
    [string]$MingwRoot = "",
    [switch]$Require
)
$ErrorActionPreference = "Stop"
# setup-msys2@v2 installs under RUNNER_TEMP by default. Workflows pass its
# exact msys2-location output; local developers may use C:\msys64 instead.
if (-not $MingwRoot) {
    $root = if ($env:MSYS2_ROOT) { $env:MSYS2_ROOT } else { 'C:\msys64' }
    $MingwRoot = Join-Path $root 'mingw64'
}
$source = Join-Path $MingwRoot "bin"
$share = Join-Path $MingwRoot "share/licenses"
$dest = Join-Path $PSScriptRoot "native"
if (-not (Test-Path (Join-Path $source "libgcrypt-20.dll"))) {
    if ($Require) { throw "MSYS2 native libgcrypt-20.dll missing at $source" }
    Write-Host "No approved bulk library; source build retains reference backend."
    exit 0
}
New-Item -ItemType Directory -Force $dest | Out-Null
# These are native ABI runtime dependencies of libgcrypt/libgpg-error.
# Only stage known names, not arbitrary DLLs from PATH/current working dir.
$required = @("libgcrypt-20.dll", "libgpg-error-0.dll")
$optional = @("libintl-8.dll", "libiconv-2.dll", "libwinpthread-1.dll")
foreach ($name in $required) {
    $path = Join-Path $source $name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Native Twofish dependency missing: $path"
    }
    Copy-Item -LiteralPath $path -Destination (Join-Path $dest $name) -Force
}
foreach ($name in $optional) {
    $path = Join-Path $source $name
    if (Test-Path -LiteralPath $path) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $dest $name) -Force
    }
}
foreach ($library in @("libgcrypt", "libgpg-error")) {
    $licenseDir = Join-Path $share $library
    $licenseOut = Join-Path $dest ("Licenses/" + $library)
    foreach ($name in @("COPYING", "COPYING.LIB")) {
        $candidate = Join-Path $licenseDir $name
        if (-not (Test-Path -LiteralPath $candidate)) {
            throw "Required redistribution license missing: $candidate"
        }
        New-Item -ItemType Directory -Force $licenseOut | Out-Null
        Copy-Item -LiteralPath $candidate -Destination $licenseOut -Force
    }
}
# Extra runtime licenses are copied when present; the core LGPL notices
# above are mandatory. Keep exact origin URLs in the shipped notice.
foreach ($library in @("gettext", "libiconv", "winpthreads")) {
    $licenseDir = Join-Path $share $library
    if (-not (Test-Path -LiteralPath $licenseDir)) { continue }
    $licenseOut = Join-Path $dest ("Licenses/" + $library)
    New-Item -ItemType Directory -Force $licenseOut | Out-Null
    Get-ChildItem -LiteralPath $licenseDir -File | ForEach-Object {
        Copy-Item -LiteralPath $_.FullName -Destination $licenseOut -Force
    }
}
$notice = @"
Native Twofish acceleration uses GNU libgcrypt and libgpg-error.
Distribution: MSYS2 MINGW64 signed packages
  https://packages.msys2.org/packages/mingw-w64-x86_64-libgcrypt
  https://packages.msys2.org/packages/mingw-w64-x86_64-libgpg-error
License: see bundled Licenses/libgcrypt and Licenses/libgpg-error.
The DLLs may be replaced by compatible, separately installed LGPL binaries.
The original Python Twofish implementation remains the fallback.
"@
Set-Content -LiteralPath (Join-Path $dest "NOTICE.txt") -Value $notice -Encoding UTF8
Write-Host "Native bulk Twofish DLLs and their license notices staged."
