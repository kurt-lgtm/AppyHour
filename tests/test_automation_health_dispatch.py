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
        # check_schtasks reads through the process-lifetime memo _schtasks_csv() added 2026-08-31
        # (so check_task_set cannot grade a DIFFERENT snapshot of the task list). Clearing it is
        # mandatory here: without this line every test after the first silently re-graded test
        # #1's CSV and 9 of these tests went red for the wrong reason.
        ah._SCHTASKS_CSV = None
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


CSV_HEAD_V = ('"HostName","TaskName","Next Run Time","Status","Last Run Time","Last Result",'
              '"Task To Run","Start In"')
PY = r"C:\Users\Work\anaconda3\python.exe"


def _csv_v(*rows: tuple) -> str:
    """Rows carrying a Task To Run — built with the csv module because the real column embeds
    quotes (`"python.exe" "script.py"`) and schtasks doubles them."""
    import csv
    import io
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\n")
    buf.write(CSV_HEAD_V + "\n")
    for name, cmd, start_in in rows:
        w.writerow(["H", "\\" + name, "x", "Ready", "9/3/2026 9:00:00 AM", "0", cmd, start_in])
    return buf.getvalue()


class ProdEntryPointTest(unittest.TestCase):
    """HEARTBEAT_RULES rule 19: `appyhour_lib` is a pip EDITABLE install mapped to the DEV tree,
    and its finder sits AFTER PathFinder on sys.meta_path — so a prod script that imports the
    library without first putting C:\\AppyHourProd\\AppyHour on sys.path runs DEV library code
    while byte-identical to dev (check_prod_parity says "in sync"). Static AST only: every
    target here is a live schtask action and is never executed."""

    GOOD = """\
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from appyhour_lib.notify import notify  # noqa: E402
        """
    BAD = """\
        import sys
        from pathlib import Path
        from appyhour_lib.notify import notify
        """

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp(prefix="ah-prodtree-"))
        self.tree = self.tmp / "AppyHourProd"
        self.prod = self.tree / "AppyHour"
        (self.prod / "appyhour_lib").mkdir(parents=True)
        (self.prod / "appyhour_lib" / "__init__.py").write_text("", encoding="utf-8")
        self.dev = self.tmp / "Claude Projects" / "AppyHour"
        (self.dev / "appyhour_lib").mkdir(parents=True)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, rel: str, text: str) -> Path:
        import textwrap
        p = self.prod / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(textwrap.dedent(text), encoding="utf-8")
        return p

    def _run(self, *actions: tuple) -> list[str]:
        """actions: (task name, Task To Run[, Start In])."""
        import subprocess as _sp
        rows = [(a[0], a[1], a[2] if len(a) > 2 else "N/A") for a in actions]
        ah._SCHTASKS_CSV = None
        real = _sp.run
        _sp.run = lambda *a, **k: types.SimpleNamespace(stdout=_csv_v(*rows))
        try:
            findings: list[str] = []
            ah.check_prod_entry_points(findings, prod_tree=self.tree, dev_root=self.dev)
        finally:
            _sp.run = real
            ah._SCHTASKS_CSV = None
        return findings

    def test_good_bad_and_bat_wrapper_yield_exactly_one_critical(self):
        good = self._write("scripts/good.py", self.GOOD)
        bad = self._write("scripts/bad.py", self.BAD)
        bat = self._write("scripts/bad.bat", f'@echo off\n"{PY}" "{bad}"\n')
        f = self._run(("Good", f'{PY} "{good}"'), ("Bad", f'"{PY}" "{bad}" --day tue'),
                      ("BadBat", f"{bat} "))
        self.assertEqual(len(f), 1, f)
        self.assertEqual(ah.finding_key(f[0]), "prod-libpath-bad.py")
        self.assertIn("without pinning the prod tree", f[0])
        self.assertIn("DEV library code", f[0])
        for task in ("Bad", "BadBat"):   # both owners named, so the fix reaches both
            self.assertIn(task, f[0])
        self.assertNotIn("good.py", f[0])

    def test_finding_key_is_per_script_and_stable_across_task_lists(self):
        a = "prod entry point 'C:\\AppyHourProd\\AppyHour\\x\\s.py' imports appyhour_lib ... (tasks: t1)"
        b = "prod entry point 'C:\\AppyHourProd\\AppyHour\\x\\s.py' imports appyhour_lib ... (tasks: t1, t2)"
        self.assertEqual(ah.finding_key(a), ah.finding_key(b))
        self.assertEqual(ah.finding_key(a), "prod-libpath-s.py")

    def test_pin_to_own_dir_is_not_a_pin(self):
        """`sys.path.insert(0, HERE)` is the commonest shape and pins nothing — the library
        lives one level up. Presence of an insert is not the test; where it points is."""
        p = self._write("scripts/own_dir.py", """\
            import sys
            from pathlib import Path
            HERE = Path(__file__).resolve().parent
            sys.path.insert(0, str(HERE))
            from appyhour_lib.paths import db_path  # noqa: E402
            """)
        f = self._run(("T", f'{PY} "{p}"'))
        self.assertEqual(len(f), 1, f)
        self.assertIn("without pinning", f[0])

    def test_dev_literal_pin_is_a_finding(self):
        p = self._write("scripts/dev_literal.py", f"""\
            import sys
            sys.path.insert(0, r"{self.dev}")
            import appyhour_lib
            """)
        f = self._run(("T", f'{PY} "{p}"'))
        self.assertEqual(len(f), 1, f)
        self.assertIn("DEV tree pinned", f[0])

    def test_named_parent_chain_resolves(self):
        """sync_logon's shape: PROJECT_DIR = Path(__file__).parent; insert(PROJECT_DIR.parent)."""
        p = self._write("GelPackCalculator/sync_logon.py", """\
            import sys
            from pathlib import Path
            PROJECT_DIR = Path(__file__).parent
            sys.path.insert(0, str(PROJECT_DIR.parent))
            from appyhour_lib import sync_heartbeat  # noqa: E402
            """)
        self.assertEqual(self._run(("T", f'{PY} "{p}"')), [])

    def test_pin_inside_a_for_loop_resolves(self):
        """daily_shipping_sync's shape: the insert runs once per tuple element."""
        p = self._write("GelPackCalculator/daily_shipping_sync.py", """\
            import sys
            from pathlib import Path
            GPC_DIR = Path(__file__).resolve().parent
            APPYHOUR = GPC_DIR.parent
            for d in (GPC_DIR, GPC_DIR / "kori", str(APPYHOUR)):
                if str(d) not in sys.path:
                    sys.path.insert(0, str(d))
            from appyhour_lib import db as ahdb  # noqa: E402
            """)
        self.assertEqual(self._run(("T", f'{PY} "{p}"')), [])

    def test_sibling_pin_covers_the_entry_point(self):
        """run_gorgias_update's shape: the entry pins only its own dir; the module it imports
        does the real pin before ITS appyhour_lib import. Same sys.path, so covered."""
        self._write("AppyHourMCP/utils.py", """\
            import sys
            from pathlib import Path
            APPYHOUR_ROOT = Path(__file__).resolve().parent.parent
            if str(APPYHOUR_ROOT) not in sys.path:
                sys.path.insert(0, str(APPYHOUR_ROOT))
            from appyhour_lib.paths import DATA_ROOT  # noqa: E402
            """)
        (self.prod / "AppyHourMCP" / "tools").mkdir()
        self._write("AppyHourMCP/tools/__init__.py", "")
        self._write("AppyHourMCP/tools/sync.py", "from utils import DATA_ROOT\n")
        p = self._write("AppyHourMCP/run.py", """\
            import sys
            from pathlib import Path
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from tools.sync import DATA_ROOT  # noqa: E402
            """)
        self.assertEqual(self._run(("T", f'{PY} "{p}"')), [])

    def test_unpinned_sibling_import_is_attributed_to_the_entry_point(self):
        self._write("scripts/helper.py", "from appyhour_lib.notify import notify\n")
        p = self._write("scripts/entry.py", "import helper\n")
        f = self._run(("T", f'{PY} "{p}"'))
        self.assertEqual(len(f), 1, f)
        self.assertEqual(ah.finding_key(f[0]), "prod-libpath-entry.py")
        self.assertIn("helper.py", f[0])   # where the import actually is

    def test_function_level_import_with_only_an_own_dir_pin(self):
        """backup_offsite's shape: the only insert is the script's own dir inside one function,
        the library imports are inside others. Nothing ever pins the parent."""
        p = self._write("scripts/backup.py", """\
            import sys
            from pathlib import Path
            REPO_ROOT = Path(__file__).resolve().parents[1]

            def _upload(path):
                sd = str(Path(__file__).resolve().parent)
                if sd not in sys.path:
                    sys.path.insert(0, sd)
                import drive_backup_upload as dbu
                return dbu.upload(path)

            def run():
                from appyhour_lib.heartbeat import beat
                beat("offsite-backup")
            """)
        f = self._run(("T", f'"{PY}" "{p}"'))
        self.assertEqual(len(f), 1, f)
        self.assertIn("backup.py", f[0])

    def test_bat_with_cd_and_relative_script(self):
        """run_carrier_sync.bat's shape: `cd /d "<dir>"` then a bare script name."""
        bad = self._write("GelPackCalculator/sync_carrier_invoices.py", self.BAD)
        bat = self._write("GelPackCalculator/run_carrier_sync.bat", f"""\
            @echo off
            REM Daily carrier-invoice sync -> shipping.db (bad.py mentioned in a REM is ignored)
            set "KMP_DUPLICATE_LIB_OK=TRUE"
            cd /d "{bad.parent}"
            "{PY}" sync_carrier_invoices.py >> "%APPDATA%\\AppyHour\\logs\\carrier_sync.log" 2>&1
            """)
        f = self._run(("Carrier", f"{bat} "))
        self.assertEqual(len(f), 1, f)
        self.assertEqual(ah.finding_key(f[0]), "prod-libpath-sync_carrier_invoices.py")

    def test_bat_with_dp0_variable(self):
        """gorgias_update.bat's shape: set "SCRIPT_DIR=%~dp0" then "%SCRIPT_DIR%script.py"."""
        bad = self._write("AppyHourMCP/run_gorgias_update.py", self.BAD)
        bat = self._write("AppyHourMCP/gorgias_update.bat", f"""\
            @echo off
            setlocal
            set "SCRIPT_DIR=%~dp0"
            set "PYTHON={PY}"
            "%PYTHON%" "%SCRIPT_DIR%run_gorgias_update.py" %* >> "%LOG%" 2>&1
            """)
        f = self._run(("Gorgias", f"{bat} "))
        self.assertEqual(len(f), 1, f)
        self.assertIn("run_gorgias_update.py", f[0])

    def test_one_script_many_tasks_one_finding(self):
        bad = self._write("GelPackCalculator/daily.py", self.BAD)
        f = self._run(*((f"appyhour_daily_{d}", f'{PY} "{bad}" --day {d}')
                        for d in ("tue", "wed", "thu", "fri")))
        self.assertEqual(len(f), 1, f)
        for d in ("tue", "wed", "thu", "fri"):
            self.assertIn(f"appyhour_daily_{d}", f[0])

    def test_actions_outside_the_prod_tree_and_missing_files_are_ignored(self):
        dev_script = self.dev / "scripts" / "x.py"
        dev_script.parent.mkdir(parents=True)
        dev_script.write_text(self.BAD, encoding="utf-8")
        gone = self.prod / "scripts" / "deleted.py"   # check_schtasks' business (Last Result 2)
        self.assertEqual(self._run(("Dev", f'{PY} "{dev_script}"'), ("Gone", f'{PY} "{gone}"')), [])

    def test_script_that_never_imports_the_library_is_clean(self):
        p = self._write("scripts/plain.py", "import json\nprint(json.dumps({}))\n")
        self.assertEqual(self._run(("T", f'{PY} "{p}"')), [])

    def test_type_checking_import_does_not_count(self):
        p = self._write("scripts/typed.py", """\
            from typing import TYPE_CHECKING
            if TYPE_CHECKING:
                from appyhour_lib.paths import DATA_ROOT
            """)
        self.assertEqual(self._run(("T", f'{PY} "{p}"')), [])

    # -- the editable mapping itself --------------------------------------------------
    def _finder(self, target: str) -> Path:
        p = self.tmp / "site-packages" / "__editable___appyhour_1_0_0_finder.py"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("import sys\nMAPPING: dict[str, str] = {'appyhour_lib': %r}\n"
                     "NAMESPACES: dict[str, list[str]] = {}\n" % target, encoding="utf-8")
        return p

    def test_editable_mapping_to_dev_is_green(self):
        f: list[str] = []
        ah.check_editable_mapping(f, finders=[self._finder(str(self.dev / "appyhour_lib"))],
                                  dev_root=self.dev)
        self.assertEqual(f, [])

    def test_editable_mapping_flipped_to_prod_is_a_warn_finding(self):
        f: list[str] = []
        ah.check_editable_mapping(f, finders=[self._finder(str(self.prod / "appyhour_lib"))],
                                  dev_root=self.dev)
        self.assertEqual(len(f), 1, f)
        self.assertIn("WARN", f[0])
        self.assertIn(str(self.prod / "appyhour_lib"), f[0])
        self.assertEqual(ah.finding_key(f[0]), "editable-install-appyhour_lib")

    def test_missing_finder_is_loud(self):
        f: list[str] = []
        ah.check_editable_mapping(f, finders=[], dev_root=self.dev)
        self.assertEqual(len(f), 1, f)
        self.assertIn("NOT FOUND", f[0])
        self.assertEqual(ah.finding_key(f[0]), "editable-install-appyhour_lib")


if __name__ == "__main__":
    unittest.main()
