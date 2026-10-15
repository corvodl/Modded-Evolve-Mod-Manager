"""Windows-only transport smoke test with a fake Evolve process.

This does not activate gameplay effects. A simulated native callback lets CI
verify token authentication, full request/response and game-thread pumping.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import unittest


class NativeBridgeSmokeTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Native DLL smoke test requires Windows")
    def test_fixture_host_authenticates_and_pumps(self):
        if len(sys.argv) != 3:
            self.fail("Run: python native/tests/native_bridge_smoke.py <fake-Evolve.exe> <bridge.dll>")
        host, dll = (Path(value).resolve() for value in sys.argv[1:3])
        source_dir = Path(__file__).resolve().parents[2] / "source"
        sys.path.insert(0, str(source_dir))
        from developer_event_live import EventBridgeClient, EventBridgeError
        from developer_event_catalog import load_catalog

        proc = subprocess.Popen(
            [str(host), str(dll)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1)
        try:
            first = proc.stdout.readline().strip()
            self.assertTrue(first.startswith("READY "), first)
            _, pid, token = first.split(" ", 2)
            self.assertEqual(len(token), 64)
            client = EventBridgeClient(
                int(pid), 22345, token, lambda: "C:/Synthetic",
                process_check=lambda target, game: None,
                listener_check=lambda target, port: None,
                offline_check=lambda game: True,
            )
            with self.assertRaises(EventBridgeError):
                EventBridgeClient(
                    int(pid), 22345, "x" * 64, lambda: "C:/Synthetic",
                    process_check=lambda target, game: None,
                    listener_check=lambda target, port: None,
                    offline_check=lambda game: True,
                ).probe()
            handlers = client.probe()
            self.assertEqual(handlers, ("local_highlights",))
            event = next(e for e in load_catalog() if e.name == "local_highlights")
            response = client.activate(event, {"active": "true"})
            self.assertEqual(response["status"], "effect_confirmed")
            # This status is from a TEST CALLBACK, never a real game effect.
            stdout, stderr = proc.communicate(timeout=8)
            self.assertEqual(proc.returncode, 0, (stdout, stderr))
            self.assertIn("SIMULATED_CALLBACK 1", stdout)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=5)


if __name__ == "__main__":
    args = sys.argv[1:3]
    sys.argv = [sys.argv[0]]
    if len(args) == 2:
        _old = NativeBridgeSmokeTests.test_fixture_host_authenticates_and_pumps
        def _wrapped(self):
            sys.argv = [sys.argv[0]] + args
            return _old(self)
        NativeBridgeSmokeTests.test_fixture_host_authenticates_and_pumps = _wrapped
    unittest.main()
