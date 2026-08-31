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


CSV_HEAD = '"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result"'


def _csv(*rows: str) -> str:
    return "\n".join([CSV_HEAD, *rows]) + "\n"


class SchtaskAuditTest(unittest.TestCase):
    """The 2026-08-31 widening: SCHTASK_PREFIXES=("appyhour_daily",) made
    `AppyHour\\GorgiasUpdate` structurally invisible for five days while it returned
    0x8007042B every Wednesday."""

    def _run(self, csv_text, now=None):
        import subprocess as _sp
        findings = []
        real = _sp.run
        _sp.run = lambda *a, **k: types.SimpleNamespace(stdout=csv_text)
        try:
            ah.check_schtasks(findings)
        finally:
            _sp.run = real
        return findings

    def test_gorgiasupdate_is_audited_at_all(self):
        """The regression under test: this row produced NO finding before the widening."""
        f = self._run(_csv('"H","\\AppyHour\\GorgiasUpdate","x","Ready",'
                           '"8/26/2026 9:00:00 AM","-2147023829"'))
        self.assertEqual(len(f), 1)
        self.assertIn("GorgiasUpdate", f[0])
        self.assertIn("-2147023829", f[0])

    def test_process_aborted_is_not_reported_as_an_access_violation(self):
        """-2147023829 is 0x8007042B (win32 1067, process KILLED), NOT 0xC0000005. The two
        point at different investigations and the raw decimal is routinely misread."""
        hint = ah._win32_hint("-2147023829")
        self.assertIn("0x8007042B", hint)
        self.assertIn("ERROR_PROCESS_ABORTED", hint)
        self.assertNotIn("ACCESS_VIOLATION", hint)
        self.assertIn("ACCESS_VIOLATION", ah._win32_hint(str(0xC0000005 - (1 << 32))))
        self.assertIn("ERROR_FILE_NOT_FOUND", ah._win32_hint("2"))

    def test_space_named_tasks_get_distinct_dispatch_keys(self):
        """`schtask (\\S+):` captured only "AppyHour", collapsing three tasks onto one key —
        two of them could then never dispatch while the third held the streak."""
        keys = {ah.finding_key(f"schtask '{n}': Last Result 2")
                for n in ("AppyHour Carrier Invoice Sync", "AppyHour Weekly Offsite Backup",
                          "AppyHour Zone Floor Rebuild")}
        self.assertEqual(len(keys), 3)
        # legacy unquoted form still keys (older/handwritten finding text)
        self.assertEqual(ah.finding_key("schtask appyhour_daily_tue: Last Result 1"),
                         "schtask-appyhour_daily_tue")

    def test_unregistered_in_scope_task_is_itself_a_finding(self):
        f = self._run(_csv('"H","\\AppyHour NewThing","x","Ready","8/31/2026 9:00:00 AM","0"'))
        self.assertEqual(len(f), 1)
        self.assertIn("UNREGISTERED", f[0])

    def test_out_of_scope_task_is_ignored(self):
        self.assertEqual(self._run(_csv('"H","\\OneDrive Reporting Task","x","Ready",'
                                        '"8/31/2026 9:00:00 AM","-2147160572"')), [])

    def test_explicit_exclusion_silences_a_task(self):
        ah.SCHTASK_EXCLUDED["appyhour excluded thing"] = "reason recorded here"
        try:
            self.assertEqual(self._run(_csv('"H","\\AppyHour Excluded Thing","x","Ready",'
                                            '"8/31/2026 9:00:00 AM","7"')), [])
        finally:
            del ah.SCHTASK_EXCLUDED["appyhour excluded thing"]

    def test_weekly_threshold_clears_a_catch_up_gap(self):
        """HEARTBEAT_RULES rule 4: weekly gets 10d, NEVER 7d — the machine sleeps through a
        fixed-time slot and the catch-up run legally lands >7d after the last one. An 8-day-old
        weekly run must NOT be graded stale."""
        self.assertEqual(ah.SCHTASK_EXPECTED["appyhour\\postmortemrunner"], 10)
        for wk in ("appyhour\\gorgiasupdate", "appyhour\\safetyfactorsweep",
                   "appyhour weekly offsite backup", "appyhour-vf-archive-refresh"):
            self.assertGreaterEqual(ah.SCHTASK_EXPECTED[wk], 10, wk)

        from datetime import datetime, timedelta
        eight_d = (datetime.now() - timedelta(days=8)).strftime("%m/%d/%Y %I:%M:%S %p")
        self.assertEqual(self._run(_csv(f'"H","\\AppyHour\\PostmortemRunner","x","Ready",'
                                        f'"{eight_d}","0"')), [])
        twelve_d = (datetime.now() - timedelta(days=12)).strftime("%m/%d/%Y %I:%M:%S %p")
        f = self._run(_csv(f'"H","\\AppyHour\\PostmortemRunner","x","Ready","{twelve_d}","0"'))
        self.assertEqual(len(f), 1)
        self.assertIn("has not run for", f[0])

    def test_logon_task_gets_no_staleness_gate(self):
        """Its Last Run Time tracks the last BOOT — an age gate there measures how long Kurt
        has gone without rebooting, which is not a health signal."""
        self.assertIsNone(ah.SCHTASK_EXPECTED["appyhour_sync_on_logon"])
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=90)).strftime("%m/%d/%Y %I:%M:%S %p")
        self.assertEqual(self._run(_csv(f'"H","\\appyhour_sync_on_logon","x","Ready","{old}","0"')), [])

    def test_disabled_expected_task_is_a_finding(self):
        f = self._run(_csv('"H","\\appyhour_daily_tue","x","Disabled","8/31/2026 12:00:00 PM","0"'))
        self.assertEqual(len(f), 1)
        self.assertIn("DISABLED", f[0])

    def test_one_finding_per_task_even_with_several_problems(self):
        from datetime import datetime, timedelta
        old = (datetime.now() - timedelta(days=40)).strftime("%m/%d/%Y %I:%M:%S %p")
        f = self._run(_csv(f'"H","\\appyhour_daily_tue","x","Disabled","{old}","1"'))
        self.assertEqual(len(f), 1)
        for part in ("DISABLED", "Last Result 1", "has not run for"):
            self.assertIn(part, f[0])

    def test_never_run_sentinel_is_not_graded_stale(self):
        self.assertEqual(self._run(_csv('"H","\\appyhour_daily_tue","x","Ready",'
                                        '"11/30/1999 12:00:00 AM","267011"')), [])

    def test_multi_trigger_task_audited_once(self):
        """schtasks /v emits one row PER TRIGGER; appyhour_sync_daily_noon has several."""
        row = '"H","\\appyhour_sync_daily_noon","x","Ready","8/31/2026 12:05:01 PM","9"'
        f = self._run(_csv(row, row, row))
        self.assertEqual(len(f), 1)

    def test_unparseable_last_run_is_loud_not_silent(self):
        f = self._run(_csv('"H","\\appyhour_daily_tue","x","Ready","not-a-date","0"'))
        self.assertEqual(len(f), 1)
        self.assertIn("BLIND", f[0])

    def test_every_registered_name_is_lowercase(self):
        """Lookup is on name.lower(); an uppercase key could never match and would silently
        report UNREGISTERED forever."""
        for k in ah.SCHTASK_EXPECTED:
            self.assertEqual(k, k.lower(), k)
            self.assertTrue(k.startswith(ah.SCHTASK_SCOPE), k)


if __name__ == "__main__":
    unittest.main()
