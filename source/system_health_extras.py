"""Read-only launch preflight, Windows crash history, private support summaries."""
from __future__ import annotations
import os
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
from launch_integration import state_status

EXES = ("evolve.exe", "moddedevolvelauncher.exe")
PATH_RE = re.compile(r"(?i)\b[a-z]:[\\/][^\s<>\"'|]*")
UNC_RE = re.compile(r"(?i)\\\\[a-z0-9_.-]+\\[^\s<>\"']+")
SECRET_RE = re.compile(r"(?i)\b(?:access[_-]?token|password|api[_-]?key|secret|authorization)\s*[:=]\s*\S+")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")


def redact_support_text(value):
    result = SECRET_RE.sub("[REDACTED]", str(value))
    result = EMAIL_RE.sub("[EMAIL]", result)
    result = UNC_RE.sub("[NETWORK PATH]", result)
    return PATH_RE.sub("[LOCAL PATH]", result)


def format_discord_report(*, os_version, manager_version, client_version,
                          game_version, checks, crashes=()):
    fence = chr(96) * 3
    output = ["**Evolve Stage 2 - Support Report**", fence + "text"]
    for label, value in (("OS", os_version), ("Manager", manager_version),
                         ("Client", client_version), ("Game", game_version)):
        output.append(f"{label}: {redact_support_text(value)[:180]}")
    for key in ("firewall", "process", "crash", "storage", "environment"):
        status, _ = checks.get(key, ("Not checked", ""))
        output.append(f"{key.title()}: {redact_support_text(status)[:100]}")
    if crashes:
        output.append("Recent crashes:")
        output.extend("- " + redact_support_text(x)[:190] for x in crashes[:3])
    return "\n".join(output + [fence, "Review private details before sharing.",
                                      "Do not share crash dumps publicly."])


def _running_game_process():
    try:
        import psutil
        return any((p.info.get("name") or "").casefold() in EXES
                   for p in psutil.process_iter(["name"]))
    except (ImportError, OSError, RuntimeError):
        return None


def prelaunch_check(*, swap_root, game_root, modded, status=None,
                    process_running=None, require_admin=None):
    """Pure read-only guard; the existing launcher still verifies stage identity."""
    st = status if status is not None else state_status(Path(swap_root))
    errors, notes = [], []
    if st.phase not in ("missing", "prepared", "restored"):
        errors.append("Swap journal is " + st.phase + ". Restore originals before launch.")
    if st.backups and st.phase in ("missing", "prepared", "restored"):
        errors.append("Unexpected original backups: inspect recovery before launch.")
    if not game_root:
        errors.append("Game installation is not configured.")
    else:
        from online_guard import target_executable
        try:
            target_executable(game_root)
        except (OSError, ValueError) as exc:
            errors.append("Game executable is unavailable: " + str(exc))
    active = process_running if process_running is not None else _running_game_process()
    if active is True:
        errors.append("Close Evolve and the launcher before starting a new session.")
    if active is None:
        notes.append("Process detection unavailable; existing launch helper will verify.")
    if os.name == "nt":
        if require_admin is None:
            try:
                import ctypes
                require_admin = bool(ctypes.windll.shell32.IsUserAnAdmin())
            except (AttributeError, OSError):
                require_admin = False
        if not require_admin:
            errors.append("Administrator permission is required to manage firewall rules.")
    if modded:
        notes.append("Modded launch must enforce offline protection before game start.")
    return tuple(errors), tuple(notes)


def windows_crash_history(*, runner=None, limit=12):
    """Bounded on-demand Event Viewer query; never reads other users' logs."""
    if os.name != "nt":
        return ()
    limit = max(1, min(int(limit), 25))
    runner = runner or subprocess.run
    exe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "wevtutil.exe"
    query = "*[System[(EventID=1000 or EventID=1001 or EventID=1026)]]"
    call = runner([str(exe), "qe", "Application", "/q:" + query,
                   "/c:" + str(min(80, limit * 5)), "/rd:true", "/f:xml"],
                  capture_output=True, text=True, timeout=15)
    if call.returncode:
        raise RuntimeError("Event Viewer unavailable: " + (call.stderr or "")[:140])
    raw = re.sub(r"<\?xml[^>]*\?>", "", (call.stdout or "")[:500000])
    try:
        root = ET.fromstring("<Events>" + raw + "</Events>")
    except ET.ParseError as exc:
        raise RuntimeError("Event Viewer returned invalid event XML.") from exc
    rows = []
    ns = {"e": "http://schemas.microsoft.com/win/2004/08/events/event"}
    for event in root:
        if len(rows) >= limit:
            break
        system = event.find("e:System", ns)
        if system is None:
            continue
        event_id = system.findtext("e:EventID", default="", namespaces=ns)
        timestamp = system.find("e:TimeCreated", ns)
        when = timestamp.get("SystemTime", "")[:19] if timestamp is not None else "Unknown"
        nodes = event.findall(".//e:EventData/e:Data", ns)
        values = " ".join(x.text or "" for x in nodes)[:5000]
        if not any(exe in values.casefold() for exe in EXES):
            continue
        module = next((x.text for x in nodes
                       if x.get("Name") in ("ModuleName", "FaultingModule") and x.text),
                      "Unknown")
        exception = next((x.text for x in nodes
                          if x.get("Name") in ("ExceptionCode", "FaultingExceptionCode")
                          and x.text), "")
        code = (" | Exception: " + exception if
                re.fullmatch(r"(?:0x)?[0-9a-fA-F]{8,16}", exception or "") else "")
        rows.append(f"{when} UTC | Event {event_id} | Fault module: "
                    f"{redact_support_text(module)}{code}")
    return tuple(rows)
