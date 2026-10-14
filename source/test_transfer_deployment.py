"""Synthetic transaction tests: no game payloads and no signing secrets."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from transfer_deployment import (TransferCancelled, install_package,
                                 restore_package, sha256_file)


def digest(value):
    return hashlib.sha256(value).hexdigest()


class Stage2TransferTransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.package, self.stage, self.recovery = (root / n for n in
                                                   ("package", "stage", "recovery"))
        self.package.mkdir()
        self.stage.mkdir()
        (self.stage / ".stage2-transfer-staging.json").write_text(json.dumps({"purpose": "isolated-stage2-transfer-staging", "schema": 1}), encoding="utf-8")
        self.verifications = []

    def verified(self, path, relative):
        # Controlled verifier stub. A live operation must use crypak_verifier.
        self.verifications.append(relative)
        if not Path(path).read_bytes().startswith(b"sign:"):
            raise ValueError("invalid synthetic signature")

    def archive(self, relative, payload, target=False):
        path = (self.stage if target else self.package) / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        return path

    def manifest(self, rows):
        (self.package / "transfer-package.json").write_text(json.dumps({
            "schema": 1, "kind": "stage2-verified-pak-transfer",
            "files": rows
        }), encoding="utf-8")

    def row(self, relative, new, old=None):
        return {"path": relative, "source": relative, "operation":
                "replace" if old is not None else "add", "sha256": digest(new),
                **({"baseline_sha256": digest(old)} if old is not None else {})}

    def install(self, **kwargs):
        return install_package(self.package, self.stage, self.recovery,
                               verify_archive=self.verified, **kwargs)

    def test_replace_and_add_restore_exact_bytes(self):
        old = b"sign:original"
        new = b"sign:replacement"
        added = b"sign:new archive"
        self.archive("Game/Libs.pak", old, target=True)
        self.archive("Game/Libs.pak", new)
        self.archive("Game/Levels/Sample/New.pak", added)
        self.manifest([self.row("Game/Libs.pak", new, old),
                       self.row("Game/Levels/Sample/New.pak", added)])
        self.install(allow_new_archives=True)
        self.assertEqual(self.archive_path("Game/Libs.pak").read_bytes(), new)
        self.assertTrue(self.archive_path("Game/Levels/Sample/New.pak").exists())
        self.assertTrue(restore_package(self.recovery))
        self.assertEqual(self.archive_path("Game/Libs.pak").read_bytes(), old)
        self.assertFalse(self.archive_path("Game/Levels/Sample/New.pak").exists())
        self.assertFalse(restore_package(self.recovery))

    def archive_path(self, rel):
        return self.stage / rel

    def test_new_archive_disallowed_by_default(self):
        blob = b"sign:hello"
        self.archive("Game/new.pak", blob)
        self.manifest([self.row("Game/new.pak", blob)])
        with self.assertRaisesRegex(ValueError, "unverified"):
            self.install()
        self.assertFalse(self.recovery.exists())

    def test_baseline_hash_mismatch_refuses_changes(self):
        old, new = b"sign:old", b"sign:new"
        self.archive("Game/a.pak", b"sign:third", target=True)
        self.archive("Game/a.pak", new)
        self.manifest([self.row("Game/a.pak", new, old)])
        with self.assertRaisesRegex(ValueError, "baseline mismatch"):
            self.install()
        self.assertFalse(self.recovery.exists())

    def test_invalid_paths_duplicate_case_and_symlink(self):
        blob = b"sign:source"
        self.archive("safe.pak", blob)
        for invalid in ("../bad.pak", "/bad.pak", "C:/bad.pak",
                        "Game//bad.pak", "Game\\bad.pak", "Game/./bad.pak"):
            with self.subTest(invalid=invalid):
                self.manifest([{"path": invalid, "source": "safe.pak",
                                "operation": "add", "sha256": digest(blob)}])
                with self.assertRaises(ValueError):
                    self.install(allow_new_archives=True)
        self.manifest([self.row("safe.pak", blob),
                       dict(self.row("safe.pak", blob), path="SAFE.PAK")])
        with self.assertRaisesRegex(ValueError, "Duplicate destination"):
            self.install(allow_new_archives=True)

    def test_signature_verifier_required_and_checked_before_writes(self):
        new = b"unsigned"
        self.archive("new.pak", new)
        self.manifest([self.row("new.pak", new)])
        with self.assertRaises(ValueError):
            install_package(self.package, self.stage, self.recovery,
                            verify_archive=None, allow_new_archives=True)
        with self.assertRaisesRegex(ValueError, "synthetic signature"):
            self.install(allow_new_archives=True)
        self.assertFalse(self.archive_path("new.pak").exists())

    def test_package_integrity_fails_closed(self):
        new = b"sign:changed"
        self.archive("x.pak", new)
        self.manifest([dict(self.row("x.pak", new), sha256=digest(b"wrong"))])
        with self.assertRaisesRegex(ValueError, "changed package"):
            self.install(allow_new_archives=True)

    def test_cancel_before_install(self):
        new = b"sign:data"
        self.archive("new.pak", new)
        self.manifest([self.row("new.pak", new)])
        with self.assertRaises(TransferCancelled):
            self.install(allow_new_archives=True, cancelled=lambda: True)
        self.assertFalse(self.archive_path("new.pak").exists())

    def test_restore_refuses_unrelated_changes(self):
        new = b"sign:data"
        self.archive("new.pak", new)
        self.manifest([self.row("new.pak", new)])
        self.install(allow_new_archives=True)
        self.archive_path("new.pak").write_bytes(b"user edited")
        with self.assertRaisesRegex(ValueError, "edited"):
            restore_package(self.recovery)
        self.assertEqual(self.archive_path("new.pak").read_bytes(), b"user edited")


if __name__ == "__main__":
    unittest.main()
