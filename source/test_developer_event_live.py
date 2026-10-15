"""Tests for offline live event protocol, without attaching to a game."""
from __future__ import annotations
import json
import socket
import threading
import unittest

from developer_event_catalog import load_catalog
from developer_event_live import (
    EventBridgeClient, EventBridgeError, PROTOCOL,
    _signed, _verify_response,
)


class LiveEventTests(unittest.TestCase):
    def setUp(self):
        self.event = next(e for e in load_catalog()
                          if e.name == "local_highlights")
        self.token = "test-token-not-used-by-a-real-module-12345"

    def client(self, port=22345, *, offline=True, listener=None):
        return EventBridgeClient(
            1234, port, self.token, lambda: "C:/Fake/EvolveGame",
            process_check=lambda pid, root: None,
            listener_check=listener or (lambda pid, port: None),
            offline_check=lambda root: offline)

    def test_refuse_online_or_unverified_target(self):
        with self.assertRaisesRegex(EventBridgeError, "firewall"):
            self.client(offline=False).probe()
        with self.assertRaisesRegex(EventBridgeError, "listener"):
            self.client(listener=lambda pid, port: (
                (_ for _ in ()).throw(EventBridgeError("Wrong listener")))).probe()

    def test_typed_parameters_and_uncertain_event_ids(self):
        uncertain = next(e for e in load_catalog() if e.uncertain_id)
        with self.assertRaisesRegex(ValueError, "token"):
            EventBridgeClient(1234, 22345, "short", lambda: "")
        # Unsupported events fail closed even with authenticated transport.
        self.assertTrue(uncertain.uncertain_id)

    def test_authenticated_round_trip(self):
        errors = []
        received = []
        ready = threading.Event()
        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        server.settimeout(6)
        port = server.getsockname()[1]

        def read_message(conn):
            data = bytearray()
            while b"\n" not in data:
                data.extend(conn.recv(4096))
            return json.loads(data.split(b"\n", 1)[0])

        def send_message(conn, packet):
            conn.sendall((json.dumps(packet) + "\n").encode("utf-8"))

        def serve():
            try:
                ready.set()
                for run in range(2):
                    conn, _ = server.accept()
                    with conn:
                        conn.settimeout(4)
                        hello = read_message(conn)
                        hello = _verify_response(self.token, hello, {
                            "op": "hello", "pid": 1234,
                            "protocol": PROTOCOL})
                        nonce = hello["nonce"]
                        send_message(conn, _signed(self.token, {
                            "protocol": PROTOCOL, "op": "ready",
                            "nonce": nonce, "pid": 1234,
                            "build": hello["build"], "status": "ready",
                            "events": [self.event.name]}))
                        if run == 1:
                            action = read_message(conn)
                            action = _verify_response(self.token, action, {
                                "op": "activate", "pid": 1234,
                                "event": self.event.name})
                            received.append(action)
                            send_message(conn, _signed(self.token, {
                                "protocol": PROTOCOL, "op": "result",
                                "nonce": nonce, "request_id": action["request_id"],
                                "pid": 1234, "event": self.event.name,
                                "status": "accepted"}))
            except Exception as exc:
                errors.append(exc)
            finally:
                server.close()

        t = threading.Thread(target=serve, daemon=True)
        t.start()
        self.assertTrue(ready.wait(2))
        client = self.client(port)
        self.assertIn(self.event.name, client.probe())
        result = client.activate(self.event, {"active": "true"})
        self.assertEqual(result["status"], "accepted")
        t.join(timeout=5)
        self.assertFalse(t.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(received[0]["parameters"], {"active": True})

    def test_incorrect_message_mac_rejected(self):
        packet = _signed(self.token, {"protocol": PROTOCOL, "op": "result"})
        packet["mac"] = "0" * 64
        with self.assertRaisesRegex(EventBridgeError, "authentication"):
            _verify_response(self.token, packet, {})


if __name__ == "__main__":
    unittest.main()
