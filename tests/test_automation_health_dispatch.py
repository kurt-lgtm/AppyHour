"""Offline tests for automation_health's findings->dispatch wiring (HEARTBEAT_RULES rule 12).

No live run: automation_health has no dry mode (a red run Slacks and writes the real
heartbeat ledger + dispatch state), so these tests exercise finding_key() stability and
dispatch_findings() isolation with a STUB finding_dispatch injected via sys.modules —
the real _coordination/finding_dispatch_state.json and handoffs.jsonl are never touched
(finding_dispatch's own state/dedupe behavior is covered by
_coordination/test_finding_dispatch.py).
"""
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import automation_health as ah  # noqa: E402


class FindingKeyTest(unittest.TestCase):
    def test_variable_parts_never_reach_the_key(self):
        # ages/counts grow run over run; the key must not move or streaks never reach 3
        self.assertEqual(ah.finding_key("ingest sync heartbeat stale: 7.0d (max 48h)"),
                         ah.finding_key("ingest sync heartbeat stale: 28.4d (max 48h)"))
        self.assertEqual(ah.finding_key("prod tree STALE vs dev on 9 DB-relevant file(s): a.py"),
                         ah.finding_key("prod tree STALE vs dev on 20 DB-relevant file(s): b.py, c.py"))
        self.assertEqual(ah.finding_key("heartbeat STALE: offsite-backup last 9.1d ago (max 8d)"),
                         ah.finding_key("heartbeat STALE: offsite-backup last 12.0d ago (max 8d)"))

    def test_keys_are_per_entity(self):
        self.assertEqual(ah.finding_key("heartbeat STALE: offsite-backup last 9.1d ago (max 8d)"),
                         "heartbeat-stale-offsite-backup")
        self.assertEqual(ah.finding_key("heartbeat MISSING: freshness-sweep (expected every 8d)"),
                         "heartbeat-missing-freshness-sweep")
        self.assertEqual(ah.finding_key("replica shopify_orders stale: newest x (5.0d, max 4d) — why"),
                         "replica-shopify_orders")
        self.assertEqual(ah.finding_key("replica weather_history EMPTY — why"),
                         "replica-weather_history")
        self.assertEqual(ah.finding_key("replica pull stamp stale: shopify_orders last pulled x"),
                         "replica-pull-stamp")
        self.assertEqual(ah.finding_key("schtask appyhour_daily_sync: Last Result 1 (last run x)"),
                         "schtask-appyhour_daily_sync")
        self.assertEqual(ah.finding_key("prod tree STALE vs dev on 20 DB-relevant file(s): x"),
                         "prod-tree-drift")
        self.assertEqual(ah.finding_key("ingest legs not ok: carriers_status"),
                         "ingest-legs-not-ok")
        self.assertEqual(ah.finding_key("shipping.db unreadable read-only (OSError: x)"),
                         "shipping-db")

    def test_fallback_slug_drops_varying_error_detail(self):
        # the parenthetical error type differs run to run — same key regardless
        self.assertEqual(ah.finding_key("heartbeat LEDGER UNREADABLE (OSError: x) — treat as red"),
                         ah.finding_key("heartbeat LEDGER UNREADABLE (ValueError: y) — treat as red"))
        self.assertEqual(ah.finding_key("prod parity check failed (PermissionError: z) — deploy state unknown"),
                         "prod-parity-check-failed")


class DispatchWiringTest(unittest.TestCase):
    def setUp(self):
        self.calls = {"report": [], "finalize": []}
        stub = types.ModuleType("finding_dispatch")
        stub.SOURCE = "stub"
        stub.report = lambda key, desc, ref="": (
            self.calls["report"].append((key, desc, ref)) or "counted 1/3")
        stub.finalize = lambda seen: self.calls["finalize"].append(list(seen))
        self._saved = sys.modules.get("finding_dispatch")
        sys.modules["finding_dispatch"] = stub

    def tearDown(self):
        if self._saved is not None:
            sys.modules["finding_dispatch"] = self._saved
        else:
            sys.modules.pop("finding_dispatch", None)

    def test_each_finding_reported_then_finalized(self):
        findings = ["ingest sync heartbeat stale: 7.0d (max 48h)",
                    "prod tree STALE vs dev on 9 DB-relevant file(s): a.py"]
        ah.dispatch_findings(findings)
        self.assertEqual([c[0] for c in self.calls["report"]],
                         ["ingest-heartbeat-stale", "prod-tree-drift"])
        self.assertEqual([c[1] for c in self.calls["report"]], findings)  # full text = title
        self.assertTrue(all(c[2].endswith("HEARTBEAT_RULES.md") for c in self.calls["report"]))
        self.assertEqual(self.calls["finalize"],
                         [["ingest-heartbeat-stale", "prod-tree-drift"]])

    def test_green_run_still_finalizes_with_no_keys(self):
        ah.dispatch_findings([])
        self.assertEqual(self.calls["report"], [])
        self.assertEqual(self.calls["finalize"], [[]])

    def test_broken_dispatcher_never_raises_out_of_the_checker(self):
        def boom(key, desc, ref=""):
            raise RuntimeError("dispatcher broken")
        sys.modules["finding_dispatch"].report = boom
        ah.dispatch_findings(["ingest sync heartbeat stale: 7.0d"])  # must not raise

    def test_report_failure_mid_loop_still_finalizes(self):
        """A skipped finalize() freezes every streak, so a finding a fix already cleared keeps
        its count and can still reach 3 — the false-alarm bug one level up (2026-08-31)."""
        def boom_on_second(key, desc, ref=""):
            self.calls["report"].append((key, desc, ref))
            if len(self.calls["report"]) == 2:
                raise RuntimeError("dispatcher broke mid-loop")
            return "counted 1/3"
        sys.modules["finding_dispatch"].report = boom_on_second
        ah.dispatch_findings(["ingest sync heartbeat stale: 7.0d (max 48h)",
                              "prod tree STALE vs dev on 9 DB-relevant file(s): a.py",
                              "shipping.db unreadable read-only (OSError: x)"])
        self.assertEqual(self.calls["finalize"],
                         [["ingest-heartbeat-stale", "prod-tree-drift"]])


if __name__ == "__main__":
    unittest.main()
