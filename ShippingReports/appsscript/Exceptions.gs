/**
 * #exceptions alerter — hourly ParcelPanel exception sweep -> private Slack channel.
 * Constraints SSOT: ShippingReports/EXCEPTIONS_ALERT_RULES.md (repo). Read it before
 * changing anything here; the rules are authored there first.
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
 * Product Mix, Product Mix (T), Daily, TnT2, Lost in Transit and Routing Match belong to the
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
var EXC_DRY_RUN = true;

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
var EXC_CHANNEL = 'C0BLKKPAW8P';          // private #exceptions. NEVER SLACK_WEBHOOK (public #reships).
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
var EXC_PP_WEEKLY_BUDGET = 2000;          // leaves ~500/wk headroom for the reship refresh
var EXC_PP_MAX_PER_RUN = 120;             // backstop; the weekly counter normally binds first
var EXC_PP_BUDGET_PROP = 'PP_WEEK_USED';  // "<isoWeekKey>|<count>" — shared, not per-job

/** ISO-ish week key in ET, e.g. 2026-W32. Resets the counter when it changes. */
function excWeekKey_() {
  var now = new Date();
  var y = Utilities.formatDate(now, EXC_TZ, 'yyyy');
  var w = Utilities.formatDate(now, EXC_TZ, 'ww');
  return y + '-W' + w;
}

/**
 * Reserve up to `want` ParcelPanel calls from the SHARED weekly budget. Returns how many are
 * allowed. Loud when it bites: a silently truncated poll set reads as "no exceptions found",
 * which is the failure mode this whole job exists to prevent.
 */
function excBudgetTake_(want) {
  var props = PropertiesService.getScriptProperties();
  var raw = String(props.getProperty(EXC_PP_BUDGET_PROP) || '');
  var parts = raw.split('|');
  var wk = excWeekKey_();
  var used = (parts[0] === wk) ? (parseInt(parts[1], 10) || 0) : 0;
  if (parts[0] !== wk && parts[0]) Logger.log('  PP budget: new week ' + wk + ', counter reset from ' + raw);
  var left = Math.max(0, EXC_PP_WEEKLY_BUDGET - used);
  var take = Math.min(want, left, EXC_PP_MAX_PER_RUN);
  if (take < want) {
    Logger.log('  🔴 PP BUDGET BIT: wanted ' + want + ', taking ' + take +
               ' (used ' + used + '/' + EXC_PP_WEEKLY_BUDGET + ' this week, per-run cap ' +
               EXC_PP_MAX_PER_RUN + '). Unpolled orders stay queued, NOT silently dropped.');
  }
  props.setProperty(EXC_PP_BUDGET_PROP, wk + '|' + (used + take));
  Logger.log('  PP budget: ' + (used + take) + '/' + EXC_PP_WEEKLY_BUDGET + ' used this week (' + wk + ')');
  return take;
}

/**
 * 🔴 FILTER BEFORE CALLING PP (Kurt 2026-08-10). Shopify already knows which of these boxes are
 * DELIVERED, and asking Shopify costs nothing against the ParcelPanel budget. Close them here so
 * they leave the poll set permanently. A delivered box cannot become an exception.
 * Returns the orders still worth polling.
 */
function excResolveDelivered_(orders, st) {
  var alive = [], closed = 0;
  for (var i = 0; i < orders.length; i += 100) {
    var batch = orders.slice(i, i + 100);
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
        if (f.displayStatus === 'DELIVERED') { delivered[num] = 1; return; }
        (((f.events || {}).edges) || []).forEach(function (x) {
          if (x.node.status === 'DELIVERED') delivered[num] = 1;
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
function excClassify_(ship) {
  var cps = (ship && ship.checkpoints) || [];
  var carrierCps = cps.filter(function (c) { return c && c.status; });
  var pick = carrierCps.length ? carrierCps[0] : (cps.length ? cps[0] : null);
  var detail = String((pick && (pick.detail || pick.description || pick.message)) || '').trim();
  var e = detail.toLowerCase();
  var status = String((ship && (ship.delivery_status || ship.status)) || '').toUpperCase();
  var pickup = String((ship && ship.pickup_date) || '');
  var delivered = String((ship && ship.delivery_date) || '');

  // eventAt = when the CARRIER scanned it (checkpoint_time), which is the number that matters for
  // triage — "damaged since Tuesday 08:14" beats "a cron noticed at 16:00". Kept separate from the
  // sweep's own stamp; the gap between the two IS the feed latency, which is its own signal.
  var eventAt = String((pick && pick.checkpoint_time) || '').replace('T', ' ').slice(0, 16);
  function r(cls, ping) {
    return { cls: cls, detail: detail, ping: ping, status: status, eventAt: eventAt };
  }

  // A real delivery_date is authoritative — nothing beats it.
  if (delivered) return r('DELIVERED', false);

  // 🔴 Failure classes are tested BEFORE the bare-text "delivered" suppress, because the word
  // "delivered" is a SUBSTRING of "unable to be delivered". Testing /\bdelivered\b/ first
  // silently swallowed UNDELIVERABLE — the single largest ping class (15 of 36 on 6/29-7/20) —
  // and a suppressed alert is invisible by definition. Caught by excSelfTest under node,
  // 2026-07-30. Do not reorder these.
  // Phrasing varies a LOT by carrier. Every alternative below came off a real event in the
  // 6/29-7/20 replay — 5 genuine failures were sitting in IN_NETWORK until this was widened
  // ("returned to the SELLER" not sender, "unable to DELIVER" not to be delivered, "unable to
  // LOCATE your package"). When adding a carrier, replay before trusting the buckets.
  if (/unable to (be )?deliver(ed)?|cannot be delivered|undeliverable/.test(e)) return r('UNDELIVERABLE', true);
  if (/\bdamaged\b|merchandise has been discarded/.test(e)) return r('DAMAGED', true);
  if (/returning package to shipper|returned to a? ?veho warehouse|returned to the (sender|seller|shipper)|returned to shipper/.test(e)) {
    return r('RETURNED', true);
  }
  if (/lost by driver|will be discarded|unable to locate your package/.test(e)) return r('LOST', true);
  if (/need additional information to complete/.test(e)) return r('ADDRESS_ISSUE', true);
  if (/was attempted but could not be completed|delivery attempt failed/.test(e)) return r('ATTEMPT_FAILED', true);

  // Bare "Delivered" text with no delivery_date — the Veho case where the bucket never flipped
  // back (23 of 71 on 6/29-7/20). Safe to suppress only now that every failure class above has
  // already been ruled out.
  // ⚠️ KNOWN GAP: a box returned to origin can also read "Delivered, <origin city>" (order 154810,
  // FedEx, dest AL, delivered back in Lebanon TN). v1 suppresses it. Catching that needs the
  // event location compared against the destination state — see EXCEPTIONS_ALERT_RULES.md.
  if (/\bdelivered\b/.test(e)) return r('DELIVERED', false);

  // Never picked up: PP knows about the label but no pickup scan ever landed. Only meaningful
  // once the box has had a day to move — before that it is just a fresh label.
  if (!pickup && (status.indexOf('INFO') >= 0 || /shipment information sent|order created/.test(e))) {
    var ful = String((ship && (ship.fulfillment_date || ship.order_date)) || '').slice(0, 10);
    if (ful && excDaysSince_(ful) >= 1) return r('NEVER_PICKED_UP', true);
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
  var out = { ships: {}, failed: 0, throttled: 0, attempted: 0, seen: {}, budgetHit: false };
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
      if (code === 429 || code === 503) { retry.push(on); return; }
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
      out.failed += slice.length;
      continue;
    }
    if (retry.length) {                       // one backoff pass, then leave it for next run
      Utilities.sleep(EXC_PP_BACKOFF_MS);
      try {
        var still = consume(retry, UrlFetchApp.fetchAll(retry.map(reqFor)));
        out.throttled += still.length;
      } catch (err2) {
        out.throttled += retry.length;
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
  var token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (!token) throw new Error('SLACK_BOT_TOKEN missing — cannot post to #exceptions');
  var r = UrlFetchApp.fetch('https://slack.com/api/chat.postMessage', {
    method: 'post', contentType: 'application/json',
    headers: { Authorization: 'Bearer ' + token },
    payload: JSON.stringify({ channel: EXC_CHANNEL, text: text, unfurl_links: false }),
    muteHttpExceptions: true,
  });
  var d = JSON.parse(r.getContentText());
  if (!d.ok) throw new Error('slack post failed: ' + d.error);
}

var EXC_EMOJI_ = {
  UNDELIVERABLE: ':x:', DAMAGED: ':boom:', RETURNED: ':leftwards_arrow_with_hook:',
  NEVER_PICKED_UP: ':no_entry:', LOST: ':question:', ADDRESS_ISSUE: ':house:',
  ATTEMPT_FAILED: ':warning:',
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
  ['SLACK_BOT_TOKEN', 'posting to #exceptions (excSlackPost_)'],
];

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
  var all = props.getKeys().sort().join(', ');
  lines.push('', 'all keys on this project: ' + (all || '(none)'));
  var msg = lines.join('\n');
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Exception sweep — properties', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) {}
  return msg;
}

function hourlyExceptionSweep() {
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
    var allowed = excBudgetTake_(pollable.length);
    var batch = pollable.slice(0, allowed);
    Logger.log('  PP: asked ' + batch.length);

    var pp = excPpFetch_(batch, new Date().getTime() + EXC_TIME_BUDGET_MS);

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
      var v = excClassify_(ship);
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
             open: open.length, neverPolled: neverPolled, dryRun: EXC_DRY_RUN };
  } catch (e) {
    try {
      // stop-write covers the failure alert too — it posts to the same channel
      if (EXC_DRY_RUN) throw e;
      excSlackPost_(':rotating_light: exceptions sweep FAILED: ' + e);
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
    .addItem('Show scheduled triggers', 'excListTriggers')
    .addItem('Install/repair hourly trigger', 'installExceptionsTrigger')
    .addToUi();
}

// 🔴 cleanupHostSheet() DELETED 2026-07-31, deliberately — do not reintroduce it.
// It dropped tabs by name (Product Mix (T), Triage, Raw Data, _seed, _state) to strip a clone
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
    ['The delivery of your package was attempted but could not be completed', 'EXCEPTION', 'ATTEMPT_FAILED', true],
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
  if (excDisplay_('NEVER_PICKED_UP') !== 'Lost in Transit (no scan)') {
    fails.push('NEVER_PICKED_UP must display as "Lost in Transit (no scan)"');
  }
  if (excDisplay_('LOST') === excDisplay_('NEVER_PICKED_UP')) {
    fails.push('LOST and NEVER_PICKED_UP must stay distinct — different reasons, not a merge');
  }
  var npu = excClassify_({ checkpoints: [{ detail: 'Order created', status: 'INFO_RECEIVED' }],
                           status: 'INFO_RECEIVED', fulfillment_date: '2026-01-01' });
  if (npu.cls !== 'NEVER_PICKED_UP') {
    fails.push('dedupe key must remain the token NEVER_PICKED_UP, got ' + npu.cls);
  }

  // newest-first ordering guard: the oldest checkpoint is storefront copy, must never win
  var ordering = excClassify_({ checkpoints: [
    { detail: 'Your package has been damaged. Please contact the seller', status: 'EXCEPTION' },
    { detail: 'Orders are prepared fresh weekly. Your box is in queue', status: null },
  ] });
  if (ordering.cls !== 'DAMAGED') fails.push('newest-first/null-status guard failed -> ' + ordering.cls);

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
