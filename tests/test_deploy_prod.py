"""deploy_prod guardrail tests (2026-08-29 review finding: highest-blast-radius file in the
batch shipped untested — it writes into the tree the live scheduled tasks execute from).

Covers the refusal logic that must never rot: prod-newer refusal (exit 2, ZERO copies),
stale->copied, dev-only gating behind --include-new, and _assert_deployable's forbidden
suffix/dir raises (currently unreachable via the *.py glob — covered so a loosened glob
can't silently drop the guard)."""

import importlib.util
import sys
import time
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "deploy_prod.py"
spec = importlib.util.spec_from_file_location("deploy_prod", SCRIPT)
dp = importlib.util.module_from_spec(spec)
sys.modules["deploy_prod"] = dp
spec.loader.exec_module(dp)


class DeployProdGuardrails(unittest.TestCase):
    def setUp(self):
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.dev = root / "dev"
        self.prod = root / "prod"
        self.log = root / "deploy.jsonl"
        (self.dev / "pkg").mkdir(parents=True)
        (self.prod / "pkg").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, base, rel, text, mtime=None):
        p = base / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
        if mtime is not None:
            import os
            os.utime(p, (mtime, mtime))
        return p

    def test_stale_is_copied_on_apply(self):
        now = time.time()
        self._write(self.prod, "pkg/a.py", "old", mtime=now - 3600)
        self._write(self.dev, "pkg/a.py", "new", mtime=now)
        classes = dp.classify(self.dev, self.prod)
        self.assertEqual([str(c["rel"]) for c in classes["stale"]], ["pkg\\a.py"])
        rc = dp.apply_copies(classes, self.prod, self.log, include_new=False)
        self.assertEqual(rc, 0)
        self.assertEqual((self.prod / "pkg/a.py").read_text(encoding="utf-8"), "new")

    def test_prod_newer_refuses_everything(self):
        now = time.time()
        self._write(self.dev, "pkg/a.py", "old", mtime=now - 3600)
        self._write(self.prod, "pkg/a.py", "hand-edit", mtime=now)
        self._write(self.prod, "pkg/b.py", "old", mtime=now - 3600)
        self._write(self.dev, "pkg/b.py", "new", mtime=now)
        classes = dp.classify(self.dev, self.prod)
        self.assertTrue(classes["prod_newer"])
        rc = dp.apply_copies(classes, self.prod, self.log, include_new=False)
        self.assertEqual(rc, 2)
        self.assertEqual((self.prod / "pkg/a.py").read_text(encoding="utf-8"), "hand-edit")
        self.assertEqual((self.prod / "pkg/b.py").read_text(encoding="utf-8"), "old",
                         "prod-newer refusal must copy NOTHING, not just skip the conflict")
        self.assertFalse(self.log.exists(), "refusal must write no log rows")

    def test_dev_only_gated_behind_include_new(self):
        self._write(self.dev, "pkg/new_mod.py", "fresh")
        classes = dp.classify(self.dev, self.prod)
        self.assertEqual([str(c["rel"]) for c in classes["dev_only"]], ["pkg\\new_mod.py"])
        dp.apply_copies(classes, self.prod, self.log, include_new=False)
        self.assertFalse((self.prod / "pkg/new_mod.py").exists())
        dp.apply_copies(classes, self.prod, self.log, include_new=True)
        self.assertEqual((self.prod / "pkg/new_mod.py").read_text(encoding="utf-8"), "fresh")

    def test_forbidden_paths_raise(self):
        for rel in ("secrets/.env", "data/shipping.db", "pkg/__pycache__/x.pyc"):
            with self.assertRaises(dp.ForbiddenPathError, msg=rel):
                dp._assert_deployable(Path(rel))


if __name__ == "__main__":
    unittest.main()
