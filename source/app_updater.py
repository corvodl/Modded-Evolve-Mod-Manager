"""Verified, opt-in upgrades from the latest successfully published main build.

Never writes game files or manager-owned Data. Network discovery/download is
separate from the standalone Windows apply process (which waits for GUI exit).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import os
import shutil
import time
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile
import urllib.request
import urllib.parse
import uuid
import zipfile
from typing import Callable

REPO = 'corvodl/Modded-Evolve-Mod-Manager'
API = f'https://api.github.com/repos/{REPO}'
RELEASE_ASSET = 'EvolveModManager-Windows.zip'
REVISION_STAMP = 'BUILD_REVISION.txt'
UPDATE_WORK_DIR = 'UpdateWork'
MANIFEST_ASSET = 'update-manifest.json'
ALLOWED_TOP = frozenset({'EvolveModManager.exe', 'EvolveModWorker.exe', '_internal',
                         'Docs', 'START-HERE.txt', 'BUILD_COMMIT.txt', 'BUILD_CHANNEL.txt', REVISION_STAMP})
MAX_ZIP_BYTES = 300 * 1024 * 1024
MAX_EXPANDED_BYTES = 1_500 * 1024 * 1024
USER_AGENT = 'EvolveModManager-Updater/2.0'
CHANNELS = {'main':'Main', 'beta':'Beta'}
_VERSION = re.compile(r'^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?(?:-(alpha|beta|rc)\.(\d+))?(?:\s+-\s+.*)?$',re.I)


@dataclass(frozen=True)
class Update:
    commit: str
    version: str
    sha256: str
    url: str
    size: int
    channel: str = 'main'
    revision: int = 0
    release_type: str = 'milestone'


@dataclass(frozen=True)
class ExperimentalUpdate:
    """Browsable Qt test build; not valid input to the stable in-place installer."""
    commit: str
    run_id: int
    artifact_id: int
    url: str


def discover_experimental() -> ExperimentalUpdate | None:
    """Find latest successful, unexpired Qt experiment. Never auto-install it.

    Actions artifacts are not the stable app's ZIP format; users must explicitly
    opt in and extract the Qt preview into a separate directory.
    """
    runs = json.loads(_get(API + '/actions/workflows/qt-preview.yml/runs?'
                           'branch=qt-migration&status=success&per_page=8'))
    for run in runs.get('workflow_runs', []):
        run_id = run.get('id')
        commit = str(run.get('head_sha') or '').lower()
        if (run.get('head_branch') != 'qt-migration' or
                run.get('conclusion') != 'success' or
                not isinstance(run_id, int) or run_id <= 0 or
                len(commit) != 40 or any(c not in '0123456789abcdef' for c in commit)):
            continue
        artifact_data = json.loads(_get(API + f'/actions/runs/{run_id}/artifacts'))
        for artifact in artifact_data.get('artifacts', []):
            artifact_id = artifact.get('id')
            if (artifact.get('name') == 'EvolveModManagerQt-Preview-Windows' and
                    artifact.get('expired') is False and
                    isinstance(artifact_id, int) and artifact_id > 0 and
                    artifact.get('size_in_bytes', 0) > 0):
                return ExperimentalUpdate(
                    commit, run_id, artifact_id,
                    f'https://github.com/{REPO}/actions/runs/{run_id}')
    return None


def _get(url: str, *, max_bytes=1_000_000) -> bytes:
    if not url.startswith('https://') or not (url.startswith(API + '/') or any(url.startswith(f'https://github.com/{REPO}/releases/download/{tag}/') for tag in CHANNELS.values())):
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


def installed_revision(app_home: Path) -> int:
    """Old installs have revision zero."""
    try:
        value = (Path(app_home) / REVISION_STAMP).read_text(encoding='ascii').strip()
        if re.fullmatch(r'0|[1-9][0-9]{0,4}', value):
            return int(value)
    except (OSError, UnicodeError):
        pass
    return 0


def _revision_fields(manifest):
    if manifest.get('schema') == 1:
        if 'revision' in manifest or 'release_type' in manifest:
            raise ValueError('Invalid schema-1 revision fields.')
        return 0, 'milestone'
    if manifest.get('schema') != 2:
        raise ValueError('Unsupported update manifest schema.')
    revision, kind = manifest.get('revision'), manifest.get('release_type')
    if (type(revision) is not int or not 0 <= revision <= 99999 or
            kind not in ('milestone', 'patch') or
            (revision == 0) != (kind == 'milestone')):
        raise ValueError('Invalid update revision and release type.')
    return revision, kind


def _validated_asset_name(manifest):
    revision, kind = _revision_fields(manifest)
    if manifest.get('schema') == 1:
        if manifest.get('asset') != RELEASE_ASSET:
            raise ValueError('Legacy release asset mismatch.')
        return RELEASE_ASSET, 0, 'milestone'
    version = str(manifest.get('version', '')).split(' - ', 1)[0]
    version_key(version)
    asset = f'EvolveModManager-Windows-{version}-p{revision}.zip'
    installer = f'EvolveModManager-Setup-{version}-p{revision}.exe'
    if manifest.get('asset') != asset or manifest.get('installer') != installer:
        raise ValueError('Revision-specific asset mismatch.')
    return asset, revision, kind


def update_workspace(app_home: Path) -> Path:
    return Path(app_home).resolve() / UPDATE_WORK_DIR


def cleanup_completed_update_work(app_home: Path, *, max_age_seconds=86400) -> None:
    """Prune old completed transactions, but retain failed-rollback recovery.

    A detached updater may still be running in a young transaction. Only
    32-hex-named folders older than a full day without rollback are eligible.
    Other files and all user-owned Data are never inspected or removed.
    """
    work = update_workspace(app_home)
    if not work.is_dir() or work.is_symlink():
        return
    now = time.time()
    try:
        children = tuple(work.iterdir())
    except OSError:
        return
    for folder in children:
        if (not re.fullmatch(r'[0-9a-f]{32}', folder.name) or
                folder.is_symlink() or not folder.is_dir()):
            continue
        try:
            if (now - folder.stat().st_mtime <= max_age_seconds or
                    (folder / 'rollback').exists()):
                continue
            shutil.rmtree(folder)
        except OSError:
            pass


def installed_commit(app_home: Path) -> str | None:
    path = Path(app_home) / 'BUILD_COMMIT.txt'
    if not path.is_file(): return None
    value = path.read_text(encoding='ascii').strip().lower()
    return value if len(value) == 40 and all(c in '0123456789abcdef' for c in value) else None


def discover(current_commit: str | None = None) -> Update | None:
    """Stable-only compatibility discovery."""
    update = discover_channel('main')
    return update if update and update.commit != current_commit else None


def version_key(value: str):
    """Stable outranks same-number prerelease; new beta outranks older stable."""
    match=_VERSION.fullmatch(str(value).strip().removeprefix('v'))
    if match is None:
        raise ValueError('Invalid release version: '+str(value)[:80])
    major, minor, patch, hotfix, label, number=match.groups()
    stage={'alpha':0,'beta':1,'rc':2,None:3}[label.lower() if label else None]
    return (int(major),int(minor),int(patch),int(hotfix or 0),
            stage,int(number or 0))


def discover_channel(channel: str) -> Update | None:
    """Validate rolling channel manifest, checksum, version and revision."""
    if channel not in CHANNELS:
        raise ValueError('Invalid release channel.')
    branch = json.loads(_get(API + '/branches/' + channel))
    head = str(branch['commit']['sha']).lower()
    if not re.fullmatch(r'[0-9a-f]{40}', head):
        raise ValueError('Invalid channel HEAD.')
    release = json.loads(_get(API + '/releases/tags/' + CHANNELS[channel]))
    assets = {a['name']: a for a in release.get('assets', [])}
    if MANIFEST_ASSET not in assets:
        return None
    manifest = json.loads(_get(assets[MANIFEST_ASSET]['browser_download_url']))
    try:
        asset_name, revision, kind = _validated_asset_name(manifest)
        version = str(manifest.get('version', '')).strip()
        version_key(version)
    except (TypeError, ValueError):
        return None
    sha = str(manifest.get('sha256', '')).lower()
    asset = assets.get(asset_name)
    if not asset:
        return None
    if (str(manifest.get('commit', '')).lower() != head or
            not re.fullmatch(r'[0-9a-f]{64}', sha) or
            manifest.get('channel', 'main') != channel or
            (channel == 'beta' and not release.get('prerelease', False)) or
            not isinstance(asset.get('size'), int) or
            not 0 < asset['size'] <= MAX_ZIP_BYTES or
            manifest.get('bytes') != asset['size']):
        return None
    digest = asset.get('digest')
    if digest and str(digest).lower() != 'sha256:' + sha:
        return None
    prefix = f'https://github.com/{REPO}/releases/download/{CHANNELS[channel]}/'
    url = asset.get('browser_download_url', '')
    if url != prefix + asset_name:
        return None
    return Update(head, version[:140], sha, url, asset['size'],
                  channel, revision, kind)


def choose_latest(candidates, installed_version, installed_commit=None,
                  installed_revision=0):
    """Reject all version/revision downgrades; prefer stable for ties."""
    valid = [candidate for candidate in candidates if candidate is not None]
    if not valid:
        return None
    valid.sort(key=lambda x: (version_key(x.version), x.revision,
                              x.channel == 'main'), reverse=True)
    winner = valid[0]
    if installed_version:
        if ((version_key(winner.version), winner.revision) <=
                (version_key(installed_version), installed_revision)):
            return None
    elif winner.commit == installed_commit:
        return None
    return winner


def discover_latest(current_commit=None, current_version=None,
                    current_revision=0):
    """Query both channels and compare revision-aware release tuples."""
    candidates, errors = [], []
    for channel in CHANNELS:
        try:
            candidates.append(discover_channel(channel))
        except (ValueError, KeyError, OSError, urllib.error.URLError) as exc:
            errors.append((channel, str(exc)))
    if not any(candidates):
        if errors:
            raise RuntimeError('Cannot verify either release channel: ' +
                               '; '.join(name + ': ' + reason for name, reason in errors))
        return None
    return choose_latest(candidates, current_version, current_commit,
                         current_revision)


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


def download_and_prepare(update: Update, scratch: Path | None = None,
                         progress: Callable[[str, int, int], None] | None = None,
                         app_home: Path | None = None) -> Path:
    """Prepare only inside a standalone scratch directory; never modify the install."""
    if scratch is None:
        base = update_workspace(
            app_home if app_home is not None else
            Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) /
            'EvolveModManager')
        base.mkdir(parents=True, exist_ok=True)
        scratch = base / uuid.uuid4().hex
    scratch = Path(scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=False)
    archive_path = scratch / 'download.zip'
    h = hashlib.sha256()
    total = 0
    if progress: progress('Downloading', 0, update.size)
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
                if progress: progress('Downloading', total, update.size)
        if total != update.size or h.hexdigest() != update.sha256:
            raise ValueError('Downloaded ZIP does not match its published SHA-256.')
        extracted = scratch / 'extracted'
        extracted.mkdir()
        if progress: progress('Verifying', 0, 1)
        with zipfile.ZipFile(archive_path) as z:
            files = _validated_zip_members(z)
            expanded = sum(member.file_size for member in files)
            unpacked = 0
            if progress: progress('Extracting', 0, max(1, expanded))
            for member in files:
                target = extracted.joinpath(*PurePosixPath(member.filename).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with z.open(member) as inp, target.open('xb') as out:
                    for block in iter(lambda: inp.read(1024 * 1024), b''):
                        out.write(block)
                        unpacked += len(block)
                        if progress: progress('Extracting', unpacked, max(1, expanded))
        staged = extracted / 'EvolveModManager'
        if installed_commit(staged) != update.commit:
            raise ValueError('Downloaded build commit differs from the selected release.')
        if (staged/'BUILD_CHANNEL.txt').read_text(encoding='ascii').strip().lower()!=update.channel:
            raise ValueError('Downloaded build channel differs from the selected release.')
        if installed_revision(staged) != update.revision:
            raise ValueError('Downloaded revision differs from the selected release.')
        if (update.revision > 0 and
                not (staged / REVISION_STAMP).is_file()):
            raise ValueError('Patched update is missing its revision stamp.')
        if progress: progress('Ready', 1, 1)
        return staged
    except BaseException:
        # A failed download must not leave unnecessary update archives.
        import shutil
        shutil.rmtree(scratch, ignore_errors=True)
        raise



def report_startup_ready(app_home: Path, *, argv=None) -> None:
    """Acknowledge a responsive replacement GUI to the detached installer.

    Elevated Windows executables must be launched through ShellExecute/runas,
    which cannot inherit custom environment variables. Accept the one-time
    --update-ack command-line path as well as the legacy environment hint.
    Both are restricted to UpdateWork and cannot target user Data/game files.
    """
    cleanup_completed_update_work(app_home)
    args = tuple(sys.argv[1:] if argv is None else argv)
    hint = os.environ.pop('EVOLVE_MANAGER_UPDATE_ACK', '')
    if not hint and args.count('--update-ack') == 1:
        at = args.index('--update-ack')
        if at + 1 < len(args):
            hint = args[at + 1]
    if not hint:
        return
    try:
        scratch = update_workspace(app_home)
        path = Path(hint).resolve()
        if (not path.is_relative_to(scratch) or
                not path.name.startswith('startup-ok-') or path.suffix != '.txt'):
            return
        commit = installed_commit(app_home)
        if commit:
            path.write_text(commit + '\n', encoding='ascii')
    except (OSError, ValueError):
        # Acknowledgement failure is handled by the installer watchdog.
        pass


APPLY_PS1 = r'''param(
  [Parameter(Mandatory=$true)][string]$TargetDir,
  [Parameter(Mandatory=$true)][string]$SourceDir,
  [Parameter(Mandatory=$true)][int]$ManagerPid,
  [Parameter(Mandatory=$true)][string]$ExpectedCommit,
  [ValidateSet('main','beta')][string]$ExpectedChannel = 'main',
  [int]$ExpectedRevision = 0,
  [switch]$Headless
)
$ErrorActionPreference = 'Stop'
$Names = @('EvolveModManager.exe','EvolveModWorker.exe','_internal','Docs','START-HERE.txt','BUILD_COMMIT.txt','BUILD_CHANNEL.txt','BUILD_REVISION.txt')
$ScratchRoot = Split-Path -Parent (Split-Path -Parent $SourceDir)
$Backup = Join-Path $ScratchRoot 'rollback'
$Moved = New-Object System.Collections.Generic.List[string]
$Installed = New-Object System.Collections.Generic.List[string]
$script:NewManager = $null
$script:ProgressWindow = $null
$script:ProgressLabel = $null
$script:ProgressPercent = $null
$script:ProgressBar = $null
$script:UpdateStep = 'preflight'
function Set-UpdateProgress([string]$Stage, [int]$Value) {
  $Value = [Math]::Max(0,[Math]::Min(100,$Value))
  Write-Output ("UPDATE STAGE: " + $Stage + " [" + $Value + "%]")
  if ($null -eq $script:ProgressWindow) { return }
  try {
    $script:ProgressLabel.Text = $Stage
    $script:ProgressPercent.Text = "$Value%"
    $script:ProgressBar.Value = $Value
    [System.Windows.Forms.Application]::DoEvents()
  } catch {
    Write-Output ("Progress UI warning (installation continues): " + $_.Exception.Message)
    $script:ProgressWindow = $null
  }
}
if (-not $Headless) {
try {
  Add-Type -AssemblyName System.Windows.Forms
  Add-Type -AssemblyName System.Drawing
  $script:ProgressWindow = New-Object System.Windows.Forms.Form
  $script:ProgressWindow.Text = 'Evolve Mod Manager - Installing Update'
  $script:ProgressWindow.Size = New-Object System.Drawing.Size(510,162)
  $script:ProgressWindow.StartPosition = 'CenterScreen'
  $script:ProgressWindow.BackColor = [System.Drawing.Color]::FromArgb(11,11,13)
  $script:ProgressWindow.ForeColor = [System.Drawing.Color]::White
  $script:ProgressWindow.FormBorderStyle = 'FixedDialog'
  $script:ProgressWindow.MaximizeBox = $false
  $script:ProgressWindow.MinimizeBox = $false
  $script:ProgressWindow.ControlBox = $false
  $script:ProgressLabel = New-Object System.Windows.Forms.Label
  $script:ProgressLabel.Location = New-Object System.Drawing.Point(20,16)
  $script:ProgressLabel.Size = New-Object System.Drawing.Size(455,34)
  $script:ProgressLabel.Text = 'Waiting for Evolve Mod Manager to close...'
  $script:ProgressLabel.ForeColor = [System.Drawing.Color]::FromArgb(242,242,244)
  $script:ProgressWindow.Controls.Add($script:ProgressLabel)
  $script:ProgressBar = New-Object System.Windows.Forms.ProgressBar
  $script:ProgressBar.Location = New-Object System.Drawing.Point(20,65)
  $script:ProgressBar.Size = New-Object System.Drawing.Size(453,22)
  $script:ProgressBar.Minimum = 0
  $script:ProgressBar.Maximum = 100
  $script:ProgressBar.Style = 'Continuous'
  $script:ProgressWindow.Controls.Add($script:ProgressBar)
  $script:ProgressPercent = New-Object System.Windows.Forms.Label
  $script:ProgressPercent.Location = New-Object System.Drawing.Point(20,95)
  $script:ProgressPercent.Size = New-Object System.Drawing.Size(453,24)
  $script:ProgressPercent.Text = '0%'
  $script:ProgressPercent.ForeColor = [System.Drawing.Color]::FromArgb(244,85,105)
  $script:ProgressWindow.Controls.Add($script:ProgressPercent)
  $script:ProgressWindow.Show()
  [System.Windows.Forms.Application]::DoEvents()
} catch {
  Write-Output ("Progress window unavailable (installation continues): " + $_.Exception.Message)
  $script:ProgressWindow = $null
}
}
try {
  $proc = Get-Process -Id $ManagerPid -ErrorAction SilentlyContinue
  if ($null -ne $proc) { $null = $proc.WaitForExit(30000) }
  if (Get-Process -Id $ManagerPid -ErrorAction SilentlyContinue) { throw 'Manager did not close in 30 seconds.' }
  if (-not (Test-Path -LiteralPath (Join-Path $TargetDir 'EvolveModManager.exe') -PathType Leaf)) { throw 'Application folder is missing.' }
  foreach ($required in @('EvolveModManager.exe','EvolveModWorker.exe','_internal','BUILD_COMMIT.txt','BUILD_CHANNEL.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $SourceDir $required))) { throw "Missing update component: $required" }
  }
  if ((Get-Content -LiteralPath (Join-Path $SourceDir 'BUILD_COMMIT.txt') -Raw).Trim() -ne $ExpectedCommit) { throw 'Update commit mismatch.' }
  if ((Get-Content -LiteralPath (Join-Path $SourceDir 'BUILD_CHANNEL.txt') -Raw).Trim().ToLowerInvariant() -ne $ExpectedChannel) { throw 'Update build channel mismatch.' }
  $RevisionStamp = Join-Path $SourceDir 'BUILD_REVISION.txt'
  if (Test-Path -LiteralPath $RevisionStamp) {
    $FoundRevision = (Get-Content -LiteralPath $RevisionStamp -Raw).Trim()
  } else {
    $FoundRevision = '0'  # Legacy schema-1 installations.
  }
  if ($FoundRevision -ne [string]$ExpectedRevision) { throw 'Update build revision mismatch.' }
  Set-UpdateProgress 'Backing up current application...' 5
  $script:UpdateStep = 'creating application backup'
  try {
    New-Item -ItemType Directory -Path $Backup -ErrorAction Stop | Out-Null
  } catch {
    throw ("Unable to create application backup at " + $Backup + ": " + $_.Exception.Message)
  }
  Write-Output ("UPDATE BACKUP: " + $Backup)
  foreach ($name in $Names) {
    $original = Join-Path $TargetDir $name
    if (Test-Path -LiteralPath $original) {
      $script:UpdateStep = "backing up $name"
      $prior = Join-Path $Backup $name
      # An older manager or worker can retain a file handle after the GUI
      # closes. Do not erase it, or proceed with a mixed installation; wait a
      # short bounded period for Windows to release the original component.
      $BackedUp = $false
      for ($attempt = 0; $attempt -lt 6; $attempt++) {
        try {
          Move-Item -LiteralPath $original -Destination $prior -ErrorAction Stop
          $Moved.Add($name)
          $BackedUp = $true
          break
        } catch {
          $Reason = $_.Exception.Message
          if ((Test-Path -LiteralPath $prior) -or
              (-not (Test-Path -LiteralPath $original))) {
            throw ("Backup state is ambiguous for " + $name +
                   "; originals and partial backups were preserved: " + $Reason)
          }
          if ($attempt -eq 5) {
            throw ("Could not back up " + $name +
                   " (file may still be in use): " + $Reason)
          }
          Start-Sleep -Milliseconds 500
        }
      }
      if (-not $BackedUp) { throw ("Could not back up " + $name) }
    }
  }
  $script:UpdateStep = 'installing staged application'
  Set-UpdateProgress 'Installing verified files...' 15
  $Files = New-Object System.Collections.Generic.List[object]
  foreach ($name in $Names) {
    $from = Join-Path $SourceDir $name
    if (-not (Test-Path -LiteralPath $from)) { continue }
    $Installed.Add($name)
    if (Test-Path -LiteralPath $from -PathType Container) {
      foreach ($file in @(Get-ChildItem -LiteralPath $from -Recurse -File -Force)) {
        $relative = $file.FullName.Substring($SourceDir.TrimEnd('\').Length + 1)
        $Files.Add([pscustomobject]@{ Source=$file.FullName; Relative=$relative })
      }
    } else {
      $Files.Add([pscustomobject]@{ Source=$from; Relative=$name })
    }
  }
  $Count = [Math]::Max(1,$Files.Count)
  for ($i=0; $i -lt $Files.Count; $i++) {
    $file = $Files[$i]
    $targetFile = Join-Path $TargetDir $file.Relative
    $targetParent = Split-Path -Parent $targetFile
    if (-not (Test-Path -LiteralPath $targetParent)) { New-Item -ItemType Directory -Path $targetParent -Force -ErrorAction Stop | Out-Null }
    Copy-Item -LiteralPath $file.Source -Destination $targetFile -Force -ErrorAction Stop
    if ($i -eq 0 -or $i % 12 -eq 0 -or $i -eq $Files.Count - 1) {
      $percent = 15 + [int][Math]::Floor(75 * ($i + 1) / $Count)
      Set-UpdateProgress 'Installing application files...' $percent
    }
  }
  if ((Get-Content -LiteralPath (Join-Path $TargetDir 'BUILD_COMMIT.txt') -Raw).Trim() -ne $ExpectedCommit) { throw 'Installed commit mismatch.' }
  Set-UpdateProgress 'Launching and checking updated manager...' 95
  $AckPath = Join-Path $ScratchRoot ('startup-ok-' + [guid]::NewGuid().ToString('N') + '.txt')
  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = Join-Path $TargetDir 'EvolveModManager.exe'
  $info.WorkingDirectory = $TargetDir
  if ($Headless) {
    # CI's invalid-EXE rollback test must not trigger an interactive UAC prompt.
    $info.UseShellExecute = $false
    $info.EnvironmentVariables['EVOLVE_MANAGER_UPDATE_ACK'] = $AckPath
  } else {
    # Patch 2's EXE requires administrator privileges. CreateProcess with
    # UseShellExecute=false returns ERROR_ELEVATION_REQUIRED (740). The
    # standard shell "runas" verb shows Windows UAC instead. Its child cannot
    # inherit EnvironmentVariables, so pass the validated one-time ACK path
    # as an argument; the manager checks it is inside UpdateWork.
    $info.UseShellExecute = $true
    $info.Verb = 'runas'
    $info.Arguments = '--update-ack "' + $AckPath.Replace('"', '') + '"'
    Set-UpdateProgress 'Approve the Windows administrator prompt to finish updating...' 95
  }
  try {
    $script:NewManager = [System.Diagnostics.Process]::Start($info)
  } catch [System.ComponentModel.Win32Exception] {
    if ($_.Exception.NativeErrorCode -eq 1223) {
      throw 'Administrator approval was canceled. Restoring the previous manager.'
    }
    throw
  }
  if ($null -eq $script:NewManager) { throw 'Could not start updated manager.' }
  $timer = [System.Diagnostics.Stopwatch]::StartNew()
  $Acknowledged = $false
  # Existing installations can contain large Asset Library catalogs. A clean
  # CI screenshot starts quickly, but the first real GUI may take longer.
  # Keep the prior executable safely backed up until startup is acknowledged.
  while ($timer.Elapsed.TotalSeconds -lt 120) {
    if ($script:NewManager.HasExited) { throw "Updated manager exited during startup (code $($script:NewManager.ExitCode))." }
    if (Test-Path -LiteralPath $AckPath -PathType Leaf) {
      $AckValue = (Get-Content -LiteralPath $AckPath -Raw).Trim()
      if ($AckValue -eq $ExpectedCommit) { $Acknowledged = $true; break }
    }
    Start-Sleep -Milliseconds 250
  }
  if (-not $Acknowledged) { throw 'Updated manager did not confirm successful startup.' }
  Start-Sleep -Milliseconds 1000
  if ($script:NewManager.HasExited) { throw 'Updated manager quit immediately after startup.' }
  Set-UpdateProgress 'Update complete; manager reopened.' 100
  Write-Output "UPDATE SUCCESS: $ExpectedCommit"
  try { Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction Stop }
  catch { Write-Output ("Temporary rollback cleanup warning: " + $_.Exception.Message) }
  foreach ($path in @((Join-Path $ScratchRoot 'download.zip'), (Join-Path $ScratchRoot 'extracted'))) {
    if (Test-Path -LiteralPath $path) {
      try { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction Stop }
      catch { Write-Output ("Update scratch cleanup warning: " + $_.Exception.Message) }
    }
  }
} catch {
  $Failure = $_.Exception.Message
  Write-Output "UPDATE FAILED: $Failure"
  Write-Output ("UPDATE FAILURE STAGE: " + $script:UpdateStep)
  # PyInstaller's GUI log is preserved outside the app install and user Data.
  # Emit only the last lines into this private update.log to help diagnose
  # startup faults that do not display a Windows dialog.
  try {
    $StartupLog = Join-Path $TargetDir 'Data\Logs\startup.log'
    if (Test-Path -LiteralPath $StartupLog -PathType Leaf) {
      Write-Output "Manager startup diagnostics: $StartupLog"
      Get-Content -LiteralPath $StartupLog -Tail 40 -ErrorAction Stop | ForEach-Object { Write-Output ("STARTUP: " + $_) }
    } else {
      Write-Output "Manager startup diagnostics not found at: $StartupLog"
    }
  } catch {
    Write-Output ("Cannot read startup diagnostics: " + $_.Exception.Message)
  }
  Set-UpdateProgress 'Update failed; restoring previous version...' 0
  $RollbackErrors = New-Object System.Collections.Generic.List[string]
  if ($null -ne $script:NewManager) {
    try {
      if (-not $script:NewManager.HasExited) {
        $script:NewManager.Kill()
        $null = $script:NewManager.WaitForExit(10000)
        if (-not $script:NewManager.HasExited) { throw 'Updated manager did not exit during rollback.' }
      }
    } catch { $RollbackErrors.Add("Stopping updated manager: " + $_.Exception.Message) }
  }
  if ($RollbackErrors.Count -eq 0) {
    foreach ($name in $Installed) {
      $new = Join-Path $TargetDir $name
      try {
        if (Test-Path -LiteralPath $new) { Remove-Item -LiteralPath $new -Recurse -Force -ErrorAction Stop }
      } catch { $RollbackErrors.Add("Removing new " + $name + ": " + $_.Exception.Message) }
    }
    foreach ($name in $Moved) {
      $prior = Join-Path $Backup $name
      if (Test-Path -LiteralPath $prior) {
        try {
          $target = Join-Path $TargetDir $name
          if (Test-Path -LiteralPath $target) { throw "Destination still exists: $target" }
          Move-Item -LiteralPath $prior -Destination $target -ErrorAction Stop
        } catch { $RollbackErrors.Add("Restoring " + $name + ": " + $_.Exception.Message) }
      } else { $RollbackErrors.Add("Recovery file missing: $prior") }
    }
  }
  if ($RollbackErrors.Count -eq 0 -and (Test-Path -LiteralPath (Join-Path $TargetDir 'EvolveModManager.exe') -PathType Leaf)) {
    Write-Output 'ROLLBACK COMPLETE: Previous application files restored.'
    try {
      Start-Process -FilePath (Join-Path $TargetDir 'EvolveModManager.exe') -WorkingDirectory $TargetDir -ErrorAction Stop
      Write-Output 'Previous manager restart requested.'
    } catch { Write-Output ("Could not reopen restored manager: " + $_.Exception.Message) }
  } else {
    Write-Output ('ROLLBACK INCOMPLETE: ' + ($RollbackErrors -join ' | '))
  }
  if ($RollbackErrors.Count -eq 0) {
    try { Remove-Item -LiteralPath $Backup -Recurse -Force -ErrorAction Stop }
    catch { Write-Output ("Temporary rollback cleanup warning: " + $_.Exception.Message) }
  } else { Write-Output "Emergency recovery files (rollback failed): $Backup" }
  Write-Output "Update log: $TargetDir\Data\Logs\update.log"
  Write-Output 'The detached updater will not show a blocking error dialog. Details are in update.log.'
  Write-Output 'Original personal Data was not modified by the updater.'
  exit 1
} finally {
  if ($null -ne $script:ProgressWindow) {
    try { $script:ProgressWindow.Close(); $script:ProgressWindow.Dispose() }
    catch { Write-Output ("Progress cleanup warning: " + $_.Exception.Message) }
  }
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
    log = app_home / 'Data' / 'Logs' / 'update.log'
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open('wb') as output:
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
                          '-File', str(script), '-TargetDir', str(app_home), '-SourceDir', str(staged),
                          '-ManagerPid', str(manager_pid), '-ExpectedCommit', update.commit,
                          '-ExpectedChannel', update.channel, '-ExpectedRevision', str(update.revision)],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    return log
