# Verified independent GPL binaries, private Java runtime, and corresponding sources.
# Executed only on Windows build machines. Never download in the installed app.
$ErrorActionPreference = 'Stop'
$root = Join-Path $PSScriptRoot 'third_party\FFDec'
$cache = Join-Path (Split-Path $PSScriptRoot -Parent) '.build\third-party-cache'
New-Item -ItemType Directory -Force -Path $root, $cache | Out-Null

$ffdecSha = '35f4930eb7c380afe66f2117f90b006deac0631473ad7500bb39c78f68645ecd'
$jreSha = 'b8aa18fef5edb69bee8618f99677d66d0873d22cb40d974c15ac9ffcdecf73ba'
$jreSourceSha = '4a7347be4f598178adffc9d91a468a857c1eaf95756837afb213bc9986032af9'
$ffdecCommit = 'b2ef2d3d3f44291a4bdfa899c986bc39a89fa6aa'
$ffdecZipUrl = 'https://github.com/jindrapetrik/jpexs-decompiler/releases/download/version26.3.0/ffdec_26.3.0.zip'
$jreZipUrl = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12%2B8/OpenJDK21U-jre_x64_windows_hotspot_21.0.12_8.zip'
$ffdecSourceUrl = "https://github.com/jindrapetrik/jpexs-decompiler/archive/$ffdecCommit.zip"
$jreSourceUrl = 'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12%2B8/OpenJDK21U-jdk-sources_21.0.12_8.tar.gz'

function Get-VerifiedArchive([string]$Url, [string]$Name, [string]$Sha256) {
  $path = Join-Path $cache $Name
  if (Test-Path -LiteralPath $path) {
    $actual = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -eq $Sha256) { return $path }
    Remove-Item -LiteralPath $path -Force
  }
  $tmp = "$path.downloading"
  try {
    Invoke-WebRequest -Uri $Url -OutFile $tmp -MaximumRedirection 8 -TimeoutSec 240
    $actual = (Get-FileHash -LiteralPath $tmp -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actual -ne $Sha256) { throw "Third-party SHA256 mismatch for $Name : $actual" }
    Move-Item -LiteralPath $tmp -Destination $path -Force
  } finally {
    Remove-Item -LiteralPath $tmp -Force -ErrorAction SilentlyContinue
  }
  return $path
}

$ffdecArchive = Get-VerifiedArchive $ffdecZipUrl 'ffdec_26.3.0.zip' $ffdecSha
$jreArchive = Get-VerifiedArchive $jreZipUrl 'OpenJDK21U-jre_21.0.12_8.zip' $jreSha
$sourceDir = Join-Path $root 'Sources'
New-Item -ItemType Directory -Force -Path $sourceDir | Out-Null
$jreSources = Join-Path $sourceDir 'OpenJDK21U-jdk-sources_21.0.12_8.tar.gz'
if (!(Test-Path -LiteralPath $jreSources) -or
    (Get-FileHash $jreSources -Algorithm SHA256).Hash.ToLowerInvariant() -ne $jreSourceSha) {
  $downloaded = Get-VerifiedArchive $jreSourceUrl 'OpenJDK21U-jdk-sources_21.0.12_8.tar.gz' $jreSourceSha
  Copy-Item -LiteralPath $downloaded -Destination $jreSources -Force
}
$ffdecSources = Join-Path $sourceDir 'FFDec-source-b2ef2d3.zip'
if (!(Test-Path -LiteralPath $ffdecSources) -or (Get-Item $ffdecSources).Length -lt 1024) {
  $tempSource = "$ffdecSources.downloading"
  try {
    Invoke-WebRequest -Uri $ffdecSourceUrl -OutFile $tempSource -MaximumRedirection 8 -TimeoutSec 240
    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $sourceZip = [IO.Compression.ZipFile]::OpenRead($tempSource)
    try {
      $license = @($sourceZip.Entries | Where-Object {
        $_.FullName -match '/(?i:license)(?:\.txt)?$'
      })
      if (!$license.Count) { throw 'Pinned FFDec source archive has no license file.' }
    } finally { $sourceZip.Dispose() }
    Move-Item -LiteralPath $tempSource -Destination $ffdecSources -Force
  } finally {
    Remove-Item -LiteralPath $tempSource -Force -ErrorAction SilentlyContinue
  }
}

$ready = Join-Path $root 'BUNDLED-READY.txt'
$ffdecJar = Join-Path $root 'ffdec.jar'
$runtime = Join-Path $root 'jre'
$javaExe = Join-Path $runtime 'bin\java.exe'
$javawExe = Join-Path $runtime 'bin\javaw.exe'
if (!(Test-Path -LiteralPath $ready) -or
    !(Test-Path -LiteralPath $ffdecJar) -or
    !(Test-Path -LiteralPath $javaExe) -or
    !(Test-Path -LiteralPath $javawExe)) {
  Remove-Item -LiteralPath $ready -Force -ErrorAction SilentlyContinue
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $ffdecScratch = Join-Path $cache 'ffdec-unpacked'
  $jreScratch = Join-Path $cache 'jre-unpacked'
  Remove-Item -LiteralPath $ffdecScratch, $jreScratch -Recurse -Force -ErrorAction SilentlyContinue
  [IO.Compression.ZipFile]::ExtractToDirectory($ffdecArchive, $ffdecScratch)
  [IO.Compression.ZipFile]::ExtractToDirectory($jreArchive, $jreScratch)
  $jar = Get-ChildItem -LiteralPath $ffdecScratch -Filter 'ffdec.jar' -File -Recurse |
    Select-Object -First 1
  if (!$jar) { throw 'FFDec release archive is missing ffdec.jar.' }
  $jarFolder = $jar.Directory.FullName
  Get-ChildItem -LiteralPath $jarFolder -Force | ForEach-Object {
    if ($_.Name -notin @('Sources','jre','NOTICE.txt','BUNDLED-READY.txt')) {
      Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $root $_.Name) -Recurse -Force
    }
  }
  $foundJava = Get-ChildItem -LiteralPath $jreScratch -Filter java.exe -File -Recurse |
    Where-Object { $_.Directory.Name -eq 'bin' } | Select-Object -First 1
  if (!$foundJava) { throw 'Verified Temurin runtime ZIP has no java.exe.' }
  $jreFolder = Split-Path $foundJava.Directory.FullName -Parent
  if (Test-Path -LiteralPath $runtime) { Remove-Item $runtime -Recurse -Force }
  Copy-Item -LiteralPath $jreFolder -Destination $runtime -Recurse -Force
  if (!(Test-Path -LiteralPath $ffdecJar) -or !(Test-Path -LiteralPath $javaExe) -or
      !(Test-Path -LiteralPath $javawExe)) {
    throw 'FFDec or private Java runtime was not prepared correctly.'
  }
  Remove-Item -LiteralPath $ffdecScratch, $jreScratch -Recurse -Force -ErrorAction SilentlyContinue
}
@"
Independent third-party programs bundled with Evolve Mod Manager.

FFDec 26.3.0: Copyright JPEXS contributors; GNU GPLv3-or-later.
Original binary: $ffdecZipUrl
Original binary SHA256: $ffdecSha
Source tag: version26.3.0 at commit $ffdecCommit
Complete upstream source: Sources/FFDec-source-b2ef2d3.zip
Upstream license: license.txt, see also the included upstream library notices.
Eclipse Temurin OpenJDK Runtime 21.0.12+8: GPLv2 + Classpath Exception
and other components under their respective licenses.
Original Windows JRE: $jreZipUrl
Original Windows JRE SHA256: $jreSha
Complete OpenJDK corresponding source: Sources/OpenJDK21U-jdk-sources_21.0.12_8.tar.gz
Source SHA256: $jreSourceSha
Runtime third-party notices: jre/legal/ and jre/release.
Eclipse project: https://adoptium.net/

Both components are unmodified stand-alone third-party programs. The manager
invokes them as external processes. Their respective open-source terms apply.
No Evolve game data or private signing material is bundled.
"@ | Set-Content -LiteralPath (Join-Path $root 'NOTICE.txt') -Encoding UTF8
if (!(Test-Path -LiteralPath (Join-Path $root 'license.txt')) -and
    !(Test-Path -LiteralPath (Join-Path $root 'LICENSE'))) {
  throw 'FFDec GPL-3.0 license is not present in downloaded release.'
}
if (!(Test-Path -LiteralPath $runtime 'legal')) {
  throw 'Temurin runtime legal notices are missing.'
}
@"
FFDec=26.3.0
FFDecSHA256=$ffdecSha
Java=21.0.12+8
JavaSHA256=$jreSha
"@ | Set-Content -LiteralPath $ready -Encoding ASCII
Write-Host 'Verified FFDec, corresponding GPL source and private Java runtime are bundled.'
