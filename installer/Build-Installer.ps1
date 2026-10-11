param(
  [string]$Compiler = "",
  [string]$Version = ""
)
$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$build = Join-Path $root ".build/app-dist/EvolveModManager"
foreach ($name in @("EvolveModManager.exe","EvolveModWorker.exe","BUILD_COMMIT.txt","BUILD_CHANNEL.txt","_internal")) {
  if (-not (Test-Path -LiteralPath (Join-Path $build $name))) {
    throw "Windows application has not been built: $name"
  }
}
if ((Test-Path (Join-Path $build "Data")) -or
    (Get-ChildItem -LiteralPath $build -Recurse -File | Where-Object {
      $_.Name -match '(?i)\.pak$|^private_key\.pem$|\.customkey-'
    })) {
  throw "Refusing to package game assets or personal Data."
}
if (-not $Version) {
  $Version = ((Get-Content -LiteralPath (Join-Path $root "source/VERSION.txt") -Raw).Trim() -split ' - ',2)[0]
}
if ($Version -notmatch '^\d+\.\d+\.\d+(?:\.\d+)?(?:-(?:alpha|beta|rc)\.\d+)?$') {
  throw "Invalid installer version."
}
$compilerPaths = @(
  $Compiler,
  (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6/ISCC.exe'),
  (Join-Path $env:ProgramFiles 'Inno Setup 6/ISCC.exe')
)
$command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
if ($command) { $compilerPaths += $command.Source }
$found = $compilerPaths | Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } | Select-Object -First 1
if (-not $found) { throw "Inno Setup 6 is required (ISCC.exe)." }
$env:EVOLVE_INSTALLER_VERSION = $Version
try {
  & $found (Join-Path $PSScriptRoot "EvolveModManager.iss")
  if ($LASTEXITCODE -ne 0) { throw "Inno Setup build failed with exit $LASTEXITCODE" }
} finally {
  Remove-Item Env:\EVOLVE_INSTALLER_VERSION -ErrorAction SilentlyContinue
}
$out = Join-Path $root "dist/EvolveModManager-Setup.exe"
if (-not (Test-Path -LiteralPath $out -PathType Leaf) -or (Get-Item -LiteralPath $out).Length -lt 1024*1024) {
  throw "Installer executable missing or abnormally small."
}
Write-Host "VERIFIED INSTALLER: $out"
