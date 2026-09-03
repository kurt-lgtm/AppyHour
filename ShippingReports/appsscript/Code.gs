/**
 * Reship tracking report — sheet-bound Apps Script port.
 * Constraints SSOT: ShippingReports/RESHIP_REPORT_RULES.md (repo). Rules R1-R13
 * apply verbatim; this port changes the host, not the rules.
 *
 * SETUP (one-time):
 *  1. Extensions -> Apps Script on the Reship Sheet, paste this file.
 *  2. Project Settings -> Script Properties, add:
 *       SHOPIFY_STORE   e.g. 504ac4  (the *.myshopify.com subdomain)
 *       SHOPIFY_TOKEN   Admin API access token
 *       GORGIAS_USER    Gorgias account email
 *       GORGIAS_KEY     Gorgias API key
 *       SLACK_WEBHOOK   DEAD 2026-08-27 — bound by URL to public #reships, read by nothing here.
 *                       Alerts go through slack_ -> excChannelOps_() -> #kurt-ops.
 *  3. Run seedStateFromSheet() once if importing the local runner's state
 *     (paste reship_report_state.json rows into a temp tab first), or just
 *     let enrichment catch up over a few runs.
 *  4. Run refresh() once manually to authorize scopes.
 *  5. Triggers -> add time-driven trigger: refresh, hourly.
 *  6. Then DISABLE the local schtask 'reship-report-refresh' (cutover step).
 */

var PIVOT_SHEET_ID = '1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU'; // Kurt's pivot sheet — the only sheet the engine touches (Kurt 2026-07-13, decoupled from the old main sheet)
var SHEET_ID = PIVOT_SHEET_ID; // state (_state) now lives on the pivot sheet
function mainSS_() { return SpreadsheetApp.openById(SHEET_ID); }
var PIVOT_CUTOVER = '2026-07-08'; // membership = frozen _seed tab (7/08 unfulfilled queue) UNION entered >= cutover; rows persist once fulfilled (Kurt 7/09)
var STATE_TAB = '_state';
var MATURITY_DAYS = 14;
var LATE_REPORT_DAYS = 16;
var HIGH_VALUE = 150;
var WEEKS_BACK = 2; // window starts 2 weeks back (Kurt 2026-07-09: from _SHIP_2026-06-22)
var MAX_ENRICH_PER_RUN = 60; // Gorgias 429 guard

// ---------- entry point ----------

function refresh() {
  // ALL Slack alerts from this file (error + breach) are OPS-class and go to the
  // #kurt-ops via slack_ — never SLACK_WEBHOOK (public #reships) and never
  // #exceptions (Kurt 2026-07-13; destination restated 2026-08-19, directive P8).
  // Apps Script failure emails are the backup.
  try {
    build_();
  } catch (e) {
    slack_(':rotating_light: Reship report (Apps Script) FAILED: ' + e, true);
    throw e;
  }
}

// Custom menu (like LTF v2). onOpen fires when the sheet is opened and draws the
// menu; each item runs a function by name. Reload the sheet after deploy to see it.
function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Reship Report')
    .addItem('Refresh now (full)', 'menuRefreshNow')
    .addSeparator()
    .addItem('Refresh Product Mix + Reship only', 'menuRefreshProductMix')
    .addItem('Refresh Triage only', 'menuRefreshTriage')
    .addItem('Preview Triage decisions (writes NOTHING)', 'menuPreviewTriageDecisions')
    .addItem('Refresh Daily counts only', 'menuRefreshDaily')
    .addSeparator()
    .addItem('Refresh Hold tab', 'menuRefreshHold')
    .addItem('Preview Hold tab (writes NOTHING)', 'menuPreviewHold')
    .addSeparator()
    .addItem('Backfill Gorgias + enrich reships', 'menuBackfillGorgias')
    .addToUi();
  // 🔴 Running Reship Report is KING (Kurt 2026-08-06): this file owns the reserved
  // onOpen. Co-hosted builds must NOT define their own onOpen (Apps Script runs only
  // one — last file loaded silently wins) and must NOT retire this menu. They expose
  // an installer we CALL here, so their menu appears without contesting ownership.
  try { if (typeof onOpenExceptions === 'function') onOpenExceptions(); } catch (e) {}
}

// shared helpers for the menu items
function menuMondays_() {
  var today = new Date(), mondays = [];
  for (var i = 0; i <= WEEKS_BACK; i++) mondays.push(mondayOf_(addDays_(today, -7 * i)));
  return mondays;
}
function menuStamp_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd'T'HH:mm:ss");
}

function menuRefreshNow() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Refreshing all tabs… (~1–2 min)', 'Reship Report', -1);
  build_();
  ss.toast('Done.', 'Reship Report', 5);
}

function menuRefreshProductMix() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Rebuilding Product Mix + Reship…', 'Reship Report', -1);
  writeProductMix_(menuMondays_(), {}, menuStamp_());
  writeProductMixT_();
  ss.toast('Done.', 'Reship Report', 5);
}

function menuRefreshTriage() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Rebuilding Triage (resolves orders/Gorgias)…', 'Reship Report', -1);
  var mondays = menuMondays_();
  writeTriage_(loadState_(), mondays[mondays.length - 1], menuStamp_());
  writeProductMixT_();  // Reship unresolved/potential depend on Triage — refresh it too
  ss.toast('Done.', 'Reship Report', 5);
}

function menuRefreshDaily() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Rebuilding Daily counts…', 'Reship Report', -1);
  writeDaily_(loadState_(), menuStamp_());
  ss.toast('Done.', 'Reship Report', 5);
}

// 🔴 DRY RUN — reads Triage!H + _triage_decisions, writes NOTHING. Run this BEFORE a refresh
// to see exactly which rows a decision will remove, what the CS-error tally will be, and which
// col-H text is unrecognized (those rows stay counted).
function menuPreviewTriageDecisions() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var ps = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  var seen = {}, unknown = [], tally = {}, lines = [];
  TRIAGE_DECISION_ORDER.forEach(function (d) { tally[d] = 0; });
  function take(key, raw, ship, src) {
    var n = normalizeTriageDecision_(raw);
    if (n === null) { unknown.push(key + ' = "' + String(raw).trim() + '" (' + src + ')'); return; }
    if (!n) return;
    if (!seen[key]) { seen[key] = n; tally[n]++; lines.push(key + '  ' + (ship || '?') + '  → ' + n + ' (' + src + ')'); }
  }
  var tsh = ps.getSheetByName('Triage');
  if (tsh && tsh.getLastRow() >= 3) {
    tsh.getRange('A3:H' + tsh.getLastRow()).getValues().forEach(function (r) {
      if (r[0] && String(r[7] == null ? '' : r[7]).trim()) take(String(r[0]), r[7], String(r[4] || ''), 'Triage!H');
    });
  }
  var dsh = ps.getSheetByName('_triage_decisions');
  if (dsh && dsh.getLastRow() >= 1) {
    var dw = Math.max(2, Math.min(5, dsh.getMaxColumns()));
    dsh.getRange(1, 1, dsh.getLastRow(), dw).getValues().forEach(function (r) {
      if (r[0]) take(String(r[0]), r[1], String(r[2] || ''), 'persisted');
    });
  }
  var total = 0;
  TRIAGE_DECISION_ORDER.forEach(function (d) { total += tally[d]; });
  var msg = 'PREVIEW ONLY — nothing was written.\n\n' +
    TRIAGE_DECISION_ORDER.map(function (d) {
      return d + ': ' + tally[d] + (TRIAGE_DECISION_NOT_A_FAILURE[d] ? '  (NOT counted as a shipping failure)' : '');
    }).join('\n') +
    '\nresolved total: ' + total +
    '\nCS error rate (of resolved): ' + (total ? (tally['cs error'] / total * 100).toFixed(1) + '%' : 'n/a') +
    '\n\nUNRECOGNIZED (NOT applied — rows stay counted): ' + (unknown.length ? '\n  ' + unknown.join('\n  ') : 'none') +
    '\n\nRows that will be removed on the next refresh:\n  ' + (lines.length ? lines.join('\n  ') : 'none');
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Triage decisions — preview', msg, SpreadsheetApp.getUi().ButtonSet.OK); }
  catch (e) { ss.toast('Preview logged (View > Executions).', 'Reship Report', 8); }
}

function menuBackfillGorgias() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Sweeping reships + backfilling Gorgias… (~1–2 min)', 'Reship Report', -1);
  var mondays = menuMondays_(), oldest = mondays[mondays.length - 1];
  var state = loadState_();
  sweepAndEnrich_(state, oldest);
  enrichBoxTypes_(state, mondays);
  enrichTransitOverride_(state, mondays);  // late (>2d transit) supersedes warm
  fillRequestedFromSlack_(state, oldest);
  saveState_(state);
  refreshPivotSheet_(state, mondays);
  ss.toast('Done — Raw Data re-rendered.', 'Reship Report', 5);
}

function build_() {
  var today = new Date();
  var mondays = [];
  for (var i = 0; i <= WEEKS_BACK; i++) mondays.push(mondayOf_(addDays_(today, -7 * i)));
  var oldest = mondays[mondays.length - 1];
  var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd'T'HH:mm:ss");

  var state = loadState_();

  // R7: live denominators, cancelled AND reship excluded
  var denoms = {};
  mondays.forEach(function (m) {
    var tag = '_SHIP_' + iso_(m);
    denoms[tag] = ordersCount_("tag:'" + tag + "' -status:cancelled -tag:'Reship'");
  });

  // R1/R5/R6 sweep + R3/R4 incremental enrichment. Sweep 92d back so the Daily
  // tab has history; enrichment stays incremental under the 6-min GAS cap.
  var histSince = iso_(addDays_(today, -HISTORY_DAYS));
  var sweepFrom = histSince < iso_(oldest) ? new Date(histSince) : oldest;
  sweepAndEnrich_(state, sweepFrom);
  enrichBoxTypes_(state, mondays);
  enrichTransitOverride_(state, mondays);  // late (>2d transit) supersedes warm
  fillRequestedFromSlack_(state, histSince);
  saveState_(state);

  refreshPivotSheet_(state, mondays);
  writeProductMix_(mondays, denoms, stamp);
  try { writeTriage_(state, oldest, stamp); writeProductMixT_(); }
  catch (e) { Logger.log('triage failed (non-fatal): ' + e); }
  writeDaily_(state, stamp);
  // Hold tab (D33) — the migration backlog. NON-FATAL, like the Triage call above: a hold-tag
  // sweep failing must never cost the reship report. Cheap by construction — once today's column
  // is stamped every later invocation returns after ONE Sheets read and zero Shopify calls.
  try { holdRefresh_(); }
  catch (e) { Logger.log('hold tab failed (non-fatal): ' + e); }
  // Headless arm bridge (D33): applies the ET backfill one-shot iff the `_hold_arm` cell is set.
  // Non-fatal like the Hold call above; on 24-of-24 hourly runs with no arm set this is ONE read.
  try { holdEtBackfillIfArmed_(); }
  catch (e) { Logger.log('hold ET backfill failed (non-fatal): ' + e); }
}

function orderNum_(key) { var n = parseInt(String(key).replace(/[^0-9]/g, ''), 10); return isNaN(n) ? 0 : n; }

function refreshPivotSheet_(state, mondays) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  var sh = ss.getSheetByName('Raw Data') || ss.insertSheet('Raw Data');
  var props = PropertiesService.getScriptProperties();
  var watermark = parseInt(props.getProperty('PIVOT_WATERMARK') || '0', 10) || 0;

  var existing = [], present = {};
  if (sh.getLastRow() >= 2) {
    sh.getRange('A2:I' + sh.getLastRow()).getValues().forEach(function (row) {
      if (row[0]) { existing.push(row); present[row[0]] = true; }
    });
  }
  if (existing.length === 0 && watermark) {
    try { slack_('Reship pivot Raw Data is EMPTY. Only new reships will populate.', true); } catch (e) {}
  }

  existing.forEach(function (row) {
    var r = state[row[0]];
    if (!r) return;
    if (!row[1] && r.requested) row[1] = r.requested;
    if (!row[2] && r.entered) row[2] = r.entered;
    if (r.issue) row[3] = r.issue;
    if (r.original_cohort) row[4] = r.original_cohort;
    if (r.outbound) row[5] = r.outbound;
    if (r.status) row[6] = r.status;
    if (r.original) row[7] = r.original;
    if (r.original_boxtype) row[8] = r.original_boxtype;
  });

  var floor = watermark;
  existing.forEach(function (row) { floor = Math.max(floor, orderNum_(row[0])); });
  var maxNum = floor;
  Object.keys(state).filter(function (k) {
    return !present[k] && orderNum_(k) > floor;
  }).sort(function (a, b) { return orderNum_(a) - orderNum_(b); }).forEach(function (k) {
    var r = state[k];
    existing.push([k, r.requested || '', r.entered || '', r.issue || '', r.original_cohort || '',
      r.outbound || '', r.status || '', r.original || '', r.original_boxtype || '']);
    maxNum = Math.max(maxNum, orderNum_(k));
  });

  var rows = [['Order', 'Requested', 'Created', 'Issue', 'Incoming week', 'Outgoing week', 'Status',
    'Original', 'Original Box Type']].concat(existing);
  // 🔴 D38 — ATOMIC SWAP (see writeTabTo_). Raw Data is the substrate of every reship COUNTIFS on
  // Product Mix (D/I/N and therefore Potential/Actual); the clearContents() that used to sit here
  // is exactly the window in which those formulas read 0 and Kurt saw the Reship pair torn
  // (`Potential 1 / Actual 0`). One covering setValues replaces old content, new content, and the
  // blanked tail in a single mutation — a reader sees the old ledger or the new one, never neither.
  var width = 9;
  var rdOldR = sh.getLastRow(), rdOldC = sh.getLastColumn();
  var rdW = Math.max(width, rdOldC), rdH = Math.max(rows.length, rdOldR);
  var rdGrid = rows.map(function (r) {
    return r.slice(0, width)
            .concat(new Array(Math.max(0, width - r.length)).fill(''))
            .concat(new Array(rdW - width).fill(''));
  });
  while (rdGrid.length < rdH) rdGrid.push(new Array(rdW).fill(''));
  sh.getRange(1, 1, rdH, rdW).setValues(rdGrid);
  sh.getRange('B:C').setNumberFormat('@');
  props.setProperty('PIVOT_WATERMARK', String(maxNum));
}

// ---------- transient-network retry (shared by every job in this project) ----------

// 🔴 WHY: 2026-08-19 04:09 and 05:09 the hourly `refresh` died with
// "Exception: Address unavailable: https://504ac4.myshopify.com/.../graphql.json" and alarmed Kurt
// in Slack; the 08:08 run succeeded untouched. That string is NOT an HTTP status — UrlFetchApp
// THROWS it when Google cannot reach the host at all, so `muteHttpExceptions` never sees it and
// every response-code check downstream is skipped. One blip killed a whole run because nothing
// retried. Bounded retry only: 3 attempts, ~1s/2s/4s plus jitter.
// 🔴 CONNECTION-CLASS ONLY. A 400/401/403 is a REAL fault and must still fail loudly and
// immediately — retrying it hides a broken token or a malformed query behind 7 seconds of silence.
// Retryable = a thrown connection error, or an HTTP 429 / 5xx. Everything else returns/throws
// unchanged, so callers that already handle 429 (shopifyGql_, ntGet_) keep their own logic.
var NET_RETRY_ATTEMPTS = 3;
var NET_RETRY_BASE_MS = 1000;

/**
 * True only for failures worth retrying. When the message carries an HTTP code, that code DECIDES
 * (4xx => false, no exceptions) — the free-text test is reached only when there is no code, so a
 * 400 whose response body happens to contain the word "unavailable" can never be mistaken for a
 * connection failure.
 */
function netRetryable_(e) {
  var m = String((e && e.message) || e || '');
  var mm = m.match(/returned code (\d{3})/i);
  if (mm) { var c = Number(mm[1]); return c === 429 || c >= 500; }
  return /address unavailable|dns error|timed? ?out|timeout|connection (was )?(reset|refused|closed|aborted)|network error|service unavailable/i.test(m);
}

/**
 * Drop-in for UrlFetchApp.fetch with bounded exponential backoff on connection-class failures.
 * `tag` is a short label for the log line; the URL is logged with its query string stripped so a
 * token in a query param can never reach the log.
 * Every retry is LOGGED — a flaky window has to be visible afterwards, not silently smoothed over.
 */
function netFetch_(url, params, tag) {
  var label = String(tag || url).split('?')[0];
  for (var a = 0; a < NET_RETRY_ATTEMPTS; a++) {
    var last = a === NET_RETRY_ATTEMPTS - 1;
    try {
      var r = UrlFetchApp.fetch(url, params);
      var code = r.getResponseCode();
      if (!last && (code === 429 || code >= 500)) {
        Logger.log('  net retry ' + (a + 1) + '/' + (NET_RETRY_ATTEMPTS - 1) + ': HTTP ' + code + ' on ' + label);
        Utilities.sleep(NET_RETRY_BASE_MS * Math.pow(2, a) + Math.floor(Math.random() * 400));
        continue;
      }
      // Exhausted, or not retryable: hand the response back UNCHANGED so existing code-checks run.
      return r;
    } catch (e) {
      if (last || !netRetryable_(e)) throw e;
      Logger.log('  net retry ' + (a + 1) + '/' + (NET_RETRY_ATTEMPTS - 1) + ': ' +
                 String(e).slice(0, 120) + ' on ' + label);
      Utilities.sleep(NET_RETRY_BASE_MS * Math.pow(2, a) + Math.floor(Math.random() * 400));
    }
  }
  throw new Error('netFetch_ fell through on ' + label);   // unreachable; never return undefined
}

// ---------- Shopify ----------

// ---------- Shopify I/O accounting (2026-08-31) ----------
// 🔴 THE BUDGET THAT BINDS IS **BYTES**, AND IT IS GOOGLE'S, NOT SHOPIFY'S. On 2026-08-31 the
// hourly sweep died with "Exception: Bandwidth quota exceeded: <shopify url>. Try reducing the rate
// of data transfer." That is thrown by UrlFetchApp when the SCRIPT OWNER's daily url-fetch
// data-received quota runs out — the Shopify URL appears only because that was the request in
// flight. It is NOT Shopify's cost bucket: measured the same day, the sweep's own queries cost
// actualQueryCost 20 (resolve) and 35 (seed) against a 4,000-point bucket restoring at 200/s, which
// never dropped below 3,965. Chasing Shopify's leaky bucket after this alarm is chasing the wrong
// meter, which is exactly what the raw message invites.
// 🔴 So: count BYTES, per run, and say so out loud. Cost is logged too, but only to keep the
// "is it Shopify throttling us?" question answerable with a number instead of a guess.
var SHOPIFY_IO_BYTES_ = 0;      // response bytes this execution
var SHOPIFY_IO_CALLS_ = 0;      // graphql calls this execution
var SHOPIFY_IO_COST_ = 0;       // summed actualQueryCost this execution
var SHOPIFY_IO_THROTTLE_ = null; // newest throttleStatus seen {currentlyAvailable,maximumAvailable,restoreRate}

/** One line naming what this execution actually drew from Shopify. Log it at the end of any job. */
function shopifyIoSummary_() {
  var t = SHOPIFY_IO_THROTTLE_;
  return 'shopify I/O this run: ' + SHOPIFY_IO_CALLS_ + ' call(s), ' +
         (SHOPIFY_IO_BYTES_ / 1024).toFixed(1) + ' KB received, actualQueryCost ' + SHOPIFY_IO_COST_ +
         (t ? '; bucket ' + t.currentlyAvailable + '/' + t.maximumAvailable +
              ' restoring ' + t.restoreRate + '/s' : '');
}

/**
 * True for the Apps Script url-fetch DATA quota wall. It is not an HTTP status and not a Shopify
 * error, so no response-code check and no `d.errors` branch can ever see it — UrlFetchApp throws it
 * straight out of `fetch`. Not retryable: a DAILY byte budget does not refill in seven seconds, and
 * retrying spends more of the thing that just ran out (netRetryable_ deliberately excludes it).
 */
function shopifyQuotaWall_(e) {
  return /bandwidth quota|reducing the rate of data transfer|service invoked too many times/i
    .test(String((e && e.message) || e || ''));
}

function shopifyGql_(query, variables) {
  var props = PropertiesService.getScriptProperties();
  var url = 'https://' + props.getProperty('SHOPIFY_STORE') + '.myshopify.com/admin/api/2026-04/graphql.json';
  for (var attempt = 0; attempt < 6; attempt++) {
    var resp = netFetch_(url, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Shopify-Access-Token': props.getProperty('SHOPIFY_TOKEN') },
      payload: JSON.stringify({ query: query, variables: variables || {} }),
      muteHttpExceptions: true,
    }, 'shopify graphql');
    if (resp.getResponseCode() === 429) { Utilities.sleep(2000); continue; }
    var body = resp.getContentText();
    SHOPIFY_IO_CALLS_ += 1;
    SHOPIFY_IO_BYTES_ += body.length;
    var d = JSON.parse(body);
    // extensions.cost used to be parsed and thrown away with the rest of the envelope. Keep it:
    // it is the only direct evidence about Shopify's bucket, and it costs nothing to read.
    var cost = (d.extensions || {}).cost;
    if (cost) {
      SHOPIFY_IO_COST_ += Number(cost.actualQueryCost) || 0;
      if (cost.throttleStatus) SHOPIFY_IO_THROTTLE_ = cost.throttleStatus;
    }
    if (d.errors) {
      if (JSON.stringify(d.errors).indexOf('THROTTLED') >= 0) { Utilities.sleep(2000); continue; }
      throw new Error(JSON.stringify(d.errors).slice(0, 400));
    }
    return d.data;
  }
  throw new Error('Shopify throttled out');
}

function ordersCount_(q) {
  return shopifyGql_('query($q:String!){ ordersCount(query:$q, limit:10000){ count } }', { q: q }).ordersCount.count;
}

function sweepAndEnrich_(state, oldest) {
  var q = "tag:'Reship' -status:cancelled created_at:>='" + iso_(oldest) + "T00:00:00-05:00'";
  var cursor = null, enriched = 0;
  while (true) {
    var d = shopifyGql_(
      'query($q:String!,$c:String){ orders(first:50, after:$c, query:$q){' +
      ' pageInfo{hasNextPage endCursor}' +
      ' edges{node{ name createdAt tags displayFulfillmentStatus totalPriceSet{shopMoney{amount}}' +
      ' customer{ id email numberOfOrders } }}}}', { q: q, c: cursor });
    var o = d.orders;
    for (var i = 0; i < o.edges.length; i++) {
      var n = o.edges[i].node;
      var rec = state[n.name] || {};
      rec.entered = n.createdAt.slice(0, 10);
      rec.issue = issueOf_(n.tags);
      rec.outbound = lastShipTag_(n.tags);
      rec.total = n.totalPriceSet.shopMoney.amount;
      rec.status = n.displayFulfillmentStatus;
      var cust = n.customer || {};
      rec.lifetime_orders = cust.numberOfOrders || '';
      // order matters: attribute FIRST (bounded by entered date), then find the
      // request ticket floored at the original's ship Monday — a bogus-early
      // ticket (signup thread) otherwise poisons attribution (#158288, 7/08)
      if (!rec.original_cohort && enriched < MAX_ENRICH_PER_RUN && cust.id) {
        var org = findOriginal_(cust.id, n.createdAt, n.name, rec.entered);
        rec.original = org[0]; rec.original_cohort = org[1]; rec.original_total = org[2];
        enriched++;
      }
      if (!rec.requested && enriched < MAX_ENRICH_PER_RUN) {
        var coh = rec.original_cohort || '';
        var floor = coh.indexOf('_SHIP_') === 0 ? coh.replace('_SHIP_', '') : '';
        var rq = findRequested_(cust.email || '', rec.entered, floor);
        rec.requested = rq[0]; rec.ticket = rq[1];
        enriched++;
      }
      state[n.name] = rec;
    }
    if (!o.pageInfo.hasNextPage) break;
    cursor = o.pageInfo.endCursor;
  }
}

function findOriginal_(customerGid, beforeIso, selfName, complaintDate) {
  var cid = customerGid.split('/').pop();
  var bound = complaintDate || beforeIso.slice(0, 10);
  var d = shopifyGql_(
    'query($q:String!){ orders(first:15, query:$q, sortKey:CREATED_AT, reverse:true){' +
    ' edges{node{ name createdAt tags totalPriceSet{shopMoney{amount}} }}}}',
    { q: 'customer_id:' + cid });
  for (var i = 0; i < d.orders.edges.length; i++) {
    var n = d.orders.edges[i].node;
    if (n.name === selfName || n.tags.indexOf('Reship') >= 0 || n.createdAt >= beforeIso) continue;
    var tag = lastShipTag_(n.tags);
    if (!tag) continue;
    if (tag.replace('_SHIP_', '') >= bound) continue; // box can't fail before it ships
    return [n.name, tag, parseFloat(n.totalPriceSet.shopMoney.amount)];
  }
  return ['', 'PRIOR-NO-SHIP-TAG', 0];
}

// ---------- Gorgias (R4) ----------

function gorgiasGet_(path, params) {
  var props = PropertiesService.getScriptProperties();
  var qs = Object.keys(params || {}).map(function (k) { return k + '=' + encodeURIComponent(params[k]); }).join('&');
  var resp = netFetch_('https://appyhour.gorgias.com/api' + path + (qs ? '?' + qs : ''), {
    headers: {
      Authorization: 'Basic ' + Utilities.base64Encode(
        props.getProperty('GORGIAS_USER') + ':' + (props.getProperty('GORGIAS_KEY') || props.getProperty('GORGIAS_API_KEY'))),
      'User-Agent': 'AppyHourReshipReport/1.0', // default UA gets Cloudflare 1010
    },
    muteHttpExceptions: true,
  }, 'gorgias api');
  Utilities.sleep(1200); // ~0.8 req/s pacing
  if (resp.getResponseCode() >= 400) return null;
  return JSON.parse(resp.getContentText());
}

function findRequested_(email, entered, floorDate) {
  if (!email) return ['', ''];
  var c = gorgiasGet_('/customers', { email: email });
  if (!c || !c.data || !c.data.length) return ['', ''];
  var t = gorgiasGet_('/tickets', { customer_id: c.data[0].id, limit: 30, order_by: 'created_datetime:desc' });
  if (!t || !t.data) return ['', ''];
  var floor = iso_(addDays_(new Date(entered), -14));
  if (floorDate && floorDate > floor) floor = floorDate; // complaint can't predate shipment
  var best = '', bestId = '';
  t.data.forEach(function (tk) {
    var tc = (tk.created_datetime || '').slice(0, 10);
    if (tc >= floor && tc <= entered) { best = tc; bestId = String(tk.id); } // desc -> last = earliest
  });
  return [best, bestId];
}

// ---------- analytics ----------

function tailCdf_(state, today) {
  var offsets = [];
  Object.keys(state).forEach(function (k) {
    var rec = state[k];
    var coh = rec.original_cohort || '', req = rec.requested || '';
    if (coh.indexOf('_SHIP_') !== 0 || !req) return;
    var cmon = new Date(coh.replace('_SHIP_', ''));
    if (daysBetween_(cmon, today) < MATURITY_DAYS) return;
    var off = daysBetween_(cmon, new Date(req));
    if (off < 0) return; // misattribution guard
    offsets.push(off);
  });
  if (offsets.length < 10) return {};
  var cdf = {};
  for (var n = 0; n <= MATURITY_DAYS; n++) {
    cdf[n] = offsets.filter(function (o) { return o <= n; }).length / offsets.length;
  }
  return cdf;
}

function requestsByDay_(state, mon, upto) {
  var tag = '_SHIP_' + iso_(mon), out = [];
  Object.keys(state).forEach(function (k) {
    var rec = state[k];
    if (rec.original_cohort !== tag || !rec.requested) return;
    var off = daysBetween_(mon, new Date(rec.requested));
    if (off < 0) return;
    if (upto == null || off <= upto) out.push(k);
  });
  return out;
}

// ---------- overrides / Raw Data ----------

function loadOverrides_() {
  var out = {};
  var sh = mainSS_().getSheetByName('Raw Data');
  if (sh) {
    sh.getRange('A3:M' + Math.max(3, sh.getLastRow())).getValues().forEach(function (row) {
      if (row[0]) {
        out[row[0]] = {
          issue: String(row[9] || ''), incoming: String(row[10] || ''),
          outgoing: String(row[11] || ''),
          exclude: String(row[12] || '').trim().toLowerCase() === 'x',
        };
      }
    });
  }
  // pivot sheet's Exclude col (L) counts too — Dan works there (Kurt 7/09)
  var psh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Raw Data');
  if (psh && psh.getLastRow() >= 2) {
    psh.getRange('A2:L' + psh.getLastRow()).getValues().forEach(function (row) {
      if (row[0] && String(row[11] || '').trim().toLowerCase() === 'x') {
        if (!out[row[0]]) out[row[0]] = { issue: '', incoming: '', outgoing: '', exclude: true };
        else out[row[0]].exclude = true;
      }
    });
  }
  return out;
}

function writeRawData_(rows) {
  var ss = mainSS_();
  var sh = ss.getSheetByName('Raw Data') || ss.insertSheet('Raw Data');
  sh.clearContents();
  var width = 16;
  var padded = rows.map(function (r) { return r.concat(new Array(width - r.length).fill('')); });
  sh.getRange(1, 1, padded.length, width).setValues(padded);
  // dates as plain text so QUERY group labels stay readable
  sh.getRange('B:D').setNumberFormat('@');
}

// ---------- tab builders ----------

function buildTabs_(work, state, overrides, mondays, denoms, cdf, today, stamp) {
  var plain = {};
  var oldest = mondays[mondays.length - 1];

  mondays.forEach(function (mon) {
    var tag = '_SHIP_' + iso_(mon);
    var denom = denoms[tag];
    var cohortNames = Object.keys(work).filter(function (k) { return work[k].original_cohort === tag; });
    var rows = [];
    rows.push(['REFRESHED ' + stamp, 'cohort ' + tag,
      'denominator ' + denom + " orders (live Shopify, tag:'" + tag + "' -status:cancelled -tag:'Reship')",
      'day ' + Math.max(0, daysBetween_(mon, today)) + ' since ship Monday']);
    rows.push([]);
    rows.push(['ISSUE BREAKDOWN (unit = reship orders, R1/R6)']);
    rows.push(['Issue', 'Count', '% of cohort']);
    var issues = countBy_(cohortNames.map(function (k) { return work[k].issue; }));
    Object.keys(issues).sort(function (a, b) { return issues[b] - issues[a]; }).forEach(function (iss) {
      rows.push([iss, issues[iss], denom ? pct_(issues[iss] / denom) : 'n/a']);
    });
    rows.push(['TOTAL', cohortNames.length, denom ? pct_(cohortNames.length / denom) : 'n/a']);
    rows.push([]);

    var off = Math.min(daysBetween_(mon, today), MATURITY_DAYS);
    var prev = addDays_(mon, -7);
    var thisN = requestsByDay_(work, mon, off).length;
    var prevN = requestsByDay_(work, prev, off).length;
    var prevDenom = denoms['_SHIP_' + iso_(prev)] || 0;
    rows.push(['SAME-DAY-OFFSET COMPARISON (day ' + off + ', requested-date basis, R8)']);
    rows.push(['Cohort', 'requests by day ' + off, 'denominator', 'rate']);
    rows.push([tag, thisN, denom, denom ? pct_(thisN / denom) : 'n/a']);
    rows.push(['_SHIP_' + iso_(prev), prevN, prevDenom || 'n/a', prevDenom ? pct_(prevN / prevDenom) : 'n/a']);
    rows.push(cdf[off]
      ? ['Projected final (to-date / tail CDF)', Math.round(thisN / cdf[off] * 10) / 10,
         'CDF(' + off + ')=' + pct_(cdf[off]) + ' from mature cohorts']
      : ['Projected final', 'n/a (insufficient mature history)']);
    rows.push(['NOTE: counts lag CS entry — a request exists only once its reship order is entered in Shopify.']);
    rows.push([]);

    var wkEnd = iso_(addDays_(mon, 6));
    var enteredWk = Object.keys(work).filter(function (k) {
      var e = work[k].entered || '';
      return e >= iso_(mon) && e <= wkEnd;
    });
    rows.push(['SHOPIFY RECONCILIATION — reship orders ENTERED this calendar week (entry date != request date, R4)']);
    rows.push(['Entered this week (deduped orders)', enteredWk.length]);
    var byCoh = countBy_(enteredWk.map(function (k) { return work[k].original_cohort || '?'; }));
    Object.keys(byCoh).sort(function (a, b) { return byCoh[b] - byCoh[a]; }).forEach(function (c) {
      rows.push(['  remediating ' + c, byCoh[c]]);
    });
    rows.push([]);
    rows.push(['DETAIL']);
    rows.push(['Reship', 'Requested', 'Entered', 'Issue', 'Original', 'Outbound week', 'Ticket', 'Status']);
    cohortNames.sort(function (a, b) { return (work[a].requested || '') < (work[b].requested || '') ? -1 : 1; })
      .forEach(function (k) {
        var r = work[k];
        rows.push([k, r.requested || 'UNKNOWN', r.entered, r.issue, r.original || '',
                   r.outbound || '', r.ticket || '', r.status || '']);
      });
    plain['RS ' + tag] = rows;
  });

  // Summary
  var srows = [['REFRESHED ' + stamp,
    'unit = deduped reship orders; attribution = original cohort; denominators exclude reships'], [],
    ['Cohort', 'Cohort size', 'Reships to date', 'Rate', 'Day', 'Projected final', 'Projected rate', 'Maturity']];
  mondays.slice().reverse().forEach(function (mon) {
    var tag = '_SHIP_' + iso_(mon);
    // headline = ALL attributed reships (incl UNKNOWN requested) — must match Pivots
    var nNow = Object.keys(work).filter(function (k) { return work[k].original_cohort === tag; }).length;
    var dated = requestsByDay_(work, mon, null).length;
    var undated = nNow - dated;
    var denom = denoms[tag];
    var off = daysBetween_(mon, today);
    var mature = off >= MATURITY_DAYS;
    var c = cdf[Math.min(off, MATURITY_DAYS)];
    var proj = mature ? nNow : (c ? Math.round((dated / c + undated) * 10) / 10 : 'n/a');
    srows.push([tag, denom, nNow, denom ? pct_(nNow / denom) : 'n/a', off, proj,
      (denom && typeof proj === 'number') ? pct_(proj / denom) : 'n/a',
      mature ? 'FINAL' : 'maturing (day ' + off + ')']);
  });
  plain['Summary'] = srows;

  // Raw Data (source A-I from UNFILTERED state so excluded rows stay visible;
  // overrides J-M re-preserved; N-P effective formulas)
  var windowKeys = Object.keys(state).filter(function (k) {
    return (state[k].entered || '') >= iso_(oldest);
  }).sort(function (a, b) {
    var x = state[a].entered + a, y = state[b].entered + b;
    return x < y ? -1 : 1;
  });
  windowKeys.forEach(function (k) {
    var coh = (overrides[k] && overrides[k].incoming) || state[k].original_cohort || '';
    if (coh.indexOf('_SHIP_') === 0 && !(coh in denoms)) {
      denoms[coh] = ordersCount_("tag:'" + coh + "' -status:cancelled -tag:'Reship'");
    }
  });
  var rrows = [
    ['REFRESHED ' + stamp, 'window: entered since ' + iso_(oldest),
     'cols A-I refresh hourly (do not edit)', 'cols J-M are YOURS (survive refresh)',
     'put x in Exclude to strike a row', 'pivots update instantly'],
    ['Order', 'Entered', 'Requested', 'Ticket', 'Issue', 'Incoming week', 'Outgoing week',
     'Status', 'Original', 'Override Issue', 'Override Incoming', 'Override Outgoing',
     'Exclude', 'Eff Issue', 'Eff Incoming', 'Eff Outgoing'],
  ];
  windowKeys.forEach(function (k, i) {
    var rec = state[k], o = overrides[k] || {};
    var rn = i + 3;
    rrows.push([k, rec.entered || '', rec.requested || '', rec.ticket || '', rec.issue || '',
      rec.original_cohort || '', rec.outbound || '', rec.status || '', rec.original || '',
      o.issue || '', o.incoming || '', o.outgoing || '', o.exclude ? 'x' : '',
      '=IF($J' + rn + '<>"",$J' + rn + ',$E' + rn + ')',
      '=IF($K' + rn + '<>"",$K' + rn + ',$F' + rn + ')',
      '=IF($L' + rn + '<>"",$L' + rn + ',$G' + rn + ')']);
  });

  // Pivots — live QUERY formulas over Raw Data effective cols
  var rd = "'Raw Data'!$A$3:$P";
  function q(col) {
    return '=IFERROR(QUERY(' + rd + ', "select ' + col + ", count(A) where A<>'' and M<>'x' group by " +
      col + ' order by ' + col + " label count(A) ''\", 0), \"no data\")";
  }
  var prows = [
    ['REFRESHED ' + stamp,
     'live formulas over Raw Data (entered since ' + iso_(oldest) + '); overrides + Exclude apply instantly',
     '', 'Grand Total (excl. excluded):',
     '=COUNTIFS(\'Raw Data\'!$A$3:$A,"<>",\'Raw Data\'!$M$3:$M,"<>x")'],
    [],
    ['Reship Created (entry date)', '', '', 'Reship Requested (ticket date)', '', '',
     'Reship Outgoing ship week', '', '', 'Reship Incoming ship week', '', 'Rate', '',
     'Cohort size (excl. reships)', ''],
    [q('B'), '', '', q('C'), '', '', q('P'), '', '', q('O'), '',
     '=ARRAYFORMULA(IF(J4:J="",,IFERROR(TEXT(K4:K/VLOOKUP(J4:J,$N$4:$O,2,FALSE),"0.00%"),"")))', '',
     '', ''],
  ];
  Object.keys(denoms).sort().forEach(function (tag, i) {
    if (i === 0) { prows[3][13] = tag; prows[3][14] = denoms[tag]; }
    else {
      var row = new Array(15).fill('');
      row[13] = tag; row[14] = denoms[tag];
      prows.push(row);
    }
  });

  // Flags
  var frows = [['REFRESHED ' + stamp], [], ['Reship', 'Flag', 'Detail']];
  Object.keys(work).sort().forEach(function (k) {
    var rec = work[k];
    var coh = rec.original_cohort || '';
    if (coh.indexOf('_SHIP_') !== 0) return;
    var cmon = new Date(coh.replace('_SHIP_', ''));
    if (cmon < oldest) return;
    if (parseFloat(rec.original_total || 0) > HIGH_VALUE)
      frows.push([k, '>$150 original — Dan-managed', 'original ' + (rec.original || '') + ' $' + rec.original_total]);
    if (rec.requested && daysBetween_(cmon, new Date(rec.requested)) > LATE_REPORT_DAYS)
      frows.push([k, 'late report (>14d post-delivery proxy)', 'requested ' + rec.requested + ' vs ship ' + iso_(cmon)]);
    if (typeof rec.lifetime_orders === 'number' && rec.lifetime_orders < 3)
      frows.push([k, '<3 lifetime boxes — check sub status', 'lifetime orders: ' + rec.lifetime_orders]);
    if (!rec.requested)
      frows.push([k, 'UNKNOWN — no ticket found, needs manual check', 'entered ' + rec.entered]);
  });
  plain['Flags'] = frows;

  return { plain: plain, rawData: rrows, pivots: prows };
}

// ---------- state (_state hidden tab) ----------

var STATE_COLS = ['key', 'entered', 'requested', 'ticket', 'issue', 'outbound', 'status',
                  'total', 'original', 'original_cohort', 'original_total', 'lifetime_orders', 'original_boxtype',
                  'transit_days'];

function loadState_() {
  var sh = mainSS_().getSheetByName(STATE_TAB);
  if (!sh) return {};
  var values = sh.getDataRange().getValues();
  var state = {};
  for (var i = 1; i < values.length; i++) {
    var rec = {};
    for (var c = 1; c < STATE_COLS.length; c++) {
      var v = values[i][c];
      if (v !== '' && v != null) rec[STATE_COLS[c]] = (v instanceof Date) ? iso_(v) : v;
    }
    if (values[i][0]) state[values[i][0]] = rec;
  }
  return state;
}

function saveState_(state) {
  var ss = mainSS_();
  var sh = ss.getSheetByName(STATE_TAB) || ss.insertSheet(STATE_TAB);
  sh.hideSheet();
  var rows = [STATE_COLS];
  Object.keys(state).sort().forEach(function (k) {
    var rec = state[k];
    rows.push([k].concat(STATE_COLS.slice(1).map(function (c) {
      return rec[c] != null ? rec[c] : '';
    })));
  });
  // D38 — write-first covering swap (see writeTabTo_). The clearContents() that used to run first
  // left a window where a crash or the 6-minute kill wiped the whole `_state` tab; the covering
  // write replaces old rows, new rows, and the blanked tail in one mutation, so state can no
  // longer be destroyed by dying between two calls. Same doctrine as excSaveState_.
  var stOldR = sh.getLastRow(), stOldC = sh.getLastColumn();
  var stW = Math.max(STATE_COLS.length, stOldC), stH = Math.max(rows.length, stOldR);
  var stGrid = rows.map(function (r) { return r.concat(new Array(stW - r.length).fill('')); });
  while (stGrid.length < stH) stGrid.push(new Array(stW).fill(''));
  sh.getRange(1, 1, stH, stW)
    .setNumberFormat('@') // plain text — stop Sheets from re-typing dates
    .setValues(stGrid);
}

// ---------- sheet writes ----------

function writeTab_(name, rows) {
  var ss = mainSS_();
  var sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clearContents();
  var width = Math.max.apply(null, rows.map(function (r) { return r.length; }).concat([1]));
  var padded = rows.map(function (r) { return r.concat(new Array(width - r.length).fill('')); });
  sh.getRange(1, 1, padded.length, width).setValues(padded);
}

// ---------- alerts ----------

function breachAlert_(state, thisMon, denoms, today) {
  var off = Math.min(daysBetween_(thisMon, today), MATURITY_DAYS);
  var cur = requestsByDay_(state, thisMon, off).length;
  var prv = requestsByDay_(state, addDays_(thisMon, -7), off).length;
  if (prv && cur > prv) {
    slack_('Reship report: _SHIP_' + iso_(thisMon) + ' at ' + cur + ' requests by day ' + off +
           ' vs ' + prv + ' last week same day — tracking WORSE. ' +
           'https://docs.google.com/spreadsheets/d/' + SHEET_ID, false);
  }
}

var KURT_SLACK_ID = 'U08R19137UL';

// Alert Kurt PRIVATELY only (Kurt 2026-07-13: never a public channel). Bot post
// via chat.postMessage (SLACK_BOT_TOKEN prop, needs Bot chat:write); email
// fallback. NEVER the SLACK_WEBHOOK — that's bound to public #reships.
// Destination is #kurt-ops (C0BT47XG8CW) as of 2026-08-27 — see excChannelOps_.
//
// 🔴 THIS IS THE OPS CLASS (directive P8, Kurt 2026-08-19). Every caller of slack_ is the job
// telling on itself — a FAILED run, a refused write, an empty Raw Data, a created-ghost tab, a
// breach warning. None of them is a customer-facing exception ping, and none belongs in
// #exceptions. This function was already pointed at the right place (the appyhour-ops-reader DM,
// D0BG1541F0A) — 2026-08-19's change routed Exceptions.gs's failures HERE, not the reverse.
// 🔴 There is NO ping-class helper in this file. Do not add one: exception pings live in
// Exceptions.gs behind excSlackPost_, and this file must have no way to reach that channel.
//
// Destination resolves through excChannelOps_ (Exceptions.gs) so BOTH classes are re-routable
// from Script Properties without a code push — a push here is the one that can take the hourly
// report down. The typeof guard is not decoration: Exceptions.gs has been deleted from this
// project once (2026-08-14, by a push carrying only [appsscript, Code]); an alert path must not
// be the thing that breaks when it happens again.
// 🔴 The typeof-guard fallback is now STALE-BY-DESIGN: if Exceptions.gs is missing, slack_ falls
// back to KURT_SLACK_ID (the old ops DM), not #kurt-ops. That is deliberate — a surviving DM is a
// worse channel but still Kurt-only, and hardcoding the new id here would create the second
// "the channel" constant P8 deleted. Fix the missing file, don't repoint this literal.
function slack_(text, critical) {
  var pfx = (critical ? ':rotating_light: ' : ':warning: ') + text;
  var to = (typeof excChannelOps_ === 'function') ? excChannelOps_() : KURT_SLACK_ID;
  var token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (token) {
    try {
      var r = netFetch_('https://slack.com/api/chat.postMessage', {
        method: 'post', contentType: 'application/json',
        headers: { Authorization: 'Bearer ' + token },
        payload: JSON.stringify({ channel: to, text: pfx }),
        muteHttpExceptions: true,
      }, 'slack chat.postMessage');
      if (JSON.parse(r.getContentText()).ok) return;
    } catch (e) { Logger.log('slack DM failed: ' + e); }
  }
  try {
    MailApp.sendEmail(Session.getEffectiveUser().getEmail(),
                      '[Reship report] alert', text);
  } catch (e) { Logger.log('email fallback failed: ' + e); }
}

// ---------- utils ----------

function iso_(d) { return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd'); }
function addDays_(d, n) { var x = new Date(d); x.setDate(x.getDate() + n); return x; }
function mondayOf_(d) { var x = new Date(d); x.setDate(x.getDate() - ((x.getDay() + 6) % 7)); return x; }
function daysBetween_(a, b) { return Math.round((b - a) / 86400000); }
function pct_(x) { return (x * 100).toFixed(2) + '%'; }
function lastShipTag_(tags) {
  var ships = tags.filter(function (t) { return t.indexOf('_SHIP_') === 0; }).sort();
  return ships.length ? ships[ships.length - 1] : '';
}
function issueOf_(tags) {
  for (var i = 0; i < tags.length; i++) {
    if (tags[i].indexOf('Reship - ') === 0) return tags[i].replace('Reship - ', '');
  }
  return 'unspecified';
}
function countBy_(arr) {
  var c = {};
  arr.forEach(function (x) { c[x] = (c[x] || 0) + 1; });
  return c;
}
function vals_(recs, field, blank) {
  return Object.keys(recs).map(function (k) { return recs[k][field] || blank || recs[k][field]; })
    .filter(function (v) { return v != null && v !== ''; });
}
function pivot_(counter, label) {
  var blk = [[label, 'Count']];
  Object.keys(counter).sort(function (a, b) {
    if (a === '(blank)') return 1;
    if (b === '(blank)') return -1;
    return a < b ? -1 : 1;
  }).forEach(function (k) { blk.push([k, counter[k]]); });
  blk.push(['Grand Total', Object.keys(counter).reduce(function (s, k) { return s + counter[k]; }, 0)]);
  blk.push([]);
  return blk;
}

// ================= HEADLESS PORT ADDITIONS (2026-07-13) =================
// Plan: .claude/plans/2026-07-13-reship-headless-port.md. Ports Product Mix,
// Triage (JS Slack parser), Daily, box-type enrichment, hide gate. Membership
// = original_cohort in the last WEEKS_BACK+1 ship weeks (full window, drops
// _seed/CUTOVER — Kurt 2026-07-13, the "23" decision).

var SLACK_CHANNEL = 'C095UVCKCBB';   // #reship-and-order-requests
var DAILY_SHEET_ID = '1VHzlyvFabVYUGpR71tgJfYDglI85KnCQOCYJFyZvGsI';
var HISTORY_DAYS = 92;

function cohortTags_(mondays) {
  var t = {};
  mondays.forEach(function (m) { t['_SHIP_' + iso_(m)] = true; });
  return t;
}

// ---- box-type enrichment (ORIGINAL order's box type; -TRAY SKUs) ----
function boxTypeOf_(skus) {
  var up = skus.map(function (s) { return (s || '').toUpperCase(); });
  if (up.some(function (s) { return s.indexOf('LCUST-TRAY') >= 0; })) return 'Large Tray';
  if (up.some(function (s) { return s.indexOf('MCUST-TRAY') >= 0; })) return 'Medium Tray';
  return 'Regular Box';
}
function enrichBoxTypes_(state, mondays) {
  var tags = cohortTags_(mondays), need = [];
  Object.keys(state).forEach(function (k) {
    var r = state[k];
    if (r.original && tags[r.original_cohort] && !r.original_boxtype) {
      var nm = String(r.original).replace(/^#/, '');
      if (need.indexOf(nm) < 0) need.push(nm);
    }
  });
  var map = {};
  for (var i = 0; i < need.length; i += 20) {
    var batch = need.slice(i, i + 20);
    var q = batch.map(function (n) { return 'name:' + n; }).join(' OR ');
    var d = shopifyGql_('query($q:String!){ orders(first:20, query:$q){ edges{node{ name lineItems(first:50){edges{node{ sku }}} }}}}', { q: q });
    d.orders.edges.forEach(function (e) {
      map[e.node.name.replace(/^#/, '')] = boxTypeOf_(e.node.lineItems.edges.map(function (le) { return le.node.sku; }));
    });
  }
  Object.keys(state).forEach(function (k) {
    var nm = String(state[k].original || '').replace(/^#/, '');
    if (nm && map[nm]) state[k].original_boxtype = map[nm];
  });
}

// ---- Parcel Panel lookup (carrier + transit_days) — centralized, Kurt 2026-07-27.
// One GET /tracking/order per order (fetchAll, 50/batch). transit_days = delivery -
// pickup (calendar days), set ONLY when delivered with both dates known.
function ppDateStr_(s) { var m = String(s || '').match(/^(\d{4})-(\d{2})-(\d{2})/); return m ? m[0] : null; }
function ppDayDiff_(a, b) {
  return Math.round((new Date(b + 'T00:00:00Z') - new Date(a + 'T00:00:00Z')) / 86400000);
}
// 🔴 THE QUOTA THIS WAS METERED AGAINST DOES NOT EXIST (directive P12 in
// EXCEPTIONS_ALERT_RULES.md — read it before touching any ParcelPanel call path here).
// "2,500 calls/week, account-wide" is Kurt's average weekly ORDER count (10,000/month plan / 4)
// misread as an API request budget. ParcelPanel's plan quota counts ORDERS TRACKED — "1 order = 1
// quota", "order lookups do not consume quota". The only real limit is 120 REQUESTS PER MINUTE per
// API key, reported in `x-ratelimit-limit` on every response.
// 🔴 SO THIS HELPER NEVER STARVED THE EXCEPTIONS SWEEP. Its measured ~4,141 calls/week is 0.35% of
// what the account serves. What starved the sweep was `excBudgetTake_` subtracting from a phantom
// balance, and that whole layer is deleted.
// 🔴 WHAT WAS REAL, AND WHAT REPLACES IT:
//   - The waste was real. Re-buying a DELIVERED box's carrier and transit_days every hour is waste
//     at any price — and it also costs wall-clock inside a 6-minute ceiling that IS scarce. The
//     `_pp_cache` below therefore STAYS, on its own merits, not as budget scaffolding.
//   - The BURST was the actual bug. This call site fired fetchAll(slice(i, i + 50)) with no pause —
//     42% of a minute's allowance in one instant — and Exceptions.gs had documented that exact
//     failure ("batches of 50 with no pause and no retry: 780 of 900 fetches failed") long before
//     this was written. It now paces at PP_TARGET_PER_MIN and retries 429s instead of dropping them.
//   - The counting survives as a RATE metric, never a balance: `excPpRecordCalls_`, daily, and
//     nothing anywhere caps or skips based on it.
// ---------------------------------------------------------------- PP answer cache (F1, P11)
// 🔴 THIS EXISTS BECAUSE THE REPORT WAS RE-BUYING FACTS THAT CANNOT CHANGE, AND IT STARVED THE
// EXCEPTIONS SWEEP TO ZERO. Measured 2026-08-20: ppLookup_ was called 3x per hourly build_ over
// sets it re-asked from scratch every run — 11 + 388 + ~75 = ~474 calls/run = ~79,600/week
// against a 2,500/week ACCOUNT-WIDE ParcelPanel plan (31.9x). It drained a zeroed ledger in 5.3
// hours, so `excBudgetTake_`'s account leg (`EXC_PP_ACCOUNT_QUOTA - total`) sat at 0 for ~163 of
// every 168 hours and the sweep's whole allowance got spent on MONDAY — the one day the day gate
// forbids it to post on. Full derivation: EXCEPTIONS_ALERT_RULES.md directive P11.
//
// 🔴 THE CORRECTNESS BAR: Dan's columns must show the SAME values. Two rules make that true and
// one makes it *nearly* true — the exception is stated, not hidden:
//   1. TERMINAL FACT — `transit_days` is set ONLY when a box is DELIVERED with both dates known
//      (see the caller below), and a delivered box's pickup->delivery span and carrier are
//      IMMUTABLE. Re-asking can only return the identical answer, so we never ask again. Exact.
//   2. GIVE UP ON A DEAD RECORD — an order whose incoming cohort is older than
//      PP_CACHE_GIVEUP_DAYS and that STILL has no transit is never going to get one. Measured:
//      169 of the 198 recurring orders are from _SHIP_2026-06-29 / _SHIP_2026-07-06, 45-52 days
//      old, re-bought 24x/day forever. Same class as the P9 quarantine, on the report side.
//      🔴 It only applies to an order we have ALREADY fetched at least once, so the carrier we
//      show today is captured into the cache before the order retires — nothing blanks.
//   3. ONCE PER CALENDAR DAY — everything else. 🔴 THIS ONE CAN DIFFER: a box that transitions to
//      DELIVERED between today's first ask and midnight now shows its `transit_days` (and the
//      >2d "Delayed supersedes Warm" reclassification derived from it) up to ~23h later than
//      before. It converges to the IDENTICAL final value within a day and nothing is lost — the
//      same "a floor delays, it cannot lose" argument the exceptions rules make for their floors.
//      To halve that window, make PP_CACHE_DAY_KEY_ resolve to date+AM/PM (cost ~2x, still tiny).
//
// The cache lives on a hidden tab, matching this project's doctrine (`_state`, `_exc_state`,
// `_triage_decisions`) rather than Script Properties, which cap at 9KB per value.
var PP_CACHE_TAB = '_pp_cache';
var PP_CACHE_COLS = ['order', 'carrier', 'transit', 'asked', 'terminal'];
var PP_CACHE_GIVEUP_DAYS = 21;   // == the report's own window (WEEKS_BACK 2 -> 3 cohorts)
// 🔴 PACING (directive P12). 10 requests per 6.0s cycle == 100/min, 83% of ParcelPanel's measured
// 120/min ceiling. The cycle is measured from DISPATCH: sleeping a flat interval AFTER a fetch that
// itself took 3s under-runs the target by the fetch's own duration, silently.
var PP_BATCH = 10;                       // requests per fetchAll == max in flight
var PP_TARGET_PER_MIN = 100;             // the rest of the 120 is margin for the other consumers
var PP_CYCLE_MS = Math.round(60000 * PP_BATCH / PP_TARGET_PER_MIN);   // 6000
// Below our own ~20 steady-state trough, and >= one batch so we never dispatch what we cannot
// afford. Frequent braking means ANOTHER consumer is on this key — signal, not noise. See P12.
var PP_BRAKE_REMAINING = 12;
var PP_RETRY_MS = [2000, 4000, 8000, 16000, 32000];   // P13: a 429 is retried, never dropped

/** Case-insensitive response-header read. Local copy on purpose: Code.gs must not depend on
 *  Exceptions.gs for its own pacing — that file has been deleted from this project once
 *  (2026-08-14), and a MISSING limiter looks exactly like a working one until you are throttled. */
function ppHeader_(resp, name) {
  try {
    var h = (resp.getAllHeaders && resp.getAllHeaders()) || (resp.getHeaders && resp.getHeaders()) || {};
    var want = String(name).toLowerCase();
    for (var k in h) {
      if (String(k).toLowerCase() === want) {
        var v = h[k];
        return String(v && v.length && typeof v !== 'string' ? v[0] : v);
      }
    }
  } catch (e) {}
  return '';
}

function ppCacheDayKey_() {
  return Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd');
}

function ppCacheLoad_() {
  var sh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName(PP_CACHE_TAB);
  var c = {};
  if (!sh || sh.getLastRow() < 2) return c;
  sh.getRange(2, 1, sh.getLastRow() - 1, PP_CACHE_COLS.length).getValues().forEach(function (r) {
    if (!r[0]) return;
    c[String(r[0])] = { carrier: String(r[1] || ''),
                        transit: (r[2] === '' || r[2] == null) ? null : Number(r[2]),
                        asked: String(r[3] || ''),
                        terminal: String(r[4]) === '1' };
  });
  return c;
}

function ppCacheSave_(c) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  var sh = ss.getSheetByName(PP_CACHE_TAB) || ss.insertSheet(PP_CACHE_TAB);
  sh.hideSheet();
  var rows = [PP_CACHE_COLS];
  Object.keys(c).sort().forEach(function (k) {
    var r = c[k];
    rows.push([k, r.carrier || '', r.transit == null ? '' : r.transit,
               r.asked || '', r.terminal ? '1' : '']);
  });
  sh.clear();
  sh.getRange(1, 1, rows.length, PP_CACHE_COLS.length).setValues(rows);
}

/** Days between an `_SHIP_yyyy-MM-dd` (or bare yyyy-MM-dd) cohort tag and today. null = unknown. */
function ppCohortAgeDays_(tag) {
  var m = String(tag || '').match(/(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return null;
  var then = new Date(m[0] + 'T12:00:00Z').getTime();
  if (!isFinite(then)) return null;
  return Math.floor((new Date().getTime() - then) / 86400000);
}

/**
 * `orderNums` — order numbers to resolve. `cohortOf` — OPTIONAL {order: shipWeekTag}; without it
 * rule 2 (give-up) cannot apply and the helper behaves as terminal-memo + once-per-day only.
 */
function ppLookup_(orderNums, cohortOf) {
  var out = {}, key = PropertiesService.getScriptProperties().getProperty('PARCELPANEL_API_KEY');
  if (!orderNums || !orderNums.length) return out;
  var uniq = orderNums.filter(function (n, i) { return n && orderNums.indexOf(n) === i; });

  // 🔴 SERVE EVERY REQUESTED ORDER FROM THE CACHE FIRST, not just the ones fetched this run.
  // Building `out` only from fresh responses would blank the carrier/transit of every cached
  // order — the exact regression this change must not cause.
  var cache = ppCacheLoad_(), today = ppCacheDayKey_(), dirty = false;
  uniq.forEach(function (n) {
    var c = cache[n];
    if (c && (c.carrier || c.transit != null)) out[n] = { carrier: c.carrier, transit: c.transit };
  });
  if (!key) return out;

  var ask = uniq.filter(function (n) {
    var c = cache[n];
    if (!c) return true;                       // never asked — always ask, whatever its age
    if (c.terminal) return false;              // rule 1/2: settled, and settled facts do not move
    return c.asked !== today;                  // rule 3: at most once per calendar day
  });
  var skipped = uniq.length - ask.length;
  if (!ask.length) {
    Logger.log('ppLookup_: ' + uniq.length + ' asked, 0 fetched (all cached: ' + skipped +
               ') — 0 ParcelPanel calls placed');
    return out;
  }
  var charged = 0;
  var throttled = 0, brakes = 0, remainingMin = null;

  // 🔴 P13: `consume` returns the orders ParcelPanel REFUSED. They are re-asked, never dropped —
  // dropping one silently blanks that order's carrier / transit_days in Dan's column for the run.
  function consume(nums) {
    var retry = [];
    var resp = UrlFetchApp.fetchAll(nums.map(function (n) {
      return { url: 'https://open.parcelwill.com/api/v2/tracking/order?order_number=' + encodeURIComponent(n),
               headers: { 'x-parcelpanel-api-key': key }, muteHttpExceptions: true };
    }));
    resp.forEach(function (r, k) {
      var onum = nums[k];
      var code = r.getResponseCode();
      var rem = parseInt(ppHeader_(r, 'x-ratelimit-remaining'), 10);
      if (isFinite(rem)) remainingMin = (remainingMin == null) ? rem : Math.min(remainingMin, rem);
      // 🔴 REFUSED IS NOT AN ANSWER (P13). Do not count it, and above all DO NOT STAMP `asked` —
      // stamping a 429 would suppress the re-ask for the rest of the day under rule 3 and turn
      // backpressure into a permanently missing value.
      if (code === 429 || code === 503) { retry.push(onum); return; }
      charged++;                                     // served, whatever it answered
      // 🔴 STAMP THE ASK EVEN ON A NON-200. A 404 is a served call and an answer ("no such
      // order"); not stamping it would re-ask the same dead record every run and rebuild the
      // exact drain this cache exists to remove.
      var cur = cache[onum] || { carrier: '', transit: null, asked: '', terminal: false };
      cur.asked = today; dirty = true;
      cache[onum] = cur;
      if (code !== 200) return;
      try {
        var o = JSON.parse(r.getContentText());
        var ships = ((o.order || {}).shipments) || ((o.data || {}).shipments) || o.shipments || [];
        if (!ships.length) return;
        var s = ships[0], c = s.carrier, cname = (c && c.name) || (c && c.code) || c || '';
        var rec = { carrier: cname ? normCarrier_(cname) : '', transit: null };
        var status = String(s.delivery_status || s.status || '').toUpperCase();
        var pk = ppDateStr_(s.pickup_date), dl = ppDateStr_(s.delivery_date);
        if (pk && dl && status.indexOf('DELIVER') >= 0) {
          var td = ppDayDiff_(pk, dl); if (td >= 0) rec.transit = td;
        }
        out[onum] = rec;
        cur.carrier = rec.carrier || cur.carrier;
        if (rec.transit != null) cur.transit = rec.transit;
      } catch (e) {}
    });
    return retry;
  }

  for (var i = 0; i < ask.length; i += PP_BATCH) {
    var slice = ask.slice(i, i + PP_BATCH);
    var t0 = new Date().getTime();
    var retry = consume(slice);
    // 🔴 P13 — retry the refused with backoff. A 429 costs TIME, never a value in Dan's column.
    for (var a = 0; retry.length && a < PP_RETRY_MS.length; a++) {
      var wait = PP_RETRY_MS[a];
      Utilities.sleep(Math.round(wait + Math.random() * wait * 0.25));      // jitter
      retry = consume(retry);
    }
    // Ladder exhausted: leave them UNSTAMPED so the next hourly run asks again. Never a hole.
    if (retry.length) throttled += retry.length;
    // --- pace: brake on the live header if the bucket is nearly gone, else hold the cycle ---
    if (remainingMin != null && remainingMin < PP_BRAKE_REMAINING) {
      var ms = 60000 - (new Date().getTime() % 60000) + 500;
      Logger.log('  PP brake: x-ratelimit-remaining below ' + PP_BRAKE_REMAINING + ' — parking ' +
                 Math.round(ms / 100) / 10 + 's to the next minute boundary. Frequent braking ' +
                 'means ANOTHER CONSUMER is on this API key; that is signal, not noise.');
      Utilities.sleep(ms);
      brakes++;
      remainingMin = null;
    } else if (i + PP_BATCH < ask.length) {
      var rest = PP_CYCLE_MS - (new Date().getTime() - t0);
      if (rest > 0) Utilities.sleep(rest);
    }
  }
  // Settle terminality AFTER the fetch, so an order always contributes at least one real answer
  // (and therefore its carrier) to the cache before it can be retired.
  var wentTerminal = 0;
  ask.forEach(function (n) {
    var cur = cache[n];
    if (!cur || cur.terminal) return;
    // 🔴 P13 — an order still refused after every retry produced NO answer this run, so it must not
    // be retired. `asked` is only stamped on a served response, so an unstamped-today entry is
    // exactly the throttled set; retiring it here would let a 429 silently make a box terminal.
    if (cur.asked !== today) return;
    if (cur.carrier && cur.transit != null) { cur.terminal = true; wentTerminal++; return; }  // rule 1
    var age = ppCohortAgeDays_(cohortOf && cohortOf[n]);                                      // rule 2
    if (age != null && age > PP_CACHE_GIVEUP_DAYS) { cur.terminal = true; wentTerminal++; }
  });
  if (dirty) { try { ppCacheSave_(cache); } catch (eC) { Logger.log('⚠️ PP cache save failed: ' + eC); } }
  // Record the call RATE. Guarded: the counter lives in Exceptions.gs, and a missing/renamed
  // helper must degrade to an unreported call, never take the reship refresh down.
  // 🔴 This is DESCRIPTIVE ONLY (directive P3 as rewritten by P12) — nothing caps, paces or skips
  // based on it, so an under-count here is harmless. It exists because knowing this consumer's
  // call rate is exactly what made its 31.9x waste findable.
  try {
    if (typeof excPpRecordCalls_ === 'function') excPpRecordCalls_(charged, 'rpt');
    else Logger.log('⚠️ PP call count NOT recorded — excPpRecordCalls_ missing (Exceptions.gs); ' +
                    'the PP_CALLS_TODAY rate metric is under-counting by ' + charged +
                    ' call(s). Harmless: it gates nothing.');
  } catch (eB) { Logger.log('⚠️ PP call count failed: ' + eB); }
  Logger.log('ppLookup_: ' + uniq.length + ' asked, ' + ask.length + ' fetched (' + skipped +
             ' cached), ' + charged + ' served at ' + PP_TARGET_PER_MIN + '/min, ' + throttled +
             ' still throttled after ' + PP_RETRY_MS.length + ' retries (unstamped — the next run ' +
             'asks again), ' + brakes + ' brake(s), min x-ratelimit-remaining ' +
             (remainingMin == null ? 'n/a' : remainingMin) + '/120; ' +
             wentTerminal + ' went terminal');
  return out;
}
// "late supersedes warm": a box >2 transit days arrived warm BECAUSE it was delayed —
// the delay is root cause. Applied post-classification (the Slack text can't see transit).
function isWarmShort_(iss) { return /arrived\s+warm/i.test(String(iss)); }        // Raw Data label
function isWarmCanon_(iss) { return String(iss).indexOf('Arrived Warm') >= 0; }    // Triage canonical
function enrichTransitOverride_(state, mondays) {
  var tags = cohortTags_(mondays), need = [], cohortOf = {};
  Object.keys(state).forEach(function (k) {
    var r = state[k];
    if (r.original && tags[r.original_cohort] && r.transit_days == null) {
      var nm = String(r.original).replace(/^#/, '');
      if (nm && need.indexOf(nm) < 0) { need.push(nm); cohortOf[nm] = r.original_cohort; }
    }
  });
  var pp = ppLookup_(need, cohortOf);
  Object.keys(state).forEach(function (k) {
    var r = state[k], nm = String(r.original || '').replace(/^#/, '');
    if (nm && pp[nm] && pp[nm].transit != null) r.transit_days = pp[nm].transit;
    // sweepAndEnrich_ re-derives r.issue (warm) every run, so re-apply the flip here
    if (r.transit_days != null && r.transit_days > 2 && isWarmShort_(r.issue)) r.issue = 'Delayed in Transit';
  });
}

// ---- Slack parser (JS port of ingest.slack_reship.parse; parity-tested) ----
var ISSUE_RULES_ = [
  [/melted\s+ice|ice\s*pack\s*melt/, 'Shipping::Damaged in transit::Arrived Warm (melted)', 'shipping'],
  [/(ice|gel)\s*pack\s*(leak|broke|exploded|leaked)/, 'Shipping::Damaged in transit::Broken/Leaking Ice Pack', 'shipping'],
  [/arriv\w*\s+warm|arrive\s+warm|arrived\s+warm|\bwarm\b/, 'Shipping::Damaged in transit::Arrived Warm', 'shipping'],
  [/lost\s+in\s+transit|\blost\b/, 'Shipping::Lost in Transit/Misdelivered::Lost', 'shipping'],
  [/misdeliver\w*|mis-deliver\w*/, 'Shipping::Lost in Transit/Misdelivered::Misdelivered', 'shipping'],
  [/cannot\s+be?\s+deliver\w*|undeliverable|can'?t\s+deliver/, 'Shipping::Cannot be delivered', 'shipping'],
  [/delay\w*\s+in\s+transit|delay\s+in\s+transit|\bdelay\w*\b/, 'Shipping::Delayed in transit', 'shipping'],
  [/damage\w*\s+box|box\s+damage\w*|damaged\s+in\s+transit/, 'Shipping::Damaged in transit::Box damaged', 'shipping'],
  [/missing\s+\d*\s*item|missing\s+\w+\s+item/, 'Order::Missing item', 'fulfillment'],
  [/wrong\s+order|wrong\s+item/, 'Order::Wrong item', 'fulfillment']
];
// "can't/cannot/don't/without/avoid delay" and "delay it further" are expedite
// REQUESTS about a not-yet-failed order, NOT a transit-delay failure — Kurt 2026-07-27
// (#165420 shipped fine but the post "we can't delay it further" tripped the delay rule).
var NEG_DELAY_ = /can'?t\s+(?:\w+\s+){0,3}delay|can\s?not\s+(?:\w+\s+){0,3}delay|do\s?n'?t\s+(?:\w+\s+){0,3}delay|do\s+not\s+(?:\w+\s+){0,3}delay|wo\s?n'?t\s+(?:\w+\s+){0,3}delay|will\s+not\s+(?:\w+\s+){0,3}delay|without\s+delay|no\s+(?:further\s+)?delay|avoid\s+(?:\w+\s+){0,2}delay|delay\w*\s+(?:it|this|them|the\s+\w+)\s+further|further\s+delay/;
function classifyReship_(text) {
  var t = String(text).toLowerCase();
  for (var i = 0; i < ISSUE_RULES_.length; i++) {
    if (ISSUE_RULES_[i][0].test(t)) {
      if (ISSUE_RULES_[i][1] === 'Shipping::Delayed in transit' && NEG_DELAY_.test(t)) continue;  // expedite request, not a failure
      return [ISSUE_RULES_[i][1], ISSUE_RULES_[i][2]];
    }
  }
  return [null, null];
}
function parseReshipMsg_(text, createdIso) {
  var c = classifyReship_(text);
  if (!c[0]) return null;
  var onum = (String(text).match(/orders\/\d+\|#?(\d{5,6})/) || [])[1]
          || (String(text).match(/#\s*(\d{5,6})\b/) || [])[1] || null;
  var gid = (String(text).match(/gorgias\.com\/app\/(?:views\/\d+\/|ticket\/)(\d+)/) || [])[1] || null;
  return { order_number: onum ? parseInt(onum, 10) : null, issue: c[0], team: c[1],
           gorgias_id: gid ? parseInt(gid, 10) : null, created_ts: createdIso };
}
function fetchSlackReship_(oldestEpoch) {
  var token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (!token) return [];
  var out = [], cursor = '', guard = 0;
  do {
    var url = 'https://slack.com/api/conversations.history?channel=' + SLACK_CHANNEL +
              '&oldest=' + oldestEpoch + '&limit=200' + (cursor ? '&cursor=' + encodeURIComponent(cursor) : '');
    var r = netFetch_(url, { headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true },
                      'slack conversations.history');
    var d = JSON.parse(r.getContentText());
    if (!d.ok) { Logger.log('slack history: ' + d.error); break; }
    (d.messages || []).forEach(function (m) {
      var iso = Utilities.formatDate(new Date(parseFloat(m.ts) * 1000), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm:ss');
      var rec = parseReshipMsg_(m.text || '', iso);
      if (rec) out.push(rec);
    });
    cursor = (d.response_metadata || {}).next_cursor || '';
  } while (cursor && ++guard < 50);
  return out;
}

// ---- Slack requested-date fill (gaps Gorgias missed; original order match) ----
function fillRequestedFromSlack_(state, sinceDate) {
  var need = {};
  Object.keys(state).forEach(function (k) {
    if (!state[k].requested && state[k].original) need[k] = String(state[k].original).replace(/^#/, '');
  });
  if (!Object.keys(need).length) return;
  var epoch = Math.floor(new Date(sinceDate).getTime() / 1000);
  var recs = fetchSlackReship_(epoch);
  var byOrder = {};
  recs.forEach(function (r) {
    if (r.order_number && r.created_ts) {
      var o = String(r.order_number), d = r.created_ts.slice(0, 10);
      if (!byOrder[o] || d < byOrder[o]) byOrder[o] = d;
    }
  });
  Object.keys(need).forEach(function (k) {
    if (byOrder[need[k]]) { state[k].requested = byOrder[need[k]]; state[k].ticket = 'slack'; }
  });
}

// ---- Product Mix (COUNTIFS over pivot Raw Data; reconciles with Counts) ----
function writeProductMix_(mondays, denoms, stamp) {
  var rows = [
    ['REFRESHED ' + stamp,
     'sizes = live Shopify; Reship = COUNTIFS over Raw Data; Unresolved = COUNTIFS over Triage (open, no reship entered); blank box-type = Regular. Triage rows resolved as no action / cs error are NOT counted here (see Triage col J tally)'],
    ['Cohort', 'Cohort size',
     // "Unresolved Issue" not "Unresolved" (Kurt 2026-09-03): the bare word read as a reship state on
     // the Reship tab. DISPLAY ONLY - formulas address these columns by letter (F/K/P), never by header.
     'Regular Box', 'Regular Box Reship', 'Regular Box Reship %', 'Regular Box Unresolved Issue', 'Regular Box Unresolved Issue %',
     'Medium Tray', 'Medium Tray Reship', 'Medium Tray Reship %', 'Medium Tray Unresolved Issue', 'Medium Tray Unresolved Issue %',
     'Large Tray', 'Large Tray Reship', 'Large Tray Reship %', 'Large Tray Unresolved Issue', 'Large Tray Unresolved Issue %',
     'Potential Reship', 'Potential %',
     'Actual Reship', 'Actual %']];  // Potential = reships + open Unresolved; Actual = reships only (D+I+N); % over cohort size
  var RD = "'Raw Data'";  // E=incoming, I=box type
  var TR = "'Triage'";    // E=ship week, F=box type
  // WALK-FORWARD cohort list (Kurt 2026-07-20): every ship-week present in the Raw
  // Data ledger, UNION the current window — accumulates, never drops old weeks.
  var cohortSet = {};
  mondays.forEach(function (m) { cohortSet['_SHIP_' + iso_(m)] = true; });
  try {
    var rdsh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Raw Data');
    if (rdsh && rdsh.getLastRow() >= 2) {
      rdsh.getRange('E2:E' + rdsh.getLastRow()).getValues().forEach(function (r) {
        if (String(r[0]).indexOf('_SHIP_') === 0) cohortSet[r[0]] = true;
      });
    }
  } catch (e) {}
  Object.keys(cohortSet).sort().forEach(function (tag, i) {
    var base = "tag:'" + tag + "' -status:cancelled -tag:'Reship'";
    var total = ordersCount_(base), med = ordersCount_(base + ' sku:AHB-MCUST-TRAY*'),
        lge = ordersCount_(base + ' sku:AHB-LCUST-TRAY*'), r = i + 3;
    // reship (Raw Data) counts — Medium=I, Large=N; Regular = all minus those two
    var medC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ',' + RD + '!$I:$I,"Medium Tray")';
    var lgeC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ',' + RD + '!$I:$I,"Large Tray")';
    var regC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ')-I' + r + '-N' + r;
    // unresolved (Triage) counts by box type
    var regU = '=COUNTIFS(' + TR + '!$E:$E,$A' + r + ',' + TR + '!$F:$F,"Regular Box")';
    var medU = '=COUNTIFS(' + TR + '!$E:$E,$A' + r + ',' + TR + '!$F:$F,"Medium Tray")';
    var lgeU = '=COUNTIFS(' + TR + '!$E:$E,$A' + r + ',' + TR + '!$F:$F,"Large Tray")';
    var pct = function (numCol, denCol) {
      return '=IF(' + denCol + r + '>0,TEXT(' + numCol + r + '/' + denCol + r + ',"0.00%"),"n/a")';
    };
    var potl = '=D' + r + '+I' + r + '+N' + r + '+F' + r + '+K' + r + '+P' + r;  // all reship + all unresolved
    var actl = '=N' + r + '+I' + r + '+D' + r;  // reships only (no unresolved)
    rows.push([tag, total,
      total - med - lge, regC, pct('D', 'C'), regU, pct('F', 'C'),
      med, medC, pct('I', 'H'), medU, pct('K', 'H'),
      lge, lgeC, pct('N', 'M'), lgeU, pct('P', 'M'),
      potl, pct('R', 'B'),
      actl, pct('T', 'B')]);  // TEXT-% so it always renders, even on new cohort rows
  });
  writeTabTo_(PIVOT_SHEET_ID, 'Product Mix', rows, true);
}

// 🔴 TAB IDENTITY (Kurt 2026-08-13). Dan renamed `Product Mix (T)` → `Reship`; that renamed tab is
// the one he reads, so it is the ONLY write target. Because writeTabTo_ CREATES a tab it cannot
// find, the rename silently made the writer mint a fresh empty `Product Mix (T)` and feed that
// instead — Dan's tab went stale and nobody was told. Never hardcode the tab name below again; it
// lives here so a rename is a one-line change. The non-transposed `Product Mix` tab keeps its name.
var PM_T_TAB = 'Reship';

// Transposed view (metrics as rows, cohorts as columns). MUST run AFTER writeTriage_
// so the Unresolved/Potential COUNTIFS (which reference the Triage tab) have re-
// evaluated — otherwise (T) lags Triage by one cycle. flush() forces recalc first.
function writeProductMixT_() {
  var pm = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Product Mix');
  if (!pm || pm.getLastRow() < 3) return;
  SpreadsheetApp.flush();  // ensure Triage-dependent formulas are recomputed
  var grid = pm.getRange(2, 1, pm.getLastRow() - 1, 21).getDisplayValues();  // A2:U{last}
  var hdr = grid[0], data = grid.slice(1);
  var cohortCols = data.map(function (r) { return r[0]; });
  var tp = [['Ship Week'].concat(cohortCols)];  // top-left label (Kurt 2026-07-27)
  for (var ci = 1; ci < hdr.length; ci++) {
    tp.push([hdr[ci]].concat(data.map(function (r) { return r[ci]; })));
  }
  var sizeByCohort = {};
  data.forEach(function (r) { sizeByCohort[r[0]] = parseInt(String(r[1]).replace(/[^0-9]/g, ''), 10) || 0; });
  tp = tp.concat(productMixBreakdown_(cohortCols, sizeByCohort));
  writeTabTo_(PIVOT_SHEET_ID, PM_T_TAB, tp, false);
}

// grouped breakdown rows for the Reship tab: By Issue, By Carrier, Arrived Warm /
// Delayed by ship-to State — emitted twice, a % (÷ cohort size) section then a
// discrete-count section. Counts from Raw Data (Issue + Incoming week); State +
// Carrier enriched from the original order's Shopify province / tracking_company.
function normCarrier_(name) {
  var m = { lasership: 'LaserShip', veho: 'Veho', ontrac: 'OnTrac', fedex: 'FedEx', ups: 'UPS' };
  return m[String(name).toLowerCase()] || String(name);
}

function productMixBreakdown_(cohortCols, sizeByCohort) {
  var rd = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Raw Data');
  if (!rd || rd.getLastRow() < 2) return [];
  var vals = rd.getRange('A2:I' + rd.getLastRow()).getValues();  // A Order..I Box; D Issue, E Incoming, H Original
  var recs = [], need = {}, cohortOf = {};
  vals.forEach(function (v) {
    if (!v[4]) return;                                    // no incoming week
    var ord = String(v[7] || v[0] || '').replace(/^#/, '');   // Original, else Order
    recs.push({ order: ord, issue: String(v[3] || 'Unknown'), cohort: String(v[4]) });
    if (ord) { need[ord] = true; cohortOf[ord] = String(v[4]); }
  });
  var orders = Object.keys(need), stateOf = {}, carrierOf = {}, shopCarrierOf = {};
  // state (province) + fallback carrier (Shopify fulfillment tracking_company) in one query
  for (var i = 0; i < orders.length; i += 20) {
    var batch = orders.slice(i, i + 20);
    var d = shopifyGql_('query($q:String!){ orders(first:20, query:$q){ edges{node{ name shippingAddress{ provinceCode } fulfillments(first:10){ trackingInfo{ company } } }}}}',
                        { q: batch.map(function (n) { return 'name:' + n; }).join(' OR ') });
    d.orders.edges.forEach(function (e) {
      var nm = e.node.name.replace(/^#/, '');
      stateOf[nm] = (e.node.shippingAddress || {}).provinceCode || '??';
      var car = '';
      (e.node.fulfillments || []).forEach(function (f) {
        (f.trackingInfo || []).forEach(function (ti) { if (!car && ti.company) car = ti.company; });
      });
      if (car) shopCarrierOf[nm] = normCarrier_(car);
    });
  }
  // carrier from Parcel Panel (Kurt 2026-07-22) — centralized in ppLookup_ (also
  // returns transit; the transit->Delayed override already ran into Raw Data col D).
  var pp = ppLookup_(orders, cohortOf);
  Object.keys(pp).forEach(function (o) { if (pp[o].carrier) carrierOf[o] = pp[o].carrier; });
  var byIssue = {}, byCarrier = {}, warm = {}, delay = {};
  function bump(tbl, key, cohort) { (tbl[key] = tbl[key] || {})[cohort] = (tbl[key][cohort] || 0) + 1; }
  recs.forEach(function (x) {
    bump(byIssue, x.issue, x.cohort);
    // PP first, Shopify tracking_company fallback — there should NEVER be Unknown (Kurt 2026-07-30)
    bump(byCarrier, carrierOf[x.order] || shopCarrierOf[x.order] || 'Unknown', x.cohort);
    var st = stateOf[x.order] || '??';
    if (x.issue === 'Arrived Warm') bump(warm, st, x.cohort);
    else if (x.issue === 'Delayed in Transit') bump(delay, st, x.cohort);
  });
  function group(title, tbl, pct) {
    var out = [new Array(cohortCols.length + 1).fill('')];
    out.push(['── ' + title + (pct ? ' (%)' : '') + ' ──'].concat(new Array(cohortCols.length).fill('')));
    Object.keys(tbl).sort(function (a, b) {
      var sa = 0, sb = 0; for (var k in tbl[a]) sa += tbl[a][k]; for (var k2 in tbl[b]) sb += tbl[b][k2];
      return sb - sa;
    }).forEach(function (key) {
      out.push([key].concat(cohortCols.map(function (c) {
        var n = tbl[key][c] || 0;
        if (!pct) return n;
        var sz = (sizeByCohort || {})[c] || 0;
        return sz ? (n / sz * 100).toFixed(2) + '%' : '';
      })));
    });
    return out;
  }
  var GROUPS = [['By Issue', byIssue], ['By Carrier', byCarrier],
                ['Arrived Warm by State', warm], ['Delayed by State', delay]];
  var out = [];
  GROUPS.forEach(function (g) { out = out.concat(group(g[0], g[1], true)); });   // % (÷ cohort size) on top
  out.push(new Array(cohortCols.length + 1).fill(''));
  GROUPS.forEach(function (g) { out = out.concat(group(g[0], g[1], false)); });  // discrete below
  return out;
}

// resolve the customer's most-recent NON-reship order from a Gorgias ticket's
// Shopify side-panel (customer.integrations), pre-dating the ticket — same as
// the old ops-sheet pipeline (Kurt 2026-07-20).
function orderFromGorgias_(gid) {
  if (!gid) return '';
  var t = gorgiasGet_('/tickets/' + gid, {});
  if (!t) return '';
  var integ = (t.customer || {}).integrations || {};
  if (typeof integ !== 'object') return '';
  var tc = t.created_datetime || '', cands = [];
  Object.keys(integ).forEach(function (k) {
    var d = integ[k];
    if (!d || typeof d !== 'object') return;
    (d.orders || []).forEach(function (o) {
      if (!o || !o.name) return;
      if (String(o.tags || '').toLowerCase().indexOf('reship') >= 0) return;
      var cr = o.created_at || '';
      if (tc && cr && cr >= tc) return;
      cands.push([cr, o.name]);
    });
  });
  if (!cands.length) return '';
  cands.sort(function (a, b) { return a[0] < b[0] ? 1 : -1; });
  return cands[0][1];
}

// order # -> customer's most-recent Gorgias ticket on/before the post date
function gorgiasForOrder_(orderName, postedIso) {
  var nm = String(orderName).replace(/^#/, '');
  var d = shopifyGql_('query($q:String!){ orders(first:1, query:$q){ edges{node{ email }}}}', { q: 'name:' + nm });
  var e = d.orders.edges, email = e.length ? e[0].node.email : '';
  if (!email) return '';
  var c = gorgiasGet_('/customers', { email: email });
  if (!c || !c.data || !c.data.length) return '';
  var t = gorgiasGet_('/tickets', { customer_id: c.data[0].id, limit: 30, order_by: 'created_datetime:desc' });
  if (!t || !t.data || !t.data.length) return '';
  var p = String(postedIso || '').slice(0, 10);
  for (var i = 0; i < t.data.length; i++) {
    if ((t.data[i].created_datetime || '').slice(0, 10) <= p) return String(t.data[i].id);
  }
  return String(t.data[t.data.length - 1].id);
}

// ---- Triage DECISION vocabulary + counting contract (Kurt 2026-08-19) ----
// 🔴 THE FAILURE THIS PREVENTS: a bad CUSTOMER-SERVICE call (CS reshipped/credited when
// nothing was wrong with the shipment) counted as a shipping failure and OVERSTATED the
// reship rate. `cs error` (and `no action`) are resolutions that are NOT shipping failures
// and must never land in a reship/refund count.
// 🔴 UNRECOGNIZED TEXT IS NOT A DECISION. Before today ANY non-blank string in col H
// suppressed the row, so a typo ("no acton", "resip") silently deleted a live failure from
// the unresolved count. Unknown text now leaves the row ACTIVE and is reported to Slack.
//
// WHERE COUNTS COME FROM (the whole surface — verified 2026-08-19):
//   Product Mix `* Unresolved` (F/K/P)  = COUNTIFS over Triage!E/F   → resolved rows drop out
//   Product Mix `Potential Reship` (R)  = D+I+N + F+K+P              → inherits the drop-out
//   Product Mix `Actual Reship`   (T)   = D+I+N, COUNTIFS over Raw Data (REAL reship orders)
//                                         → col H never feeds it; a `cs error` cannot inflate it
//   Reship tab (ex-`Product Mix (T)`)   = transpose of Product Mix    → inherits both
//   Triage col J/K                      = byWeek over the ACTIVE entries → resolved rows drop out
// So: every reship/refund count is Triage-derived only through the UNRESOLVED path, and a
// recognized decision removes the row from it. `no action` + `cs error` are additionally
// tallied below so the CS-error rate is visible instead of merely invisible.
var TRIAGE_DECISION_CANON = {
  'reship': 'reship', 'reships': 'reship', 're ship': 'reship', 're-ship': 'reship',
  'reshipped': 'reship', 'reship sent': 'reship',
  'refund': 'refund', 'refunded': 'refund', 'refund issued': 'refund',
  'no action': 'no action', 'noaction': 'no action', 'no-action': 'no action',
  'none': 'no action', 'na': 'no action', 'n/a': 'no action',
  'cs error': 'cs error', 'cs': 'cs error', 'cs mistake': 'cs error', 'cs-error': 'cs error',
  'cserror': 'cs error', 'cs fault': 'cs error', 'cs issue': 'cs error',
  'customer service error': 'cs error', 'customer service mistake': 'cs error'
};
// resolutions that are NOT a shipping failure — never counted as a reship/refund anywhere
var TRIAGE_DECISION_NOT_A_FAILURE = { 'no action': true, 'cs error': true };
var TRIAGE_DECISION_ORDER = ['reship', 'refund', 'no action', 'cs error'];

// '' = blank/undecided · null = UNRECOGNIZED (must not act like a decision) · else canonical
function normalizeTriageDecision_(raw) {
  var s = String(raw == null ? '' : raw).trim().toLowerCase().replace(/[\s_]+/g, ' ');
  if (!s) return '';
  return Object.prototype.hasOwnProperty.call(TRIAGE_DECISION_CANON, s) ? TRIAGE_DECISION_CANON[s] : null;
}

// an 11-wide Triage row carrying only the right-hand J/K summary pair
function triageSummaryRow_(j, k) { return ['', '', '', '', '', '', '', '', '', j, k]; }

// 🔴 NAMED ASSERTS — throw BEFORE any write, so a shape regression never reaches Kurt's tab.
function assertTriageOut_(out, entries, decided) {
  if (!out || out.length < 2) throw new Error('assertTriageHasHeaders: Triage output has no header rows');
  if (out[1][7] !== 'Decision') throw new Error('assertTriageDecisionColumn: H2 must be "Decision", got "' + out[1][7] + '"');
  if (out[1][0] !== 'Key') throw new Error('assertTriageKeyColumn: A2 must be "Key", got "' + out[1][0] + '"');
  for (var i = 0; i < out.length; i++) {
    if (out[i].length !== 11) throw new Error('assertTriageRowWidth: row ' + i + ' width ' + out[i].length + ' != 11');
  }
  // WALK-FORWARD: a resolved key must NEVER be re-added to the active list.
  entries.forEach(function (e) {
    if (decided[e.key]) throw new Error('assertTriageWalkForward: resolved key ' + e.key + ' (' + decided[e.key] + ') was re-added');
  });
}

// ---- Triage (Slack-only feed, requested-not-entered) ----
function writeTriage_(state, oldest, stamp) {
  var originals = {};
  Object.keys(state).forEach(function (k) {
    var o = String(state[k].original || '').replace(/^#/, ''); if (o) originals[o] = true;
  });
  // preserve Decision (key->H), Gorgias id (order->G), Box Type (order->F),
  // Ship Week (order->E) so we don't re-hit Shopify/Gorgias for same rows hourly
  var prevDec = {}, gidByOrder = {}, shipByOrder = {}, boxByOrder = {}, orderByGid = {}, decided = {};
  var decMeta = {};      // key -> {ship, issue, at} so the tally can be read back per ship week
  var unknownDec = [];   // [key, rawText, source] — reported loudly, NEVER treated as resolved
  // persisted decisions (hidden _triage_decisions tab): a row with a RECOGNIZED Decision is
  // "handled" and drops off the active list — Triage regenerates from Slack each run,
  // so the decision must live off-list or the row would just reappear (Kurt 2026-07-27).
  // Schema A=key B=decision C=ship week D=issue E=decided-at (C-E added 2026-08-19; older
  // 2-column rows still load — the extra fields default to '').
  try {
    var dsh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('_triage_decisions');
    if (dsh && dsh.getLastRow() >= 1) {
      var dw = Math.max(2, Math.min(5, dsh.getMaxColumns()));
      dsh.getRange(1, 1, dsh.getLastRow(), dw).getValues().forEach(function (r) {
        if (!r[0]) return;
        var dk = String(r[0]), dn = normalizeTriageDecision_(r[1]);
        if (dn === null) { unknownDec.push([dk, String(r[1]), '_triage_decisions']); return; }
        if (!dn) return;
        decided[dk] = dn;
        decMeta[dk] = { ship: String(r[2] || ''), issue: String(r[3] || ''), at: String(r[4] || '') };
      });
    }
  } catch (e) {}
  try {
    var psh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Triage');
    if (psh && psh.getLastRow() >= 3) {
      psh.getRange('A3:H' + psh.getLastRow()).getValues().forEach(function (row) {
        if (row[0]) prevDec[String(row[0])] = row[7];
        // newly-typed Decision → suppress. Unknown text does NOT suppress (it is reported).
        if (row[0] && String(row[7] == null ? '' : row[7]).trim()) {
          var tk = String(row[0]), tn = normalizeTriageDecision_(row[7]);
          if (tn === null) unknownDec.push([tk, String(row[7]).trim(), 'Triage!H']);
          else if (tn) {
            decided[tk] = tn;
            decMeta[tk] = { ship: String(row[4] || ''), issue: String(row[2] || ''), at: stamp };
          }
        }
        var ord = String(row[3] || '').replace(/^#/, '');
        if (ord && row[4]) shipByOrder[ord] = String(row[4]);
        if (ord && row[5]) boxByOrder[ord] = String(row[5]);
        if (ord && row[6]) gidByOrder[ord] = String(row[6]);
        // once a gid-only row's order is resolved (auto OR hand-corrected), keep it —
        // orderFromGorgias_ can mis-pick (skips reship-tagged orders, so it misses a
        // two-in-a-row where the FAILED order is itself a reship). Kurt 2026-07-21.
        var g = String(row[6] || ''); if (g && ord) orderByGid[g] = ord;
      });
    }
  } catch (e) {}
  var recs = fetchSlackReship_(Math.floor(new Date(oldest).getTime() / 1000));
  var TRIAGE_SINCE = '2026-06-29 11:53';  // Kurt 2026-07-20: don't list older posts
  var lookups = 0, gLookups = 0, entries = [];
  recs.forEach(function (r) {
    if ((r.created_ts || '') < TRIAGE_SINCE) return;      // before the start cutoff
    if (r.team === 'fulfillment') return;                 // Order::* = fulfillment, not shipping
    var onum = String(r.order_number || '');
    var gid = String(r.gorgias_id || '');
    if (!onum && gid && orderByGid[gid]) { onum = orderByGid[gid]; }   // keep an already-resolved order
    if (!onum && gid && lookups < 40) { onum = orderFromGorgias_(gid).replace(/^#/, ''); lookups++; }
    if (!onum && !gid) return;             // no order + no ticket = noise, drop
    if (onum && originals[onum]) return;   // a reship already exists for THIS order
    // NOTE: a row whose order is ITSELF a reship stays if that reship hasn't been
    // re-reshipped — legit "two-in-a-row" (reship got messed up), pending a new
    // reship. `originals` excludes only once a further reship is entered.
    if (!gid && onum) {
      if (gidByOrder[onum]) gid = gidByOrder[onum];
      else if (gLookups < 15) { gid = gorgiasForOrder_(onum, r.created_ts); gLookups++; }
    }
    var key = String(gid || onum || (r.created_ts || ''));
    if (decided[key]) return;              // a Decision was entered → handled, drop from active list
    entries.push({ key: key, posted: (r.created_ts || '').slice(0, 16), issue: r.issue || '',
                   onum: onum, gid: gid, dec: prevDec[key] || '' });
  });
  // incoming ship week + box type (order's _SHIP_ tag / line-item SKUs): cache first, batch Shopify
  var need = [];
  entries.forEach(function (e) {
    if (e.onum && (!shipByOrder[e.onum] || !boxByOrder[e.onum]) && need.indexOf(e.onum) < 0) need.push(e.onum);
  });
  for (var i = 0; i < need.length; i += 20) {
    var batch = need.slice(i, i + 20);
    var d = shopifyGql_('query($q:String!){ orders(first:20, query:$q){ edges{node{ name tags lineItems(first:50){edges{node{ sku }}} }}}}',
                        { q: batch.map(function (n) { return 'name:' + n; }).join(' OR ') });
    d.orders.edges.forEach(function (ed) {
      var nm = ed.node.name.replace(/^#/, '');
      shipByOrder[nm] = lastShipTag_(ed.node.tags);
      boxByOrder[nm] = boxTypeOf_(ed.node.lineItems.edges.map(function (le) { return le.node.sku; }));
    });
  }
  // late supersedes warm: >2 transit days (Parcel Panel) reclassifies Arrived Warm -> Delayed
  // 🔴 This leg is why F1/P11 lives inside ppLookup_ rather than at a call site. The block above
  // caches Shopify AND Gorgias precisely so we "don't re-hit them for same rows hourly" — and
  // ParcelPanel, the only METERED one of the three, was the single API left re-bought every run.
  // The cache now covers all three legs because it sits in the helper, not in the callers.
  var ppT = ppLookup_(entries.map(function (e) { return e.onum; }).filter(Boolean),
                      shipByOrder);
  entries.forEach(function (e) {
    if (e.onum && ppT[e.onum] && ppT[e.onum].transit != null && ppT[e.onum].transit > 2 && isWarmCanon_(e.issue)) {
      e.issue = 'Shipping::Delayed in transit';
    }
  });
  var byWeek = {};
  entries.forEach(function (e) {
    e.ship = (e.onum && shipByOrder[e.onum]) || '';
    e.box = (e.onum && boxByOrder[e.onum]) || '';
    var w = e.ship || '(unknown)';
    byWeek[w] = (byWeek[w] || 0) + 1;
  });
  var weeks = Object.keys(byWeek).sort();
  // 🔴 LOUD: unrecognized col-H text is NOT applied — say so, or a typo silently under-counts.
  if (unknownDec.length) {
    Logger.log('TRIAGE unrecognized Decision values (rows stay ACTIVE): ' + JSON.stringify(unknownDec));
    try {
      slack_(':warning: Triage Decision column: ' + unknownDec.length + ' unrecognized value(s) — NOT applied, ' +
             'those rows STAY in the unresolved count: ' +
             unknownDec.map(function (u) { return '`' + u[0] + '`="' + u[1] + '" (' + u[2] + ')'; }).join(', ') +
             '. Accepted: reship / refund / no action / cs error.', true);
    } catch (e) {}
  }
  // decision tally (resolved rows — these are OFF the unresolved count by construction)
  var tally = {};
  TRIAGE_DECISION_ORDER.forEach(function (d) { tally[d] = 0; });
  Object.keys(decided).forEach(function (k) { if (tally[decided[k]] != null) tally[decided[k]]++; });
  var decidedTotal = 0;
  TRIAGE_DECISION_ORDER.forEach(function (d) { decidedTotal += tally[d]; });
  Logger.log('TRIAGE decisions: ' + JSON.stringify(tally) + ' (total ' + decidedTotal + '); active entries ' + entries.length);
  // main table A-H + gap I + by-ship-week summary J-K (to the right)
  var out = [
    ['REFRESHED ' + stamp, 'Slack posts w/o an entered reship — Col H is YOURS: type reship / refund / no action / cs error to REMOVE the row (persists); no action + cs error are NOT counted as a reship or refund anywhere; unknown text is ignored and reported',
     '', '', '', '', '', 'Decision', '', 'Unresolved reships by ship week', ''],
    ['Key', 'Posted', 'Issue', 'Order', 'Ship Week', 'Box Type', 'Gorgias', 'Decision', '', 'Ship Week', 'Count']];
  var n = Math.max(entries.length, weeks.length);
  for (var r2 = 0; r2 < n; r2++) {
    var m = r2 < entries.length
      ? [entries[r2].key, entries[r2].posted, entries[r2].issue, entries[r2].onum ? '#' + entries[r2].onum : '',
         entries[r2].ship, entries[r2].box, entries[r2].gid, entries[r2].dec]
      : ['', '', '', '', '', '', '', ''];
    var s = r2 < weeks.length ? [weeks[r2], byWeek[weeks[r2]]] : ['', ''];
    out.push(m.concat(['']).concat(s));
  }
  out.push(triageSummaryRow_('Total', entries.length));
  // ---- resolved-decision tally (J/K, under the unresolved summary) ----
  // Lives HERE rather than on a new tab (Kurt: no new tabs). Every row below is already
  // OFF the unresolved count; `no action` + `cs error` are additionally not shipping failures.
  out.push(triageSummaryRow_('', ''));
  out.push(triageSummaryRow_('Resolved decisions (removed from the counts above)', ''));
  TRIAGE_DECISION_ORDER.forEach(function (d) {
    out.push(triageSummaryRow_('  ' + d + (TRIAGE_DECISION_NOT_A_FAILURE[d] ? '  (NOT a shipping failure)' : ''), tally[d]));
  });
  out.push(triageSummaryRow_('  resolved total', decidedTotal));
  out.push(triageSummaryRow_('CS error rate (of resolved)',
    decidedTotal ? (tally['cs error'] / decidedTotal * 100).toFixed(1) + '%' : 'n/a'));
  assertTriageOut_(out, entries, decided);
  writeTabTo_(PIVOT_SHEET_ID, 'Triage', out, false);
  // persist the suppressed decisions so they survive the next Slack-fed rebuild
  var decRows = Object.keys(decided).map(function (k) {
    var m = decMeta[k] || {};
    return [k, decided[k], m.ship || '', m.issue || '', m.at || stamp];
  });
  if (decRows.length) {
    writeTabTo_(PIVOT_SHEET_ID, '_triage_decisions', decRows, false);
    try { SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('_triage_decisions').hideSheet(); } catch (e) {}
  }
}

// ---- Daily 92-day tab (Date / requested / created / ship week) ----
function writeDaily_(state, stamp) {
  var today = new Date(), start = addDays_(today, -HISTORY_DAYS);
  var req = {}, ent = {}, unknown = 0, startIso = iso_(start);
  Object.keys(state).forEach(function (k) {
    var r = state[k];
    if (r.requested) req[r.requested] = (req[r.requested] || 0) + 1;
    if (r.entered) ent[r.entered] = (ent[r.entered] || 0) + 1;
    if (!r.requested && (r.entered || '') >= startIso) unknown++;
  });
  var rows = [['Date', 'Count of requested', 'Count of created', 'Ship week',
    'REFRESHED ' + stamp, unknown + ' reships in window have no ticket found (excluded from requested)']];
  for (var d = new Date(start); d <= today; d = addDays_(d, 1)) {
    var s = iso_(d);
    rows.push([s, req[s] || 0, ent[s] || 0, '_SHIP_' + iso_(mondayOf_(d))]);
  }
  writeTabTo_(DAILY_SHEET_ID, 'Daily', rows, false);
}

// ---- generic write helper for the extra sheets ----
function writeTabTo_(sheetId, name, rows, userEntered) {
  var ss = SpreadsheetApp.openById(sheetId);
  var sh = ss.getSheetByName(name);
  if (!sh) {
    // 🔴 SILENT TAB CREATION IS HOW A RENAME BECOMES A GHOST (2026-08-12). Dan renamed
    // `Product Mix (T)` → `Reship`; this line then minted an empty `Product Mix (T)` and every
    // subsequent run fed the ghost while Dan's tab sat frozen. Creation still happens (a fresh
    // clone needs it) but it is now LOUD.
    sh = ss.insertSheet(name);
    slack_(':warning: Reship report CREATED a missing tab `' + name + '` — if a tab was just ' +
           'RENAMED, this is a ghost and the writer is pointed at the wrong tab.', true);
  }
  // 🔴 COLUMN CREATION = FORMAT FROM THE PREVIOUS COLUMN FIRST (Kurt 2026-08-13). On the transposed
  // tabs a new ship-week is a new COLUMN; without this it lands unformatted next to nine styled
  // ones. formatOnly, so nothing below is overwritten by the copy — values come from setValues.
  // ONLY the transposed cohort tab — there a column IS a ship week. On Raw Data / Triage / Daily a
  // column is a field and row 1 is a banner, not a header row, so neither rule applies there.
  var colsAreCohorts = (name === PM_T_TAB);
  var prevCols = sh.getLastColumn();
  var w = Math.max.apply(null, rows.map(function (r) { return r.length; }).concat([1]));
  if (colsAreCohorts && prevCols >= 2 && w > prevCols) {
    var maxRows = sh.getMaxRows();
    for (var c = prevCols + 1; c <= w; c++) {
      try {
        sh.getRange(1, c - 1, maxRows, 1).copyTo(sh.getRange(1, c, maxRows, 1), { formatOnly: true });
        sh.setColumnWidth(c, sh.getColumnWidth(c - 1));
      } catch (e) { Logger.log('format carry failed ' + name + ' col ' + c + ': ' + e); }
    }
  }
  // 🔴 D38 — ATOMIC SWAP, NEVER CLEAR-THEN-SET (Kurt 2026-08-26). clearContents() + setValues()
  // is TWO document mutations; between them this tab is momentarily EMPTY, and every cross-tab
  // COUNTIFS over it collapses to 0 while formulas over OTHER tabs keep their values. Kurt caught
  // the torn pair live: Reship read `Potential 1 / Actual 0` mid-refresh — Raw Data was mid-swap,
  // so the D+I+N reship COUNTIFS on Product Mix read 0 while the Triage-side F+K+P still read 1,
  // and the pair a human sees was written by no run at all. ONE setValues over a rectangle
  // covering BOTH the new rows and the old extent overwrites and blanks the tail in a single
  // mutation, so no reader (human, live formula, or the Reship transpose's getDisplayValues) can
  // catch the swap half-applied. Same doctrine as excSaveState_'s "write FIRST, then trim"
  // (Exceptions.gs) — here the covering write IS the trim. The format-carry above stays BEFORE
  // the write and the header assert stays AFTER it (the D13 ordering).
  var oldRows = sh.getLastRow(), oldCols = sh.getLastColumn();
  var wAll = Math.max(w, oldCols);
  var hAll = Math.max(rows.length, oldRows);
  var padded = rows.map(function (r) { return r.concat(new Array(wAll - r.length).fill('')); });
  while (padded.length < hAll) padded.push(new Array(wAll).fill(''));
  sh.getRange(1, 1, hAll, wAll).setValues(padded);
  // assert: every data column carries a row-1 header (col 1 is the label column).
  if (!colsAreCohorts) return;
  var hdr = sh.getRange(1, 1, 1, w).getValues()[0];
  var seenTag = {};
  for (var i = 1; i < w; i++) {
    var h = String(hdr[i]).trim();
    if (seenTag[h]) {
      throw new Error('PA_ASSERT_DUPLICATE_SHIP_TAG: ' + name + ' has two columns for ' + h + '.');
    }
    seenTag[h] = true;
    // rows.length, not padded.length: blank FILLER rows that only cover the old extent must not
    // arm the headerless assert on a deliberate 1-row write.
    if (String(hdr[i]).trim() === '' && rows.length > 1) {
      throw new Error('PA_ASSERT_HEADERLESS_COLUMN: ' + name + ' column ' + (i + 1) +
                      ' has no row-1 header after write.');
    }
  }
}

// =====================================================================================
// Hold tab (D33) — the `_HOLD` -> `_CSHOLD` / `_FLOWHOLD` / `_UNRESOLVED` migration backlog.
//
// 🔴 WHY THIS EXISTS: the `Hold` tab was rebuilt and hand-filled ONCE, on 2026-08-20 (column J),
// by a local one-shot. It had NO writer in this project — a grep for the hold tags over the five
// .gs files returned only the Reship tab's "Unresolved" COUNTIFS — so columns K..N (08-21..08-24)
// went blank and nobody was told. Dead-cadence class: a writer with no scheduled owner is not
// shipped. This is that owner. Constraints SSOT: RESHIP_REPORT_RULES.md D33.
//
// 🔴 WRITE-ONCE PER DATE. A cell that already holds something is NEVER overwritten — not by a
// re-run, not by a backfill. A hold snapshot is unreconstructible (Shopify order tags carry no
// application timestamp and the Orders API exposes no tag history), so a clobbered column is gone
// for good. Same rule as `Routing Match` (D23) and the never-overwrite-a-dated-output rule.
//
// 🔴 BLANK != ZERO. Snapshot rows go into TODAY's column only; 08-12..08-19 stay blank forever
// rather than carry a fabricated back-cast. The four HOLDS-OPENED rows key on order `createdAt`,
// which IS historical, so they are backfilled across every past date column — and a 0 there is a
// real observation, not a gap.
//
// 🔴 SHOPIFY TAG COUNTS ONLY — ZERO ParcelPanel calls. PP has no weekly budget to spend here and
// is in a failure state; nothing on this tab needs a tracking event.
//
// 🔴 UNFULFILLED-ONLY SEMANTICS (Kurt 2026-08-26: "we only care about open unfulfilled holds" /
// "all types. if it was shipped on hold, then it was fulfilled, so not on hold"). An order counts
// as ON HOLD only while its fulfillment status is UNFULFILLED — for EVERY hold tag (_HOLD,
// _CSHOLD, _FLOWHOLD alike). A fulfilled order still carrying a hold tag is tag noise, not a hold,
// and is excluded from every open-holds row. The fulfilled/other populations are still COMPUTED
// (INTERNAL keys below) so the partition gate can prove the sweep was complete; they are just no
// longer published as rows. Two deliberate exceptions, both documented in D33:
//   - `Orders on _UNRESOLVED` stays ALL-fulfillment: _UNRESOLVED is terminal, never an open hold,
//     and the row tracks the size of the terminal bucket, not a hold backlog.
//   - The four HOLDS-OPENED daily rows stay ALL-fulfillment: they measure hold OPENINGS by created
//     date, not open holds — and rebasing them would invalidate HOLD_ET_BACKFILL's recorded
//     from/to values and put a third basis inside already-written rows.
// 🔴 MIXED-BASIS RECORD: columns stamped on or before 2026-08-26 hold SNAPSHOT-basis values (all
// uncancelled, fulfilled included); later columns are unfulfilled-only. Write-once means the old
// columns are never rewritten — D33 records the cutover so the trend is read on two bases.
// =====================================================================================

var HOLD_TAB = 'Hold';
// 🔴 EVERY date on this tab is EASTERN — which column is today AND what calendar day an order was
// created on (Kurt 2026-08-25: "it has to be all Eastern"). There is no second basis anywhere.
// 🔴 NOT Session.getScriptTimeZone(): this project's manifest timeZone is America/Chicago, so the
// script clock is CENTRAL. It must never reach a date calculation — using it would stamp
// tomorrow's column at 23:30 CT and would date an order to the wrong day twice a night.
var HOLD_TZ = 'America/New_York';
var HOLD_TAGS = ['_HOLD', '_CSHOLD', '_FLOWHOLD', '_UNRESOLVED'];
var HOLD_ACTIVE_TAGS = ['_HOLD', '_CSHOLD', '_FLOWHOLD'];   // _UNRESOLVED is TERMINAL, not active
var HOLD_REASON_TAGS = ['_DUP_SFO_10MINS', '_DUP_SRO_1DAY', '_POBOX',
                        '_PRODUCT_ERROR',   // add-ons arrived without a box (no AHB-* parent) — Kurt 2026-08-26
                        '_ELEVATE_FOODS'];  // staff-email sweep (@elevatefoods.co orders) — Kurt 2026-08-26
var HOLD_PAGE = 250;
var HOLD_MAX_SWEEP = 6000;              // a sweep this big means the query broke — refuse, don't truncate
var HOLD_GAP_ALERT_DAYS = 2;            // columns missed before the job tells on itself
var HOLD_PROP_LAST_RUN = 'HOLD_LAST_RUN_AT';
var HOLD_PROP_GAP_ALERTED = 'HOLD_GAP_ALERTED_ON';
var HOLD_PROP_ALLOW_ALL_ZERO = 'HOLD_ALLOW_ALL_ZERO';
var HOLD_PROP_LEGACY_ALERTED = 'HOLD_LEGACY_ALERTED_ON';   // HOLD_LEGACY_REGRESSION: once per ET day

// (kind, label). Rows are resolved by LABEL against column A — NEVER by index. The label strings
// are byte-copies of the live tab (generated, not transcribed); three of them are deliberately
// INDENTED and those leading spaces are part of the key.
// 🔴 This is the OWNED set, a SUBSET of the physical tab: four rows were retired 2026-08-26 under
// the unfulfilled-only semantics (`_HOLD unfulfilled`, `_HOLD fulfilled`, `_HOLD other fulfillment
// status`, `List of Order IDs - _HOLD fulfilled`) — the fulfilled/unfulfilled split is gone,
// unfulfilled IS the number. The writer neither writes nor asserts a retired label, so the
// physical rows can be deleted from the sheet at any time after this deploys (the row map
// re-derives positions by label each run). A label listed here must stay byte-identical on the
// sheet; renaming one requires sheet + this table to move in the same moment.
// 🔴 EIGHT MORE retired 2026-08-26 (later the same day): the _HOLD migration COMPLETED — live
// non-cancelled `_HOLD` reached 0 (43 fulfilled stripped, last 2 unfulfilled externally resolved;
// new holds land only on _CSHOLD/_FLOWHOLD). Retired: `Orders on _HOLD (LEGACY…)`, `Legacy share
// of active holds` (numerator structurally 0), the whole MIGRATION BACKLOG block (`_HOLD created
// on/after 2026-08-15`, `_HOLD also carrying _UNRESOLVED`, the three `Customers with N Orders on
// _HOLD` rows) and `List of Order IDs HELD  (_HOLD, unfulfilled)`. The sweep STILL queries _HOLD
// (380 cancelled orders carry it; a regression must stay detectable) — the watchdog is now the
// HOLD_LEGACY_REGRESSION tripwire in holdRefresh_, not screen rows. Pre-deletion history for the
// retired physical rows lives only in git / the _outputs/cache PRE-FIX snapshot. D33.
var HOLD_ROWS = [
  ['SNAP', "Orders on _CSHOLD"],
  ['SNAP', "Orders on _FLOWHOLD"],
  ['SNAP', "Orders on _UNRESOLVED  (terminal, not an active hold)"],
  ['SNAP', "Total on active hold  (_HOLD + _CSHOLD + _FLOWHOLD, union)"],
  // Renamed 2026-08-26 (Kurt): was "Orders moved to _HOLD status  (proxy: created that date, hold
  // tag present now)" — wrong twice since the purge: it counts ALL hold types (the Flow/CS/legacy
  // breakdown sits beneath it), and legacy `_HOLD` is retired. HOLD_LABEL_ALIASES below accepts
  // the old sheet label transiently, so the code deploys FIRST and cell A10 is renamed after.
  ['DAILY', "Holds opened (all types, by order-created date)"],
  ['DAILY', "   By Flow  (_FLOWHOLD)"],
  ['DAILY', "   By Customer Support  (_CSHOLD)"],
  ['DAILY', "   Legacy _HOLD, origin not recorded"],
  ['SNAP', "_DUP_SFO_10MINS  (marker, sits on BOTH of the pair)"],
  ['SNAP', "_DUP_SRO_1DAY  (marker, sits on BOTH of the pair)"],
  ['SNAP', "_POBOX"],
  ['SNAP', "_FLOWHOLD with no reason tag  (should be 0)"],
  ['SNAP', "$ held on _HOLD  (unfulfilled)"],
  ['SNAP', "$ held on _CSHOLD + _FLOWHOLD  (unfulfilled)"],
  ['SNAP', "$ held on _UNRESOLVED  (unfulfilled)"],
  ['SNAP', "Active holds unfulfilled  (aging denominator)"],
  ['SNAP', "Aged 0-7 days (since order created)"],
  ['SNAP', "Aged 8-30 days"],
  ['SNAP', "Aged 31+ days"],
  ['SNAP', "Oldest unfulfilled hold (order)"],
  ['SNAP', "Oldest unfulfilled hold (days)"],
  ['SNAP', "Unfulfilled active holds carrying a _SHIP_ cohort tag"],
  ['SNAP', "Unfulfilled _UNRESOLVED carrying a _SHIP_ cohort tag"],
  ['SNAP', "List of Order IDs - held in a live cohort"],
  ['SNAP', "List of Order IDs - _UNRESOLVED in a live cohort"],
  ['SNAP', "List of Order IDs - _CSHOLD"],
  ['SNAP', "List of Order IDs - _FLOWHOLD"],
  ['SNAP', "List of Order IDs - _UNRESOLVED (unfulfilled)"],
];

// ---------- dates ----------

/** Today in ET, or an explicit yyyy-MM-dd. Anything else (an event object) means "today". */
function holdTodayIso_(dateIso) {
  if (typeof dateIso === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(dateIso)) return dateIso;
  return Utilities.formatDate(new Date(), HOLD_TZ, 'yyyy-MM-dd');
}

/**
 * A row-1 header cell -> yyyy-MM-dd. Handles both a text header and a real date cell.
 * Duck-typed rather than `instanceof Date`: a Date handed across a realm boundary fails
 * instanceof, and a header silently reading as '' would send the writer off to append a
 * duplicate column.
 */
function holdIsDate_(v) { return !!v && typeof v.getFullYear === 'function'; }
function holdIsoOf_(v) {
  if (holdIsDate_(v)) return Utilities.formatDate(v, HOLD_TZ, 'yyyy-MM-dd');
  var m = String(v === null || v === undefined ? '' : v).trim().match(/^\d{4}-\d{2}-\d{2}/);
  return m ? m[0] : '';
}

/** Days since epoch for a yyyy-MM-dd, anchored at UTC so no DST hour can shift a day count. */
function holdDayNum_(iso) {
  var p = String(iso).split('-');
  return Math.floor(Date.UTC(Number(p[0]), Number(p[1]) - 1, Number(p[2])) / 86400000);
}

// ---------- fetch ----------

/**
 * Every uncancelled order carrying `tag`, with only the fields the metrics need.
 * Nodes whose `tags` array does not actually contain the tag are DROPPED — Shopify's `tag:` search
 * is exact here (measured 2026-08-25: raw == exact on all seven tags), and the filter keeps it that
 * way if the search ever loosens.
 */
function holdSweep_(tag) {
  var q = 'tag:"' + tag + '" AND -status:cancelled';
  var out = [], cursor = null, raw = 0;
  var gq = 'query($q:String!,$c:String){orders(first:' + HOLD_PAGE + ',query:$q,after:$c){' +
           ' pageInfo{hasNextPage endCursor}' +
           ' nodes{name createdAt displayFulfillmentStatus tags customer{id}' +
           ' totalPriceSet{shopMoney{amount}}}}}';
  while (true) {
    var d = shopifyGql_(gq, { q: q, c: cursor }).orders;
    for (var i = 0; i < d.nodes.length; i++) {
      var n = d.nodes[i];
      raw++;
      var tags = n.tags || [];
      if (tags.indexOf(tag) < 0) continue;
      out.push({
        name: n.name,
        // 🔴 EASTERN calendar date, not `createdAt.slice(0,10)`. Shopify returns createdAt in UTC,
        // so slicing it dates every order placed after 20:00 ET (19:00 EST) to TOMORROW. That is
        // the basis the 08-20 one-shot used and it is what D33's ET-backfill corrects.
        created: Utilities.formatDate(new Date(n.createdAt), HOLD_TZ, 'yyyy-MM-dd'),
        ff: n.displayFulfillmentStatus,
        tags: tags,
        cust: (n.customer && n.customer.id) || null,
        amt: parseFloat(n.totalPriceSet.shopMoney.amount),
      });
    }
    if (raw > HOLD_MAX_SWEEP) {
      throw new Error('HOLD_ASSERT_SWEEP: >' + HOLD_MAX_SWEEP + ' rows for ' + q +
                      ' — refusing a partial sweep.');
    }
    if (!d.pageInfo.hasNextPage) break;
    cursor = d.pageInfo.endCursor;
  }
  if (raw !== out.length) Logger.log('  Hold NOTE ' + tag + ': sweep ' + raw + ', exact-tag ' + out.length);
  return out;
}

/**
 * The four hold tags are PAGED (their per-order fields drive nearly every row). The three
 * _FLOWHOLD reason tags feed count rows only, so they use `ordersCount` — one cheap query instead
 * of a 250-node page. Equivalence measured live 2026-08-25: ordersCount == exact-tag sweep length
 * on all seven tags (4/2/1 for the reason tags).
 */
function holdFetch_() {
  var pop = { tags: {}, counts: {} }, i;
  for (i = 0; i < HOLD_TAGS.length; i++) pop.tags[HOLD_TAGS[i]] = holdSweep_(HOLD_TAGS[i]);
  for (i = 0; i < HOLD_REASON_TAGS.length; i++) {
    pop.counts[HOLD_REASON_TAGS[i]] =
      ordersCount_('tag:"' + HOLD_REASON_TAGS[i] + '" AND -status:cancelled');
  }
  return pop;
}

// ---------- metric helpers ----------

function holdUnf_(rows) {
  return rows.filter(function (r) { return r.ff === 'UNFULFILLED'; });
}
function holdFul_(rows) {
  return rows.filter(function (r) { return r.ff === 'FULFILLED'; });
}
/** Sum in integer CENTS. Float addition of 200+ order totals is order-dependent at the 1e-10; the
 *  cell is a currency value and must not depend on which page an order arrived on. */
function holdMoney_(rows) {
  var cents = 0;
  for (var i = 0; i < rows.length; i++) cents += Math.round(rows[i].amt * 100);
  return '$' + (cents / 100).toFixed(2);
}
/** Stable sort by created date — V8's sort is stable, so same-date orders keep sweep order. */
function holdByCreated_(rows) {
  return rows.slice().sort(function (a, b) {
    return a.created < b.created ? -1 : (a.created > b.created ? 1 : 0);
  });
}
/** 🔴 "(none)", never "" — an empty cell reads as NOT MEASURED. Zero orders is a measurement. */
function holdIds_(rows) {
  if (!rows.length) return '(none)';
  return holdByCreated_(rows).map(function (r) { return r.name; }).join(', ');
}
function holdShipTags_(r) {
  return r.tags.filter(function (t) { return t.indexOf('_SHIP_') === 0; });
}
function holdCohortIds_(rows) {
  if (!rows.length) return '(none)';
  return holdByCreated_(rows).map(function (r) {
    return r.name + ' [' + holdShipTags_(r).join(',') + ']';
  }).join(', ');
}
function holdOrdNum_(name) {
  var d = String(name).replace(/[^0-9]/g, '');
  return d ? parseInt(d, 10) : 0;
}

/**
 * Every value the tab reports, from a fetched population. PURE — no sheet, no network — so the
 * port can be differentially tested against the Python reference on a frozen capture.
 */
function holdMetrics_(pop, todayIso) {
  var T = pop.tags, i, r;
  var H = T['_HOLD'], CS = T['_CSHOLD'], FH = T['_FLOWHOLD'], UN = T['_UNRESOLVED'];

  var byName = {};
  for (i = 0; i < HOLD_TAGS.length; i++) {
    T[HOLD_TAGS[i]].forEach(function (x) { byName[x.name] = x; });
  }

  // 🔴 Union in a FIXED order (_HOLD, _CSHOLD, _FLOWHOLD; first-seen wins). The Python one-shot
  // iterated a set here, so `Oldest unfulfilled hold` and the cohort ID list could differ between
  // two runs over identical data. Deterministic by construction.
  var activeRows = [], seen = {};
  for (i = 0; i < HOLD_ACTIVE_TAGS.length; i++) {
    T[HOLD_ACTIVE_TAGS[i]].forEach(function (x) {
      if (seen[x.name]) return;
      seen[x.name] = true;
      activeRows.push(byName[x.name]);
    });
  }
  var activeUnf = holdUnf_(activeRows);

  // 🔴 UNFULFILLED-ONLY (Kurt 2026-08-26): a fulfilled order is not on hold, for every tag.
  // Every open-holds row below counts these filtered populations. The full sweeps (H, CS, FH)
  // survive only for the DAILY origin rows, the INTERNAL partition check, and _UNRESOLVED.
  var Hu = holdUnf_(H), CSu = holdUnf_(CS), FHu = holdUnf_(FH);

  function age(x) { return holdDayNum_(todayIso) - holdDayNum_(x.created); }

  var shipHeld = activeUnf.filter(function (x) { return holdShipTags_(x).length > 0; });
  var flowNoReason = FHu.filter(function (x) {
    for (var j = 0; j < HOLD_REASON_TAGS.length; j++) {
      if (x.tags.indexOf(HOLD_REASON_TAGS[j]) >= 0) return false;
    }
    return true;
  });

  // _CSHOLD + _FLOWHOLD as a set of ORDERS (an order can carry both; it must not be counted twice)
  var csFh = [], csFhSeen = {};
  CS.concat(FH).forEach(function (x) {
    if (csFhSeen[x.name]) return;
    csFhSeen[x.name] = true;
    csFh.push(byName[x.name]);
  });

  var V = {};
  // Open-holds rows: UNFULFILLED ONLY (2026-08-26 semantics — see the block comment above).
  V["Orders on _CSHOLD"] = CSu.length;
  V["Orders on _FLOWHOLD"] = FHu.length;
  // _UNRESOLVED is TERMINAL, never an open hold — this row tracks the terminal bucket's size, so
  // it deliberately stays all-fulfillment (D33).
  V["Orders on _UNRESOLVED  (terminal, not an active hold)"] = UN.length;
  V["Total on active hold  (_HOLD + _CSHOLD + _FLOWHOLD, union)"] = activeUnf.length;

  // 🔴 INTERNAL keys — computed for the partition gate (holdGates_) and the HOLD_LEGACY_REGRESSION
  // tripwire, NEVER written to the sheet (the write plan iterates HOLD_ROWS, and these labels are
  // not in it). The fulfilled/other populations must stay computed: without them a partial sweep
  // that dropped every fulfilled order would be invisible. `_HOLD unfulfilled` moved from a
  // published row to an INTERNAL key 2026-08-26 when the migration completed (target 0 reached) —
  // the union above still counts _HOLD members, so a regression moves the published union too.
  V["INTERNAL _HOLD unfulfilled"] = Hu.length;
  V["INTERNAL _HOLD total (uncancelled, any fulfillment)"] = H.length;
  V["INTERNAL _HOLD fulfilled"] = holdFul_(H).length;
  V["INTERNAL _HOLD other fulfillment status"] =
    H.filter(function (x) { return x.ff !== 'UNFULFILLED' && x.ff !== 'FULFILLED'; }).length;

  V["_DUP_SFO_10MINS  (marker, sits on BOTH of the pair)"] = pop.counts['_DUP_SFO_10MINS'];
  V["_DUP_SRO_1DAY  (marker, sits on BOTH of the pair)"] = pop.counts['_DUP_SRO_1DAY'];
  V["_POBOX"] = pop.counts['_POBOX'];
  V["_FLOWHOLD with no reason tag  (should be 0)"] = flowNoReason.length;

  V["$ held on _HOLD  (unfulfilled)"] = holdMoney_(holdUnf_(H));
  V["$ held on _CSHOLD + _FLOWHOLD  (unfulfilled)"] = holdMoney_(holdUnf_(csFh));
  V["$ held on _UNRESOLVED  (unfulfilled)"] = holdMoney_(holdUnf_(UN));

  V["Active holds unfulfilled  (aging denominator)"] = activeUnf.length;
  V["Aged 0-7 days (since order created)"] = activeUnf.filter(function (x) { return age(x) <= 7; }).length;
  V["Aged 8-30 days"] = activeUnf.filter(function (x) { return age(x) >= 8 && age(x) <= 30; }).length;
  V["Aged 31+ days"] = activeUnf.filter(function (x) { return age(x) >= 31; }).length;

  var oldest = null;
  for (i = 0; i < activeUnf.length; i++) {
    r = activeUnf[i];
    if (!oldest || r.created < oldest.created ||
        (r.created === oldest.created && holdOrdNum_(r.name) < holdOrdNum_(oldest.name))) oldest = r;
  }
  V["Oldest unfulfilled hold (order)"] = oldest ? oldest.name : '';
  V["Oldest unfulfilled hold (days)"] = oldest ? age(oldest) : 0;

  var unShip = holdUnf_(UN).filter(function (x) { return holdShipTags_(x).length > 0; });
  V["Unfulfilled active holds carrying a _SHIP_ cohort tag"] = shipHeld.length;
  V["Unfulfilled _UNRESOLVED carrying a _SHIP_ cohort tag"] = unShip.length;
  V["List of Order IDs - held in a live cohort"] = holdCohortIds_(shipHeld);
  V["List of Order IDs - _UNRESOLVED in a live cohort"] = holdCohortIds_(unShip);

  V["List of Order IDs - _CSHOLD"] = holdIds_(CSu);
  V["List of Order IDs - _FLOWHOLD"] = holdIds_(FHu);
  V["List of Order IDs - _UNRESOLVED (unfulfilled)"] = holdIds_(holdUnf_(UN));

  // per-day origin rows — backfillable, because they key on createdAt, which is historical.
  // 🔴 Deliberately ALL-fulfillment (not Hu/CSu/FHu): these measure hold OPENINGS, not open
  // holds, and rebasing them would invalidate HOLD_ET_BACKFILL's recorded from/to values and
  // put a third basis inside rows that are already partly written (D33).
  var daily = {}, dates = {};
  activeRows.forEach(function (x) { dates[x.created] = true; });
  Object.keys(dates).sort().forEach(function (d) {
    var dayH = H.filter(function (x) { return x.created === d; });
    var dayCs = CS.filter(function (x) { return x.created === d; });
    var dayFh = FH.filter(function (x) { return x.created === d; });
    var legacyOnly = dayH.filter(function (x) {
      return x.tags.indexOf('_CSHOLD') < 0 && x.tags.indexOf('_FLOWHOLD') < 0;
    });
    var names = {};
    dayH.concat(dayCs).concat(dayFh).forEach(function (x) { names[x.name] = true; });
    var row = {};
    row["Holds opened (all types, by order-created date)"] = Object.keys(names).length;
    row["   By Flow  (_FLOWHOLD)"] = dayFh.length;
    row["   By Customer Support  (_CSHOLD)"] = dayCs.length;
    row["   Legacy _HOLD, origin not recorded"] = legacyOnly.length;
    daily[d] = row;
  });

  return { snapshot: V, daily: daily };
}

/**
 * 🔴 Every partition must close before a single cell is planned. A partition that does not close
 * means the sweep was partial, and a partial sweep writes a number that looks like progress.
 * Returns a list of failures; non-empty => the whole write is refused.
 */
function holdGates_(S) {
  var bad = [];
  // The _HOLD unfulfilled count is INTERNAL since 2026-08-26 (row retired, migration complete)
  // but the partition still closes against the INTERNAL sweep total. `other` is counted directly
  // (not by subtraction), so this is a real three-way partition of the sweep, not an identity.
  var h = S["INTERNAL _HOLD unfulfilled"];
  var parts = h +
              S["INTERNAL _HOLD fulfilled"] +
              S["INTERNAL _HOLD other fulfillment status"];
  var hTot = S["INTERNAL _HOLD total (uncancelled, any fulfillment)"];
  if (parts !== hTot) bad.push('_HOLD fulfilment partition ' + parts + ' != sweep total ' + hTot);

  var den = S["Active holds unfulfilled  (aging denominator)"];
  var ag = S["Aged 0-7 days (since order created)"] + S["Aged 8-30 days"] + S["Aged 31+ days"];
  if (ag !== den) bad.push('aging buckets ' + ag + ' != unfulfilled active holds ' + den);

  var act = S["Total on active hold  (_HOLD + _CSHOLD + _FLOWHOLD, union)"];
  var cs = S["Orders on _CSHOLD"], fh = S["Orders on _FLOWHOLD"];
  var tot = h + cs + fh;
  if (act > tot || act < Math.max(h, cs, fh)) {
    bad.push('active-hold union ' + act + ' is impossible against per-tag ' + tot);
  }
  // (The old `List of Order IDs HELD` vs count-row check retired with both rows 2026-08-26: with
  // neither side published it degenerated to holdIds_(Hu) vs Hu.length — an identity, not a check.)
  return bad;
}

// ---------- sheet ----------

// 🔴 TRANSIENT rename aliases: old sheet label -> the label the code now uses. Both writer and
// sheet resolve by label (HOLD_ASSERT_ROW_SHAPE kills the refresh on a mismatch), so a rename must
// be code-first: this map lets the deployed code accept the OLD cell text until the cell itself is
// rewritten (service-account one-shot, after deploy). Remove an entry once its cell is renamed.
var HOLD_LABEL_ALIASES = {
  'Orders moved to _HOLD status  (proxy: created that date, hold tag present now)':
    'Holds opened (all types, by order-created date)',
};

/** Canonical form of a column-A label: exact/trimmed alias match maps old -> new, else as-is. */
function holdCanonLabel_(lab) {
  var s = String(lab === null || lab === undefined ? '' : lab);
  if (HOLD_LABEL_ALIASES[s] !== undefined) return HOLD_LABEL_ALIASES[s];
  var t = s.trim();
  for (var k in HOLD_LABEL_ALIASES) {
    if (k.trim() === t) return HOLD_LABEL_ALIASES[k];
  }
  return s;
}

/** label -> 1-based row, resolved against column A. Throws rather than fall back to a position. */
function holdRowMap_(grid) {
  var want = {}, i;
  for (i = 0; i < HOLD_ROWS.length; i++) want[HOLD_ROWS[i][1]] = true;

  var exact = {}, trimmed = {}, dupes = [];
  for (var r = 2; r <= grid.length; r++) {
    var cell = grid[r - 1] && grid[r - 1].length ? grid[r - 1][0] : '';
    var lab = holdCanonLabel_(String(cell === null || cell === undefined ? '' : cell));
    if (!lab.replace(/\s/g, '')) continue;
    if (exact[lab] !== undefined) { if (want[lab]) dupes.push(lab); } else { exact[lab] = r; }
    var t = lab.trim();
    trimmed[t] = trimmed[t] === undefined ? r : -1;
  }
  if (dupes.length) {
    throw new Error('HOLD_ASSERT_DUP_LABEL: column A repeats ' + JSON.stringify(dupes) +
                    ' — resolution by label would be ambiguous.');
  }

  var map = {}, missing = [], loose = [];
  for (i = 0; i < HOLD_ROWS.length; i++) {
    var lbl = HOLD_ROWS[i][1];
    if (exact[lbl] !== undefined) { map[lbl] = exact[lbl]; continue; }
    var t2 = trimmed[lbl.trim()];
    if (t2 !== undefined && t2 > 0) { map[lbl] = t2; loose.push(lbl); continue; }
    missing.push(lbl);
  }
  if (missing.length) {
    throw new Error('HOLD_ASSERT_ROW_SHAPE: ' + missing.length + ' of ' + HOLD_ROWS.length +
                    ' labels are not in column A, e.g. ' + JSON.stringify(missing.slice(0, 3)) +
                    ' — refusing to write by position.');
  }
  if (loose.length) {
    Logger.log('  Hold: ' + loose.length + ' row(s) matched only after trimming: ' +
               JSON.stringify(loose));
  }
  return map;
}

/** Is the cell already exactly what we would write? Money/percent read back as NUMBERS. */
function holdSameValue_(cell, want) {
  if (cell === '' || cell === null || cell === undefined) return String(want) === '';
  var w = String(want);
  if (typeof cell === 'number') {
    if (w.charAt(0) === '$') return Math.abs(cell - parseFloat(w.slice(1).replace(/,/g, ''))) < 5e-3;
    if (w.charAt(w.length - 1) === '%') return Math.abs(cell - parseFloat(w.slice(0, -1)) / 100) < 5e-7;
    var n = parseFloat(w);
    return !isNaN(n) && Math.abs(cell - n) < 1e-9;
  }
  return String(cell).trim() === w.trim();
}

/**
 * Today's column is absent -> append ONE column at the right edge and stamp row 1.
 * 🔴 Refuses if today is EARLIER than the last header date: appending would put the date columns
 * out of order, and every consumer of this tab reads it left-to-right.
 */
function holdAppendColumn_(sh, hdr, today, dry) {
  var lastIdx = -1;
  for (var i = 1; i < hdr.length; i++) if (hdr[i]) lastIdx = i;
  if (lastIdx < 0) {
    throw new Error('HOLD_ASSERT_HEADER: row 1 of "' + HOLD_TAB + '" carries no yyyy-MM-dd column ' +
                    'at all — refusing to guess where the date columns start.');
  }
  if (today <= hdr[lastIdx]) {
    throw new Error('HOLD_ASSERT_HEADER_ORDER: ' + today + ' is absent but row 1 already runs to ' +
                    hdr[lastIdx] + '. Appending would break the left-to-right date order — fix row 1 by hand.');
  }
  var ci = lastIdx + 1;   // 0-based
  if (!dry) {
    if (ci + 1 > sh.getMaxColumns()) sh.insertColumnsAfter(sh.getMaxColumns(), ci + 1 - sh.getMaxColumns());
    var cell = sh.getRange(1, ci + 1);
    if (holdIsDate_(sh.getRange(1, lastIdx + 1).getValue())) {
      cell.setValue(new Date(today + 'T12:00:00'));   // noon: no timezone can move the calendar day
    } else {
      cell.setNumberFormat('@');                      // keep row 1 the TEXT it already is
      cell.setValue(today);
    }
  }
  return ci;
}

/**
 * The writer. Fill-blanks-only, one cell at a time, read back after the flush.
 *
 * The cheap gate comes FIRST: one Sheets read decides whether anything is missing. On 23 of the 24
 * hourly invocations of a day the answer is no and the function returns having made ZERO Shopify
 * calls. That is what makes hosting this on an existing hourly trigger free.
 */
function holdRefresh_(dateIso, dry) {
  var today = holdTodayIso_(dateIso);
  var out = { date: today, dry: !!dry, planned: 0, written: 0, filled: 0, disagree: [],
              appended: false, plan: [], skipped: false };
  var props = PropertiesService.getScriptProperties();

  var sh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName(HOLD_TAB);
  if (!sh) throw new Error('HOLD_ASSERT_TAB: no "' + HOLD_TAB + '" tab on ' + PIVOT_SHEET_ID);

  var nRows = Math.max(sh.getLastRow(), 1), nCols = Math.max(sh.getLastColumn(), 1);
  var grid = sh.getRange(1, 1, nRows, nCols).getValues();
  var rowMap = holdRowMap_(grid);
  var hdr = grid[0].map(holdIsoOf_);

  function cellAt(row1, ci) {
    var row = grid[row1 - 1] || [];
    var v = ci < row.length ? row[ci] : '';
    return (v === null || v === undefined) ? '' : v;
  }
  function blank(row1, ci) { return String(cellAt(row1, ci)).trim() === ''; }

  var col = hdr.indexOf(today);
  if (col < 0) {
    col = holdAppendColumn_(sh, hdr, today, dry);
    hdr[col] = today;
    out.appended = true;
    Logger.log('Hold: ' + (dry ? 'WOULD append' : 'appended') + ' column ' + (col + 1) +
               ' and stamp ' + today);
  }

  // freshness: tell on ourselves BEFORE writing, while the gap is still visible
  holdGapAlert_(grid, hdr, rowMap, today, col, props, dry);

  var past = [];
  for (var i = 1; i < hdr.length; i++) if (hdr[i] && hdr[i] <= today) past.push({ iso: hdr[i], ci: i });

  var needSnap = [], needDaily = [];
  HOLD_ROWS.forEach(function (kv) {
    if (kv[0] === 'SNAP') {
      if (out.appended || blank(rowMap[kv[1]], col)) needSnap.push(kv[1]);
    } else {
      past.forEach(function (d) {
        if ((out.appended && d.ci === col) || blank(rowMap[kv[1]], d.ci)) {
          needDaily.push({ label: kv[1], iso: d.iso, ci: d.ci });
        }
      });
    }
  });

  if (!needSnap.length && !needDaily.length) {
    out.skipped = true;
    Logger.log('Hold: ' + today + ' already stamped and no past origin cell is blank — ' +
               'nothing to do (0 Shopify calls).');
    return out;
  }
  Logger.log('=== Hold ' + today + ' — ' + (dry ? 'DRY RUN (writes NOTHING)' : 'WRITING') + ' — ' +
             needSnap.length + ' snapshot cell(s), ' + needDaily.length + ' origin cell(s) blank ===');

  var pop = holdFetch_();
  var total = 0, t;
  for (t in pop.tags) if (pop.tags.hasOwnProperty(t)) total += pop.tags[t].length;
  for (t in pop.counts) if (pop.counts.hasOwnProperty(t)) total += pop.counts[t];
  // 🔴 A zero is a claim. One tag at zero is a real state (_HOLD reaching zero IS the goal, and
  // _CSHOLD was legitimately 0 on 08-20). ALL SEVEN at zero simultaneously is a dead token or a
  // broken query far more often than it is the truth, and the fabricated column it would write is
  // unrecoverable. Override with Script Property HOLD_ALLOW_ALL_ZERO=1 when it is genuinely true.
  if (!total && props.getProperty(HOLD_PROP_ALLOW_ALL_ZERO) !== '1') {
    throw new Error('HOLD_ASSERT_ALL_ZERO: all seven hold/reason tags returned zero orders. ' +
                    'Refusing to stamp a column that is far more likely a broken query than an ' +
                    'empty queue. Set HOLD_ALLOW_ALL_ZERO=1 if it is real.');
  }

  // 🔴 HOLD_LEGACY_REGRESSION tripwire (2026-08-26 — the migration's watchdog rows are deleted,
  // this replaces them). Non-cancelled `_HOLD` reached 0 that day (380 cancelled orders still
  // carry the tag, which is why the sweep keeps querying it); ANY non-cancelled order on the tag
  // again means something is RE-APPLYING the legacy tag — a Flow, a stale Mechanic task, a human
  // habit — and must be said loudly. DM once per ET day (gap-alert pattern); logged every run.
  var legacy = pop.tags['_HOLD'];
  if (legacy.length > 0) {
    var legacyIds = holdByCreated_(legacy).map(function (x) { return x.name; });
    Logger.log('HOLD_LEGACY_REGRESSION: ' + legacy.length + ' non-cancelled order(s) on _HOLD: ' +
               legacyIds.slice(0, 20).join(', ') + (legacyIds.length > 20 ? ', …' : ''));
    if (!dry && props.getProperty(HOLD_PROP_LEGACY_ALERTED) !== today) {
      props.setProperty(HOLD_PROP_LEGACY_ALERTED, today);
      slack_('HOLD_LEGACY_REGRESSION: ' + legacy.length + ' non-cancelled order(s) carry the ' +
             'legacy _HOLD tag again (migration reached 0 on 2026-08-26 — something is ' +
             're-applying it): ' + legacyIds.slice(0, 20).join(', ') +
             (legacyIds.length > 20 ? ', …' : '') +
             '. New holds belong on _CSHOLD/_FLOWHOLD.', true);
    }
  }

  var m = holdMetrics_(pop, today);
  var bad = holdGates_(m.snapshot);
  if (bad.length) {
    throw new Error('HOLD_ASSERT_PARTITION: nothing written — ' + bad.join(' | '));
  }

  var plan = [];
  HOLD_ROWS.forEach(function (kv) {
    if (kv[0] !== 'SNAP') return;
    var row = rowMap[kv[1]], v = m.snapshot[kv[1]];
    if (v === undefined) throw new Error('HOLD_ASSERT_UNMAPPED: row ' + kv[1] + ' has no metric.');
    if (!out.appended && !blank(row, col)) {
      var cur = cellAt(row, col);
      out.filled++;
      if (!holdSameValue_(cur, v)) {
        out.disagree.push(kv[1] + ' @' + today + ': sheet=' + cur + ' computed=' + v);
      }
      return;   // 🔴 write-once: a filled cell is somebody's reading, not ours to correct
    }
    plan.push({ row: row, ci: col, label: kv[1], date: today, value: v });
  });
  needDaily.forEach(function (d) {
    var day = m.daily[d.iso] || {};
    // 0 here is a real observation: the sweep covered every hold-tagged order and none was
    // created that day.
    plan.push({ row: rowMap[d.label], ci: d.ci, label: d.label, date: d.iso,
                value: day[d.label] === undefined ? 0 : day[d.label] });
  });

  out.planned = plan.length;
  plan.forEach(function (p) {
    out.plan.push(p.date + '  r' + p.row + 'c' + (p.ci + 1) + '  ' + p.label + '  = ' + p.value);
  });
  out.disagree.forEach(function (d) { Logger.log('  Hold DISAGREEMENT (kept the sheet value) ' + d); });
  Logger.log('  Hold: ' + plan.length + ' cell(s) to write, ' + out.filled + ' already filled, ' +
             out.disagree.length + ' disagreement(s)');
  if (dry) {
    out.plan.forEach(function (line) { Logger.log('    WOULD WRITE ' + line); });
    Logger.log('Hold: DRY RUN — nothing was written.');
    return out;
  }

  // 🔴 D38 — coupled cells land in as few mutations as possible. Today's snapshot column is a
  // PARTITION (holdGates_ asserts it before planning); painting it one setValue at a time gives a
  // reader a column that satisfies no gate. Planned cells are grouped per column and each
  // CONTIGUOUS run lands as one setValues. Only planned (blank) cells are ever covered — the
  // write-once rule above already excluded filled cells from the plan, and batching contiguous
  // runs cannot touch a cell between two runs (a gap splits the run). Read-back below unchanged.
  var holdByCol = {};
  plan.forEach(function (p) { (holdByCol[p.ci] = holdByCol[p.ci] || []).push(p); });
  Object.keys(holdByCol).forEach(function (ciKey) {
    var cells = holdByCol[ciKey].sort(function (a, b) { return a.row - b.row; });
    var run = [];
    function holdFlushRun_() {
      if (!run.length) return;
      sh.getRange(run[0].row, Number(ciKey) + 1, run.length, 1)
        .setValues(run.map(function (p) { return [p.value]; }));
      run = [];
    }
    cells.forEach(function (p) {
      if (run.length && p.row !== run[run.length - 1].row + 1) holdFlushRun_();
      run.push(p);
    });
    holdFlushRun_();
  });
  SpreadsheetApp.flush();

  var after = sh.getRange(1, 1, Math.max(sh.getLastRow(), nRows),
                          Math.max(sh.getLastColumn(), nCols)).getValues();
  var failed = [];
  plan.forEach(function (p) {
    var got = (after[p.row - 1] || [])[p.ci];
    if (holdSameValue_(got, p.value)) out.written++;
    else failed.push(p.label + ' @' + p.date + ': wrote ' + p.value + ', read back ' + got);
  });
  if (failed.length) {
    throw new Error('HOLD_ASSERT_READBACK: ' + failed.length + ' cell(s) did not survive the ' +
                    'write — ' + failed.slice(0, 3).join(' | '));
  }
  props.setProperty(HOLD_PROP_LAST_RUN, new Date().toISOString());
  Logger.log('Hold: ' + out.written + ' cell(s) written and read back clean.');
  return out;
}

/**
 * 🔴 Freshness assert — the writer-ownership gate's second half. Silence must fail loudly: this is
 * the tab that sat five days stale with nobody told. If the newest stamped snapshot column is more
 * than HOLD_GAP_ALERT_DAYS behind today, name the missing dates in the ops DM. Once per ET day.
 */
function holdGapAlert_(grid, hdr, rowMap, today, col, props, dry) {
  var probe = null, i;
  for (i = 0; i < HOLD_ROWS.length; i++) if (HOLD_ROWS[i][0] === 'SNAP') { probe = HOLD_ROWS[i][1]; break; }
  var row = (grid[rowMap[probe] - 1] || []);
  var last = '';
  for (i = 1; i < hdr.length; i++) {
    if (!hdr[i] || hdr[i] > today || i === col) continue;
    var v = i < row.length ? row[i] : '';
    if (String(v === null || v === undefined ? '' : v).trim() !== '') last = hdr[i];
  }
  if (!last) return;
  var gap = holdDayNum_(today) - holdDayNum_(last);
  if (gap <= HOLD_GAP_ALERT_DAYS) return;
  Logger.log('  Hold GAP: newest stamped column is ' + last + ', ' + gap + ' day(s) behind ' + today);
  if (dry || props.getProperty(HOLD_PROP_GAP_ALERTED) === today) return;
  props.setProperty(HOLD_PROP_GAP_ALERTED, today);
  slack_('Hold tab is ' + gap + ' days stale — newest stamped column is ' + last + ', today is ' +
         today + '. A hold snapshot cannot be back-filled, so those columns are lost. ' +
         'Check the hourly `refresh` trigger.', false);
}

// ---------- entry points ----------

/**
 * 🔴 TRIGGER-ARG GUARD. A time-driven trigger passes an EVENT OBJECT as argument 1 — the exact bug
 * that killed paRefreshCurrentColumn_ in prod for two nights. Anything that is not a bare
 * yyyy-MM-dd string means "today"; a date is never derived from an event.
 *
 * Nothing needs to be scheduled for this: holdRefresh_ is called from build_(), which runs on the
 * project's existing hourly `refresh` trigger. These entries exist for the menu and for a manual
 * catch-up run.
 */
function holdRefreshNow(dateIso) {
  if (typeof dateIso !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(dateIso)) dateIso = '';
  return holdRefresh_(dateIso, false);
}

/** DRY — computes the whole plan and writes NOTHING. Same shape as ntPreviewCurrentColumn. */
function holdPreview(dateIso) {
  if (typeof dateIso !== 'string' || !/^\d{4}-\d{2}-\d{2}$/.test(dateIso)) dateIso = '';
  return holdRefresh_(dateIso, true);
}

function menuRefreshHold() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Refreshing the Hold tab…', 'Reship Report', -1);
  var r = holdRefreshNow();
  ss.toast(r.skipped ? 'Already stamped for ' + r.date + ' — nothing to do.'
                     : r.written + ' cell(s) written for ' + r.date + '.', 'Reship Report', 6);
}

function menuPreviewHold() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  ss.toast('Previewing the Hold tab (writes NOTHING)…', 'Reship Report', -1);
  var r = holdPreview();
  var msg = 'PREVIEW ONLY — nothing was written.\n\ncolumn: ' + r.date +
    (r.appended ? '  (would be APPENDED)' : '') +
    '\ncells that would be written: ' + r.planned +
    '\ncells already filled (left alone): ' + r.filled +
    '\n\nDISAGREEMENTS (sheet value kept):\n  ' +
    (r.disagree.length ? r.disagree.join('\n  ') : 'none') +
    '\n\nPLAN:\n  ' + (r.plan.length ? r.plan.slice(0, 80).join('\n  ') : 'nothing to write');
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('Hold tab — preview', msg, SpreadsheetApp.getUi().ButtonSet.OK); }
  catch (e) { ss.toast('Preview logged (View > Executions).', 'Reship Report', 8); }
}

// -------------------------------------------------------------------------------------
// ONE-SHOT: correct the four HOLDS-OPENED cells the 08-20 run wrote on the UTC basis (D33).
//
// 🔴 THIS IS THE ONLY THING IN THIS FILE ALLOWED TO OVERWRITE A CELL THAT ALREADY HAS A VALUE.
// It is DISARMED by default, DRY by default, and can touch nothing but the four cells named
// below. The arm is the `_hold_arm` cell (headless — see HOLD_ARM_TAB below); it gets set only
// after the diff in D33 is read and approved. Nothing auto-corrects — the
// write-once rule (D33 rule 1) is what makes this tab trustworthy and a self-healing exception
// would quietly repeal it.
//
// WHAT IT CORRECTS. The 08-20 one-shot dated orders by `createdAt.slice(0,10)`, i.e. UTC.
// Kurt's 2026-08-25 ruling is all-Eastern. Exactly ONE order in the written range crosses a day
// boundary: #174489, created 2026-08-18T03:10:32Z = 2026-08-17 23:10 EDT. It moves off 08-18 and
// onto 08-17, which moves two rows on each of those two days. Nothing else in 08-12..08-20 moves.
//
// HOW THE NUMBERS WERE DERIVED (not recomputed, not estimated). A recompute today would be WRONG:
// these rows say "created that date and carrying a hold tag NOW", so re-running them in August
// replaces an 08-20 measurement with an 08-25 one (`_HOLD` went 94 -> 46 in between). Instead the
// 08-20 population was RECONSTRUCTED: membership from the id lists the 08-20 run published (94
// _HOLD + 0 _CSHOLD + 2 _FLOWHOLD — the lists account for every published count, so who was on
// which tag is a recorded fact), and each order's IMMUTABLE `createdAt` from live Shopify. That
// reconstruction was then required to reproduce, on the UTC basis, all 36 cells the sheet already
// holds for 08-12..08-20. It reproduced 36 of 36 exactly. Only then was the ET basis applied.
// Script: scratchpad/hold_et_backfill.py.
// -------------------------------------------------------------------------------------

// 🔴 HEADLESS ARM (Kurt 2026-08-26 — headless is the north star of this report system; no step
// may require a human click). The arm moved OFF Script Property HOLD_ARM_ET_BACKFILL (settable
// only in the GAS UI) and onto a CELL the service account can write: tab `_hold_arm` on the pivot
// sheet, A1 = the literal label 'HOLD_ARM_ET_BACKFILL', B1 = '1' to arm. Deliberately NOT the
// `_state` tab — saveState_() clearContents()-wipes that whole tab on every hourly build_() run
// BEFORE the hold path executes, so an arm written there would be erased before it was ever read.
// This tab is touched by nothing else in the project.
var HOLD_ARM_TAB = '_hold_arm';
var HOLD_ARM_LABEL = 'HOLD_ARM_ET_BACKFILL';

function holdArmSheet_() {
  return SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName(HOLD_ARM_TAB);
}

/** Armed only when the tab exists, A1 carries the EXACT label, and B1 reads 1. The label check
 *  means a drifted/renamed tab fails closed (disarmed), never open. */
function holdArmIsSet_() {
  var sh = holdArmSheet_();
  if (!sh) return false;
  var v = sh.getRange(1, 1, 1, 2).getValues()[0];
  return String(v[0]).trim() === HOLD_ARM_LABEL && String(v[1]).trim() === '1';
}

// The complete, closed set. A cell not in this table is not touchable by this function.
// (Labels updated with the 2026-08-26 row rename — this one-shot is DONE and disarmed; the D33
// record's four-cell table now describes the RENAMED row. holdRowMap_ resolves canonically either
// way, and the all-or-nothing precondition refuses re-application regardless: cells already read
// `to`.)
var HOLD_ET_BACKFILL = [
  { date: '2026-08-17', label: "Holds opened (all types, by order-created date)", from: 5, to: 6 },
  { date: '2026-08-17', label: "   Legacy _HOLD, origin not recorded", from: 5, to: 6 },
  { date: '2026-08-18', label: "Holds opened (all types, by order-created date)", from: 3, to: 2 },
  { date: '2026-08-18', label: "   Legacy _HOLD, origin not recorded", from: 2, to: 1 },
];

/**
 * DRY BY DEFAULT — pass `false` explicitly to write, exactly like ntBackfillFrozen. That default is
 * also the trigger guard: a time-driven trigger passes an EVENT OBJECT as argument 1, and an event
 * object is not `=== false`, so a stray binding previews and writes nothing.
 *
 * 🔴 ALL-OR-NOTHING. If ANY cell's current value is not the recorded `from`, the whole set is
 * refused. The four cells are one fact — #174489 moving off 08-18 and onto 08-17 — and applying
 * half of it leaves 08-17 saying 6 while its Legacy row still says 5. A partially-corrected column
 * is worse than an uncorrected one, because it is no longer internally consistent.
 */
function holdFixEtBasis(dry) {
  var wet = (dry === false);
  Logger.log('=== holdFixEtBasis — ' + (wet ? 'WRITING' : 'DRY (pass false to write)') + ' ===');

  if (wet && !holdArmIsSet_()) {
    throw new Error('HOLD_ASSERT_BACKFILL_DISARMED: to overwrite a written column, write ' +
                    HOLD_ARM_LABEL + ' in A1 and 1 in B1 of tab "' + HOLD_ARM_TAB + '" on the ' +
                    'pivot sheet (service-account writable — the headless arm). This is the ' +
                    'only writer allowed past the write-once rule and it stays off until Kurt ' +
                    'reads the diff in RESHIP_REPORT_RULES D33.');
  }

  var sh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName(HOLD_TAB);
  if (!sh) throw new Error('HOLD_ASSERT_TAB: no "' + HOLD_TAB + '" tab on ' + PIVOT_SHEET_ID);
  var nRows = Math.max(sh.getLastRow(), 1), nCols = Math.max(sh.getLastColumn(), 1);
  var grid = sh.getRange(1, 1, nRows, nCols).getValues();
  var rowMap = holdRowMap_(grid);              // same label resolution, same refusals
  var hdr = grid[0].map(holdIsoOf_);

  var plan = [], refuse = [];
  HOLD_ET_BACKFILL.forEach(function (c) {
    var ci = hdr.indexOf(c.date);
    if (ci < 0) { refuse.push(c.date + ' has no column on the tab'); return; }
    var row = rowMap[c.label];
    if (!row) { refuse.push(c.date + ' ' + c.label + ': label not on the tab'); return; }
    var cur = (grid[row - 1] || [])[ci];
    cur = (cur === null || cur === undefined) ? '' : cur;
    if (holdSameValue_(cur, c.to)) { refuse.push(c.date + ' ' + c.label + ': already ' + c.to); return; }
    if (!holdSameValue_(cur, c.from)) {
      refuse.push(c.date + ' ' + c.label + ': holds ' + cur + ', expected the recorded ' + c.from);
      return;
    }
    plan.push({ row: row, ci: ci, date: c.date, label: c.label, from: c.from, to: c.to });
  });

  plan.forEach(function (p) {
    Logger.log('  ' + p.date + '  ' + p.label + '  ' + p.from + ' -> ' + p.to);
  });
  refuse.forEach(function (r) { Logger.log('  REFUSED  ' + r); });

  if (refuse.length) {
    throw new Error('HOLD_ASSERT_BACKFILL_PRECONDITION: ' + refuse.length + ' of ' +
                    HOLD_ET_BACKFILL.length + ' cells are not in the recorded pre-state — ' +
                    refuse.join(' | ') + '. Refusing the WHOLE set: these four cells are one ' +
                    'fact and a half-applied correction leaves the column self-inconsistent.');
  }
  if (!wet) {
    Logger.log('holdFixEtBasis: DRY — ' + plan.length + ' cell(s) would change. Nothing written.');
    return { dry: true, planned: plan.length, plan: plan };
  }

  plan.forEach(function (p) { sh.getRange(p.row, p.ci + 1).setValue(p.to); });
  SpreadsheetApp.flush();

  var after = sh.getRange(1, 1, Math.max(sh.getLastRow(), nRows),
                          Math.max(sh.getLastColumn(), nCols)).getValues();
  var failed = [];
  plan.forEach(function (p) {
    if (!holdSameValue_((after[p.row - 1] || [])[p.ci], p.to)) {
      failed.push(p.date + ' ' + p.label + ': wrote ' + p.to + ', read back ' +
                  (after[p.row - 1] || [])[p.ci]);
    }
  });
  if (failed.length) throw new Error('HOLD_ASSERT_READBACK: ' + failed.join(' | '));

  // 🔴 Disarm immediately. This is a one-shot; leaving it armed turns the single exception to the
  // write-once rule into a standing one. The arm is a CELL now, so the disarm clears B1.
  var armSh = holdArmSheet_();
  if (armSh) armSh.getRange(1, 2).setValue('');
  SpreadsheetApp.flush();
  Logger.log('holdFixEtBasis: ' + plan.length + ' cell(s) corrected and read back clean. ' +
             HOLD_ARM_LABEL + ' arm cell cleared.');
  return { dry: false, written: plan.length, plan: plan };
}

/**
 * Hourly bridge (headless). If the `_hold_arm` cell is set, the NEXT hourly refresh applies the
 * one-shot — no human click anywhere. Called from build_() in a non-fatal try/catch, exactly like
 * holdRefresh_. Takes no arguments, so the trigger-arg hazard cannot reach holdFixEtBasis's dry
 * flag: the explicit `false` below is written here, after the arm-cell read, never derived from
 * an event object. Every other fence (closed table, per-cell pre-state, all-or-nothing, readback,
 * self-disarm) lives in holdFixEtBasis and is unchanged.
 */
function holdEtBackfillIfArmed_() {
  if (!holdArmIsSet_()) return;
  Logger.log('holdEtBackfillIfArmed_: arm cell is set — applying the ET backfill one-shot.');
  holdFixEtBasis(false);
}
