"""Read-only live GFx hierarchy client; no process-memory read/write API.

The game-side bridge must register a verified build-specific Scaleform
provider before returning any nodes. This client authenticates the exact
offline process, loopback listener and one-time session token before asking
for a single bounded snapshot. An unavailable provider is NOT live data.
"""
from __future__ import annotations

import json
import math
import secrets
import socket

from developer_event_catalog import CATALOG_VERSION, SOURCE_BUILD
from developer_event_live import (
    EventBridgeClient, EventBridgeError, PROTOCOL, TIMEOUT_SECONDS,
    _read_line, _signed, _transmit, _verify_response,
)

MAX_NODES = 128
MAX_JSON_BYTES = 12000
KINDS = frozenset(("movieclip", "button", "textfield", "sprite",
                   "shape", "unknown"))


def validate_ui_snapshot(payload):
    """Reject untrusted, incomplete or fabricated hierarchy responses."""
    if not isinstance(payload, str) or len(payload.encode("utf-8")) > MAX_JSON_BYTES:
        raise EventBridgeError("Invalid or oversized UI snapshot.")
    try:
        value = json.loads(payload, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError) as exc:
        raise EventBridgeError("Invalid UI snapshot JSON.") from exc
    if not isinstance(value, dict) or type(value.get("schema")) is not int or value["schema"] != 1:
        raise EventBridgeError("Unsupported UI snapshot schema.")
    movie = value.get("movie")
    frame = value.get("frame")
    nodes = value.get("nodes")
    if (not isinstance(movie, str) or not 1 <= len(movie) <= 256 or
            type(frame) is not int or not 0 <= frame <= 100000 or
            not isinstance(nodes, list) or len(nodes) > MAX_NODES):
        raise EventBridgeError("Invalid UI snapshot metadata.")
    ids = set()
    parents = {}
    for node in nodes:
        if not isinstance(node, dict):
            raise EventBridgeError("Malformed live UI node.")
        ident = node.get("id")
        kind = node.get("kind")
        parent = node.get("parent")
        if (not isinstance(ident, str) or not 1 <= len(ident) <= 120 or
                ident in ids or kind not in KINDS or
                parent is not None and
                (not isinstance(parent, str) or not 1 <= len(parent) <= 120)):
            raise EventBridgeError("Invalid or duplicate live UI identity.")
        ids.add(ident)
        parents[ident] = parent
        for name, limit in (("name", 256), ("text", 512)):
            if not isinstance(node.get(name), str) or len(node[name]) > limit:
                raise EventBridgeError("Invalid UI node " + name + ".")
        for name in ("x", "y", "width", "height"):
            number = node.get(name)
            if (type(number) not in (float, int) or not math.isfinite(number)
                    or abs(number) > 100000):
                raise EventBridgeError("Invalid UI node position/size.")
        if type(node.get("visible")) is not bool:
            raise EventBridgeError("Invalid UI node visibility.")
    for ident, parent in parents.items():
        if parent is not None and parent not in ids:
            raise EventBridgeError("UI node has a missing parent.")
        visited = {ident}
        while parent is not None:
            if parent in visited:
                raise EventBridgeError("UI hierarchy contains a parent cycle.")
            visited.add(parent)
            parent = parents[parent]
    return {"schema": 1, "movie": movie, "frame": frame, "nodes": tuple(nodes)}


class UiSnapshotBridgeClient(EventBridgeClient):
    """No write endpoints. Only an explicitly registered native provider."""

    def snapshot(self):
        self._preflight()
        nonce = secrets.token_hex(16)
        hello = _signed(self.token, {
            "protocol": PROTOCOL, "op": "hello", "nonce": nonce,
            "pid": self.pid, "source": CATALOG_VERSION, "build": SOURCE_BUILD})
        try:
            with socket.create_connection(("127.0.0.1", self.port),
                                          timeout=TIMEOUT_SECONDS) as sock:
                sock.settimeout(TIMEOUT_SECONDS)
                _transmit(sock, hello)
                ready = _verify_response(self.token, _read_line(sock), {
                    "protocol": PROTOCOL, "op": "ready", "nonce": nonce,
                    "pid": self.pid, "build": SOURCE_BUILD})
                if ready.get("status") != "ready" or ready.get("ui_snapshot") != "available":
                    raise EventBridgeError(
                        "No verified Scaleform snapshot provider is registered in this game. "
                        "The existing event bridge alone cannot inspect live UI.")
                request_id = secrets.token_hex(16)
                self._preflight()
                _transmit(sock, _signed(self.token, {
                    "protocol": PROTOCOL, "op": "ui_snapshot",
                    "nonce": nonce, "request_id": request_id, "pid": self.pid}))
                result = _verify_response(self.token, _read_line(sock), {
                    "protocol": PROTOCOL, "op": "ui_snapshot_result",
                    "nonce": nonce, "request_id": request_id, "pid": self.pid})
                if result.get("status") != "captured":
                    raise EventBridgeError(
                        "The verified game-thread provider did not return a valid snapshot.")
                return validate_ui_snapshot(result.get("snapshot"))
        except (ConnectionError, TimeoutError, OSError) as exc:
            raise EventBridgeError(
                "Unable to obtain a live UI snapshot from the verified offline game.") from exc
