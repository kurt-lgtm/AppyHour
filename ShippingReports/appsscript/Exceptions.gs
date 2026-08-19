/**
 * #exceptions alerter — hourly ParcelPanel exception sweep -> private Slack channel.
 * Constraints SSOT: ShippingReports/EXCEPTIONS_ALERT_RULES.md (repo). Read it before
 * changing anything here; the rules are authored there first.
 *
 * 🔴 TWO SLACK CLASSES, TWO DESTINATIONS, TWO HELPERS (directive P8, Kurt 2026-08-19):
 *   customer box problem  -> excSlackPost_ -> #exceptions            (Wed-Sun gate + EXC_DRY_RUN)
 *   the job telling on itself -> excSlackOps_ -> appyhour-ops-reader DM (NO gates)
 * Never add a third path to chat.postMessage; both go through excSlackSend_.
 *
 * LIVES IN: the LIVE Running Reship project, scriptId
 * 15K0MrUssFqacWybQAToz6CeHTouRU4IeNY4-DzZ4NeE1rBCCNGpGjAjv, bound to the reship sheet
 * 1weQz0AO... — co-hosted with Code.gs, writing its two tabs onto that same sheet
 * (Kurt 2026-07-31: "what if we just add the exceptions here?").
 *
 * 🔴 RULING (Kurt 2026-08-06, verbatim): "running reship report will be king." Code.gs owns the
 * reserved onOpen and the Reship Report menu. Apps Script concatenates files and runs exactly ONE
 * onOpen — the last definition silently wins — so this file must never define one. Code.gs
 * tail-calls onOpenExceptions (coordinator's 1d8f0aa); that is the only reason the menu appears.
 *
 * 🔴 DEPENDS on Code.gs's shopifyGql_() for the cohort seed. Deleting or renaming it breaks the
 * sweep with "shopifyGql_ is not defined".
 *
 * 🔴 COUPLING, the price of co-hosting: a syntax error in THIS file breaks the whole project and
 * takes the hourly reship report down with it. Run excSelfTest() after every edit.
 * hourlyExceptionSweep must NEVER be called from refresh() — it throws on failure by design,
 * which would abort the reship run. It carries its OWN trigger so the two fail independently.
 *
 * 🔴 DEPLOY: projects/.../content PUT replaces ALL files. Always GET live content and swap only
 * this file — a push carrying just [appsscript, Exceptions] DELETES Code.gs, and vice versa.
 * Assert the resulting file set and Code.gs's length after every push.
 *
 * 🔴 TAB SCOPE: this job owns exactly two tabs, Exceptions and _exc_state. Raw Data, Triage,
 * Product Mix, Reship (ex-`Product Mix (T)`, renamed by Dan 2026-08-12), Daily, TnT2, Lost in
 * Transit and Routing Match belong to the
 * reship report — never read or written here.
 *
 * HISTORY worth keeping (both cost a live debugging cycle):
 *  • Deployed into the wrong project 2026-07-31. A clone of this project kept the name, so BOTH
 *    were titled "Running Reship" — key on parentId/scriptId, NEVER the title. It sat dormant
 *    (a file creates no trigger) and was removed the same day, Code.gs byte-identical throughout.
 *  • A CLONED project does not inherit Script Properties, only code. The clone's first run died
 *    with "Attribute provided with invalid value: Header:null" — a null SHOPIFY_TOKEN reaching
 *    UrlFetchApp, an error naming neither the property nor the cause. excPreflight_ now names it.
 *
 * PROPERTIES (already set on this project): SHOPIFY_STORE, SHOPIFY_TOKEN, PARCELPANEL_API_KEY,
 * SLACK_BOT_TOKEN (= appyhouropsreader U0BG153RTNW, in #exceptions with chat:write + groups:read
 * + groups:history). SLACK_WEBHOOK is NOT read here — it points at #reships.
 *
 * SETUP: excSelfTest() -> "PASS: 20 cases"; hourlyExceptionSweep() once by hand;
 * installExceptionsTrigger() once to schedule it; excListTriggers() to confirm.
 */

// The reship report sheet — this job now writes its Exceptions + _exc_state tabs alongside the
// reship tabs rather than to a separate clone (Kurt 2026-07-31). Same sheet the project is bound
// to, so SpreadsheetApp.getActive() would also work; openById is kept so the target is explicit.
// 🔴 STOP-WRITE GUARD (Kurt via coordinator, 2026-08-07): "do not push anything to the
// #exceptions Slack channel yet." While true, a sweep is fully READ-ONLY — no Slack post, no
// Exceptions row, no state write — and instead logs what it WOULD have posted.
// Defaults ON deliberately: the queue holds 4,289 open orders whose backlog would dump into the
// channel on the first real drain. Kurt decides when the channel goes live; flipping this to
// false is that decision, and nothing else should flip it.
// Dry runs deliberately persist NOTHING: marking an order alerted here would silently swallow
// its real ping later, which is the opposite of the failure this whole job exists to prevent.
var EXC_DRY_RUN = false;

// 🔴 SHEET-RECORD AND SLACK-POST ARE INDEPENDENTLY GATED (Kurt 2026-08-07: record _SHIP_2026-08-03's
// exceptions on the tab, Slack stays silent). Recording while dry does NOT weaken the invariant
// above, because it deliberately does NOT touch `alerted` and does NOT close the record: the row
// lands on the Exceptions tab, and the real Slack ping still fires for that (order, class) on the
// first live sweep. Tab-write dedup rides its OWN key (`rec.logged`), never `rec.alerted`.
// Slack remains hard-blocked by EXC_DRY_RUN inside excSlackPost_ regardless of this flag.
var EXC_RECORD_WHEN_SILENT = true;

// 🔴 FORWARD-ONLY SEEDING (Kurt 2026-08-10: "from now I want just new shit to come in").
// When true, a sweep classifies exactly as normal but records every hit into state as ALREADY
// logged AND already alerted, appending NO row and posting NOTHING. That makes the whole current
// backlog invisible to the tab and — when Slack unmutes — kills the 4,289-order burst, which was
// the open decision. Never leave this on: with it set, genuinely new exceptions are swallowed too.
// Set only by excSeedBacklogAsLogged(), which restores it in a finally block.
var EXC_SEEDING = false;

// 🔴 WEEKLY RHYTHM (Kurt 2026-08-10, committed to Dan): the TAB records every day, Mon-Sun; SLACK
// pings only Wed-Sun. Mon/Tue are labels-created-nothing-moving days — pinging them is noise, so
// those exceptions accumulate silently and post on WEDNESDAY if still live. No extra bookkeeping
// makes that work: `alerted` is only stamped when a post actually happens, so a Mon/Tue hit stays
// un-alerted and the Wednesday sweep picks it up naturally.
// Day-of-week is evaluated in ET, not the script timezone, so a late-evening run cannot land on
// the wrong side of midnight.
var EXC_PING_DAYS = { Wed: 1, Thu: 1, Fri: 1, Sat: 1, Sun: 1 };
var EXC_TZ = 'America/New_York';

function excPingDayET_() {
  return !!EXC_PING_DAYS[Utilities.formatDate(new Date(), EXC_TZ, 'EEE')];
}

var EXC_HOST_SHEET_ID = '1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU';
// 🔴 `EXC_CHANNEL` DELETED 2026-08-19 (directive P8) — do not reintroduce it. A single constant
// named "the channel" is exactly how an ops alert ends up in Dan's ping channel: it reads as
// correct at every call site. There is no "the" channel any more. Use excChannelPings_() or
// excChannelOps_(), and reach them only through excSlackPost_ / excSlackOps_.
// NEVER SLACK_WEBHOOK for either — that property is bound to public #reships.

// 🔴 TWO DESTINATIONS, ONE PER CLASS (directive P8 — Kurt 2026-08-19: "you should message failures
// on the appyhour ops reader channel and not there"). He was looking at
// ":rotating_light: exceptions sweep FAILED: ParcelPanel fetch failing: 9/19 hard failures" sitting
// in #exceptions between two customer pings. Dan reads #exceptions for BOXES; an infra crash there
// is noise to him and is exactly the kind of post that gets a channel muted — and a muted
// #exceptions is the failure this whole job exists to prevent.
//
//   PINGS — a customer's box has a problem  -> #exceptions (private, C0BLKKPAW8P). Unchanged.
//   OPS   — the job telling on itself       -> the appyhour-ops-reader DM (U08R19137UL resolves to
//                                              DM D0BG1541F0A). Kurt only.
//
// 🔴 The ops destination is NOT a new channel and needs NO new Slack scope: `slack_` in Code.gs has
// posted there with THIS SAME `SLACK_BOT_TOKEN` since 2026-07-13 (reship-report FAILED alerts, the
// missing-tab warning). Posting a user id to chat.postMessage opens/reuses the bot DM. Verified by
// reading D0BG1541F0A on 2026-08-19: it already holds this project's Code.gs failure alerts.
//
// Both are overridable by Script Property so routing can change WITHOUT a code push (a push here
// takes the hourly reship report down with it if it lands wrong). 🔴 An unset property falls back
// to the literal below — never to empty, which would post nowhere and lose the alert silently.
var EXC_CHANNEL_PINGS_DEFAULT = 'C0BLKKPAW8P';   // private #exceptions — Kurt + Dan + the bot
var EXC_CHANNEL_OPS_DEFAULT   = 'U08R19137UL';   // -> DM D0BG1541F0A with appyhour-ops-reader

function excChannelPings_() {
  return PropertiesService.getScriptProperties().getProperty('EXC_CHANNEL_PINGS') ||
         EXC_CHANNEL_PINGS_DEFAULT;
}

function excChannelOps_() {
  return PropertiesService.getScriptProperties().getProperty('EXC_CHANNEL_OPS') ||
         EXC_CHANNEL_OPS_DEFAULT;
}
var EXC_LOG_TAB = 'Exceptions';
var EXC_STATE_TAB = '_exc_state';
// 🔴 PACING: ParcelPanel rate-limits hard and per-minute. The proven client
// (GelPackCalculator/parcel_panel.py) sleeps 0.3s between calls and backs off SIXTY-FIVE seconds
// on a 429. The first live run here fired UrlFetchApp.fetchAll in batches of 50 with no pause and
// no retry: 780 of 900 fetches failed — it throttled itself out after roughly the first two
// batches. Small batches with a pause between them, and a single backoff retry, stay under it.
// A 65s backoff cannot be repeated inside the 6-minute execution ceiling, so throttled orders are
// left for the next run rather than retried to death.
var EXC_PP_BATCH = 10;                    // requests per fetchAll
var EXC_PP_PAUSE_MS = 1000;               // pause between batches -> ~10 req/s
var EXC_PP_BACKOFF_MS = 5000;             // one retry for a batch that came back throttled
// 🔴 THROUGHPUT: 400/run was set by guessing, not by measuring, and it starved the queue — on
// 2026-08-07 4,287 of 4,587 seeded orders had NEVER been polled, including 2,325 of the 2,361 in
// the LIVE cohort, so 26 of 27 no-scan boxes aged 3-5 days undetected. At the pacing above, 400
// orders costs ~40s of sleep plus fetch time: well under two minutes of a six-minute ceiling.
// The cap is now bounded by a TIME BUDGET instead of a guess.
// 🔴 The budget is not optional. If the run is killed at 6 minutes, excSaveState_ never executes,
// so last_seen never advances and the NEXT run re-polls the same head of the queue — a starvation
// loop that looks like activity. Stop fetching at the budget, then always save.
var EXC_MAX_POLL_PER_RUN = 1200;          // hard ceiling; the time budget normally binds first
var EXC_TIME_BUDGET_MS = 240000;          // 4 min of fetching, leaving 2 min to write state
var EXC_PP_FAIL_RATIO = 0.2;              // share of NON-throttle failures that means CRITICAL
// 🔴 NEVER_PICKED_UP FLOOR (Kurt 2026-08-10). Was 1 day, which fired at ~32h while carrier scans
// were still landing: of 598 not-picked-up rows on the tab, 299 had a movement scan arrive AFTER
// the row was written (one at +1.5h, one at +16h) — pure feed lag, invisible to any feed at sweep
// time. A floor cannot lose a real case: the sweep re-polls hourly, so raising it DELAYS detection
// rather than dropping it, and the genuine wk0803 never-collected boxes were silent 7-33 DAYS.
var EXC_NEVER_PICKED_MIN_DAYS = 3;

var EXC_COHORTS_BACK = 2;                 // current + previous ship week stay in the poll set

// 🔴 PARCELPANEL WEEKLY BUDGET — 2,500 calls/week, ACCOUNT-WIDE (Kurt, standing). This job polls
// hourly = 168 runs/week and was never measured against it. Measured 2026-08-10: 487 open orders
// x 168 runs = 81,816 calls/week, THIRTY-THREE TIMES the cap, and a full fresh cohort (~2,300
// open) would project 386k. The reship refresh's ~180/week was never the risk; this was.
// Three controls, in order of how much they save:
//   1. excResolveDelivered_ closes delivered orders using SHOPIFY (free) before PP is called at
//      all — the same narrowing that took the analytics refresh from 2,300 to 45.
//   2. a shared WEEKLY counter both jobs draw from, so total consumption is visible in one place.
//   3. a per-run cap as a backstop.
var EXC_PP_WEEKLY_BUDGET = 2000;          // this sweep's RESERVATION inside the account quota
var EXC_PP_MAX_PER_RUN = 120;             // backstop; the weekly counter normally binds first
// 🔴 THE COUNTER WAS FICTION — ONE ACCOUNTANT, FOUR SPENDERS (Kurt 2026-08-19, directive P3).
// `PP_WEEK_USED` was decremented by THIS FILE ONLY, while `Code.gs ppLookup_` (UNCAPPED, hourly
// with the reship refresh) and `PivotAnalytics.gs paPpFetch_` (200/run, daily) spent the same
// ACCOUNT-WIDE 2,500/wk quota silently. A number that measures one of at least four drains cannot
// answer "how much is left", and every pacing decision above was computed from it.
// The counter is now a LEDGER: "<wkKey>|<total>|<exc>|<rpt>|<pa>".
//   exc = this sweep · rpt = Code.gs ppLookup_ (reship report) · pa = PivotAnalytics paPpFetch_
// 🔴 NOT COUNTED, AND THE NUMBER IS THEREFORE A LOWER BOUND — say so, never imply completeness:
//   - `ShipRouting/server/sync_delivery_status.py` (PP_CAP_PER_RUN=300, hourly on the cloud ingest
//     worker, gated DELIVERY_SYNC). It is a different runtime with no access to these properties.
//     If that flag is 1 the account quota is being spent entirely outside this project.
//   - ad-hoc local python probes.
// The webhook standing up on DigitalOcean replaces polling outright; this ledger is interim.
var EXC_PP_ACCOUNT_QUOTA = 2500;          // ParcelPanel PLAN, account-wide, ALL consumers
var EXC_PP_BUDGET_PROP = 'PP_WEEK_USED';  // "<wkKey>|<total>|<exc>|<rpt>|<pa>" — shared ledger
// 🔴 THE BUDGET MUST BE PACED, NOT SPENT FIRST-COME (Kurt 2026-08-19). Measured on _exc_state's
// own last_seen column for week 33: Mon 8/11 polled 1,676 orders and Tue 8/12 polled 651 — then
// Wed 8/13, Thu 8/14 and Fri 8/15 polled ZERO. Mon/Tue are exactly the days this job is FORBIDDEN
// to post on (record-only, per the Wed-Sun day gate), so the entire weekly ParcelPanel allowance
// was consumed by the two silent days and the whole ping window ran starved. A run that takes 0
// finds no exceptions, throws nothing and posts nothing — indistinguishable from a quiet week,
// which is the exact failure this job exists to prevent.
// The fix is arithmetic, not a bigger number: 2,000 calls / 168 hourly runs is ~12 per run, but
// the per-run cap was 120, so ~17 consecutive runs could drain the week. Each run now takes at
// most its fair share of what REMAINS, spread over the runs still left in the week.
// 🔴 THE FLOOR IS NOW CONDITIONAL (directive P6). An unconditional floor is not a floor, it is a
// second budget: 109 runs still left in a week x 10 = 1,090 calls that pacing is powerless to
// prevent, spent on a poll set that the once-per-day tier says is not due. The floor may only
// apply when the remaining budget can AFFORD it for EVERY remaining run.
var EXC_PP_MIN_PER_RUN = 10;              // floor; applied only while left >= runsLeft * floor
// 🔴 POLL POLICY — Kurt-approved interim (a)+(b)+(c), 2026-08-19. See directive P5.
// (a) SHOPIFY TRIAGES FIRST. `excResolveDelivered_` already fetches fulfillment displayStatus and
//     events for the whole triage window on a request that was being made anyway — free against
//     this quota. ParcelPanel is then asked ONLY about the ambiguous remainder.
// (b) AT MOST ONCE PER DAY PER BOX, longest-unpolled first.
// (c) AGE GATE. Measured on the local mirror (mature cohorts 07-20, 07-27, 08-03, 08-10): 94-99%
//     of a cohort is still legitimately in transit on day +1/+2, and NO ping class can fire then
//     (EXC_NEVER_PICKED_MIN_DAYS = 3, EXC_DELAYED_MIN_DAYS = 3). Polling the day-1/-2 mass is
//     where the entire budget went and it can produce nothing.
var EXC_POLL_MIN_AGE_DAYS = 3;            // cohort age before the untriaged mass is worth polling
// 🔴 BUT NOT ZERO ON MON/TUE. EXC_CP_SCAN = 5: a Monday exception buried under more than five
// routine facility scans by Wednesday is invisible forever — the exact #170893 failure mode that
// 112ba5a was written to fix. So Mon/Tue keep a THIN sweep, restricted to boxes Shopify already
// flags (DELAYED / ATTEMPTED_DELIVERY / no movement scan at all), which cost nothing to identify.
var EXC_THIN_DAY_CAP = 100;               // hard per-DAY ceiling for the Mon/Tue record-only sweep
var EXC_THIN_DAY_PROP = 'EXC_THIN_DAY';   // "<yyyy-MM-dd>|<charged>" in the stamp's timezone
// Heartbeat + silent-starvation alarm. A dead trigger and a zero-budget run look identical from
// the outside — both are silence — so the sweep stamps every run and shouts when it polls nothing.
var EXC_HEARTBEAT_PROP = 'EXC_LAST_RUN_AT';
var EXC_SILENT_ALARM_PROP = 'EXC_LAST_SILENT_ALARM';
var EXC_SILENT_ALARM_EVERY_MS = 6 * 60 * 60 * 1000;   // at most one starvation alarm per 6h

/**
 * How many hourly runs remain in this ET week, counting the current one. Monday-start, because
 * the week key rolls on Monday; a Sunday-evening run must not think it has 168 runs left.
 */
/** Open orders that have never been polled once — the queue's starvation depth. */
function excNeverPolled_(st, openKeys) {
  return openKeys.filter(function (k) {
    return !String((st[k] && st[k].last_seen) || '').trim();
  }).length;
}

function excRunsLeftThisWeek_() {
  var now = new Date();
  var dow = Number(Utilities.formatDate(now, EXC_TZ, 'u'));   // 1=Mon .. 7=Sun
  var hr = Number(Utilities.formatDate(now, EXC_TZ, 'H'));    // 0..23
  return Math.max(1, (7 - dow) * 24 + (24 - hr));
}

/**
 * 🔴 MONDAY-ANCHORED WEEK KEY (directive P2). The old key was
 * `formatDate(now, tz, 'ww')` — Java's `ww` is LOCALE week-of-year and the US locale starts the
 * week on SUNDAY, while `excRunsLeftThisWeek_` counts hours to Sunday-midnight from a MONDAY
 * start. The two disagreed by a day, and the disagreement is not theoretical: week 33 was already
 * drained (1,676 + 651 = 2,327 >= 2,000) yet Sun 2026-08-16 20:00 and 21:00 stamped 22 and 63
 * orders — only possible if the counter had already reset before Sunday evening. With pacing in
 * force that mismatch is WORSE, not better: on a Sunday `runsLeft = 24 - hr <= 24` against
 * `left = 2000`, so `pace = ceil(2000/24) = 84` and 24 Sunday runs x 84 = 2,016 — the entire
 * week's allowance legally spent on the one day, recurring Sun 2026-08-23.
 *
 * ONE CONVENTION, STATED: the ParcelPanel week runs **Monday 00:00 ET through Sunday 24:00 ET**.
 * The key is that Monday's ET calendar date. `excRunsLeftThisWeek_` already uses it.
 *
 * The day arithmetic is done on a UTC noon anchor built from the ET calendar parts, so a DST
 * transition cannot shift the answer to the previous day (a naive `now - 6*86400000` formatted in
 * ET lands on Sunday 23:00 for a Sunday-00:30 run in a fall-back week).
 *
 * ⚠️ The key FORMAT changes with this deploy ('2026-W34' -> 'WK2026-08-17'), so the first run
 * after the push sees a key mismatch and resets the ledger to zero. That is deliberate — see
 * directive P7 for why zeroing this week is the correct opening balance.
 */
function excWeekKey_() {
  var now = new Date();
  var dow = Number(Utilities.formatDate(now, EXC_TZ, 'u'));                 // 1=Mon .. 7=Sun
  var ymd = Utilities.formatDate(now, EXC_TZ, 'yyyy-MM-dd').split('-');
  var anchor = new Date(Date.UTC(Number(ymd[0]), Number(ymd[1]) - 1, Number(ymd[2]), 12, 0, 0));
  anchor.setUTCDate(anchor.getUTCDate() - (dow - 1));
  return 'WK' + Utilities.formatDate(anchor, 'UTC', 'yyyy-MM-dd');
}

/** Read the shared ledger, rolling it to zero when the week key has changed. */
function excBudgetRead_() {
  var raw = String(PropertiesService.getScriptProperties().getProperty(EXC_PP_BUDGET_PROP) || '');
  var p = raw.split('|'), wk = excWeekKey_();
  if (p[0] !== wk) {
    if (p[0]) Logger.log('  PP ledger: new week ' + wk + ', reset from "' + raw + '"');
    return { wk: wk, total: 0, exc: 0, rpt: 0, pa: 0 };
  }
  var total = parseInt(p[1], 10) || 0;
  // Legacy "<key>|<count>" rows carried one number, and it was this sweep's spend.
  return { wk: wk, total: total,
           exc: (p.length > 2 ? (parseInt(p[2], 10) || 0) : total),
           rpt: parseInt(p[3], 10) || 0,
           pa:  parseInt(p[4], 10) || 0 };
}

function excBudgetWrite_(b) {
  PropertiesService.getScriptProperties().setProperty(
    EXC_PP_BUDGET_PROP, [b.wk, b.total, b.exc, b.rpt, b.pa].join('|'));
}

/**
 * Move `n` calls onto (n > 0) or off (n < 0) the shared ledger for one consumer.
 * `who` is 'exc' | 'rpt' | 'pa'. Never throws — an accounting failure must not take a caller down.
 *
 * 🔴 This is a RECORDER for `rpt`/`pa`, not a gate. The reship report's carrier/transit enrichment
 * and the analytics union are shipped surfaces Dan reads; starving them to protect this sweep
 * would trade a visible number for an invisible one. They report honestly and are allowed to
 * overdraw; the sweep is the consumer that yields, because it is the one that can retry next hour.
 */
function excBudgetCharge_(n, who) {
  n = Math.round(Number(n) || 0);
  if (!n) return;
  try {
    var b = excBudgetRead_();
    var k = (who === 'rpt' || who === 'pa') ? who : 'exc';
    b[k] = Math.max(0, b[k] + n);
    b.total = Math.max(0, b.total + n);
    excBudgetWrite_(b);
    Logger.log('  PP ledger ' + (n > 0 ? '+' : '') + n + ' [' + k + '] -> total ' + b.total +
               '/' + EXC_PP_ACCOUNT_QUOTA + ' (exc ' + b.exc + ', rpt ' + b.rpt + ', pa ' + b.pa +
               ') week ' + b.wk);
    if (b.total > EXC_PP_ACCOUNT_QUOTA) {
      Logger.log('  🔴 PP ACCOUNT QUOTA EXCEEDED: ' + b.total + ' > ' + EXC_PP_ACCOUNT_QUOTA +
                 ' this week. Every consumer is now spending into 429 territory.');
    }
  } catch (e) { Logger.log('  PP ledger charge failed (' + who + ', ' + n + '): ' + e); }
}

/** Calls still available account-wide this week, across every consumer that reports. */
function excBudgetLeftAccount_() {
  try { return Math.max(0, EXC_PP_ACCOUNT_QUOTA - excBudgetRead_().total); }
  catch (e) { Logger.log('  PP ledger read failed: ' + e); return EXC_PP_ACCOUNT_QUOTA; }
}

/**
 * Reserve up to `want` ParcelPanel calls for THIS SWEEP. Returns how many are allowed. Loud when
 * it bites: a silently truncated poll set reads as "no exceptions found", which is the failure
 * mode this whole job exists to prevent.
 *
 * 🔴 RESERVE-THEN-SETTLE (directive P1). This used to charge the counter BEFORE `excPpFetch_` ran
 * and never credit anything back, so a run that was throttled out or killed at the 6-minute
 * ceiling spent its whole reservation on nothing. Week 34 is the proof: 2,000 charged by
 * Wednesday while `_exc_state` recorded ZERO orders polled Mon/Tue/Wed — ~1,912 calls that bought
 * no observation at all, and the live `_SHIP_2026-08-17` cohort (1,344 open) never polled once.
 * The reservation still happens first (two concurrent runs must not both spend the same balance),
 * but `excBudgetSettle_` credits back everything ParcelPanel never actually served.
 */
function excBudgetTake_(want) {
  var b = excBudgetRead_();
  var leftMine = Math.max(0, EXC_PP_WEEKLY_BUDGET - b.exc);
  var leftAcct = Math.max(0, EXC_PP_ACCOUNT_QUOTA - b.total);
  var left = Math.min(leftMine, leftAcct);
  var runsLeft = excRunsLeftThisWeek_();
  var fair = Math.ceil(left / runsLeft);
  // The floor only applies while the budget can afford it for every run still to come.
  var pace = (left >= runsLeft * EXC_PP_MIN_PER_RUN) ? Math.max(EXC_PP_MIN_PER_RUN, fair) : fair;
  var take = Math.max(0, Math.min(want, left, EXC_PP_MAX_PER_RUN, pace));
  Logger.log('  PP pace: ' + pace + '/run (' + left + ' left over ' + runsLeft + ' runs; mine ' +
             leftMine + ', account ' + leftAcct + ')');
  if (take < want) {
    Logger.log('  🔴 PP BUDGET BIT: wanted ' + want + ', taking ' + take +
               ' (exc ' + b.exc + '/' + EXC_PP_WEEKLY_BUDGET + ', account total ' + b.total + '/' +
               EXC_PP_ACCOUNT_QUOTA + ', per-run cap ' + EXC_PP_MAX_PER_RUN +
               '). Unpolled orders stay queued, NOT silently dropped.');
  }
  excBudgetCharge_(take, 'exc');
  return take;
}

/**
 * Credit back the part of a reservation ParcelPanel never served. `charged` is what `excPpFetch_`
 * counted as genuinely dispatched-and-answered (any response that was not 429/503); everything
 * else — throttled batches, requests never reached before the time budget, orders the run died
 * before touching — is refunded. Returns the refund for logging.
 */
function excBudgetSettle_(reserved, charged, who) {
  var refund = Math.max(0, (Number(reserved) || 0) - (Number(charged) || 0));
  if (refund) {
    Logger.log('  PP refund: ' + refund + ' of ' + reserved + ' reserved were never served ' +
               '(throttled / unreached) — credited back, not burned.');
    excBudgetCharge_(-refund, who);
  }
  return refund;
}

/** Day key in the SAME timezone `last_seen` is stamped in, so "already polled today" is exact. */
function excStampDay_(d) {
  return Utilities.formatDate(d || new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

/** Calls left in today's Mon/Tue thin-sweep allowance. Independent of the weekly ledger. */
function excThinDayLeft_() {
  var raw = String(PropertiesService.getScriptProperties().getProperty(EXC_THIN_DAY_PROP) || '');
  var p = raw.split('|'), today = excStampDay_();
  var used = (p[0] === today) ? (parseInt(p[1], 10) || 0) : 0;
  return Math.max(0, EXC_THIN_DAY_CAP - used);
}

function excThinDayAdd_(n) {
  n = Math.round(Number(n) || 0);
  if (!n) return;
  try {
    var props = PropertiesService.getScriptProperties();
    var raw = String(props.getProperty(EXC_THIN_DAY_PROP) || '');
    var p = raw.split('|'), today = excStampDay_();
    var used = (p[0] === today) ? (parseInt(p[1], 10) || 0) : 0;
    props.setProperty(EXC_THIN_DAY_PROP, today + '|' + Math.max(0, used + n));
    Logger.log('  thin-sweep day counter: ' + Math.max(0, used + n) + '/' + EXC_THIN_DAY_CAP +
               ' (' + today + ')');
  } catch (e) { Logger.log('  thin-sweep counter failed: ' + e); }
}

/**
 * MANUAL LEVER — zero the shared weekly ledger for the CURRENT week. Menu item, never automatic.
 * Use only when the recorded spend is known to be fiction (e.g. week 34, where 1,912 of 2,000
 * were charged for calls that produced no observation). State the reason in the log.
 */
function excResetWeeklyBudget() {
  var before = String(PropertiesService.getScriptProperties().getProperty(EXC_PP_BUDGET_PROP) || '(unset)');
  var b = { wk: excWeekKey_(), total: 0, exc: 0, rpt: 0, pa: 0 };
  excBudgetWrite_(b);
  var msg = 'PP weekly ledger reset\n  was: ' + before + '\n  now: ' +
            [b.wk, b.total, b.exc, b.rpt, b.pa].join('|');
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('ParcelPanel budget', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
  return msg;
}

/**
 * 🔴 FILTER BEFORE CALLING PP (Kurt 2026-08-10). Shopify already knows which of these boxes are
 * DELIVERED, and asking Shopify costs nothing against the ParcelPanel budget. Close them here so
 * they leave the poll set permanently. A delivered box cannot become an exception.
 * Returns the orders still worth polling.
 */
var EXC_MOVED_ = { IN_TRANSIT: 1, OUT_FOR_DELIVERY: 1, ATTEMPTED_DELIVERY: 1,
                   READY_FOR_PICKUP: 1, PICKED_UP: 1, DELIVERED: 1 };
var EXC_SHOPIFY_MOVED_ = {};   // order -> 1 when Shopify has a real movement scan (union input)
// order -> 1 when Shopify's fulfillment displayStatus is DELAYED. Same free ride as the movement
// union: it comes off the request below, so the DELAYED class costs ZERO ParcelPanel budget.
var EXC_SHOPIFY_DELAYED_ = {};
// order -> 1 when Shopify's fulfillment displayStatus is ATTEMPTED_DELIVERY. Same free ride.
// 🔴 This map plus DELAYED plus "no movement scan at all" IS the Shopify triage of directive P5(a):
// the set ParcelPanel is still worth asking about. Everything else Shopify has already answered.
var EXC_SHOPIFY_ATTEMPTED_ = {};

function excResolveDelivered_(orders, st) {
  EXC_SHOPIFY_MOVED_ = {};
  EXC_SHOPIFY_DELAYED_ = {};
  EXC_SHOPIFY_ATTEMPTED_ = {};
  var alive = [], closed = 0;
  for (var i = 0; i < orders.length; i += 100) {
    var batch = orders.slice(i, i + 100);
    // 🔴 ONE Shopify call serves BOTH jobs: closing delivered orders AND supplying the union's
    // movement signal. The union therefore costs ZERO extra ParcelPanel budget — it rides on a
    // request that was already being made.
    var q = 'query($q:String!){orders(first:100, query:$q){edges{node{ name ' +
            'fulfillments(first:10){ displayStatus events(first:50){edges{node{status}}} } }}}}';
    var qs = batch.map(function (n) { return 'name:' + n; }).join(' OR ');
    var d;
    try {
      d = shopifyGql_(q, { q: qs });
    } catch (e) {
      Logger.log('  ⚠️ delivered-filter batch failed, polling it anyway: ' + e);
      alive = alive.concat(batch);
      continue;
    }
    var delivered = {};
    d.orders.edges.forEach(function (e) {
      var num = String(e.node.name).replace(/^#/, '');
      (e.node.fulfillments || []).forEach(function (f) {
        if (f.displayStatus === 'DELIVERED') { delivered[num] = 1; }
        if (f.displayStatus === 'DELAYED') { EXC_SHOPIFY_DELAYED_[num] = 1; }
        if (f.displayStatus === 'ATTEMPTED_DELIVERY') { EXC_SHOPIFY_ATTEMPTED_[num] = 1; }
        (((f.events || {}).edges) || []).forEach(function (x) {
          if (x.node.status === 'DELIVERED') delivered[num] = 1;
          if (x.node.status === 'ATTEMPTED_DELIVERY') EXC_SHOPIFY_ATTEMPTED_[num] = 1;
          if (EXC_MOVED_[x.node.status]) EXC_SHOPIFY_MOVED_[num] = 1;   // union signal
        });
      });
    });
    batch.forEach(function (n) {
      if (delivered[n]) { if (st[n]) st[n].open = false; closed += 1; }
      else alive.push(n);
    });
  }
  Logger.log('  pre-PP filter: ' + orders.length + ' open -> ' + alive.length +
             ' pollable (' + closed + ' already DELIVERED per Shopify, closed for good)');
  return alive;
}

/** Whole days since a `_SHIP_yyyy-MM-dd` cohort tag's ship Monday. -1 when unparseable. */
function excCohortAgeDays_(cohort) {
  var m = String(cohort || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return -1;
  var ship = Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]), 12, 0, 0);
  var t = excStampDay_().split('-');
  var today = Date.UTC(Number(t[0]), Number(t[1]) - 1, Number(t[2]), 12, 0, 0);
  return Math.round((today - ship) / 86400000);
}

/**
 * Directive P5(a): the ambiguous set Shopify could NOT settle for free — a fulfillment Shopify
 * calls DELAYED or ATTEMPTED_DELIVERY, or a box with no movement scan at all (the never-picked-up
 * class). A box Shopify shows moving normally is not worth a ParcelPanel call today.
 */
function excShopifyFlagged_(on) {
  return !!(EXC_SHOPIFY_DELAYED_[on] || EXC_SHOPIFY_ATTEMPTED_[on] || !EXC_SHOPIFY_MOVED_[on]);
}

/**
 * Build the ordered poll set from the Shopify-narrowed survivors. Directive P5, (a)+(b)+(c):
 *   (a) Shopify triaged first (excResolveDelivered_ already ran and closed the delivered);
 *   (b) at most once per calendar day per box, LONGEST-UNPOLLED FIRST;
 *   (c) cohort age >= EXC_POLL_MIN_AGE_DAYS, OR Shopify-flagged. Mon/Tue: flagged ONLY.
 * Returns the eligible order numbers in poll order. Never truncates — the caller's budget does
 * that, loudly, so "we ran out" and "there was nothing to do" stay distinguishable.
 */
function excPollSet_(pollable, st) {
  var today = excStampDay_();
  var pingDay = excPingDayET_();
  var skipToday = 0, skipYoung = 0, skipMoving = 0;
  var out = pollable.filter(function (on) {
    var rec = st[on];
    if (!rec) return false;
    if (String(rec.last_seen || '').slice(0, 10) === today) { skipToday++; return false; }
    var flagged = excShopifyFlagged_(on);
    if (!pingDay) { if (!flagged) { skipMoving++; return false; } return true; }
    if (flagged) return true;
    if (excCohortAgeDays_(rec.cohort) >= EXC_POLL_MIN_AGE_DAYS) return true;
    skipYoung++;
    return false;
  });
  out.sort(function (a, b) {
    var sa = String(st[a].last_seen || ''), sb = String(st[b].last_seen || '');
    if (sa !== sb) return sa < sb ? -1 : 1;                  // never-polled ('') sorts first
    var ca = String(st[a].cohort || ''), cb = String(st[b].cohort || '');
    return cb < ca ? -1 : (cb > ca ? 1 : 0);                 // tiebreak: newest cohort first
  });
  Logger.log('  poll set: ' + out.length + ' of ' + pollable.length + ' pollable eligible' +
             ' (skipped: ' + skipToday + ' already polled today, ' + skipYoung +
             ' younger than ' + EXC_POLL_MIN_AGE_DAYS + 'd and moving normally, ' + skipMoving +
             ' moving normally on a record-only day)' +
             (pingDay ? '' : ' [Mon/Tue THIN sweep — Shopify-flagged only]'));
  return out;
}

function excSS_() { return SpreadsheetApp.openById(EXC_HOST_SHEET_ID); }

// ---------------------------------------------------------------- classification

/**
 * Classify a PP shipment. Returns {cls, detail, ping}.
 *
 * 🔴 Classify on the checkpoint `detail` text, NEVER the status bucket: on real 6/29-7/20 data
 * 23 of 71 exception-bucket boxes had ALREADY been delivered (Veho stamps an exception scan en
 * route and never flips back). Status-based alerting = ~1 in 3 pings false, channel gets muted.
 * 🔴 checkpoints are NEWEST-FIRST and the text lives in `detail` (not description/message) —
 * reading [-1] or the wrong key yields blank, which is what silently broke the local sync for
 * its entire life (fixed 2026-07-30, GelPackCalculator@b49a4ba).
 * Checkpoints with a null `status` are AppyHour storefront copy injected into the PP timeline
 * ("Orders are prepared fresh weekly"), not carrier scans — skip them.
 */
/**
 * 🔴 STRUCTURED FIELD BEATS FREE TEXT (Kurt 2026-08-10) — and one feed is never enough.
 * `movedElsewhere` is a movement scan seen in the OTHER feed (Shopify fulfillment events). PP's
 * checkpoint PROSE keeps saying "we have yet to receive the package" after the box has moved, so
 * classifying off that text alone invented 224 never-picked-up rows whose scan Shopify already
 * held at sweep time. Same family as CONFIRMED-is-not-movement: never let narrative text outrank
 * a structured signal, and never trust a single feed.
 */
// 🔴 HOW MANY CHECKPOINTS THE CLASSIFIER SEES (Kurt 2026-08-17). Was ONE — `checkpoints[0]`, the
// newest carrier scan. That is why #170893 (Lisa Olson, VA) never fired: FedEx stamped
// "Delivery exception, Incorrect address, HERNDON VA 20171" on 8/14 19:53 and then a perfectly
// benign "At local FedEx facility" on 8/15 09:31. One harmless later scan made a real failure
// invisible forever, because the sweep only ever looked at the top of the list.
// Five is deliberate: enough to see through a burst of routine facility scans (the observed gap
// was ONE scan), small enough that a week-old resolved exception on a long-transit box cannot
// resurface. Widening this without a replay is exactly the move EXCEPTIONS_ALERT_RULES.md forbids.
var EXC_CP_SCAN = 5;

// 🔴 DELAYED floor. Shopify stamps displayStatus DELAYED off routine carrier "Package Delayed"
// scans that clear within a day, so firing on the flag alone would flood the channel with ordinary
// in-transit noise — the one failure mode this job must never have. Three days is the same floor
// and the same reasoning as EXC_NEVER_PICKED_MIN_DAYS: by then the 2-day promise is already broken,
// so a still-DELAYED box is a real failure rather than feed lag, and because the sweep re-polls
// hourly a floor DELAYS detection rather than dropping it. #169174 (Maria Wood, NY) sat 4 days
// between label and pickup — it clears this floor comfortably.
var EXC_DELAYED_MIN_DAYS = 3;

/**
 * The failure matcher, factored OUT of excClassify_ so it can be run over a WINDOW of checkpoints
 * rather than only the newest one. Returns a class token, or '' when the text is not a failure.
 *
 * 🔴 Order inside here is load-bearing and must not be rearranged: "delivered" is a SUBSTRING of
 * "unable to be delivered", so every failure class is tested before any caller applies the
 * bare-text delivered suppress. Caught by excSelfTest under node, 2026-07-30.
 * Phrasing varies a LOT by carrier. Every alternative below came off a real event — 5 genuine
 * failures sat in IN_NETWORK until this was widened ("returned to the SELLER" not sender, "unable
 * to DELIVER" not to be delivered, "unable to LOCATE your package"). When adding a carrier,
 * replay before trusting the buckets.
 */
function excMatchFailure_(e) {
  if (/unable to (be )?deliver(ed)?|cannot be delivered|undeliverable/.test(e)) return 'UNDELIVERABLE';
  if (/\bdamaged\b|merchandise has been discarded/.test(e)) return 'DAMAGED';
  if (/returning package to shipper|returned to a? ?veho warehouse|returned to the (sender|seller|shipper)|returned to shipper/.test(e)) {
    return 'RETURNED';
  }
  if (/lost by driver|will be discarded|unable to locate your package/.test(e)) return 'LOST';
  // Real OnTrac wording observed 2026-08-13: access-code requests are address/actionability
  // failures. 🔴 WIDENED 2026-08-17: the predicate only knew OnTrac's "need additional information
  // to complete" phrasing, so FedEx's "Delivery exception, Incorrect address, HERNDON VA 20171"
  // (#170893) fell straight through to IN_NETWORK. One carrier's wording is never the class.
  if (/need additional information to complete|lack of an access code|access code|incorrect address|address (is )?(incorrect|invalid)|delivery exception.*address/.test(e)) {
    return 'ADDRESS_ISSUE';
  }
  // A closed recipient/business or an incomplete delivery is an attempted-delivery failure.
  if (/was attempted but could not be completed|delivery attempt failed|unable to complete (your )?delivery|driver tried to deliver|business (was )?closed|recipient business closed|package not delivered\/?not attempted/.test(e)) {
    return 'ATTEMPT_FAILED';
  }
  return '';
}

function excClassify_(ship, movedElsewhere, delayedElsewhere) {
  var cps = (ship && ship.checkpoints) || [];
  var carrierCps = cps.filter(function (c) { return c && c.status; });
  var pick = carrierCps.length ? carrierCps[0] : (cps.length ? cps[0] : null);
  var status = String((ship && (ship.delivery_status || ship.status)) || '').toUpperCase();
  var pickup = String((ship && ship.pickup_date) || '');
  var delivered = String((ship && ship.delivery_date) || '');

  function textOf(c) {
    return String((c && (c.detail || c.description || c.message)) || '').trim();
  }
  // eventAt = when the CARRIER scanned it (checkpoint_time), which is the number that matters for
  // triage — "damaged since Tuesday 08:14" beats "a cron noticed at 16:00". Kept separate from the
  // sweep's own stamp; the gap between the two IS the feed latency, which is its own signal.
  // 🔴 It is the scan time of the checkpoint that CLASSIFIED, not of the newest one — otherwise a
  // buried failure would be reported with the timestamp of the benign scan that hid it.
  function r(cls, ping, cp) {
    var c = (cp === undefined) ? pick : cp;
    return {
      cls: cls, detail: textOf(c), ping: ping, status: status,
      eventAt: String((c && c.checkpoint_time) || '').replace('T', ' ').slice(0, 16),
    };
  }

  // A real delivery_date is authoritative — nothing beats it.
  if (delivered) return r('DELIVERED', false);

  // 🔴 WINDOW SCAN, newest-first (Kurt 2026-08-17). Precedence, stated plainly:
  //   walk the newest EXC_CP_SCAN carrier checkpoints from newest to oldest and stop at the first
  //   one that decides. Within a single checkpoint, failure text is tested before the bare-text
  //   "delivered" suppress (the substring trap above).
  // The consequence that matters both ways:
  //   • a benign scan NEWER than a failure no longer hides it (the #170893 bug), because the benign
  //     scan decides nothing and the walk continues past it;
  //   • a DELIVERED scan newer than a failure still suppresses, because it decides immediately.
  //     That is what preserves the 23-of-71 already-delivered suppression that keeps this channel
  //     trustworthy — Veho stamps an exception en route and never flips the bucket back, and those
  //     boxes have the delivery scan on TOP. Removing that guard would re-import a ~32% false rate.
  // Checkpoints with a null `status` are AppyHour storefront copy injected into the PP timeline
  // ("Orders are prepared fresh weekly"), not carrier scans — carrierCps already dropped them.
  var window_ = carrierCps.length ? carrierCps.slice(0, EXC_CP_SCAN)
                                  : (pick ? [pick] : []);
  for (var i = 0; i < window_.length; i++) {
    var cp = window_[i];
    var t = textOf(cp).toLowerCase();
    var hit = excMatchFailure_(t);
    if (hit) return r(hit, true, cp);
    // ⚠️ KNOWN GAP: a box returned to origin can also read "Delivered, <origin city>" (order
    // 154810, FedEx, dest AL, delivered back in Lebanon TN). v1 suppresses it. Catching that needs
    // the event location compared against the destination state — see EXCEPTIONS_ALERT_RULES.md.
    if (/\bdelivered\b/.test(t) || String(cp.status || '').toUpperCase() === 'DELIVERED') {
      return r('DELIVERED', false, cp);
    }
  }

  var e = textOf(pick).toLowerCase();

  // 🔴 TRUST THE STRUCTURED FIELD (Kurt 2026-08-17). ParcelPanel's own `status` said
  // FAILED_ATTEMPT on #170893 while this function classified purely off prose and threw the field
  // away. Structured-beats-free-text was already the standing directive; it was parsed here and
  // never read. Placed AFTER the window walk so a more specific failure text (damaged, returned,
  // lost) still refines it and so a genuine delivery still suppresses — the directive means a
  // structured failure must never be outranked by BENIGN narrative, which is exactly what a
  // position above the in-network fallback guarantees.
  if (status === 'FAILED_ATTEMPT') return r('ATTEMPT_FAILED', true);

  // 🔴 DELAYED / STUCK (Kurt 2026-08-17). Signal is Shopify's fulfillment displayStatus, which
  // rides the call excResolveDelivered_ already makes — ZERO extra ParcelPanel budget, same
  // one-request-two-jobs trick as the movement union. #169174 (Maria Wood, NY) had displayStatus
  // DELAYED / "Package Delayed." while its newest PP checkpoint was a benign IN_TRANSIT, so no
  // text-based class could ever have caught it. Gated by EXC_DELAYED_MIN_DAYS — see that constant
  // for why a floor cannot lose a real case. Tested BEFORE the movement union on purpose: a
  // delayed box HAS moved, so the union would otherwise swallow every one of them.
  if (delayedElsewhere) {
    var fulD = String((ship && (ship.fulfillment_date || ship.order_date)) || '').slice(0, 10);
    if (fulD && excDaysSince_(fulD) >= EXC_DELAYED_MIN_DAYS) return r('DELAYED', true);
    return r('IN_NETWORK', false);
  }

  // Never picked up: PP knows about the label but no pickup scan ever landed. Only meaningful
  // once the box has had a day to move — before that it is just a fresh label.
  if (movedElsewhere) return r('IN_NETWORK', false);   // the other feed has a real scan
  if (!pickup && (status.indexOf('INFO') >= 0 || /shipment information sent|order created/.test(e))) {
    var ful = String((ship && (ship.fulfillment_date || ship.order_date)) || '').slice(0, 10);
    if (ful && excDaysSince_(ful) >= EXC_NEVER_PICKED_MIN_DAYS) return r('NEVER_PICKED_UP', true);
    return r('PRE_TRANSIT', false);
  }
  return r('IN_NETWORK', false);
}

function excDaysSince_(iso) {
  var d = new Date(iso + 'T00:00:00Z');
  if (isNaN(d)) return 0;
  return Math.floor((new Date() - d) / 86400000);
}

// ---------------------------------------------------------------- ParcelPanel

/**
 * Fetch raw PP shipments. Returns {ships, failed, throttled, attempted, seen}.
 *
 * `seen` is the set of order numbers PP actually answered for — callers must only stamp
 * last_seen on those. Stamping an order we never reached pushes it to the BACK of the
 * oldest-first queue, so the same orders starve run after run while the log looks healthy.
 * Throttled (429) is counted apart from failed: throttling is expected backpressure and is
 * retried next run, whereas a real failure rate means something is broken and must be loud.
 */
function excPpFetch_(orderNums, deadline) {
  // 🔴 `charged` is the ONLY honest unit of spend (directive P1): a request ParcelPanel actually
  // served, whatever it answered. A 429/503 is refused backpressure, and a request never dispatched
  // (time budget, 6-minute kill) never existed — neither may be billed to the weekly ledger, which
  // is precisely how week 34 charged 2,000 for 88 observed polls.
  var out = { ships: {}, failed: 0, throttled: 0, attempted: 0, charged: 0, seen: {}, budgetHit: false };
  var key = PropertiesService.getScriptProperties().getProperty('PARCELPANEL_API_KEY');
  if (!key || !orderNums.length) return out;
  var uniq = orderNums.filter(function (n, i) { return n && orderNums.indexOf(n) === i; });
  out.attempted = uniq.length;

  function reqFor(n) {
    return {
      url: 'https://open.parcelwill.com/api/v2/tracking/order?order_number=' + encodeURIComponent(n),
      headers: { 'x-parcelpanel-api-key': key },
      muteHttpExceptions: true,
    };
  }

  function consume(slice, resp) {
    var retry = [];
    resp.forEach(function (rp, k) {
      var on = slice[k], code = rp.getResponseCode();
      if (code === 429 || code === 503) { retry.push(on); return; }   // refused — not billable
      out.charged++;                                                  // served, whatever it said
      if (code !== 200) { out.failed++; return; }
      try {
        var o = JSON.parse(rp.getContentText());
        var ships = ((o.order || {}).shipments) || ((o.data || {}).shipments) || o.shipments || [];
        out.seen[on] = true;
        if (ships.length) out.ships[on] = ships[0];
      } catch (e) { out.failed++; }
    });
    return retry;
  }

  for (var i = 0; i < uniq.length; i += EXC_PP_BATCH) {
    if (deadline && new Date().getTime() > deadline) { out.budgetHit = true; break; }
    var slice = uniq.slice(i, i + EXC_PP_BATCH);
    var retry;
    try {
      retry = consume(slice, UrlFetchApp.fetchAll(slice.map(reqFor)));
    } catch (err) {
      // fetchAll threw: the requests may well have left the building. Bill them — an under-count
      // of spend is the failure this whole ledger exists to end.
      out.failed += slice.length;
      out.charged += slice.length;
      continue;
    }
    if (retry.length) {                       // one backoff pass, then leave it for next run
      Utilities.sleep(EXC_PP_BACKOFF_MS);
      try {
        var still = consume(retry, UrlFetchApp.fetchAll(retry.map(reqFor)));
        out.throttled += still.length;
      } catch (err2) {
        out.throttled += retry.length;
        out.charged += retry.length;      // dispatched, outcome unknown — bill it
      }
    }
    if (i + EXC_PP_BATCH < uniq.length) Utilities.sleep(EXC_PP_PAUSE_MS);
  }
  return out;
}

// ---------------------------------------------------------------- cohort seed

/** Order numbers + customer/state for the live ship cohorts, from Shopify. */
function excSeedCohort_() {
  var tags = [], d = new Date();
  var mon = new Date(d); mon.setDate(mon.getDate() - ((mon.getDay() + 6) % 7));
  for (var i = 0; i < EXC_COHORTS_BACK; i++) {
    var m = new Date(mon); m.setDate(m.getDate() - 7 * i);
    tags.push('_SHIP_' + Utilities.formatDate(m, Session.getScriptTimeZone(), 'yyyy-MM-dd'));
  }
  var rows = [];
  tags.forEach(function (tag) {
    var cursor = null, page = 0;
    do {
      var d2 = shopifyGql_(
        'query($q:String!,$after:String){ orders(first:250, query:$q, after:$after){ ' +
        'pageInfo{hasNextPage endCursor} edges{ node{ name id ' +
        'shippingAddress{ provinceCode } customer{ displayName } } } } }',
        { q: "tag:'" + tag + "' -status:cancelled", after: cursor }
      ).orders;
      d2.edges.forEach(function (ed) {
        var n = ed.node;
        rows.push({
          order: String(n.name).replace(/^#/, ''),
          cohort: tag,
          customer: (n.customer && n.customer.displayName) || '',
          state: (n.shippingAddress && n.shippingAddress.provinceCode) || '',
        });
      });
      cursor = d2.pageInfo.hasNextPage ? d2.pageInfo.endCursor : null;
    } while (cursor && ++page < 20);
  });
  return rows;
}

// ---------------------------------------------------------------- state

/**
 * _exc_state columns: order | cohort | customer | state | carrier | open | alerted_classes | last_seen
 * `open` = 1 while we still poll it. Goes 0 on DELIVERED or once alerted (constraint: notify once,
 * humans take it from there). alerted_classes is a comma list — dedup key is (order, class).
 */
function excLoadState_() {
  var sh = excSS_().getSheetByName(EXC_STATE_TAB);
  var st = {};
  if (!sh || sh.getLastRow() < 2) return st;
  // 9 columns: the 9th (`logged_classes`) is the SHEET-RECORD dedup, separate from `alerted`.
  // It must round-trip through state or the same exception is re-appended to the tab every sweep.
  sh.getRange(2, 1, sh.getLastRow() - 1, EXC_STATE_COLS.length).getValues().forEach(function (r) {
    if (!r[0]) return;
    st[String(r[0])] = {
      order: String(r[0]), cohort: r[1], customer: r[2], state: r[3], carrier: r[4],
      open: String(r[5]) === '1',
      alerted: String(r[6] || '').split(',').filter(String),
      last_seen: r[7],
      logged: String(r[8] || '').split(',').filter(String),
    };
  });
  return st;
}

// 🔴 ONE schema definition. The width was a literal 8 while the header grew to 9, which threw
// "The number of columns in the data does not match the number of columns in the range" on EVERY
// save — and because clear() runs first, each throw left _exc_state EMPTY. Derive the width from
// the header row so adding a column can never desync it again.
var EXC_STATE_COLS = ['order', 'cohort', 'customer', 'state', 'carrier', 'open',
                      'alerted_classes', 'last_seen', 'logged_classes'];

function excSaveState_(st) {
  var ss = excSS_();
  var sh = ss.getSheetByName(EXC_STATE_TAB) || ss.insertSheet(EXC_STATE_TAB);
  var rows = [EXC_STATE_COLS.slice()];
  Object.keys(st).forEach(function (k) {
    var r = st[k];
    rows.push([r.order, r.cohort, r.customer, r.state, r.carrier, r.open ? '1' : '0',
               r.alerted.join(','), r.last_seen || '', (r.logged || []).join(',')]);
  });
  // write FIRST, then trim: clearing up front means any failure here destroys the state outright.
  sh.getRange(1, 1, rows.length, EXC_STATE_COLS.length).setValues(rows);
  if (sh.getLastRow() > rows.length) {
    sh.getRange(rows.length + 1, 1, sh.getLastRow() - rows.length, EXC_STATE_COLS.length).clearContent();
  }
  sh.hideSheet();
}

// ---------------------------------------------------------------- Slack

/**
 * The ONLY function in this file that calls chat.postMessage. It takes the channel explicitly and
 * applies NO gates of its own — every gate belongs to the class helper above it, so that reading
 * one helper tells you the whole truth about when that class posts.
 * 🔴 NOT to be called directly from sweep code. Call excSlackPost_ or excSlackOps_.
 */
function excSlackSend_(channel, text, what) {
  var token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (!token) throw new Error('SLACK_BOT_TOKEN missing — cannot post ' + what + ' to ' + channel);
  var r = netFetch_('https://slack.com/api/chat.postMessage', {
    method: 'post', contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token },
    payload: JSON.stringify({ channel: channel, text: text, unfurl_links: false }),
    muteHttpExceptions: true,
  }, 'slack chat.postMessage');
  var d = JSON.parse(r.getContentText());
  if (!d.ok) throw new Error('slack post failed (' + what + ' -> ' + channel + '): ' + d.error);
}

/**
 * CLASS 1 — EXCEPTION PING. A customer's box has a problem. Goes to #exceptions, where Dan and Kurt
 * watch for boxes. Keeps BOTH gates: the stop-write and the Wed–Sun rhythm.
 * 🔴 ONE HELPER PER CLASS, NOT ONE HELPER WITH A BOOLEAN (directive P8). A `excSlackPost_(text,
 * isFailure)` shape would let a future call site put an infra crash in Dan's ping channel — or,
 * far worse, a real customer exception into a DM only Kurt reads — by getting one argument wrong.
 * The destination is a property of WHICH function you call, and there is no argument that changes it.
 */
function excSlackPost_(text) {
  // 🔴 Last line of defence for the stop-write. Callers already check EXC_DRY_RUN, but this makes
  // the channel unreachable from ANY call site, including one added later by someone who did not
  // read the flag. Belt and braces on purpose — the cost of a stray post is Kurt's channel.
  if (EXC_DRY_RUN) { Logger.log('[DRY RUN] suppressed Slack post: ' + String(text).slice(0, 120)); return; }
  // Second, INDEPENDENT gate. Mon/Tue never post; the row is already on the tab and `alerted` is
  // left unstamped, so the same exception posts on Wednesday if it is still live.
  if (!excPingDayET_()) {
    Logger.log('[Mon/Tue] suppressed Slack post (records only): ' + String(text).slice(0, 120));
    return;
  }
  excSlackSend_(excChannelPings_(), text, 'exception ping');
}

/**
 * CLASS 2 — OPS / HEALTH. The sweep telling on itself: sweep FAILED, PP hard-failure ratio,
 * starved/blindspot alarms, budget bits, refused asserts. Goes to the appyhour-ops-reader DM, NOT
 * #exceptions (directive P8).
 *
 * 🔴 NO Wed–Sun day gate. "This job did nothing at all" is not an exception ping and must be visible
 * on the day it happens; Mon/Tue silence is about sparing Dan customer noise, never about hiding a
 * broken sweep. (This was already true of the old excSlackHealth_ — P4 — and stays true.)
 *
 * 🔴 NO EXC_DRY_RUN GATE EITHER — CHANGED 2026-08-19, deliberately. The old helper was hard-blocked
 * by the dry-run flag. That flag is the stop-write on **#exceptions**, and its whole justification
 * was "do not dump 4,289 customer exceptions into Kurt's channel yet" — a statement about customer
 * pings, which no longer share a destination with failures. Meanwhile a crash during a muted period
 * is PRECISELY the failure nobody would otherwise notice: the sweep is silent by design, so a
 * broken sweep and a working one look identical, and the operator's only signal is this post. A
 * mute that also mutes the smoke alarm is the silent-failure class this file exists to kill.
 * The blast radius of getting this wrong is one DM to Kurt — not a channel Dan reads.
 * If a future stop-write must cover ops alerts too, it gets its OWN flag; do not re-couple them.
 *
 * Rate-limiting is still the CALLER's job (EXC_SILENT_ALARM_EVERY_MS) — an alarm that repeats
 * hourly gets muted, and a muted alarm is the same failure one level up.
 */
function excSlackOps_(text) {
  excSlackSend_(excChannelOps_(), text, 'ops/health alert');
}

/**
 * 🔴 COMPATIBILITY SHIM ONLY — the old name for CLASS 2, kept because it is referenced from the
 * rules doc and from earlier commits. New call sites use excSlackOps_. Not a second class.
 */
function excSlackHealth_(text) {
  excSlackOps_(text);
}

var EXC_EMOJI_ = {
  UNDELIVERABLE: ':x:', DAMAGED: ':boom:', RETURNED: ':leftwards_arrow_with_hook:',
  NEVER_PICKED_UP: ':no_entry:', LOST: ':question:', ADDRESS_ISSUE: ':house:',
  ATTEMPT_FAILED: ':warning:', DELAYED: ':hourglass_flowing_sand:',
};

/**
 * Human-facing label per class. DISPLAY ONLY — the class token stays the internal identity.
 *
 * 🔴 NEVER put a display label into `alerted_classes` or any dedupe/state key. The "already
 * pinged" check is (order, class token); rewriting the stored token would make every
 * previously-alerted order look new and re-spam #exceptions with old boxes. Rename here, at
 * render time, and the state file never changes.
 *
 * 🔴 NEVER_PICKED_UP and LOST stay SEPARATE classes — Kurt renamed the display, not the
 * taxonomy. NEVER_PICKED_UP = label created, carrier never scanned it (lost before it moved);
 * LOST = scanned into the network, then vanished. Merging them destroys the reason, which is the
 * whole point of the wording.
 */
var EXC_DISPLAY_ = {
  // 🔴 Aligned to the D16 taxonomy on the analytics tabs (Kurt 2026-08-10): the same condition
  // must not read two ways across tabs. Was 'Lost in Transit (no scan)'.
  NEVER_PICKED_UP: 'never picked up by carrier',
  // 🔴 DISPLAY ONLY, same rule as above — the tokens ATTEMPT_FAILED / DELAYED stay the identity in
  // alerted_classes and logged_classes. Renaming a token would make every previously-recorded row
  // look new and re-spam the channel.
  ATTEMPT_FAILED: 'delivery attempt failed',
  DELAYED: 'delayed / stuck in transit',
};

/**
 * Display label for a class token. The fallback title-cases the token rather than returning it
 * raw: `String(cls).replace(/_/g,' ')` alone emitted 'ADDRESS ISSUE' while a mapped sibling
 * emitted 'Address Issue', so the SAME class rendered two ways on the tab. Casing is not naming —
 * an unmapped token still shows its own words, just consistently.
 */
function excDisplay_(cls) {
  if (EXC_DISPLAY_[cls]) return EXC_DISPLAY_[cls];
  return String(cls || '').replace(/_/g, ' ').toLowerCase().replace(/[a-z]/g, function (ch) {
    return ch.toUpperCase();
  });
}

function excMessage_(rec, cls, detail, eventAt) {
  // Verbatim carrier text is non-negotiable — it's what lets Dan judge in 2s without opening
  // anything. Order link last so Slack doesn't unfurl over the detail.
  return (EXC_EMOJI_[cls] || ':warning:') + ' *' + excDisplay_(cls) + '* — #' + rec.order +
         (rec.customer ? ' · ' + rec.customer : '') +
         (rec.carrier ? ' · ' + rec.carrier : '') +
         (rec.state ? ' · ' + rec.state : '') +
         (eventAt ? '\n_carrier scan: ' + eventAt + '_' : '') +
         '\n> ' + (detail || '(no carrier text)') +
         '\nhttps://admin.shopify.com/store/' +
         PropertiesService.getScriptProperties().getProperty('SHOPIFY_STORE') +
         '/orders?query=' + encodeURIComponent(rec.order);
}

var EXC_LOG_HEADERS = ['detected', 'event when', 'order', 'customer', 'carrier', 'state',
                       'class', 'carrier event'];

/**
 * 🔴 Canonical carrier name is OnTrac; LaserShip is the ALIAS (Kurt 2026-08-07). The rule was
 * codified for the reship/analytics tabs and this writer never got it, so every row here read
 * "LaserShip". Local `exc`-prefixed on purpose: Code.gs owns a `normCarrier_` that maps ontrac to
 * its OWN bucket, and Apps Script shares one global scope — redefining it would silently change
 * the hourly reship report.
 */
function excCarrier_(raw) {
  var s = String(raw || '').toLowerCase();
  if (s.indexOf('lasership') >= 0 || s.indexOf('ontrac') >= 0) return 'OnTrac';
  if (s.indexOf('veho') >= 0) return 'Veho';
  if (s.indexOf('fedex') >= 0) return 'FedEx';
  if (s.indexOf('ups') >= 0) return 'UPS';
  return raw ? String(raw) : '';
}

/**
 * Append one alert row.
 *
 * Two timestamps on purpose: `detected` = when this sweep ran and posted (shared by every row
 * from the same run); `event when` = the carrier's own checkpoint_time. Triage wants the second.
 * Self-heals the header if the tab predates the event-when column.
 */
function excLog_(stamp, rec, cls, detail, eventAt) {
  var ss = excSS_();
  var sh = ss.getSheetByName(EXC_LOG_TAB);
  if (!sh) {
    sh = ss.insertSheet(EXC_LOG_TAB);
    sh.setFrozenRows(1);
  }
  var width = EXC_LOG_HEADERS.length;
  var head = sh.getLastRow() ? sh.getRange(1, 1, 1, width).getValues()[0] : [];
  if (String(head[1] || '') !== 'event when') {
    sh.getRange(1, 1, 1, width).setValues([EXC_LOG_HEADERS]).setFontWeight('bold');
  }
  // display label in the sheet; the internal token stays in _exc_state.alerted_classes
  sh.appendRow([stamp, eventAt || '', '#' + rec.order, rec.customer, excCarrier_(rec.carrier),
                rec.state, excDisplay_(cls), detail]);
}

// ---------------------------------------------------------------- entry point

/**
 * Which Script Properties this file needs, and who reads them.
 *
 * 🔴 Apps Script reports a missing property as "Attribute provided with invalid value:
 * Header:null" — thrown deep inside UrlFetchApp, naming neither the property nor the caller.
 * That error burned two runs on 2026-07-31. Preflight so the message says what is actually
 * wrong. A cloned project inherits code but NOT properties, and both projects here are titled
 * "Running Reship", so it is easy to set them on the wrong one.
 */
var EXC_REQUIRED_PROPS = [
  ['SHOPIFY_STORE', 'cohort seed (shopifyGql_) + order links'],
  ['SHOPIFY_TOKEN', 'cohort seed (shopifyGql_)'],
  ['PARCELPANEL_API_KEY', 'exception polling (excPpFetch_)'],
  ['SLACK_BOT_TOKEN', 'posting pings to #exceptions (excSlackPost_) AND ops alerts to the ' +
                      'appyhour-ops-reader DM (excSlackOps_) — one token, two destinations'],
];

// 🔴 OPTIONAL, deliberately NOT in EXC_REQUIRED_PROPS: EXC_CHANNEL_PINGS / EXC_CHANNEL_OPS. They
// exist so routing can be changed without a code push. Unset is the NORMAL state and falls back to
// the literals at the top of this file — requiring them would turn "nobody set an override" into a
// preflight crash that stops the sweep entirely.

function excPreflight_() {
  var props = PropertiesService.getScriptProperties();
  var missing = EXC_REQUIRED_PROPS.filter(function (p) {
    return !String(props.getProperty(p[0]) || '').trim();
  });
  if (missing.length) {
    throw new Error(
      'Script Properties missing on THIS project (' + ScriptApp.getScriptId() + '): ' +
      missing.map(function (p) { return p[0] + ' [' + p[1] + ']'; }).join(', ') +
      '. Set them in Project Settings -> Script Properties on this project. A cloned project ' +
      'does NOT inherit properties, and both projects are named "Running Reship" — check the ' +
      'script id above matches the one you edited.');
  }
}

/** Menu item: report which properties are set, WITHOUT ever printing their values. */
function excCheckProperties() {
  var props = PropertiesService.getScriptProperties();
  var lines = ['script id: ' + ScriptApp.getScriptId(),
               'bound sheet: ' + SpreadsheetApp.getActiveSpreadsheet().getId(), ''];
  EXC_REQUIRED_PROPS.forEach(function (p) {
    var v = String(props.getProperty(p[0]) || '').trim();
    lines.push((v ? 'SET     (' + v.length + ' chars)  ' : 'MISSING              ') + p[0]);
  });
  // 🔴 These three answer "is it running, and does it have budget left" WITHOUT another sweep.
  // They are the only values printed verbatim here, and none of them is a secret.
  lines.push('',
    'last sweep run (heartbeat): ' + (props.getProperty(EXC_HEARTBEAT_PROP) || '(never — trigger may be gone)'),
    'ParcelPanel ledger (wk|total|exc|rpt|pa): ' + (props.getProperty(EXC_PP_BUDGET_PROP) || '(unset)'),
    '  week key now ' + excWeekKey_() + ' (Mon 00:00 ET - Sun 24:00 ET), runs left ' +
      excRunsLeftThisWeek_() + ', sweep budget ' + EXC_PP_WEEKLY_BUDGET +
      ' inside account quota ' + EXC_PP_ACCOUNT_QUOTA + ', account left ' + excBudgetLeftAccount_(),
    '  🔴 LOWER BOUND — ShipRouting sync_delivery_status.py and ad-hoc probes spend the same' +
      ' account quota and cannot report into this ledger.',
    '  thin Mon/Tue sweep today: ' + (EXC_THIN_DAY_CAP - excThinDayLeft_()) + '/' + EXC_THIN_DAY_CAP +
      ' used (' + (props.getProperty(EXC_THIN_DAY_PROP) || 'unset') + ')',
    'Slack PINGS -> ' + excChannelPings_() +
      (props.getProperty('EXC_CHANNEL_PINGS') ? ' (property override)' : ' (default #exceptions)') +
      ': ' + (EXC_DRY_RUN ? 'DRY RUN (muted)' : 'LIVE') +
      ', today is a ' + (excPingDayET_() ? 'PING day' : 'record-only day (Mon/Tue)'),
    'Slack OPS   -> ' + excChannelOps_() +
      (props.getProperty('EXC_CHANNEL_OPS') ? ' (property override)' : ' (default appyhour-ops-reader DM)') +
      ': ALWAYS LIVE — no day gate, and EXC_DRY_RUN does NOT mute it (directive P8)');
  var all = props.getKeys().sort().join(', ');
  lines.push('', 'all keys on this project: ' + (all || '(none)'));
  var msg = lines.join('\n');
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Exception sweep — properties', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
  return msg;
}

function hourlyExceptionSweep() {
  // 🔴 HEARTBEAT FIRST, before anything that can throw. "Is the trigger even firing?" was
  // unanswerable on 2026-08-19: the tab's newest row was 8/12, _exc_state's newest last_seen was
  // 8/16 21:46, #exceptions had been silent since 8/07 and Google's failure digests named
  // `refresh` and `refreshCurrentColumn` but never `hourlyExceptionSweep` — so a dead trigger and
  // a run that polls nothing produced identical evidence. This stamp separates them: if it is
  // fresh, the trigger fires and the fault is downstream; if it is stale, the trigger is gone.
  // Read it from the "Check properties" menu item.
  try {
    PropertiesService.getScriptProperties().setProperty(
      EXC_HEARTBEAT_PROP, Utilities.formatDate(new Date(), EXC_TZ, 'yyyy-MM-dd HH:mm') + ' ET');
  } catch (eHb) { Logger.log('heartbeat stamp failed: ' + eHb); }
  try {
    excPreflight_();
    var st = excLoadState_();

    // seed any cohort orders we haven't seen yet
    excSeedCohort_().forEach(function (row) {
      if (!st[row.order]) {
        st[row.order] = { order: row.order, cohort: row.cohort, customer: row.customer,
                          state: row.state, carrier: '', open: true, alerted: [], last_seen: '' };
      }
    });

    var open = Object.keys(st).filter(function (k) { return st[k].open; });

    // 🔴 Priority: NEWEST cohort first, then never-polled, then oldest-seen. A pure oldest-seen
    // sort let a matured cohort compete with the live one for poll budget — on 2026-08-07 the
    // live _SHIP_2026-08-03 cohort had 2,325 of 2,361 orders never polled while 2,226 rows from
    // the previous week sat in the same queue. Matured boxes are already delivered or already
    // someone's problem; a no-scan box in the LIVE cohort is the one that still costs a reship.
    open.sort(function (a, b) {
      var ca = String(st[a].cohort || ''), cb = String(st[b].cohort || '');
      if (ca !== cb) return cb.localeCompare(ca);              // newest cohort tag first
      var sa = String(st[a].last_seen || ''), sb = String(st[b].last_seen || '');
      return sa.localeCompare(sb);                             // never-polled ('') sorts first
    });
    // 🔴 narrow BEFORE spending ParcelPanel budget: Shopify is free, PP is capped.
    var pollable = excResolveDelivered_(open.slice(0, EXC_MAX_POLL_PER_RUN), st);
    // 🔴 Directive P5 — the poll POLICY sits between the free Shopify triage and the budget, so
    // "the budget bit" and "nothing was due" can never be confused for each other again.
    var elig = excPollSet_(pollable, st);
    // Mon/Tue thin sweep: a hard per-DAY ceiling on top of the weekly pacing (directive P5c).
    var thinLeft = excPingDayET_() ? elig.length : excThinDayLeft_();
    if (!excPingDayET_()) {
      Logger.log('  thin-sweep allowance left today: ' + thinLeft + '/' + EXC_THIN_DAY_CAP);
    }
    var allowed = excBudgetTake_(Math.min(elig.length, thinLeft));
    var batch = elig.slice(0, allowed);
    Logger.log('  PP: asked ' + batch.length);

    // 🔴 SILENCE MUST FAIL LOUDLY. Constraint 7 says a PP OUTAGE cannot read as "no exceptions";
    // this is the same hole one step earlier — a run that polls ZERO boxes while boxes are open
    // reports nothing, throws nothing, and looks exactly like a clean week. That is what happened
    // Wed 8/13 through Fri 8/15 (weekly budget already drained by Mon+Tue) and it is why Kurt saw
    // an alerter that "isn't working" with no error anywhere. Rate-limited to one alarm per 6h so
    // the alarm itself can never become the noise that gets the channel muted.
    // 🔴 Under the once-per-day tier an EMPTY batch is often correct ("everything due was already
    // polled today"), and alarming on that would make the alarm the noise that gets the channel
    // muted. So the alarm now distinguishes the two shapes it must never confuse:
    //   STARVED   — work was due and the budget allowed none of it.
    //   BLINDSPOT — nothing was judged due, yet open boxes have never been polled ONCE. That is a
    //               policy bug, not a quiet week, and it is exactly the week-34 signature.
    var neverP_ = excNeverPolled_(st, open);
    var starved_ = (elig.length > 0 && !batch.length);
    var blind_ = (!elig.length && neverP_ > 0);
    if (starved_ || blind_) {
      var props_ = PropertiesService.getScriptProperties();
      var lastAlarm = Number(props_.getProperty(EXC_SILENT_ALARM_PROP) || 0);
      if (new Date().getTime() - lastAlarm > EXC_SILENT_ALARM_EVERY_MS) {
        props_.setProperty(EXC_SILENT_ALARM_PROP, String(new Date().getTime()));
        try {
          excSlackOps_(':warning: exceptions sweep polled 0 boxes while ' + open.length +
            ' are still open (' + neverP_ + ' never polled once). ' +
            (starved_ ? 'ParcelPanel budget allowed ' + allowed + ' of ' + elig.length + ' due'
                      : 'The poll policy judged NOTHING due while boxes have never been polled ' +
                        '— that is a policy bug, not a quiet week') +
            ' (' + pollable.length + ' pollable after the free Shopify triage). ' +
            'Nothing was checked, so this run is NOT an all-clear.');
        } catch (eA) { Logger.log('silent-starvation alarm failed to post: ' + eA); }
      }
      Logger.log('  🔴 ZERO POLLED with ' + open.length + ' open — not an all-clear.');
    }

    var pp = excPpFetch_(batch, new Date().getTime() + EXC_TIME_BUDGET_MS);
    // 🔴 SETTLE THE RESERVATION (directive P1) — before anything below can throw. A run killed at
    // the 6-minute ceiling after this point has already returned what it did not spend.
    var refunded = excBudgetSettle_(allowed, pp.charged, 'exc');
    if (!excPingDayET_()) excThinDayAdd_(pp.charged);

    // 🔴 A PP outage must not read as "no exceptions" — silence has to fail loudly. But THROTTLING
    // is not an outage: those orders keep their old last_seen, stay at the front of the queue and
    // are picked up next run. Only genuine failures count against the ratio.
    if (pp.attempted && pp.failed / pp.attempted > EXC_PP_FAIL_RATIO) {
      throw new Error('ParcelPanel fetch failing: ' + pp.failed + '/' + pp.attempted +
                      ' hard failures (throttled: ' + pp.throttled + ')' +
                      ' — results suppressed rather than reported as all-clear');
    }

    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
    var posted = 0, recorded = 0, wouldPost = [];
    batch.forEach(function (on) {
      var rec = st[on], ship = pp.ships[on];
      // Only stamp orders PP actually answered for. Stamping an unreached order sends it to the
      // back of the oldest-first queue, starving it indefinitely while the log looks fine.
      if (!pp.seen[on]) return;
      rec.last_seen = stamp;
      if (!ship) return;
      var c = ship.carrier;
      rec.carrier = excCarrier_((c && (c.name || c.code)) || rec.carrier || '');
      var v = excClassify_(ship, !!EXC_SHOPIFY_MOVED_[on], !!EXC_SHOPIFY_DELAYED_[on]);
      if (v.cls === 'DELIVERED') { rec.open = false; return; }
      if (!v.ping) return;
      if (rec.alerted.indexOf(v.cls) >= 0) return;   // dedup on (order, class)
      if (EXC_SEEDING) {                             // record as already-handled, emit nothing
        if (!rec.logged) rec.logged = [];
        if (rec.logged.indexOf(v.cls) < 0) { rec.logged.push(v.cls); recorded++; }
        if (rec.alerted.indexOf(v.cls) < 0) rec.alerted.push(v.cls);
        return;
      }
      if (EXC_DRY_RUN) {                             // Slack silent
        wouldPost.push('#' + rec.order + '  ' + excDisplay_(v.cls) + '  ' + rec.carrier +
                       '  ' + rec.state + '  ' + (v.eventAt || 'no scan time'));
        if (EXC_RECORD_WHEN_SILENT) {
          if (!rec.logged) rec.logged = [];          // tab-write dedup — NOT the alert dedup
          if (rec.logged.indexOf(v.cls) < 0) {
            excLog_(stamp, rec, v.cls, v.detail, v.eventAt);
            rec.logged.push(v.cls);
            recorded++;
          }
        }
        // 🔴 `alerted` untouched and `open` left true ON PURPOSE: the Slack ping for this
        // (order, class) must still fire on the first live sweep. See the EXC_DRY_RUN note.
        return;
      }
      // 🔴 Mon/Tue: RECORD but do not alert. The gate must be here as well as inside
      // excSlackPost_ — that one only suppresses the HTTP call, while `alerted` is stamped right
      // after it returns. Relying on the post-path gate alone would mark the exception alerted
      // with nothing ever posted, and Wednesday would skip it: silently swallowed, which is the
      // exact failure this job exists to prevent.
      if (!excPingDayET_()) {
        if (!rec.logged) rec.logged = [];
        if (rec.logged.indexOf(v.cls) < 0) {
          excLog_(stamp, rec, v.cls, v.detail, v.eventAt);
          rec.logged.push(v.cls);
          recorded++;
        }
        return;                                      // `alerted` untouched -> posts on Wednesday
      }
      excSlackPost_(excMessage_(rec, v.cls, v.detail, v.eventAt));
      excLog_(stamp, rec, v.cls, v.detail, v.eventAt);
      rec.alerted.push(v.cls);
      rec.open = false;                              // notified once; a human owns it now
      posted++;
    });

    // Persist when recording, so the tab-write dedup (`rec.logged`) survives the next sweep and the
    // same exception is not appended hourly. `alerted` is still untouched while dry.
    if (!EXC_DRY_RUN || EXC_RECORD_WHEN_SILENT || EXC_SEEDING) excSaveState_(st);
    var reached = Object.keys(pp.seen).length;
    var neverPolled = open.filter(function (k) { return !String(st[k].last_seen || '').trim(); }).length;
    // 🔴 ACTUAL SPEND PER RUN, always logged (directive P5): reserved / served / refunded. An
    // optimisation you cannot measure is one you cannot falsify.
    Logger.log('  PP spend this run: reserved ' + allowed + ', served (billed) ' + pp.charged +
               ', refunded ' + refunded + ', answered ' + Object.keys(pp.seen).length +
               '  [ledger ' + String(PropertiesService.getScriptProperties()
                                      .getProperty(EXC_PP_BUDGET_PROP) || '(unset)') + ']');
    Logger.log('exceptions sweep: reached ' + reached + ' of ' + batch.length + ' polled (' +
               open.length + ' open, ' + neverPolled + ' still never polled, throttled ' +
               pp.throttled + ', hard failures ' + pp.failed +
               (pp.budgetHit ? ', TIME BUDGET hit' : '') + '), posted ' + posted +
               ', recorded ' + recorded +
               (EXC_DRY_RUN ? (EXC_RECORD_WHEN_SILENT
                  ? ' [SLACK SILENT — ' + recorded + ' row(s) written to the ' + EXC_LOG_TAB +
                    ' tab; alerts still pending for the first live sweep]'
                  : ' [DRY RUN — nothing posted, nothing saved]') : ''));
    if (EXC_DRY_RUN) {
      Logger.log('DRY RUN would post ' + wouldPost.length + ' alert(s):\n  ' +
                 wouldPost.slice(0, 25).join('\n  ') +
                 (wouldPost.length > 25 ? '\n  ...and ' + (wouldPost.length - 25) + ' more' : ''));
    }
    return { posted: posted, recorded: recorded, wouldPost: wouldPost.length, reached: reached,
             open: open.length, neverPolled: neverPolled, eligible: elig.length,
             reserved: allowed, charged: pp.charged, refunded: refunded, dryRun: EXC_DRY_RUN };
  } catch (e) {
    try {
      // 🔴 THE STOP-WRITE NO LONGER COVERS THIS (changed 2026-08-19, directive P8). It used to:
      // `if (EXC_DRY_RUN) throw e;` sat here because the failure alert posted to the SAME channel
      // the stop-write was protecting. It no longer does — failures go to the ops DM — and a crash
      // during a muted period is exactly the one nobody would otherwise see. See excSlackOps_.
      // 🔴 excSlackOps_, NOT excSlackPost_ (directive P4). excSlackPost_ applies the Wed-Sun
      // day gate, so EVERY Mon/Tue sweep failure posted absolutely nothing — Mon 8/17 and Tue 8/18
      // are exactly those days, and exactly the days that produced no evidence of anything wrong.
      // A crash is not an exception PING; the day gate exists to spare Dan customer noise, never
      // to hide a broken sweep. Exception pings keep the gate; this does not.
      excSlackOps_(':rotating_light: exceptions sweep FAILED: ' + e);
    } catch (e2) {
      MailApp.sendEmail(Session.getEffectiveUser().getEmail(), '[exceptions] sweep failed', String(e));
    }
    throw e;
  }
}

// ---------------------------------------------------------------- host cleanup (manual)

// 🔴 This owns onOpen in the CLONE. Two onOpen definitions in one Apps Script project do not
// merge — the later silently wins — so the cloned Code.gs's onOpen is renamed to
// onOpen_reshipMenu_DISABLED_ there (clone-only patch, 2026-07-31; the live reship project keeps
// its own onOpen untouched). That rename is deliberate and is also a SAFETY fix: the inherited
// "Reship Report" menu ran refresh/menuRefresh* against the LIVE pivot sheet from this clone.
// Do not restore Code.gs's onOpen here without renaming this one.
/**
 * Install/repair the hourly trigger. Run once from the editor; safe to re-run.
 *
 * 🔴 Scheduling lives HERE, not in a hand-made UI trigger. A UI-created trigger is invisible to
 * source control and to the Apps Script API (which cannot list triggers), so "is the sweep
 * actually scheduled?" becomes unanswerable — exactly the dead-cadence signature that has burned
 * shopify_orders, ontrac_master, mfg_translations and fulfillments-sync. Idempotent: drops any
 * existing hourlyExceptionSweep triggers before creating one, so re-running cannot stack duplicates.
 *
 * 🔴 Deliberately does NOT touch triggers for any other function — the reship report's `refresh`
 * trigger must keep running independently. hourlyExceptionSweep throws on failure by design; if
 * the two ever shared a trigger, an exception-sweep failure would abort the reship run.
 */
function installExceptionsTrigger() {
  var existing = ScriptApp.getProjectTriggers().filter(function (t) {
    return t.getHandlerFunction() === 'hourlyExceptionSweep';
  });
  existing.forEach(function (t) { ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('hourlyExceptionSweep').timeBased().everyHours(1).create();
  var msg = 'hourlyExceptionSweep: removed ' + existing.length + ' existing trigger(s), installed 1 hourly';
  Logger.log(msg);
  return msg;
}

/** Report what IS scheduled on this project, so the answer is never a guess. */
function excListTriggers() {
  var lines = ScriptApp.getProjectTriggers().map(function (t) {
    return '  ' + t.getHandlerFunction() + '  [' + t.getEventType() + ']';
  });
  var msg = 'triggers on this project (' + ScriptApp.getScriptId() + '):\n' +
            (lines.length ? lines.join('\n') : '  (none)');
  Logger.log(msg);
  return msg;
}

// 🔴 NOT named onOpen. Code.gs owns the reserved onOpen (the Reship Report menu) and Apps Script
// runs exactly ONE — files are concatenated and the last definition silently wins, so defining
// onOpen here is a coin-flip that can kill the reship menu with no error. Ruling, Kurt 2026-08-06:
// "running reship report will be king." Code.gs's onOpen tail-calls this installer (coordinator's
// 1d8f0aa), which is why the menu appears at all — onOpenExceptions is not a reserved name and is
// never auto-invoked on its own. Keep this name, keep it idempotent, never rename it.
function onOpenExceptions() {
  SpreadsheetApp.getUi().createMenu('Shipping Exceptions')
    .addItem('Check properties', 'excCheckProperties')
    .addItem(EXC_DRY_RUN ? 'Run sweep now (DRY RUN — no Slack)' : 'Run sweep now (LIVE — posts to Slack)',
             'hourlyExceptionSweep')
    .addItem('Replay classifier self-test', 'excSelfTest')
    .addItem('Repair _exc_state (clear stale logged rows)', 'excRepairLoggedState')
    .addItem('Mark recorded rows as alerted (do BEFORE unmuting)', 'excMarkRecordedAsAlerted')
    .addItem('Reset ParcelPanel weekly ledger (manual — read the doc first)', 'excResetWeeklyBudget')
    .addItem('Show scheduled triggers', 'excListTriggers')
    .addItem('Install/repair hourly trigger', 'installExceptionsTrigger')
    .addToUi();
}

// 🔴 cleanupHostSheet() DELETED 2026-07-31, deliberately — do not reintroduce it.
// It dropped tabs by name (Product Mix (T) - now `Reship`, Triage, Raw Data, _seed, _state) to strip a clone
// back to purpose. Those same names are the REAL reship report on this sheet. Its only guard
// was `if (ss.getId() !== EXC_HOST_SHEET_ID) throw` — so pointing EXC_HOST_SHEET_ID at the live
// sheet, which is exactly what moving here does, turned that guard from a fence into an aim.
// A destructive helper whose safety depends on a constant that the migration itself changes is
// not safe. The clone's tabs were removed by hand; nothing needs this function.

/**
 * Replays the classifier against the 6/29-7/20 events pulled on 2026-07-30.
 * Expected: every ping-class true, every suppress-class false. Guards the regression that
 * matters most — an already-delivered box must never ping.
 */
function excSelfTest() {
  var cases = [
    ['Issue with order. Your package from was unable to be delivered. We have let know.', 'IN_TRANSIT', 'UNDELIVERABLE', true],
    ['Your package has been damaged. Please contact the seller directly for assistance.', 'IN_TRANSIT', 'DAMAGED', true],
    ['The package has been damaged and all merchandise has been discarded, V', 'EXCEPTION', 'DAMAGED', true],
    ['Returning package to shipper, Return tracking number, WASHINGTON DC', 'EXCEPTION', 'RETURNED', true],
    ['Package was returned to the sender, WOBURN MA US', 'EXCEPTION', 'RETURNED', true],
    ['Issue with order. Your order has been returned to a Veho warehouse due to an issue', 'EXCEPTION', 'RETURNED', true],
    ['Issue with order. Lost by driver', 'EXCEPTION', 'LOST', true],
    // carrier phrasings that leaked into IN_NETWORK until the 6/29-7/20 replay caught them
    ['Your package was returned to the seller. Please contact them for further', 'EXCEPTION', 'RETURNED', true],
    ["We're sorry. We are unable to locate your package. Please contact the", 'IN_TRANSIT', 'LOST', true],
    ['Delivery exception, Damaged, handling per shipper instructions, HAGERSTOWN', 'EXCEPTION', 'DAMAGED', true],
    ['Shipment exception, Unable to deliver, BUFFALO NY', 'EXCEPTION', 'UNDELIVERABLE', true],
    ['Your package will be discarded. Please contact them for further assistance', 'EXCEPTION', 'LOST', true],
    ['We need additional information to complete your delivery and avoid a return', 'EXCEPTION', 'ADDRESS_ISSUE', true],
    // 🔴 FedEx wording off #170893 (Lisa Olson, VA, 8/14 19:53). Fell through to IN_NETWORK until
    // the ADDRESS_ISSUE predicate was widened past OnTrac's phrasing on 2026-08-17.
    ['Delivery exception, Incorrect address, HERNDON VA 20171', 'EXCEPTION', 'ADDRESS_ISSUE', true],
    ['The delivery of your package was attempted but could not be completed due to a lack of an access code.', 'EXCEPTION', 'ADDRESS_ISSUE', true],
    ['The delivery of your package was attempted but could not be completed', 'EXCEPTION', 'ATTEMPT_FAILED', true],
    ["We're sorry but we were unable to complete your delivery. Please continue to check your tracking", 'EXCEPTION', 'ATTEMPT_FAILED', true],
    ['The driver tried to deliver the package, but the business was closed. We will reattempt up to 3 times.', 'EXCEPTION', 'ATTEMPT_FAILED', true],
    ['At local FedEx facility, Package not delivered/not attempted', 'EXCEPTION', 'ATTEMPT_FAILED', true],
    ['Delivered', 'EXCEPTION', 'DELIVERED', false],
    ['Delivered, Lebanon TN', 'EXCEPTION', 'DELIVERED', false],
    ['DELIVERED, SHILOH GA US', 'DELIVERED', 'DELIVERED', false],
    ['Arrived at Veho facility, Avenel, NJ', 'IN_TRANSIT', 'IN_NETWORK', false],
    ['On FedEx vehicle for delivery, QUINCY MA', 'OUT_FOR_DELIVERY', 'IN_NETWORK', false],
  ];
  var fails = [];
  cases.forEach(function (c) {
    var got = excClassify_({ checkpoints: [{ detail: c[0], status: c[1] }], status: c[1] });
    if (got.cls !== c[2] || got.ping !== c[3]) {
      fails.push('"' + c[0].slice(0, 40) + '" -> ' + got.cls + '/' + got.ping + ' expected ' + c[2] + '/' + c[3]);
    }
  });
  // display-label guard (Kurt 2026-08-07): the rename is render-time ONLY. If the internal token
  // ever leaks into the dedupe key, every already-alerted order re-fires and spams #exceptions.
  if (excDisplay_('NEVER_PICKED_UP') !== 'never picked up by carrier') {
    fails.push('NEVER_PICKED_UP must display as "never picked up by carrier"');
  }
  if (excDisplay_('LOST') === excDisplay_('NEVER_PICKED_UP')) {
    fails.push('LOST and NEVER_PICKED_UP must stay distinct — different reasons, not a merge');
  }
  var npu = excClassify_({ checkpoints: [{ detail: 'Order created', status: 'INFO_RECEIVED' }],
                           status: 'INFO_RECEIVED', fulfillment_date: '2026-01-01' });
  // union guard: the SAME shipment must NOT be never-picked-up once the other feed has a scan
  var npu2 = excClassify_({ checkpoints: [{ detail: 'Order created', status: 'INFO_RECEIVED' }],
                            status: 'INFO_RECEIVED', fulfillment_date: '2026-01-01' }, true);
  if (npu2.cls === 'NEVER_PICKED_UP') {
    fails.push('a movement scan in the other feed must suppress NEVER_PICKED_UP');
  }
  // floor guard: a fresh label is not an exception
  var npu3 = excClassify_({ checkpoints: [{ detail: 'Order created', status: 'INFO_RECEIVED' }],
                            status: 'INFO_RECEIVED',
                            fulfillment_date: Utilities.formatDate(new Date(), EXC_TZ, 'yyyy-MM-dd') });
  if (npu3.cls === 'NEVER_PICKED_UP') {
    fails.push('a same-day label must not classify as NEVER_PICKED_UP (floor ' +
               EXC_NEVER_PICKED_MIN_DAYS + 'd)');
  }
  if (npu.cls !== 'NEVER_PICKED_UP') {
    fails.push('dedupe key must remain the token NEVER_PICKED_UP, got ' + npu.cls);
  }

  // newest-first ordering guard: the oldest checkpoint is storefront copy, must never win
  var ordering = excClassify_({ checkpoints: [
    { detail: 'Your package has been damaged. Please contact the seller', status: 'EXCEPTION' },
    { detail: 'Orders are prepared fresh weekly. Your box is in queue', status: null },
  ] });
  if (ordering.cls !== 'DAMAGED') fails.push('newest-first/null-status guard failed -> ' + ordering.cls);

  // 🔴 WINDOW guard — the #170893 regression. A benign scan NEWER than the failure must not hide
  // it, and the reported eventAt must belong to the failing checkpoint, not the benign one.
  var buried = excClassify_({ checkpoints: [
    { detail: 'At local FedEx facility, HERNDON VA', status: 'IN_TRANSIT', checkpoint_time: '2026-08-15T09:31:00' },
    { detail: 'Delivery exception, Incorrect address, HERNDON VA 20171', status: 'EXCEPTION', checkpoint_time: '2026-08-14T19:53:00' },
  ], status: 'FAILED_ATTEMPT' });
  if (buried.cls !== 'ADDRESS_ISSUE' || !buried.ping) {
    fails.push('a failure buried under a later benign scan must still classify -> ' + buried.cls);
  }
  if (buried.eventAt.indexOf('2026-08-14') !== 0) {
    fails.push('eventAt must come from the classifying checkpoint, got ' + buried.eventAt);
  }
  // 🔴 The other direction, and the one that protects the channel: a DELIVERED scan NEWER than a
  // failure still suppresses (23 of 71 on 6/29-7/20 were already delivered). Do not "fix" this.
  var deliveredOnTop = excClassify_({ checkpoints: [
    { detail: 'Delivered, SHILOH GA US', status: 'DELIVERED' },
    { detail: 'Your package was unable to be delivered', status: 'EXCEPTION' },
  ] });
  if (deliveredOnTop.cls !== 'DELIVERED' || deliveredOnTop.ping) {
    fails.push('a delivery scan newer than a failure must still suppress -> ' + deliveredOnTop.cls);
  }
  // structured-field guard: PP's own status is authoritative over benign prose
  var structured = excClassify_({ status: 'FAILED_ATTEMPT',
    checkpoints: [{ detail: 'In transit, MEMPHIS TN', status: 'IN_TRANSIT' }] });
  if (structured.cls !== 'ATTEMPT_FAILED' || !structured.ping) {
    fails.push('PP status FAILED_ATTEMPT must classify as ATTEMPT_FAILED -> ' + structured.cls);
  }
  // DELAYED guard + its floor (#169174, Maria Wood NY: label 8/10, pickup 8/14, benign newest scan)
  var delayedOld = excClassify_({ status: 'IN_TRANSIT', fulfillment_date: '2026-01-01',
    checkpoints: [{ detail: 'In transit, ELMSFORD NY', status: 'IN_TRANSIT' }] }, true, true);
  if (delayedOld.cls !== 'DELAYED' || !delayedOld.ping) {
    fails.push('Shopify DELAYED past the floor must classify as DELAYED -> ' + delayedOld.cls);
  }
  var delayedFresh = excClassify_({ status: 'IN_TRANSIT',
    fulfillment_date: Utilities.formatDate(new Date(), EXC_TZ, 'yyyy-MM-dd'),
    checkpoints: [{ detail: 'In transit, ELMSFORD NY', status: 'IN_TRANSIT' }] }, true, true);
  if (delayedFresh.cls === 'DELAYED') {
    fails.push('a same-day DELAYED flag must not fire (floor ' + EXC_DELAYED_MIN_DAYS + 'd)');
  }
  // display guards for the two new/renamed classes — display only, tokens unchanged
  if (excDisplay_('ATTEMPT_FAILED') !== 'delivery attempt failed') {
    fails.push('ATTEMPT_FAILED must display as "delivery attempt failed"');
  }
  if (excDisplay_('DELAYED') !== 'delayed / stuck in transit') {
    fails.push('DELAYED must display as "delayed / stuck in transit"');
  }

  Logger.log(fails.length ? 'FAIL:\n' + fails.join('\n') : 'PASS: ' + (cases.length + 1) + ' cases');
  return fails;
}

/**
 * ONE-SHOT: mark every exception the sweep can currently see as already logged AND already
 * alerted, without touching the Exceptions tab or Slack. Run repeatedly until it reports
 * `seeded 0` — the poll budget is ~1,200 orders per run against a queue of several thousand,
 * so a single pass does NOT cover the backlog.
 */
function excSeedBacklogAsLogged() {
  EXC_SEEDING = true;
  try {
    var r = hourlyExceptionSweep();
    // 🔴 report `recorded`, not `wouldPost`: the seeding branch returns before wouldPost is
    // populated, so reading that would print "SEEDED 0" on every run and look like a no-op.
    var msg = 'SEEDED ' + r.recorded + ' exception(s) as already-handled; ' +
              'polled ' + r.reached + ', still never polled ' + r.neverPolled +
              '. Re-run until seeded reaches 0. No rows appended, nothing posted.';
    Logger.log(msg);
    try { SpreadsheetApp.getUi().alert('Seed backlog', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
    return r;
  } finally {
    EXC_SEEDING = false;                            // never leave seeding armed
  }
}

// ---------------------------------------------------------------- state repair (manual)

/**
 * 🔴 STATE ROT REPAIR (Kurt 2026-08-17). `_exc_state` claimed ~1,839 orders had a `logged_classes`
 * entry while the Exceptions tab held THREE rows: the false-positive purge cleared the tab but not
 * the state, and `rec.logged` is the tab-write dedup — so every one of those (order, class) pairs
 * could never be appended again. A genuine exception among them was permanently invisible, which
 * is precisely the silent failure this job exists to prevent.
 *
 * WHAT IT DOES, exactly:
 *   • reads the Exceptions tab and builds the set of (order, display-label) pairs actually PRESENT
 *     (order normalised without '#', label lower-cased — historical rows carry both 'Address Issue'
 *     and 'address issue' from a casing drift that was fixed later);
 *   • for every state row, DROPS from `logged_classes` any token whose display label is not on the
 *     tab, so that exception can be recorded again on the next sweep;
 *   • touches NOTHING else — `alerted_classes`, `open`, `carrier`, `cohort` and `last_seen` are
 *     copied through untouched. Dropping a `logged` entry cannot cause a duplicate Slack ping,
 *     because Slack dedup rides `alerted`, which is a different column and is not modified here.
 * Idempotent: a second run finds nothing to drop and reports 0. Read-only against ParcelPanel and
 * against Slack — it makes no network call at all.
 */
function excRepairLoggedState() {
  var ss = excSS_();
  var sh = ss.getSheetByName(EXC_LOG_TAB);
  var onTab = {};
  if (sh && sh.getLastRow() > 1) {
    sh.getRange(2, 1, sh.getLastRow() - 1, EXC_LOG_HEADERS.length).getValues().forEach(function (row) {
      var ord = String(row[2] || '').replace(/^#/, '').trim();
      var cls = String(row[6] || '').trim().toLowerCase();
      if (ord && cls) onTab[ord + '|' + cls] = 1;
    });
  }
  var st = excLoadState_();
  var dropped = 0, rowsTouched = 0, kept = 0;
  Object.keys(st).forEach(function (k) {
    var rec = st[k];
    var before = (rec.logged || []).slice();
    if (!before.length) return;
    rec.logged = before.filter(function (tok) {
      var ok = !!onTab[String(rec.order) + '|' + excDisplay_(tok).toLowerCase()];
      if (ok) kept++;
      return ok;
    });
    if (rec.logged.length !== before.length) {
      dropped += before.length - rec.logged.length;
      rowsTouched++;
    }
  });
  excSaveState_(st);
  var msg = 'state repair: Exceptions tab holds ' + Object.keys(onTab).length +
            ' (order,class) pair(s); cleared ' + dropped + ' stale logged_classes entr(ies) across ' +
            rowsTouched + ' order(s), kept ' + kept + ' that are really on the tab. ' +
            'alerted_classes / open / last_seen untouched. Re-run is a no-op.';
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Repair _exc_state', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
  return msg;
}

/**
 * 🔴 THE UNMUTE GUARD (Kurt 2026-08-17: sheet current tonight, Slack silent, unmute AFTERWARD).
 * While EXC_DRY_RUN is true the sweep appends rows and stamps `logged` but deliberately leaves
 * `alerted` empty, so that flipping EXC_DRY_RUN to false would post the ENTIRE recorded backlog in
 * one burst — the 4,289-order dump this project has been avoiding since 2026-08-07.
 *
 * This closes that: for every state row it unions `logged_classes` into `alerted_classes`, so each
 * (order, class) already sitting on the tab is treated as already pinged and the first live sweep
 * posts only exceptions classified AFTER this ran. It appends no row, makes no Slack call, and
 * spends no ParcelPanel budget. `open` is left alone on purpose — the order keeps being polled, so
 * a DIFFERENT class on the same box can still fire, which is the behaviour we want.
 * Idempotent. Run it AFTER the muted populate runs and BEFORE flipping EXC_DRY_RUN.
 */
function excMarkRecordedAsAlerted() {
  var st = excLoadState_();
  var marked = 0, orders = 0;
  Object.keys(st).forEach(function (k) {
    var rec = st[k], n = 0;
    (rec.logged || []).forEach(function (tok) {
      if (rec.alerted.indexOf(tok) < 0) { rec.alerted.push(tok); n++; }
    });
    if (n) { marked += n; orders++; }
  });
  excSaveState_(st);
  var msg = 'unmute guard: marked ' + marked + ' recorded (order,class) pair(s) across ' + orders +
            ' order(s) as already alerted. Flipping EXC_DRY_RUN to false now posts ONLY exceptions ' +
            'classified from here on. Nothing appended, nothing posted, no ParcelPanel calls.';
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Mark recorded as alerted', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
  return msg;
}
