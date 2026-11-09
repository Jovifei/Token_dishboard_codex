import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

try:
    import sync
except ImportError:
    sync = None


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(sync, "independent sync boundary is missing")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        vault = self.root / "vault"
        vault.mkdir()
        self.a, self.b = vault / "now.md", vault / "decisions.md"
        self.a.write_bytes(b"old A")
        self.b.write_bytes(b"old B")
        self.store = sync.Store([self.a, self.b], self.root / "private")

    def plan(self, both=False):
        return self.store.preview({self.a: "new A", **({self.b: "new B"} if both else {})})

    def test_denied_and_unanswered_write_nothing(self):
        p = self.plan()
        for consent in (None, False, "", p["digest"] + "wrong"):
            r = self.store.apply(p, consent, True)
            self.assertEqual(r["status"], "WAITING_OR_DENIED")
            self.assertEqual(self.a.read_bytes(), b"old A")

    def test_phase_evidence_required(self):
        p = self.plan()
        r = self.store.apply(p, p["digest"], False)
        self.assertEqual(r["status"], "BLOCKED_STAGE")

    def test_conflict_preflight_all_targets(self):
        p = self.plan(True)
        self.b.write_bytes(b"external")
        r = self.store.apply(p, p["digest"], True)
        self.assertEqual(r["status"], "BLOCKED")
        self.assertEqual(self.a.read_bytes(), b"old A")

    def test_write_readback_and_rollback(self):
        p = self.plan(True)
        r = self.store.apply(p, p["digest"], True)
        self.assertEqual(r["status"], "SYNCED")
        self.assertEqual(self.a.read_bytes(), b"new A")
        receipt = self.store.load_receipt(r["receipt"])
        self.assertEqual(receipt["status"], "SYNCED")
        undone = self.store.rollback(receipt, True)
        self.assertEqual(undone["status"], "ROLLED_BACK")
        self.assertEqual(self.b.read_bytes(), b"old B")

    def test_partial_failure_lists_residual_and_rollback(self):
        p = self.plan(True)
        real = sync.replace_checked
        def fail_second(path, before, after):
            if path == self.b:
                raise OSError("fixture write failure")
            return real(path, before, after)
        with patch.object(sync, "replace_checked", fail_second):
            r = self.store.apply(p, p["digest"], True)
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["residual_paths"], [str(self.a)])
        self.assertEqual(self.b.read_bytes(), b"old B")
        self.assertEqual(self.store.rollback(r, True)["status"], "ROLLED_BACK")

    def test_rollback_preserves_external_edit(self):
        p = self.plan()
        r = self.store.apply(p, p["digest"], True)
        self.a.write_bytes(b"external")
        r = self.store.rollback(r, True)
        self.assertEqual(r["status"], "ROLLBACK_PARTIAL")
        self.assertEqual(self.a.read_bytes(), b"external")
        self.assertEqual(r["residual_paths"], [str(self.a)])

    def test_lock_blocks_concurrent_apply(self):
        p = self.plan()
        with sync.locks([self.a]):
            r = self.store.apply(p, p["digest"], True)
        self.assertEqual(r["status"], "BLOCKED")
        self.assertEqual(self.a.read_bytes(), b"old A")

    def test_unapproved_path_and_tampered_plan(self):
        with self.assertRaises(ValueError):
            self.store.preview({self.root / "other.md": "bad"})
        p = self.plan()
        p["operations"][0]["after"] = "tampered"
        self.assertEqual(self.store.apply(p, p["digest"], True)["status"], "BLOCKED")

    def test_missing_target_not_created(self):
        self.a.unlink()
        with self.assertRaises((ValueError, OSError)):
            self.plan()

    def test_no_change_not_synced(self):
        p = self.store.preview({self.a: "old A"})
        self.assertEqual(self.store.apply(p, p["digest"], True)["status"], "NO_CHANGE")

    def test_reject_unsafe_backup_path(self):
        p = self.plan()
        r = self.store.apply(p, p["digest"], True)
        r["written"][0]["backup"] = str(self.b)
        result = self.store.rollback(r, True)
        self.assertEqual(result["status"], "ROLLBACK_PARTIAL")
        self.assertEqual(self.a.read_bytes(), b"new A")


if __name__ == "__main__":
    unittest.main()
