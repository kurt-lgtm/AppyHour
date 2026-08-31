"""Offline tests for automation_health.check_task_set — the SET-level checks.

No live run and no live state: the Windows task list is injected through the memoised
`_SCHTASKS_CSV` global, the Claude-routine list through a temp snapshot file, and the script
tree through `WORKSPACE_ROOT`. automation_health has no dry mode (a red run Slacks and bumps
the real dispatch streaks), so nothing here may reach main().

🔴 The FreshnessSweep two-owner state is RECONSTRUCTED here rather than asserted live. It was
resolved on 2026-08-31 — the duplicate `\\AppyHour\\FreshnessSweep` schtask was deleted and the
Claude routine is sole owner — so the live tree no longer contains it. A regression check for a
fixed bug has to carry its own fixture or it silently stops testing anything.
"""
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import automation_health as ah  # noqa: E402

_CSV_HEAD = ('"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time",'
             '"Last Result","Author","Task To Run","Start In","Comment","Scheduled Task State",'
             '"Schedule Type","Start Time","Start Date","End Date","Days","Months"')


def _row(name, run, sched="Weekly", start="12:00:00 PM", days="MON", status="Ready"):
    return (f'"PC","{name}","N/A","{status}","Interactive","8/31/2026 12:00:00 PM","0","me",'
            f'"{run}","","","Enabled","{sched}","{start}","8/1/2026","N/A","{days}","N/A"')


def _csv(*rows):
    return "\n".join([_CSV_HEAD, *rows]) + "\n"


def _snapshot(dirpath, tasks, age_days=0.0):
    p = Path(dirpath) / "snap.json"
    cap = datetime.now(timezone.utc) - timedelta(days=age_days)
    p.write_text(json.dumps({"captured_at": cap.isoformat(), "tasks": tasks}), encoding="utf-8")
    return p


def _routine(task_id, cron, skill_body, enabled=True, jitter=0, dirpath=None):
    skill = Path(dirpath) / f"{task_id}.md"
    skill.write_text(skill_body, encoding="utf-8")
    return {"taskId": task_id, "cronExpression": cron, "enabled": enabled,
            "jitterSeconds": jitter, "path": str(skill)}


class TaskSetHarness(unittest.TestCase):
    """Swaps every input automation_health reads for a temp one, and restores it."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        (self.tmp / "_outputs" / "scripts").mkdir(parents=True)
        (self.tmp / "AppyHour" / "scripts").mkdir(parents=True)
        self._saved = (ah._SCHTASKS_CSV, ah.WORKSPACE_ROOT, dict(ah.TASK_SURFACES))
        ah.WORKSPACE_ROOT = self.tmp

    def tearDown(self):
        ah._SCHTASKS_CSV, ah.WORKSPACE_ROOT, surfaces = self._saved
        ah.TASK_SURFACES.clear()
        ah.TASK_SURFACES.update(surfaces)
        self._tmp.cleanup()

    def script(self, rel, body):
        p = self.tmp / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
        return p

    def run_check(self, csv_text, tasks, age_days=0.0):
        ah._SCHTASKS_CSV = csv_text
        findings: list[str] = []
        ah.check_task_set(findings, snapshot=_snapshot(self.tmp, tasks, age_days))
        return findings

    @staticmethod
    def of(findings, kind):
        return [f for f in findings if f.startswith(f"task-set {kind}")]


class FreshnessSweepReconstruction(TaskSetHarness):
    """The pair that made the dead-man switch unable to see its own owner die."""

    def _state(self):
        # The real script's shape, INCLUDING the comment that quotes another task's beat key —
        # that comment is why a regex-based scanner reported a bogus DUAL-BEAT on first run.
        self.script("_outputs/scripts/freshness_sweep.py",
                    'from appyhour_lib.heartbeat import beat\n'
                    '# 2026-08-31: the deprecated file, while `beat("slack-reship")` wrote canonical\n'
                    'def main():\n'
                    '    """Sweep. Historically this also did beat("shipping-cost-sheet")."""\n'
                    '    beat("freshness-sweep")\n')
        csv_text = _csv(_row(
            "\\AppyHour\\FreshnessSweep",
            "C:\\Users\\Work\\anaconda3\\python.exe C:\\Users\\Work\\Claude Projects"
            "\\_outputs\\scripts\\freshness_sweep.py\"\"",
            days="MON", start="12:00:00 PM"))
        routines = [_routine(
            "freshness-sweep", "30 12 * * 1",
            'Run:\n`PYTHONIOENCODING=utf-8 /c/Users/Work/anaconda3/python.exe '
            '"C:/Users/Work/Claude Projects/_outputs/scripts/freshness_sweep.py"`\n',
            jitter=205, dirpath=self.tmp)]
        return csv_text, routines

    def test_dual_beat_is_reported(self):
        f = self.run_check(*self._state())
        dual = self.of(f, "DUAL-BEAT")
        self.assertEqual(len(dual), 1, f)
        self.assertIn("'freshness-sweep'", dual[0])
        self.assertIn("FreshnessSweep", dual[0])
        # the whole point: name the consequence, not just the duplication
        self.assertIn("CANNOT detect", dual[0])

    def test_dual_owner_is_reported_across_path_spellings(self):
        # schtask names it with backslashes, the routine with forward slashes and a git-bash
        # interpreter. Comparing raw command strings finds nothing here.
        dual = self.of(self.run_check(*self._state()), "DUAL-OWNER")
        self.assertEqual(len(dual), 1, dual)
        self.assertIn("_outputs/scripts/freshness_sweep.py", dual[0])

    def test_no_finding_once_the_duplicate_owner_is_removed(self):
        # Today's real state: schtask deleted, routine sole owner.
        _csv_text, routines = self._state()
        f = self.run_check(_csv(), routines)
        self.assertEqual(self.of(f, "DUAL-BEAT"), [])
        self.assertEqual(self.of(f, "DUAL-OWNER"), [])

    def test_a_disabled_duplicate_is_not_an_owner(self):
        csv_text, routines = self._state()
        routines[0]["enabled"] = False
        f = self.run_check(csv_text, routines)
        self.assertEqual(self.of(f, "DUAL-BEAT"), [])
        self.assertEqual(self.of(f, "DUAL-OWNER"), [])

    def test_beat_key_quoted_in_a_comment_or_docstring_is_not_a_writer(self):
        # freshness_sweep.py quotes beat("slack-reship") in a comment and beat("shipping-cost-sheet")
        # in a docstring. Counting either would file a DUAL-BEAT against a task sharing nothing.
        keys = ah._beats_in_file(self.tmp / "_outputs/scripts/freshness_sweep.py") \
            if (self.tmp / "_outputs/scripts/freshness_sweep.py").exists() else None
        if keys is None:
            self._state()
            keys = ah._beats_in_file(self.tmp / "_outputs/scripts/freshness_sweep.py")
        self.assertEqual(keys, {"freshness-sweep"})


class BenignOverlapStaysSilent(TaskSetHarness):
    """Rule 4: a check that fires on benign overlap is worse than no check."""

    def test_same_script_different_arguments_is_one_job_scheduled_N_times(self):
        self.script("AppyHour/sync.py", "print(1)\n")
        rows = [_row(f"appyhour_daily_{d.lower()}",
                     "C:\\Users\\Work\\anaconda3\\python.exe C:\\AppyHourProd\\AppyHour\\sync.py\""
                     f" --day {d.lower()}\"", days=d)
                for d in ("TUE", "WED", "THU", "FRI")]
        self.assertEqual(self.of(self.run_check(_csv(*rows), []), "DUAL-OWNER"), [])

    def test_a_shared_pipeline_step_is_not_a_duplicated_owner(self):
        self.script("AppyHour/step.py", "print(1)\n")
        self.script("AppyHour/a.py", "print(1)\n")
        self.script("AppyHour/b.py", "print(1)\n")
        body = ('`python "C:/Users/Work/Claude Projects/AppyHour/step.py" X`\n'
                '`python "C:/Users/Work/Claude Projects/AppyHour/{}.py"`\n')
        routines = [_routine("job-a", "0 12 * * 1", body.format("a"), dirpath=self.tmp),
                    _routine("job-b", "0 13 * * 1", body.format("b"), dirpath=self.tmp)]
        self.assertEqual(self.of(self.run_check(_csv(), routines), "DUAL-OWNER"), [])

    def test_same_slot_without_a_shared_surface_is_not_reported(self):
        # a reminder colliding with a sheet writer is not a collision worth waking anyone for
        ah.TASK_SURFACES["a-reminder"] = frozenset()
        ah.TASK_SURFACES["a-sheet-writer"] = frozenset({"gsheets"})
        routines = [_routine("a-reminder", "0 12 * * 2", "x", dirpath=self.tmp),
                    _routine("a-sheet-writer", "0 12 * * 2", "x", dirpath=self.tmp)]
        self.assertEqual(self.of(self.run_check(_csv(), routines), "SAME-SLOT"), [])

    def test_same_slot_with_an_unregistered_surface_is_not_reported(self):
        # an expectation nobody triaged is not an expectation (the widen-the-prefix scar)
        routines = [_routine("brand-new-thing", "0 12 * * 2", "x", dirpath=self.tmp),
                    _routine("another-new-thing", "0 12 * * 2", "x", dirpath=self.tmp)]
        self.assertEqual(self.of(self.run_check(_csv(), routines), "SAME-SLOT"), [])

    def test_allowed_dual_owner_pair_is_suppressed_but_dual_beat_never_is(self):
        self.script("AppyHour/s.py", 'from appyhour_lib.heartbeat import beat\nbeat("k")\n')
        run = ("C:\\Users\\Work\\anaconda3\\python.exe C:\\AppyHourProd\\AppyHour\\s.py\"\"")
        csv_text = _csv(_row("appyhour_x", run, sched="Daily", days="Every 1 day(s)"),
                        _row("appyhour_y", run, sched="Daily", days="Every 1 day(s)"))
        ah.ALLOWED_DUAL_OWNERS[("appyhour_x", "appyhour_y")] = "test reason"
        try:
            f = self.run_check(csv_text, [])
        finally:
            ah.ALLOWED_DUAL_OWNERS.pop(("appyhour_x", "appyhour_y"))
        self.assertEqual(self.of(f, "DUAL-OWNER"), [])
        self.assertEqual(len(self.of(f, "DUAL-BEAT")), 1, f)


class SameSlotReporting(TaskSetHarness):
    def test_shared_surface_at_one_cron_minute_reports_with_the_jitter_gap(self):
        ah.TASK_SURFACES["sheet-one"] = frozenset({"gsheets"})
        ah.TASK_SURFACES["sheet-two"] = frozenset({"gsheets"})
        routines = [_routine("sheet-one", "0 12 * * 2", "x", jitter=19, dirpath=self.tmp),
                    _routine("sheet-two", "0 12 * * 2", "x", jitter=566, dirpath=self.tmp)]
        hits = self.of(self.run_check(_csv(), routines), "SAME-SLOT")
        self.assertEqual(len(hits), 1, hits)
        self.assertIn("Tue 12:00", hits[0])
        self.assertIn("gsheets", hits[0])
        self.assertIn("gap 9m07s", hits[0])   # the jitter separation is STATED, not assumed away

    def test_one_pair_reports_once_even_when_it_shares_many_slots(self):
        ah.TASK_SURFACES["daily-one"] = frozenset({"gsheets"})
        ah.TASK_SURFACES["daily-two"] = frozenset({"gsheets"})
        routines = [_routine("daily-one", "0 12 * * 1-5", "x", dirpath=self.tmp),
                    _routine("daily-two", "0 12 * * 1-5", "x", dirpath=self.tmp)]
        self.assertEqual(len(self.of(self.run_check(_csv(), routines), "SAME-SLOT")), 1)


class OrphanRegistration(TaskSetHarness):
    """A registry row whose task was deleted goes SILENT — check_schtasks only visits rows the
    query returns. Observed live: `appyhour zone floor rebuild`, deleted 2026-08-31."""

    def test_registered_task_that_no_longer_exists_is_reported(self):
        # one real in-scope task present, so the query is provably not empty
        csv_text = _csv(_row("appyhour_daily_tue", "x.py", days="TUE"))
        f = self.run_check(csv_text, [])
        hits = self.of(f, "ORPHAN-REGISTRATION")
        self.assertTrue(hits)
        self.assertTrue(any("appyhour weekly offsite backup" in h for h in hits), hits)
        self.assertFalse(any("appyhour_daily_tue" in h for h in hits), hits)

    def test_an_empty_or_failed_query_reports_nothing_rather_than_everything(self):
        # 🔴 the failure mode this guard exists for: a broken query must not mass-report every
        # registry row as orphaned, which is ~15 findings in one run and instant alarm-deafness
        self.assertEqual(self.of(self.run_check(_csv(), []), "ORPHAN-REGISTRATION"), [])


class SnapshotBlindness(TaskSetHarness):
    def test_stale_snapshot_reports_blind_and_arms_no_routine_pairs(self):
        ah.TASK_SURFACES["sheet-one"] = frozenset({"gsheets"})
        ah.TASK_SURFACES["sheet-two"] = frozenset({"gsheets"})
        routines = [_routine("sheet-one", "0 12 * * 2", "x", dirpath=self.tmp),
                    _routine("sheet-two", "0 12 * * 2", "x", jitter=566, dirpath=self.tmp)]
        f = self.run_check(_csv(), routines, age_days=ah.CLAUDE_TASKS_SNAPSHOT_MAX_D + 1)
        self.assertEqual(len(self.of(f, "SNAPSHOT BLIND")), 1, f)
        # 🔴 A stale snapshot must not arm pairs off possibly-disabled routines. Blind, not green.
        self.assertEqual(self.of(f, "SAME-SLOT"), [])

    def test_missing_snapshot_is_a_finding_not_a_silent_pass(self):
        ah._SCHTASKS_CSV = _csv()
        f: list[str] = []
        ah.check_task_set(f, snapshot=self.tmp / "does-not-exist.json")
        self.assertEqual(len(self.of(f, "SNAPSHOT BLIND")), 1, f)


class DispatchKeys(unittest.TestCase):
    def test_distinct_pairs_never_collapse_onto_one_key(self):
        a = ah.finding_key(f"task-set SAME-SLOT: 'x' and 'y-z' both fire ... [{ah._pair_hash('x', 'y-z')}]")
        b = ah.finding_key(f"task-set SAME-SLOT: 'x-y' and 'z' both fire ... [{ah._pair_hash('x-y', 'z')}]")
        self.assertNotEqual(a, b)

    def test_key_is_stable_across_runs(self):
        h = ah._pair_hash("b", "a")
        self.assertEqual(h, ah._pair_hash("a", "b"))          # order-independent
        self.assertTrue(ah.finding_key(f"task-set DUAL-BEAT: heartbeat 'k' ... [{h}]")
                        .endswith(h))

    def test_each_class_gets_its_own_key(self):
        h = ah._pair_hash("a", "b")
        keys = {ah.finding_key(f"task-set {k}: ... [{h}]")
                for k in ("SAME-SLOT", "DUAL-OWNER", "DUAL-BEAT")}
        self.assertEqual(len(keys), 3)

    def test_orphan_registration_is_keyed_by_the_task_name(self):
        self.assertEqual(
            ah.finding_key("task-set ORPHAN-REGISTRATION: SCHTASK_EXPECTED['appyhour zone floor "
                           "rebuild'] names a Windows task that no longer exists"),
            "taskset-orphan-registration-appyhour zone floor rebuild")

    def test_per_key_findings_are_keyed_by_the_heartbeat_name(self):
        self.assertEqual(
            ah.finding_key("task-set ORPHAN-EXPECTATION: EXPECTED['loop-scorecard'] is graded"),
            "taskset-orphan-expectation-loop-scorecard")
        self.assertEqual(
            ah.finding_key("task-set UNWATCHED-BEAT: live task(s) ['x'] write heartbeat 'k' but"),
            "taskset-unwatched-beat-k")


class CronAndTriggerModelling(unittest.TestCase):
    def test_unmodelled_schedules_return_None_not_an_empty_set(self):
        # None = ineligible for a collision finding. An empty set would read as "never fires",
        # which is a claim this expander is not entitled to make.
        self.assertIsNone(ah._cron_slots("0 12 1 * *"))       # day-of-month
        self.assertIsNone(ah._cron_slots("0 12 * 3 *"))       # month
        self.assertIsNone(ah._cron_slots("nonsense"))
        self.assertIsNone(ah._win_slots("At logon time", "N/A", "N/A"))

    def test_cron_ranges_lists_and_sunday_seven(self):
        self.assertEqual(ah._cron_slots("0 12 * * 2"), {(2, 12, 0)})
        self.assertEqual(len(ah._cron_slots("25 9-19 * * 1-5")), 55)
        self.assertEqual(ah._cron_slots("0 1 * * 7"), ah._cron_slots("0 1 * * 0"))

    def test_windows_daily_covers_every_day_weekly_only_its_days(self):
        self.assertEqual(len(ah._win_slots("Daily ", "12:10:00 PM", "Every 1 day(s)")), 7)
        self.assertEqual(ah._win_slots("Weekly", "12:00:00 PM", "TUE"), {(2, 12, 0)})

    def test_path_spellings_collapse_to_one_id(self):
        ids = {ah._norm_target(p) for p in (
            'C:\\AppyHourProd\\AppyHour\\scripts\\backup_offsite.py',
            '"C:/Users/Work/Claude Projects/AppyHour/scripts/backup_offsite.py"',
            '/c/Users/Work/Claude Projects/AppyHour/scripts/backup_offsite.py')}
        self.assertEqual(ids, {"appyhour/scripts/backup_offsite.py"})

    def test_a_path_containing_a_space_is_extracted(self):
        # "Claude Projects" — a token regex drops every real invocation on this machine
        got = ah._targets_from_text(
            'python.exe "C:/Users/Work/Claude Projects/AppyHour/x.py" --flag')
        self.assertEqual(got, {("appyhour/x.py", "--flag")})

    def test_a_relative_script_after_the_interpreter_is_skipped_not_guessed(self):
        self.assertEqual(
            ah._targets_from_text("C:\\Users\\Work\\anaconda3\\python.exe rebuild_zone_floor.py"),
            set())

    def test_a_script_merely_mentioned_in_prose_is_not_an_invocation(self):
        self.assertEqual(ah._targets_from_text(
            "Do NOT run C:/Users/Work/Claude Projects/AppyHour/danger.py from here."), set())


if __name__ == "__main__":
    unittest.main()
