"""Offline tests for automation_health's WEEKEND SCOPE (HEARTBEAT_RULES rule 20).

The routine went 7-day on 2026-09-07 because the `fulfillments` ingest leg crossed its
threshold on Saturday 2026-09-06 21:36 and nothing looked until Monday 12:15. The scope gate
is what keeps that 7-day cron from becoming weekend noise, so it needs both halves tested:
a weekend-eligible subject past its threshold on a Saturday must be LOUD, and a weekday-only
subject must be SILENT on that same Saturday.

No live run and no live state: the heartbeat ledger is stubbed on the module, the Windows task
list is injected through the memoised `_SCHTASKS_CSV` global, and nothing reaches main() (a red
run Slacks and bumps the real dispatch streaks).
"""
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import automation_health as ah  # noqa: E402

SATURDAY = datetime(2026, 9, 5, 12, 15)
SUNDAY = datetime(2026, 9, 6, 12, 15)
MONDAY = datetime(2026, 9, 7, 12, 15)

_CSV_HEAD = ('"HostName","TaskName","Next Run Time","Status","Logon Mode","Last Run Time",'
             '"Last Result","Author","Task To Run","Start In","Comment","Scheduled Task State",'
             '"Schedule Type","Start Time","Start Date","End Date","Days","Months"')


def _ago(hours: float) -> str:
    """A heartbeats.json timestamp `hours` old. Aware UTC — rule 3b(e): this ledger is aware,
    sync_heartbeat.json is naive local, and they must not be harmonised."""
    return (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()


class IsWeekendRunTest(unittest.TestCase):
    def test_saturday_and_sunday_are_the_weekend(self):
        self.assertTrue(ah.is_weekend_run(SATURDAY))
        self.assertTrue(ah.is_weekend_run(SUNDAY))

    def test_monday_through_friday_are_not(self):
        for d in range(7, 12):  # 2026-09-07 Mon .. 2026-09-11 Fri
            self.assertFalse(ah.is_weekend_run(datetime(2026, 9, d, 12, 15)),
                             f"2026-09-{d:02d} graded as weekend")


class WeekendBeatScopeTest(unittest.TestCase):
    """The core split: same ledger, same staleness, opposite verdicts by subject."""

    def setUp(self):
        self._saved = ah.read_ledger

    def tearDown(self):
        ah.read_ledger = self._saved

    def _findings(self, ledger, weekend):
        ah.read_ledger = lambda: ledger
        out: list[str] = []
        ah.check_beats(out, weekend=weekend)
        return out

    def test_weekend_eligible_beat_past_threshold_is_critical_on_a_weekend(self):
        # offsite-backup = the Sunday 02:00 schtask, the machine's only safety net. 9d > its 8d.
        found = self._findings({"offsite-backup": _ago(9 * 24), "automation-health": _ago(2)},
                               weekend=True)
        self.assertEqual(len(found), 1, found)
        self.assertIn("offsite-backup", found[0])
        self.assertTrue(found[0].startswith("heartbeat STALE"), found[0])

    def test_missing_weekend_eligible_beat_is_also_critical(self):
        found = self._findings({"automation-health": _ago(2)}, weekend=True)
        self.assertEqual([f for f in found if "offsite-backup" in f],
                         ["heartbeat MISSING: offsite-backup (expected every 8d)"])

    def test_weekday_only_beat_is_SILENT_on_a_weekend(self):
        # warm-cohort-report runs Monday; on a Saturday a 5-day-old beat is what HEALTHY looks
        # like. Even blown well past its limit it must not page — nobody can act before Monday,
        # and a guaranteed-wrong weekend alarm is how the channel gets muted (gotcha class A1).
        ledger = {"automation-health": _ago(2), "offsite-backup": _ago(1),
                  "warm-cohort-report": _ago(30 * 24), "truffle-watch": _ago(30 * 24),
                  "carrier-sla-monitor": _ago(30 * 24)}
        self.assertEqual(self._findings(ledger, weekend=True), [])

    def test_the_same_weekday_only_beat_IS_loud_on_a_weekday(self):
        """Guards against the silence above coming from a broken fixture rather than the gate."""
        ledger = {"automation-health": _ago(2), "offsite-backup": _ago(1),
                  "warm-cohort-report": _ago(30 * 24), "truffle-watch": _ago(30 * 24),
                  "carrier-sla-monitor": _ago(30 * 24)}
        found = self._findings(ledger, weekend=False)
        self.assertTrue(any("warm-cohort-report" in f for f in found), found)
        self.assertTrue(any("truffle-watch" in f for f in found), found)

    def test_unreadable_ledger_is_loud_even_on_a_weekend(self):
        """Rule 1: blind is not green. The scope gate narrows WHICH subjects are graded, never
        whether the checker admits it cannot see them."""
        def boom():
            raise OSError("ledger gone")
        ah.read_ledger = boom
        out: list[str] = []
        ah.check_beats(out, weekend=True)
        self.assertEqual(len(out), 1)
        self.assertIn("LEDGER UNREADABLE", out[0])


class WeekendSchtaskScopeTest(unittest.TestCase):
    def setUp(self):
        self._saved = ah._SCHTASKS_CSV

    def tearDown(self):
        ah._SCHTASKS_CSV = self._saved

    def _findings(self, name, weekend, result="1"):
        # 🔴 reset the process-lifetime memo or this grades the previous test's fixture
        ah._SCHTASKS_CSV = None
        last_run = (datetime.now() - timedelta(days=1)).strftime("%m/%d/%Y %I:%M:%S %p")
        ah._SCHTASKS_CSV = (
            _CSV_HEAD + "\n"
            f'"PC","\\{name}","N/A","Ready","Interactive","{last_run}","{result}","me",'
            '"x.bat","","","Enabled","Daily","12:00:00 PM","8/1/2026","N/A","","N/A"\n')
        out: list[str] = []
        ah.check_schtasks(out, weekend=weekend)
        return out

    def test_weekend_eligible_schtask_failure_is_critical_on_a_weekend(self):
        # AppyHour Carrier Invoice Sync fires DAILY 16:00 — a non-zero Last Result on a Saturday
        # is a real, same-day failure.
        found = self._findings("AppyHour Carrier Invoice Sync", weekend=True)
        self.assertEqual(len(found), 1, found)
        self.assertIn("Last Result 1", found[0])

    def test_weekday_only_schtask_failure_is_silent_on_a_weekend(self):
        # \AppyHour\GorgiasUpdate fires Wednesday; on a Saturday its result is Wednesday's news.
        self.assertEqual(self._findings("AppyHour\\GorgiasUpdate", weekend=True), [])
        # ...and is still reported on a weekday, so the silence is the gate, not the fixture.
        self.assertEqual(len(self._findings("AppyHour\\GorgiasUpdate", weekend=False)), 1)

    def test_unregistered_task_is_not_a_weekend_page(self):
        """"Nobody added this to the registry" is Monday hygiene, not an outage — and it would be
        the loudest line in a post that is supposed to be CRITICAL-only."""
        self.assertEqual(self._findings("AppyHour Brand New Thing", weekend=True, result="0"), [])
        self.assertTrue(any("UNREGISTERED" in f for f in
                            self._findings("AppyHour Brand New Thing", weekend=False, result="0")))


class WeekendRegistryIntegrityTest(unittest.TestCase):
    """Rule 4's shape applied to the weekend rosters: an eligibility row for a subject nobody
    grades is an expectation that can neither pass nor fail — the ORPHAN-REGISTRATION class."""

    def test_every_eligible_beat_is_a_real_expectation(self):
        self.assertEqual(ah.WEEKEND_ELIGIBLE_BEATS - set(ah.EXPECTED), set())

    def test_every_eligible_schtask_is_a_real_expectation(self):
        self.assertEqual(ah.SCHTASK_WEEKEND_ELIGIBLE - set(ah.SCHTASK_EXPECTED), set())

    def test_the_checkers_own_beat_is_weekend_eligible(self):
        """Rule 7 + rule 20: on a 7-day dead-man switch, a missing self-beat on Sunday means
        Saturday's run did not happen. If this row is ever dropped, the watcher stops watching
        itself for two days a week — the exact hole the 7-day move closed."""
        self.assertIn("automation-health", ah.WEEKEND_ELIGIBLE_BEATS)


if __name__ == "__main__":
    unittest.main()
