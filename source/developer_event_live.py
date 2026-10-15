"""Fail-closed live Evolve event-activation bridge for developer-owned modules.

The V3 event document is an event *catalog*, not an engine dispatcher.
A verified in-process companion module must provide this local authenticated
protocol and explicitly advertise each event it can actually dispatch.
This module does NOT execute EXE RVAs, inject arbitrary native calls, or
send event traffic to online games.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess

from developer_event_catalog import CATALOG_VERSION, SOURCE_BUILD, make_fixture

PROTOCOL = "evolve-dev-event-bridge-v1"
MAX_MESSAGE = 65536
TIMEOUT_SECONDS = 2.0


class EventBridgeError(RuntimeError):
    pass


def _payload_signature(token, payload):
    body = json.dumps(payload, sort_keys=True, ensure_ascii=True,
                      separators=(",", ":"), allow_nan=False).encode("ascii")
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _signed(token, payload):
    result = dict(payload)
    result["mac"] = _payload_signature(token, result)
    return result


def _verify_response(token, message, expected):
    if not isinstance(message, dict) or "mac" not in message:
        raise EventBridgeError("In-game bridge did not authenticate its response.")
    mac = message["mac"]
    body = {key: value for key, value in message.items() if key != "mac"}
    if not isinstance(mac, str) or not hmac.compare_digest(
            mac, _payload_signature(token, body)):
        raise EventBridgeError("Invalid game-bridge message authentication.")
    for key, expected_value in expected.items():
        if body.get(key) != expected_value:
            raise EventBridgeError("Game-bridge response did not match " + key)
    return body


def _read_line(sock):
    output = bytearray()
    while len(output) <= MAX_MESSAGE:
        chunk = sock.recv(4096)
        if not chunk:
            raise EventBridgeError("Game bridge disconnected without replying.")
        if b"\n" in chunk:
            output.extend(chunk.split(b"\n", 1)[0])
            break
        output.extend(chunk)
    if len(output) > MAX_MESSAGE:
        raise EventBridgeError("Game-bridge response exceeds 64 KiB.")
    try:
        return json.loads(output.decode("utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise EventBridgeError("Malformed game-bridge JSON response.") from exc


def _transmit(sock, payload):
    raw = (json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=True, allow_nan=False) + "\n").encode("ascii")
    if len(raw) > MAX_MESSAGE:
        raise EventBridgeError("Event request exceeds 64 KiB.")
    sock.sendall(raw)


def verify_evolve_process(pid, game_root):
    """Verify the target is the exact selected Evolve.exe, not just its name."""
    from developer_memory import _game_handle
    import ctypes
    from ctypes import wintypes

    if os.name != "nt":
        raise EventBridgeError("Live game activation requires 64-bit Windows.")
    try:
        exe = (Path(game_root).resolve() / "bin64_SteamRetail" / "Evolve.exe").resolve()
        if not exe.is_file():
            raise EventBridgeError("Configured Evolve.exe does not exist.")
        k, handle = _game_handle(pid, 0x1000)
        try:
            name = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(name))
            k.QueryFullProcessImageNameW.argtypes = [
                wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
                ctypes.POINTER(wintypes.DWORD)]
            k.QueryFullProcessImageNameW.restype = wintypes.BOOL
            if not k.QueryFullProcessImageNameW(handle, 0, name,
                                                ctypes.byref(length)):
                raise ctypes.WinError(ctypes.get_last_error())
            running = Path(name.value).resolve()
            if os.path.normcase(str(running)) != os.path.normcase(str(exe)):
                raise EventBridgeError(
                    "Running Evolve.exe is not the game selected in the manager.")
        finally:
            k.CloseHandle(handle)
    except (OSError, ValueError) as exc:
        raise EventBridgeError("Cannot verify running Evolve.exe: " + str(exc)) from exc


def verify_local_listener(pid, port):
    """Fail closed unless Windows reports the selected PID owns the loopback port."""
    if os.name != "nt":
        raise EventBridgeError("Local listener verification requires Windows.")
    cmd = [
        "powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
        "$r = Get-NetTCPConnection -LocalPort " + str(port) +
        " -State Listen -ErrorAction SilentlyContinue | "
        "Where-Object { $_.OwningProcess -eq " + str(pid) +
        " -and $_.LocalAddress -in @('127.0.0.1','::1') }; "
        "if ($r) { 'OWNED' } else { 'NOT_OWNED' }",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EventBridgeError("Cannot verify the local game-bridge listener.") from exc
    if result.returncode or result.stdout.strip() != "OWNED":
        raise EventBridgeError(
            "No authenticated local event bridge is listening inside this Evolve.exe PID.")


class EventBridgeClient:
    """Short-lived authenticated loopback sessions; no direct game memory writes."""

    def __init__(self, pid, port, token, game_root, *, process_check=None,
                 listener_check=None, offline_check=None):
        if isinstance(pid, bool) or not isinstance(pid, int) or pid < 1:
            raise ValueError("Select a valid Evolve.exe PID.")
        if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
            raise ValueError("Bridge port must be 1024 to 65535.")
        if not isinstance(token, str) or not 24 <= len(token) <= 256:
            raise ValueError("Enter the current in-game developer bridge token (24+ characters).")
        self.pid = pid
        self.port = port
        self.token = token
        self.game_root = game_root
        self.process_check = process_check or verify_evolve_process
        self.listener_check = listener_check or verify_local_listener
        self.offline_check = offline_check

    def _preflight(self):
        root = self.game_root()
        if not root:
            raise EventBridgeError("Configure an EvolveGame folder first.")
        self.process_check(self.pid, root)
        if self.offline_check is None:
            from online_guard import offline_rule_active
            check = offline_rule_active
        else:
            check = self.offline_check
        if check(root) is not True:
            raise EventBridgeError(
                "Live activation requires the manager's verified outbound "
                "Evolve firewall block. Online activation is unavailable.")
        self.listener_check(self.pid, self.port)

    def _transaction(self, event=None, fields=None):
        self._preflight()
        nonce = secrets.token_hex(16)
        hello = _signed(self.token, {
            "protocol": PROTOCOL, "op": "hello",
            "nonce": nonce, "pid": self.pid,
            "source": CATALOG_VERSION, "build": SOURCE_BUILD})
        try:
            with socket.create_connection(("127.0.0.1", self.port),
                                          timeout=TIMEOUT_SECONDS) as sock:
                sock.settimeout(TIMEOUT_SECONDS)
                _transmit(sock, hello)
                welcome = _verify_response(self.token, _read_line(sock), {
                    "protocol": PROTOCOL, "op": "ready", "nonce": nonce,
                    "pid": self.pid, "build": SOURCE_BUILD})
                if welcome.get("status") != "ready":
                    raise EventBridgeError("Game bridge is not ready.")
                capabilities = welcome.get("events")
                if (not isinstance(capabilities, list) or len(capabilities) > 534
                        or any(not isinstance(n, str) for n in capabilities)):
                    raise EventBridgeError("Game bridge returned invalid event capabilities.")
                if event is None:
                    return tuple(capabilities)
                if event.uncertain_id:
                    raise EventBridgeError("This event ID is unverified; activation refused.")
                if event.name not in capabilities:
                    raise EventBridgeError(
                        "The in-game companion has not verified a dispatcher for "
                        + event.name + ".")
                fixture = make_fixture(event, fields)
                if fixture["unfilled_fields"]:
                    raise EventBridgeError(
                        "Fill all event fields: " + ", ".join(fixture["unfilled_fields"]))
                request_id = secrets.token_hex(16)
                activate = _signed(self.token, {
                    "protocol": PROTOCOL, "op": "activate", "nonce": nonce,
                    "request_id": request_id, "pid": self.pid,
                    "event": event.name, "event_id": event.event_id,
                    "parameters": fixture["fields"]})
                # Reconfirm game isolation immediately before sending.
                self._preflight()
                _transmit(sock, activate)
                response = _verify_response(self.token, _read_line(sock), {
                    "protocol": PROTOCOL, "op": "result",
                    "nonce": nonce, "request_id": request_id,
                    "pid": self.pid, "event": event.name})
                status = response.get("status")
                if status not in ("accepted", "effect_confirmed"):
                    raise EventBridgeError(
                        str(response.get("error") or "In-game bridge rejected activation.")[:300])
                return response
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise EventBridgeError(
                "Cannot communicate with an in-process Evolve event bridge. "
                "No activation was confirmed: " + str(exc)) from exc

    def probe(self):
        return self._transaction()

    def activate(self, event, values):
        return self._transaction(event, values)
