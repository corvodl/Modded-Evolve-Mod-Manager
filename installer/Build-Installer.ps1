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
# The compiled application's screenshot smoke test creates a writable Data
# folder in this build directory. It is deliberately *never* an installer source.
# Inspect only whitelisted application resources, not the user's Data folder.
$allowed = @("EvolveModManager.exe","EvolveModWorker.exe","_internal",
             "Docs","START-HERE.txt","BUILD_COMMIT.txt","BUILD_CHANNEL.txt")
foreach ($file in @(Get-ChildItem -LiteralPath $build -Force)) {
  if ($file.Name -notin ($allowed + @("Data"))) {
    throw "Unexpected file at application build root: $($file.Name)"
  }
}
foreach ($folder in @("_internal","Docs")) {
  $source = Join-Path $build $folder
  if (!(Test-Path -LiteralPath $source -PathType Container)) { continue }
  $sensitive = Get-ChildItem -LiteralPath $source -Recurse -File -Force |
    Where-Object { $_.Name -match '(?i)\.pak$|^private_key\.pem$|\.customkey-' } |
    Select-Object -First 1
  if ($sensitive) { throw "Sensitive file found in installer source: $($sensitive.FullName)" }
}
if (Test-Path (Join-Path $build "Data")) {
  Write-Host "Skipping runtime-generated Data folder (not packaged)."
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
