# Explicitly preserve redistributable native crypto notices in PyInstaller's
# onedir _internal folder. This is defense in depth: the .spec also stages
# these as datas, but a packaging change must never silently omit licenses.
$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$source = Join-Path $PSScriptRoot 'native'
$bundle = Join-Path $root '.build/app-dist/EvolveModManager/_internal'
$destination = Join-Path $bundle 'Licenses/Native-Twofish'
$required = @(
    'NOTICE.txt',
    'Licenses/libgcrypt/COPYING',
    'Licenses/libgcrypt/COPYING.LIB',
    'Licenses/libgpg-error/COPYING',
    'Licenses/libgpg-error/COPYING.LIB'
)
foreach ($relative in $required) {
    $path = Join-Path $source $relative
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Approved Twofish redistribution notice absent at build time: $relative"
    }
}
if (!(Test-Path (Join-Path $bundle 'libgcrypt-20.dll')) -or
    !(Test-Path (Join-Path $bundle 'libgpg-error-0.dll'))) {
    throw 'Packaged native Twofish DLLs missing; cannot produce an accelerated release.'
}
New-Item -ItemType Directory -Path $destination -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $source 'NOTICE.txt') -Destination $destination -Force
Copy-Item -LiteralPath (Join-Path $source 'Licenses/*') -Destination $destination -Force -Recurse
foreach ($relative in $required) {
    $inBundle = if ($relative -eq 'NOTICE.txt') {
        Join-Path $destination 'NOTICE.txt'
    } else {
        Join-Path $destination ($relative.Substring('Licenses/'.Length))
    }
    if (!(Test-Path -LiteralPath $inBundle -PathType Leaf)) {
        throw "Native Twofish license could not be staged: $relative"
    }
}
Write-Host 'Packaged native crypto DLLs and complete redistribution license notices verified.'
