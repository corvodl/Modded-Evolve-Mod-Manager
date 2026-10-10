"""Verified, opt-in upgrades from the latest successfully published main build.

Never writes game files or manager-owned Data. Network discovery/download is
separate from the standalone Windows apply process (which waits for GUI exit).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse
import uuid
import zipfile

REPO = 'corvodl/Modded-Evolve-Mod-Manager'
API = f'https://api.github.com/repos/{REPO}'
RELEASE_ASSET = 'EvolveModManager-Windows.zip'
MANIFEST_ASSET = 'update-manifest.json'
ALLOWED_TOP = frozenset({'EvolveModManager.exe', 'EvolveModWorker.exe', '_internal',
                         'Docs', 'START-HERE.txt', 'BUILD_COMMIT.txt', 'BUILD_CHANNEL.txt'})
MAX_ZIP_BYTES = 300 * 1024 * 1024
MAX_EXPANDED_BYTES = 1_500 * 1024 * 1024
USER_AGENT = 'EvolveModManager-Updater/1.0'


@dataclass(frozen=True)
class Update:
    commit: str
    version: str
    sha256: str
    url: str
    size: int


def _get(url: str, *, max_bytes=1_000_000) -> bytes:
    if not url.startswith('https://') or not (url.startswith(API + '/') or url.startswith(f'https://github.com/{REPO}/releases/download/Main/')):
        raise ValueError('Update URL is not the official project endpoint.')
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'})
    # Disallow redirect destinations outside github.com / api.github.com.
    with urllib.request.urlopen(req, timeout=15) as response:
        host = urllib.parse.urlparse(response.geturl()).hostname
        if host not in ('github.com', 'api.github.com', 'release-assets.githubusercontent.com',
                        'objects.githubusercontent.com'):
            raise ValueError('Unexpected update download host.')
        data = response.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError('Update response exceeds size limit.')
    return data


def installed_commit(app_home: Path) -> str | None:
    path = Path(app_home) / 'BUILD_COMMIT.txt'
    if not path.is_file(): return None
    value = path.read_text(encoding='ascii').strip().lower()
    return value if len(value) == 40 and all(c in '0123456789abcdef' for c in value) else None


def discover(current_commit: str | None = None) -> Update | None:
    """Only offer an update when the main HEAD and published release are identical."""
    head = json.loads(_get(API + '/branches/main'))['commit']['sha'].lower()
    if current_commit == head: return None
    release = json.loads(_get(API + '/releases/tags/Main'))
    assets = {a['name']: a for a in release.get('assets', [])}
    if MANIFEST_ASSET not in assets or RELEASE_ASSET not in assets:
        return None  # CI may still be building the latest commit.
    manifest = json.loads(_get(assets[MANIFEST_ASSET]['browser_download_url']))
    sha = manifest.get('sha256', '').lower()
    if (manifest.get('schema') != 1 or manifest.get('commit', '').lower() != head or
            manifest.get('asset') != RELEASE_ASSET or
            len(sha) != 64 or any(ch not in '0123456789abcdef' for ch in sha)):
        return None
    asset = assets[RELEASE_ASSET]
    if (asset.get('size', 0) <= 0 or asset['size'] > MAX_ZIP_BYTES or
            manifest.get('bytes') != asset['size']):
        return None
    digest = asset.get('digest')
    if digest and digest.lower() != 'sha256:' + sha:
        return None
    return Update(head, str(manifest.get('version', 'Updated build')).strip()[:140],
                  sha, asset['browser_download_url'], asset['size'])


def _validated_zip_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    files = []
    seen = set()
    expanded = 0
    for item in archive.infolist():
        name = item.filename
        if '\\' in name or name.startswith('/') or '\x00' in name or ':' in name:
            raise ValueError('Invalid path inside update ZIP.')
        segments = PurePosixPath(name).parts
        if (len(segments) < 2 or segments[0] != 'EvolveModManager' or '..' in segments or '.' in segments
                or any(part.rstrip('. ') != part for part in segments)):
            raise ValueError('Unexpected file location in update ZIP.')
        if segments[1] not in ALLOWED_TOP:
            raise ValueError('Updater refuses game files, personal keys or unknown paths.')
        if (item.external_attr >> 16) & 0o170000 == 0o120000:
            raise ValueError('Links are not allowed in app updates.')
        if item.is_dir(): continue
        low = name.casefold()
        if low in seen or name.lower().endswith('.pak') or name.lower().endswith('private_key.pem'):
            raise ValueError('Duplicate or prohibited update file.')
        seen.add(low)
        expanded += item.file_size
        if item.file_size > MAX_EXPANDED_BYTES or expanded > MAX_EXPANDED_BYTES:
            raise ValueError('Update ZIP is unreasonably large.')
        files.append(item)
    required = {'EvolveModManager/EvolveModManager.exe', 'EvolveModManager/EvolveModWorker.exe',
                'EvolveModManager/BUILD_COMMIT.txt', 'EvolveModManager/BUILD_CHANNEL.txt', 'EvolveModManager/START-HERE.txt'}
    if not required.issubset({x.filename for x in files}):
        raise ValueError('Update ZIP lacks required app files.')
    if not any(x.filename.startswith('EvolveModManager/_internal/') for x in files):
        raise ValueError('Update ZIP lacks the bundled runtime.')
    return files


def download_and_prepare(update: Update, scratch: Path | None = None) -> Path:
    """Prepare only inside a standalone scratch directory; never modify the install."""
    if scratch is None:
        base = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'EvolveModManagerUpdates'
        scratch = base / uuid.uuid4().hex
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    archive_path = scratch / 'download.zip'
    h = hashlib.sha256()
    total = 0
    req = urllib.request.Request(update.url, headers={'User-Agent': USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=45) as stream, archive_path.open('xb') as output:
            if urllib.parse.urlparse(stream.geturl()).hostname not in ('github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'):
                raise ValueError('Unexpected ZIP download host.')
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                total += len(block)
                if total > MAX_ZIP_BYTES or total > update.size:
                    raise ValueError('Downloaded ZIP exceeds expected size.')
                h.update(block); output.write(block)
        if total != update.size or h.hexdigest() != update.sha256:
            raise ValueError('Downloaded ZIP does not match its published SHA-256.')
        extracted = scratch / 'extracted'
        extracted.mkdir()
        with zipfile.ZipFile(archive_path) as z:
            files = _validated_zip_members(z)
            for member in files:
                target = extracted.joinpath(*PurePosixPath(member.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as inp, target.open('xb') as out:
                    import shutil
                    shutil.copyfileobj(inp, out, 1024 * 1024)
        staged = extracted / 'EvolveModManager'
        if installed_commit(staged) != update.commit:
            raise ValueError('Build commit in downloaded application does not match GitHub main.')
        return staged
    except BaseException:
        # Retain scratch as evidence for diagnosis, never touch installed data.
        raise


APPLY_PS1 = r'''param(
  [Parameter(Mandatory=$true)][string]$TargetDir,
  [Parameter(Mandatory=$true)][string]$SourceDir,
  [Parameter(Mandatory=$true)][int]$ManagerPid,
  [Parameter(Mandatory=$true)][string]$ExpectedCommit
)
$ErrorActionPreference = 'Stop'
$Names = @('EvolveModManager.exe','EvolveModWorker.exe','_internal','Docs','START-HERE.txt','BUILD_COMMIT.txt','BUILD_CHANNEL.txt')
$Backup = Join-Path $TargetDir ('.update-backup-' + [guid]::NewGuid().ToString('N'))
$Moved = New-Object System.Collections.Generic.List[string]
$Installed = New-Object System.Collections.Generic.List[string]
try {
  $proc = Get-Process -Id $ManagerPid -ErrorAction SilentlyContinue
  if ($null -ne $proc) { $null = $proc.WaitForExit(30000) }
  if (Get-Process -Id $ManagerPid -ErrorAction SilentlyContinue) { throw 'Manager did not close in 30 seconds.' }
  if (-not (Test-Path -LiteralPath (Join-Path $TargetDir 'EvolveModManager.exe') -PathType Leaf)) { throw 'Application folder is missing.' }
  if ((Get-Content -LiteralPath (Join-Path $SourceDir 'BUILD_COMMIT.txt') -Raw).Trim() -ne $ExpectedCommit) { throw 'Update commit mismatch.' }
  New-Item -ItemType Directory -Path $Backup -ErrorAction Stop | Out-Null
  foreach ($name in $Names) {
    $original = Join-Path $TargetDir $name
    if (Test-Path -LiteralPath $original) {
      Move-Item -LiteralPath $original -Destination (Join-Path $Backup $name) -ErrorAction Stop
      $Moved.Add($name)
    }
  }
  foreach ($name in $Names) {
    $from = Join-Path $SourceDir $name
    if (Test-Path -LiteralPath $from) {
      Copy-Item -LiteralPath $from -Destination (Join-Path $TargetDir $name) -Recurse -Force -ErrorAction Stop
      $Installed.Add($name)
    }
  }
  Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction Stop
  Start-Process -FilePath (Join-Path $TargetDir 'EvolveModManager.exe') -WorkingDirectory $TargetDir
  Write-Output "Update successful: $ExpectedCommit"
} catch {
  Write-Output "Update failed: $_"
  foreach ($name in $Installed) { Remove-Item -LiteralPath (Join-Path $TargetDir $name) -Recurse -Force -ErrorAction SilentlyContinue }
  foreach ($name in $Moved) {
    $prior = Join-Path $Backup $name
    if (Test-Path -LiteralPath $prior) {
      Move-Item -LiteralPath $prior -Destination (Join-Path $TargetDir $name) -ErrorAction SilentlyContinue
    }
  }
  Write-Output "Previous files restored where possible. Recovery backup (if present): $Backup"
  exit 1
}
'''


def launch_apply(update: Update, staged: Path, app_home: Path, manager_pid: int) -> Path:
    """Start Windows-owned detached updater; GUI must close immediately after."""
    if os.name != 'nt':
        raise OSError('Automatic install is only available on Windows.')
    app_home = Path(app_home).resolve()
    if not (app_home / 'EvolveModManager.exe').is_file():
        raise ValueError('Automatic install requires the packaged EXE.')
    scratch = staged.parent.parent
    script = scratch / 'apply.ps1'
    script.write_text(APPLY_PS1, encoding='utf-8-sig')
    log = scratch / 'update.log'
    with log.open('wb') as output:
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                          '-File', str(script), '-TargetDir', str(app_home), '-SourceDir', str(staged),
                          '-ManagerPid', str(manager_pid), '-ExpectedCommit', update.commit],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                         creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    return log
