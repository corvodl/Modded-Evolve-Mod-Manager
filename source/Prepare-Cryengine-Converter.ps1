# Bundle the upstream GPLv2 converter and its corresponding source.
# Used by all Windows release builds; abort if pinned binary is altered.
$ErrorActionPreference = 'Stop'
$release = 'v2.0.0'
$commit = '6ae79ce6aeb669b632ce9adee452a6ad99e1f883'
$digest = '75feca2813a6517cad9a8e5df28687860a3b0bab9dcbcfeef65e56850d53b0b7'
$root = Join-Path $PSScriptRoot 'third_party\Cryengine-Converter'
New-Item -ItemType Directory -Force -Path $root | Out-Null
$exe = Join-Path $root 'cgf-converter.exe'
$source = Join-Path $root 'Cryengine-Converter-source-v2.0.0.zip'
$binaryUrl = "https://github.com/Markemp/Cryengine-Converter/releases/download/$release/cgf-converter.exe"
$sourceUrl = "https://github.com/Markemp/Cryengine-Converter/archive/$commit.zip"

if (!(Test-Path -LiteralPath $exe) -or
        (Get-FileHash -LiteralPath $exe -Algorithm SHA256).Hash.ToLowerInvariant() -ne $digest) {
    $temporary = "$exe.downloading"
    try {
        Invoke-WebRequest -Uri $binaryUrl -OutFile $temporary -MaximumRedirection 8
        $actual = (Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actual -ne $digest) { throw "Pinned Cryengine-Converter SHA256 mismatch: $actual" }
        Move-Item -LiteralPath $temporary -Destination $exe -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
if (!(Test-Path -LiteralPath $source) -or (Get-Item $source).Length -lt 1024) {
    $temporary = "$source.downloading"
    try {
        Invoke-WebRequest -Uri $sourceUrl -OutFile $temporary -MaximumRedirection 8
        Add-Type -AssemblyName System.IO.Compression
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        $zip = [IO.Compression.ZipFile]::OpenRead($temporary)
        try {
            if (!$zip.Entries.Where({ $_.FullName -match '/LICENSE$' }).Count) {
                throw "Corresponding source archive is missing its GPLv2 LICENSE"
            }
        } finally { $zip.Dispose() }
        Move-Item -LiteralPath $temporary -Destination $source -Force
    } finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}
@"
Cryengine-Converter by Markemp and contributors
Version: $release
Upstream source revision: $commit
License: GNU GPL version 2 (see LICENSE inside source ZIP).
Unmodified upstream executable SHA256: $digest
Corresponding source is included in Cryengine-Converter-source-v2.0.0.zip.
Upstream project: https://github.com/Markemp/Cryengine-Converter

This converter is an independent command-line program. The Mod Manager runs
it as a separate executable and does not incorporate its source code.
The user can copy and redistribute the GPLv2 source under its terms.
"@ | Set-Content -LiteralPath (Join-Path $root 'NOTICE.txt') -Encoding UTF8
Write-Host 'Pinned Cryengine-Converter and corresponding source are ready.'
