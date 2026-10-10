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
from typing import Callable

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


def download_and_prepare(update: Update, scratch: Path | None = None,
                         progress: Callable[[str, int, int], None] | None = None) -> Path:
    """Prepare only inside a standalone scratch directory; never modify the install."""
    if scratch is None:
        base = Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) / 'EvolveModManagerUpdates'
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
            raise ValueError('Build commit in downloaded application does not match GitHub main.')
        if progress: progress('Ready', 1, 1)
        return staged
    except BaseException:
        # Retain scratch as evidence for diagnosis, never touch installed data.
        raise



def report_startup_ready(app_home: Path) -> None:
    """Acknowledge a responsive replacement GUI to the detached installer.

    The path must reside in the updater's scratch area, never the game or
    user-owned Data directory. A missing or invalid hint is ignored.
    """
    hint = os.environ.pop('EVOLVE_MANAGER_UPDATE_ACK', '')
    if not hint:
        return
    try:
        scratch = (Path(os.environ.get('LOCALAPPDATA') or tempfile.gettempdir()) /
                   'EvolveModManagerUpdates').resolve()
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
  [switch]$Headless
)
$ErrorActionPreference = 'Stop'
$Names = @('EvolveModManager.exe','EvolveModWorker.exe','_internal','Docs','START-HERE.txt','BUILD_COMMIT.txt','BUILD_CHANNEL.txt')
$Backup = Join-Path $TargetDir ('.update-backup-' + [guid]::NewGuid().ToString('N'))
$Moved = New-Object System.Collections.Generic.List[string]
$Installed = New-Object System.Collections.Generic.List[string]
$script:NewManager = $null
$script:ProgressWindow = $null
$script:ProgressLabel = $null
$script:ProgressPercent = $null
$script:ProgressBar = $null
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
  if ((Get-Content -LiteralPath (Join-Path $SourceDir 'BUILD_CHANNEL.txt') -Raw).Trim().ToLowerInvariant() -ne 'main') { throw 'Update build channel mismatch.' }
  Set-UpdateProgress 'Backing up current application...' 5
  New-Item -ItemType Directory -Path $Backup -ErrorAction Stop | Out-Null
  foreach ($name in $Names) {
    $original = Join-Path $TargetDir $name
    if (Test-Path -LiteralPath $original) {
      Move-Item -LiteralPath $original -Destination (Join-Path $Backup $name) -ErrorAction Stop
      $Moved.Add($name)
    }
  }
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
  $ScratchRoot = Split-Path -Parent (Split-Path -Parent $SourceDir)
  $AckPath = Join-Path $ScratchRoot ('startup-ok-' + [guid]::NewGuid().ToString('N') + '.txt')
  $info = New-Object System.Diagnostics.ProcessStartInfo
  $info.FileName = Join-Path $TargetDir 'EvolveModManager.exe'
  $info.WorkingDirectory = $TargetDir
  $info.UseShellExecute = $false
  $info.EnvironmentVariables['EVOLVE_MANAGER_UPDATE_ACK'] = $AckPath
  $script:NewManager = [System.Diagnostics.Process]::Start($info)
  if ($null -eq $script:NewManager) { throw 'Could not start updated manager.' }
  $timer = [System.Diagnostics.Stopwatch]::StartNew()
  $Acknowledged = $false
  while ($timer.Elapsed.TotalSeconds -lt 45) {
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
  catch { Write-Output ("Backup retained (cleanup warning): " + $_.Exception.Message + " at " + $Backup) }
} catch {
  $Failure = $_.Exception.Message
  Write-Output "UPDATE FAILED: $Failure"
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
  Write-Output "Recovery backup location (if present): $Backup"
  Write-Output 'The detached updater will not show a blocking error dialog. Details are in update.log.'
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
    log = scratch / 'update.log'
    with log.open('wb') as output:
        subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-STA', '-WindowStyle', 'Hidden', '-ExecutionPolicy', 'Bypass',
                          '-File', str(script), '-TargetDir', str(app_home), '-SourceDir', str(staged),
                          '-ManagerPid', str(manager_pid), '-ExpectedCommit', update.commit],
                         stdin=subprocess.DEVNULL, stdout=output, stderr=subprocess.STDOUT,
                         creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP)
    return log
