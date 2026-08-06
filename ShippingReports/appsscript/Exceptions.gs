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
var EXC_MAX_POLL_PER_RUN = 400;           // fits the 6-min ceiling at the pacing above
var EXC_PP_FAIL_RATIO = 0.2;              // share of NON-throttle failures that means CRITICAL
var EXC_COHORTS_BACK = 2;                 // current + previous ship week stay in the poll set

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
function excPpFetch_(orderNums) {
  var out = { ships: {}, failed: 0, throttled: 0, attempted: 0, seen: {} };
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
  sh.getRange(2, 1, sh.getLastRow() - 1, 8).getValues().forEach(function (r) {
    if (!r[0]) return;
    st[String(r[0])] = {
      order: String(r[0]), cohort: r[1], customer: r[2], state: r[3], carrier: r[4],
      open: String(r[5]) === '1',
      alerted: String(r[6] || '').split(',').filter(String),
      last_seen: r[7],
    };
  });
  return st;
}

function excSaveState_(st) {
  var ss = excSS_();
  var sh = ss.getSheetByName(EXC_STATE_TAB) || ss.insertSheet(EXC_STATE_TAB);
  sh.clear();
  var rows = [['order', 'cohort', 'customer', 'state', 'carrier', 'open', 'alerted_classes', 'last_seen']];
  Object.keys(st).forEach(function (k) {
    var r = st[k];
    rows.push([r.order, r.cohort, r.customer, r.state, r.carrier, r.open ? '1' : '0',
               r.alerted.join(','), r.last_seen || '']);
  });
  sh.getRange(1, 1, rows.length, 8).setValues(rows);
  sh.hideSheet();
}

// ---------------------------------------------------------------- Slack

function excSlackPost_(text) {
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

function excMessage_(rec, cls, detail, eventAt) {
  // Verbatim carrier text is non-negotiable — it's what lets Dan judge in 2s without opening
  // anything. Order link last so Slack doesn't unfurl over the detail.
  return (EXC_EMOJI_[cls] || ':warning:') + ' *' + cls.replace(/_/g, ' ') + '* — #' + rec.order +
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
  sh.appendRow([stamp, eventAt || '', '#' + rec.order, rec.customer, rec.carrier, rec.state,
                cls, detail]);
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
    // oldest-seen first so nothing starves under the per-run cap
    open.sort(function (a, b) { return String(st[a].last_seen || '').localeCompare(String(st[b].last_seen || '')); });
    var batch = open.slice(0, EXC_MAX_POLL_PER_RUN);

    var pp = excPpFetch_(batch);

    // 🔴 A PP outage must not read as "no exceptions" — silence has to fail loudly. But THROTTLING
    // is not an outage: those orders keep their old last_seen, stay at the front of the queue and
    // are picked up next run. Only genuine failures count against the ratio.
    if (pp.attempted && pp.failed / pp.attempted > EXC_PP_FAIL_RATIO) {
      throw new Error('ParcelPanel fetch failing: ' + pp.failed + '/' + pp.attempted +
                      ' hard failures (throttled: ' + pp.throttled + ')' +
                      ' — results suppressed rather than reported as all-clear');
    }

    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
    var posted = 0;
    batch.forEach(function (on) {
      var rec = st[on], ship = pp.ships[on];
      // Only stamp orders PP actually answered for. Stamping an unreached order sends it to the
      // back of the oldest-first queue, starving it indefinitely while the log looks fine.
      if (!pp.seen[on]) return;
      rec.last_seen = stamp;
      if (!ship) return;
      var c = ship.carrier;
      rec.carrier = (c && (c.name || c.code)) || rec.carrier || '';
      var v = excClassify_(ship);
      if (v.cls === 'DELIVERED') { rec.open = false; return; }
      if (!v.ping) return;
      if (rec.alerted.indexOf(v.cls) >= 0) return;   // dedup on (order, class)
      excSlackPost_(excMessage_(rec, v.cls, v.detail, v.eventAt));
      excLog_(stamp, rec, v.cls, v.detail, v.eventAt);
      rec.alerted.push(v.cls);
      rec.open = false;                              // notified once; a human owns it now
      posted++;
    });

    excSaveState_(st);
    Logger.log('exceptions sweep: reached ' + Object.keys(pp.seen).length + ' of ' + batch.length +
               ' polled (' + open.length + ' open, throttled ' + pp.throttled +
               ', hard failures ' + pp.failed + '), posted ' + posted);
  } catch (e) {
    try {
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
    .addItem('Run sweep now', 'hourlyExceptionSweep')
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
  // newest-first ordering guard: the oldest checkpoint is storefront copy, must never win
  var ordering = excClassify_({ checkpoints: [
    { detail: 'Your package has been damaged. Please contact the seller', status: 'EXCEPTION' },
    { detail: 'Orders are prepared fresh weekly. Your box is in queue', status: null },
  ] });
  if (ordering.cls !== 'DAMAGED') fails.push('newest-first/null-status guard failed -> ' + ordering.cls);

  Logger.log(fails.length ? 'FAIL:\n' + fails.join('\n') : 'PASS: ' + (cases.length + 1) + ' cases');
  return fails;
}
