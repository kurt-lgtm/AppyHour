"""Every EXPECTED heartbeat row added 2026-09-03 has a writer, and each writer sits AFTER the work.

HEARTBEAT_RULES rule 16 order: land beat() in the code path that does the work, THEN register the
name in EXPECTED. An EXPECTED row with no writer is a guaranteed future false alarm
(ORPHAN-EXPECTATION); a beat with no row is read by nobody (UNWATCHED-BEAT). These tests pin both
halves for the four live-write/business routines wired from the Migration Triage finding, using
the checker's own tokeniser (`_beats_in_file`) so a beat quoted in a comment cannot satisfy them.
Offline: reads source files only.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKSPACE = ROOT.parent
sys.path.insert(0, str(ROOT / "scripts"))
import automation_health as ah  # noqa: E402

WIRED = {
    "truffle-watch": (ROOT / "InventoryReorder" / "Errors" / "truffle_autoswap.py", 4 * 24),
    "wrong-address-handler": (ROOT / "scripts" / "automations" / "wrong_address_automation.py", 4 * 24),
    "sku-lifecycle-scan": (ROOT / "InventoryReorder" / "Errors" / "sku_lifecycle_scan.py", 10 * 24),
    "carrier-sla-monitor": (WORKSPACE / "_outputs" / "scripts" / "coldchain_health_brief.py", 10 * 24),
}


class WiredBeats(unittest.TestCase):
    def test_each_new_row_is_graded_with_the_schedule_clearing_max_age(self):
        for key, (_, max_h) in WIRED.items():
            self.assertEqual(ah.EXPECTED.get(key), max_h, key)

    def test_each_new_row_has_exactly_its_writer_in_executable_code(self):
        for key, (path, _) in WIRED.items():
            self.assertTrue(path.exists(), path)
            self.assertIn(key, ah._beats_in_file(path), f"{path.name} does not beat {key!r}")

    def test_writer_files_are_inside_the_checkers_scan_roots(self):
        roots = (WORKSPACE / "_outputs" / "scripts", WORKSPACE / "AppyHour", WORKSPACE / "ShipRouting")
        for key, (path, _) in WIRED.items():
            self.assertTrue(any(root in path.parents for root in roots), f"{key}: {path} unscanned")

    def test_truffle_beat_is_skipped_on_the_error_action(self):
        src = (ROOT / "InventoryReorder" / "Errors" / "truffle_autoswap.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)
                  and n.name == "_beat_if_completed")
        # first executable statement (after the docstring) guards on action == "error" and
        # returns before any beat
        body = [s for s in fn.body if not (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))]
        first = body[0]
        self.assertIsInstance(first, ast.If)
        self.assertIsInstance(first.body[0], ast.Return)
        self.assertIn("error", ast.dump(first.test))

    def test_wrong_address_beats_only_on_exit_zero(self):
        src = (ROOT / "scripts" / "automations" / "wrong_address_automation.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        main_guard = next(n for n in tree.body if isinstance(n, ast.If) and "__main__" in ast.dump(n.test))
        beat_ifs = [n for n in ast.walk(main_guard) if isinstance(n, ast.If)
                    and 'beat' in ast.dump(n) and n is not main_guard]
        self.assertTrue(beat_ifs, "beat is not gated")
        self.assertIn("_rc", ast.dump(beat_ifs[0].test))
        self.assertIn("value=0", ast.dump(beat_ifs[0].test))


if __name__ == "__main__":
    sys.exit(unittest.main())
