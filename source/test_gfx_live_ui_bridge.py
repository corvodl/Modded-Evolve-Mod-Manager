"""Mock authenticated UI snapshot transport; never touches Evolve.exe memory."""
from __future__ import annotations

import json
import socket
import threading
import unittest

from developer_event_live import PROTOCOL, _signed, _verify_response
from gfx_live_ui_bridge import UiSnapshotBridgeClient, validate_ui_snapshot, EventBridgeError

SAMPLE = {
    "schema": 1, "movie": "Libs/UI/main_menu.gfx", "frame": 1,
    "nodes": [
        {"id": "root", "parent": None, "name": "Main", "kind": "movieclip",
         "text": "", "x": 0, "y": 0, "width": 1920, "height": 1080, "visible": True},
        {"id": "try", "parent": "root", "name": "TRY", "kind": "button",
         "text": "TRY", "x": 42.5, "y": 55, "width": 100, "height": 24,
         "visible": True},
    ],
}


def _read(conn):
    data = bytearray()
    while b"\n" not in data:
        data.extend(conn.recv(4096))
    return json.loads(data.split(b"\n", 1)[0])


def _send(conn, data):
    conn.sendall((json.dumps(data, separators=(",", ":")) + "\n").encode("ascii"))


class LiveUiClientTests(unittest.TestCase):
    token = "fake-ui-session-token-not-a-real-game-12345"

    def client(self, port, *, offline=True):
        return UiSnapshotBridgeClient(
            4242, port, self.token, lambda: "C:/Synthetic",
            process_check=lambda pid, root: None,
            listener_check=lambda pid, port: None,
            offline_check=lambda root: offline)

    def _server(self, *, advertised=True, reply="captured", snapshot=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        errors = []

        def run():
            try:
                conn, _ = listener.accept()
                with conn:
                    conn.settimeout(3)
                    hello = _verify_response(self.token, _read(conn), {
                        "op": "hello", "protocol": PROTOCOL, "pid": 4242})
                    nonce = hello["nonce"]
                    _send(conn, _signed(self.token, {
                        "protocol": PROTOCOL, "op": "ready", "nonce": nonce,
                        "pid": 4242, "build": hello["build"], "status": "ready",
                        "events": [], "ui_snapshot":
                        "available" if advertised else "unavailable"}))
                    if advertised:
                        requested = _verify_response(self.token, _read(conn), {
                            "op": "ui_snapshot", "protocol": PROTOCOL,
                            "nonce": nonce, "pid": 4242})
                        packet = {
                            "protocol": PROTOCOL, "op": "ui_snapshot_result",
                            "nonce": nonce, "pid": 4242,
                            "request_id": requested["request_id"], "status": reply}
                        if reply == "captured":
                            packet["snapshot"] = json.dumps(
                                SAMPLE if snapshot is None else snapshot)
                        _send(conn, _signed(self.token, packet))
            except Exception as exc:
                errors.append(str(exc))
            finally:
                listener.close()
        task = threading.Thread(target=run, daemon=True)
        task.start()
        return port, task, errors

    def test_authenticated_read_only_snapshot(self):
        port, task, errors = self._server()
        snapshot = self.client(port).snapshot()
        task.join(timeout=4)
        self.assertFalse(errors, errors)
        self.assertEqual(snapshot["movie"], SAMPLE["movie"])
        self.assertEqual(snapshot["nodes"][1]["text"], "TRY")
        self.assertIsInstance(snapshot["nodes"], tuple)

    def test_unregistered_provider_fails_closed(self):
        port, task, errors = self._server(advertised=False)
        with self.assertRaisesRegex(EventBridgeError, "No verified Scaleform"):
            self.client(port).snapshot()
        task.join(timeout=4)
        self.assertFalse(errors, errors)

    def test_offline_gate_is_mandatory(self):
        with self.assertRaisesRegex(EventBridgeError, "firewall"):
            self.client(22345, offline=False).snapshot()

    def test_invalid_snapshots_rejected(self):
        cases = [
            dict(SAMPLE, schema=2),
            dict(SAMPLE, nodes=SAMPLE["nodes"] * 80),
            dict(SAMPLE, nodes=[dict(SAMPLE["nodes"][0], width=float("nan"))]),
            dict(SAMPLE, nodes=[dict(SAMPLE["nodes"][0], parent="root")]),
            dict(SAMPLE, nodes=[dict(SAMPLE["nodes"][0], id="same"),
                                dict(SAMPLE["nodes"][1], id="same")]),
            dict(SAMPLE, nodes=[dict(SAMPLE["nodes"][0], id="other",
                                    parent="unknown")]),
            dict(SAMPLE, nodes=[dict(SAMPLE["nodes"][0], visible="yes")]),
        ]
        for invalid in cases:
            with self.subTest(invalid=invalid):
                with self.assertRaises(EventBridgeError):
                    validate_ui_snapshot(json.dumps(invalid))
        self.assertEqual(validate_ui_snapshot(json.dumps(SAMPLE))["frame"], 1)

    def test_signed_rejection_is_not_a_snapshot(self):
        port, task, errors = self._server(reply="unavailable")
        with self.assertRaisesRegex(EventBridgeError, "did not return"):
            self.client(port).snapshot()
        task.join(timeout=4)
        self.assertFalse(errors, errors)


if __name__ == "__main__":
    unittest.main()
