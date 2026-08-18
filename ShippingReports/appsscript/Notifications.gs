/**
 * Notifications.gs — the `Notifications` tab of the pivot sheet, filled from KLAVIYO.
 * =====================================================================================
 * Bound project "Running Reship" (scriptId 15K0MrUss…). GLOBAL SCOPE IS SHARED with Code.gs,
 * Exceptions.gs and PivotAnalytics.gs — every symbol here is prefixed `nt` / `NT_`. A duplicate
 * name silently breaks the live report, so nothing in this file is a bare word.
 *
 * 🔴 NEVER LOG THE KEY. `ntKey_()` returns the secret; no code path prints it, and the diagnostics
 * print property NAMES only.
 *
 * WHAT EACH ROW MEANS (interpretation — confirmed with Kurt where noted):
 *   Total Shipments / Arrived   — NOT OURS. Owned elsewhere; read-only here, used as denominator.
 *   Order Placed / Shipped / Delivered
 *     Email Sent      = DISTINCT cohort profiles with a Klaviyo "Received Email" event whose
 *                       $flow is that section's flow, inside the cohort window.
 *     SMS Sent        = same, metric "Received SMS".
 *     SMS Engaged     = same, metric "Clicked SMS"  (Klaviyo exposes no reply metric; clicks are
 *                       the only engagement signal on SMS). Stated as an ASSUMPTION.
 *     Percent of Total= sent / `Total Shipments` on this same column, written as a TEXT "12.34%"
 *                       string (Product Mix precedent — new columns render without hand formatting).
 *   Time of Sending   — Kurt 2026-08-18: split of messages by the RECIPIENT'S LOCAL time at the
 *                       destination, derived from the shipping address zip.
 *     "From 8A and 8P - Destination Time"   = local hour 8..19:59  (i.e. 08:00:00–19:59:59)
 *     "From 8:01P to 7:59A - Local Time"    = everything else
 *     These two PARTITION the sends. Anything whose zip cannot be resolved to ONE timezone is
 *     counted as MISSING and REPORTED — never silently bucketed (`NT_ASSERT_TIME_PARTITION`).
 *
 * 🔴 JOIN LIMITATION (read before trusting a number). Klaviyo's flow-message events
 * ("Received Email" / "Received SMS") do NOT carry a Shopify order number — they carry a profile
 * and a $flow. So the cohort join is:
 *      profile.email ∈ {emails of the cohort's Shopify orders}  AND  event in the cohort window
 *      AND  $flow == the section's flow id.
 * That is an EMAIL-keyed join, which the house rule normally forbids. It is the only join Klaviyo
 * makes available. Consequence: a customer with TWO orders in the same window is counted ONCE
 * (distinct profiles), so these counts are "customers messaged", not "boxes messaged". The run
 * logs the duplicate-email count in the cohort so the size of that gap is always visible.
 *
 * 🔴 KLAVIYO PAGING IS PER-ENDPOINT (live burn 2026-08-18). `page[size]` is NOT a universal param:
 * /metrics/ pages by CURSOR ONLY and hard-400s on it, while /flows/ and /events/ accept it. The
 * per-path contract is declared ONCE in NT_PAGING and applied via ntPageParam_() — never inline at a
 * call site, never inside ntPaged_(). Related trap in the same family: a filter's DATETIME literals
 * are UNQUOTED (a metric_id is quoted); quoting them 400s with "Verify your datetimes are not in
 * quotes." Every 400 from ntGet_ now prints the endpoint's declared paging mode, the size actually
 * sent, the revision, and the decoded query, so the next one of this class self-diagnoses.
 *
 * BLANK ≠ ZERO. A section whose flow cannot be resolved, a cohort whose fetch did not complete,
 * and a cohort that predates a flow all write NOTHING. A 0 in this tab means Klaviyo answered 0.
 *
 * OWNERSHIP / SAFETY (mirrors PivotAnalytics D13–D23):
 *   - label-keyed, per-cell, owned rows only. Never a range write, never a row insert.
 *   - section-scoped keys: "Percent of Total" appears 5×, so a key carries its section AND its
 *     channel (the nearest preceding "Email Sent" / "SMS Sent" row).
 *   - format-from-previous → header → values → assert, in that order, on a new column.
 *   - walk-forward freeze at NT_MATURITY_DAYS; matured columns are Kurt-owned.
 *   - every assert is NAMED (NT_ASSERT_*) and throws BEFORE anything is written.
 *   - dry-run preview writes nothing: `ntPreviewCurrentColumn()`.
 *
 * SCRIPT PROPERTIES USED (values never logged):
 *   <klaviyo key>            — name auto-discovered from NT_KEY_CANDIDATES; run
 *                              `ntCheckProperties()` to see which names exist.
 *   NOTIFICATIONS_WRITE = '1'    arms writes (own switch — a brand-new job does not ride on
 *                                PIVOT_ANALYTICS_WRITE).
 *   NOTIFICATIONS_BACKFILL = '1' additionally allows filling EMPTY cells of already-frozen columns.
 *   KLAVIYO_FLOW_PLACED / _SHIPPED / _DELIVERED — optional flow-id pins. Without them the flow is
 *                                resolved by name regex and the resolution is logged every run.
 */

// ------------------------------------------------------------------ config

var NT_TAB = 'Notifications';
var NT_TZ = 'America/New_York';
var NT_MATURITY_DAYS = 10;               // same walk-forward window as PivotAnalytics (D15)
var NT_REV = '2024-10-15';               // Klaviyo API revision header — REQUIRED on every call
var NT_BASE = 'https://a.klaviyo.com/api';
/**
 * 🔴 PAGING IS PER-ENDPOINT, NOT UNIFORM (live-verified against revision 2024-10-15 on 2026-08-18).
 * A blanket `page[size]` 400s on /metrics/: {"detail":"'page_size' is not a valid field for the
 * resource 'metric'","source":{"pointer":"/data/attributes/page_size"}} — that endpoint pages by
 * CURSOR ONLY. Sending the param where it is unsupported is not "ignored", it is a hard 400, so the
 * size lives HERE per path and `null` means "send nothing". Verified live per endpoint:
 *   /metrics/  size=null  cursor-only          page[size]=100 -> 400 ; bare -> 200, links.next present
 *   /flows/    size=50    page[size] + cursor  page[size]=50  -> 200 (26 flows, no next)
 *   /events/   size=200   page[size] + cursor  page[size]=200 -> 200, links.next present
 * links.next carries every param forward, so the size is set on the FIRST url only.
 * Adding an endpoint? Verify it live before assuming it takes a size — do not copy a neighbour.
 */
var NT_PAGING = {
  '/metrics/': { size: null, mode: 'CURSOR-ONLY (page[size] is rejected 400 by this resource)' },
  '/flows/':   { size: 50,   mode: 'page[size]<=50 + cursor' },
  '/events/':  { size: 200,  mode: 'page[size]<=200 + cursor' }
};
var NT_PAGE = 200;                       // events page[size] — mirrors NT_PAGING['/events/'].size
var NT_MAX_PAGES = 120;                  // per metric, per cohort. Hitting it = INCOMPLETE = no write
var NT_TIME_BUDGET_MS = 240000;          // 4 min of fetching, leaving 2 min of the 360s ceiling
var NT_WIN_LEAD_DAYS = 9;                // order-placed mail fires up to ~a week before ship Monday
var NT_WIN_TAIL_DAYS = 12;               // delivered mail trails the ship week
var NT_DENOM_TOLERANCE = 0.02;           // cohort size vs the sheet's Total Shipments

/** Property names the Klaviyo key might live under. First one PRESENT wins. Never logged. */
var NT_KEY_CANDIDATES = ['KLAVIYO_API_KEY', 'KLAVIYO_PRIVATE_KEY', 'KLAVIYO_KEY',
                         'KLAVIYO_TOKEN', 'KLAVIYO_API_TOKEN', 'KLAVIYO_PRIVATE_API_KEY'];

/** Metric names as Klaviyo ships them. Verified against /api/metrics by `ntCheckKlaviyo()`. */
var NT_METRIC = { email: 'Received Email', sms: 'Received SMS', smsEngaged: 'Clicked SMS' };

/** Section header text on the sheet -> internal section key. */
var NT_SECTIONS = { 'Order Placed': 'placed', 'Order Shipped': 'shipped',
                    'Order Delivered': 'delivered', 'Time of Sending': 'time' };

/** Flow resolution: property pin first, then name regex. Logged every run, never guessed silently. */
var NT_FLOW_CFG = {
  placed:    { prop: 'KLAVIYO_FLOW_PLACED',    re: /order\s*(placed|confirm)/i },
  shipped:   { prop: 'KLAVIYO_FLOW_SHIPPED',   re: /ship(ped|ping|ment)/i },
  delivered: { prop: 'KLAVIYO_FLOW_DELIVERED', re: /deliver/i }
};

function ntLog_(m) { Logger.log(m); }
function ntWriteArmed_() {
  return PropertiesService.getScriptProperties().getProperty('NOTIFICATIONS_WRITE') === '1';
}
function ntBackfillArmed_() {
  return PropertiesService.getScriptProperties().getProperty('NOTIFICATIONS_BACKFILL') === '1';
}

/**
 * 🔴 The key. Discovered by NAME so this file does not have to be edited when the property is
 * named something else. Throws a NAMED assert listing the candidate names and the property names
 * that DO exist — names only, never a value.
 */
function ntKey_() {
  var props = PropertiesService.getScriptProperties();
  for (var i = 0; i < NT_KEY_CANDIDATES.length; i++) {
    var v = props.getProperty(NT_KEY_CANDIDATES[i]);
    if (v && String(v).trim()) return String(v).trim();
  }
  throw new Error('NT_ASSERT_NO_KLAVIYO_KEY: none of [' + NT_KEY_CANDIDATES.join(', ') +
                  '] is set. Property names present: [' + props.getKeys().sort().join(', ') +
                  ']. Add the key under one of the candidate names (or add its real name to ' +
                  'NT_KEY_CANDIDATES) — values are never printed by this script.');
}

// ------------------------------------------------------------------ klaviyo transport

/**
 * The paging contract for a url, looked up by the endpoint path inside it. Returns the NT_PAGING
 * entry, or a loud UNDECLARED marker — an endpoint nobody verified must SAY so in the error rather
 * than silently inherit a neighbour's contract.
 */
function ntPagingFor_(url) {
  var keys = Object.keys(NT_PAGING);
  for (var i = 0; i < keys.length; i++) {
    if (url.indexOf(NT_BASE + keys[i]) === 0) return { path: keys[i], size: NT_PAGING[keys[i]].size,
                                                       mode: NT_PAGING[keys[i]].mode };
  }
  return { path: '(unknown)', size: null,
           mode: 'UNDECLARED — this endpoint is not in NT_PAGING; verify its paging live and add it' };
}

/** '' or 'page[size]=N', per the endpoint's own contract. Callers must not hardcode a size. */
function ntPageParam_(path) {
  var e = NT_PAGING[path];
  return (e && e.size) ? 'page[size]=' + e.size : '';
}

/** One GET. Retries a 429 with the Retry-After Klaviyo sends. Never logs the Authorization header. */
function ntGet_(url) {
  for (var attempt = 0; attempt < 4; attempt++) {
    var resp = UrlFetchApp.fetch(url, {
      method: 'get',
      muteHttpExceptions: true,
      headers: { Authorization: 'Klaviyo-API-Key ' + ntKey_(), revision: NT_REV, accept: 'application/json' }
    });
    var code = resp.getResponseCode();
    if (code === 200) return JSON.parse(resp.getContentText());
    if (code === 429) {
      var wait = Number(resp.getHeaders()['Retry-After'] || resp.getHeaders()['retry-after'] || 3);
      ntLog_('  klaviyo 429 — sleeping ' + wait + 's (attempt ' + (attempt + 1) + ')');
      Utilities.sleep(Math.min(30, wait) * 1000);
      continue;
    }
    // 🔴 the URL is safe to print (no key in it); the body may name the bad filter.
    // The PAGING MODE is printed with it: the 400 this class produces ("'page_size' is not a valid
    // field for the resource 'metric'") is only diagnosable next to what we actually sent, so the
    // declared contract, the param we sent, and the query string all go in the message.
    var pg = ntPagingFor_(url);
    var qs = url.split('?')[1] || '';
    throw new Error('NT_ASSERT_KLAVIYO_HTTP: ' + code + ' on ' + url.split('?')[0] +
                    ' — ' + resp.getContentText().slice(0, 400) +
                    ' | paging contract for ' + pg.path + ': ' + pg.mode +
                    ' | sent page[size]=' + (pg.size === null ? '(omitted)' : pg.size) +
                    ' | revision ' + NT_REV +
                    ' | query: ' + decodeURIComponent(qs).slice(0, 300));
  }
  throw new Error('NT_ASSERT_KLAVIYO_THROTTLED: gave up after 4 attempts on ' + url.split('?')[0]);
}

/**
 * Paginate a Klaviyo collection by links.next. `cap` pages; returns {data, complete}.
 * 🔴 It does NOT add a page-size param — that is the CALLER's job via ntPageParam_(path), because
 * the param is illegal on some endpoints (see NT_PAGING). A previous version applied one size to
 * every collection here and hard-400'd every run on /metrics/.
 */
function ntPaged_(url, cap, deadline) {
  var out = [], next = url, pages = 0;
  while (next) {
    if (pages >= cap) return { data: out, complete: false, why: 'page cap ' + cap };
    if (new Date().getTime() > deadline) return { data: out, complete: false, why: 'time budget' };
    var j = ntGet_(next);
    out = out.concat(j.data || []);
    next = (j.links && j.links.next) || null;
    pages++;
  }
  return { data: out, complete: true, pages: pages };
}

/** name -> id for every metric in the account. */
function ntMetricIds_(deadline) {
  // cursor-only: ntPageParam_ returns '' here, and MUST — page[size] is a 400 on this resource.
  var q = ntPageParam_('/metrics/');
  var r = ntPaged_(NT_BASE + '/metrics/' + (q ? '?' + q : ''), 20, deadline);
  if (!r.complete) throw new Error('NT_ASSERT_METRICS_INCOMPLETE: ' + r.why);
  var map = {};
  r.data.forEach(function (m) { map[String(m.attributes.name)] = m.id; });
  return map;
}

/** id -> name for every flow. */
function ntFlows_(deadline) {
  // /flows/ DOES accept page[size] (<=50) — verified live; only /metrics/ rejects it.
  var q2 = ntPageParam_('/flows/');
  var r = ntPaged_(NT_BASE + '/flows/' + (q2 ? '?' + q2 : ''), 20, deadline);
  if (!r.complete) throw new Error('NT_ASSERT_FLOWS_INCOMPLETE: ' + r.why);
  var out = [];
  r.data.forEach(function (f) { out.push({ id: f.id, name: String(f.attributes.name) }); });
  return out;
}

/**
 * section -> flow id. A pinned property wins. Otherwise the NAME regex must match EXACTLY ONE
 * live flow — 0 or 2+ is unresolved, and an unresolved section writes NOTHING rather than a guess.
 */
function ntResolveFlows_(flows) {
  var props = PropertiesService.getScriptProperties(), out = {};
  Object.keys(NT_FLOW_CFG).forEach(function (sec) {
    var cfg = NT_FLOW_CFG[sec], pinned = props.getProperty(cfg.prop);
    if (pinned && String(pinned).trim()) {
      out[sec] = String(pinned).trim();
      ntLog_('  flow ' + sec + ': PINNED ' + out[sec] + ' (' + cfg.prop + ')');
      return;
    }
    var hits = flows.filter(function (f) { return cfg.re.test(f.name); });
    if (hits.length === 1) {
      out[sec] = hits[0].id;
      ntLog_('  flow ' + sec + ': ' + hits[0].name + ' (' + hits[0].id + ') by name');
    } else {
      out[sec] = null;
      ntLog_('  ⚠️ flow ' + sec + ': UNRESOLVED — ' + hits.length + ' flows match ' + cfg.re +
             (hits.length ? ' [' + hits.map(function (h) { return h.name; }).join(' | ') + ']' : '') +
             '. Pin it with script property ' + cfg.prop + '. This section writes NOTHING.');
    }
  });
  return out;
}

/**
 * Every event of one metric in [lo,hi). Returns {byFlow:{flowId:{email:{hourUtcIso:1}}}, complete}.
 * We keep, per flow, per profile-email, the FIRST send timestamp — that is the message whose
 * local hour the Time-of-Sending split is measured on.
 */
function ntEvents_(metricId, lo, hi, deadline) {
  // 🔴 DATETIME LITERALS ARE UNQUOTED IN A KLAVIYO FILTER; a metric_id is a QUOTED string. Quoting
  // the datetimes returns 400 "Invalid filter provided. Verify your datetimes are not in quotes."
  // (verified live 2026-08-18, revision 2024-10-15 — this was the next 400 after the paging one).
  var q = ntPageParam_('/events/');
  var url = NT_BASE + '/events/?filter=' + encodeURIComponent(
              'and(equals(metric_id,"' + metricId + '"),greater-or-equal(datetime,' + lo +
              '),less-than(datetime,' + hi + '))') +
            '&include=profile&fields[profile]=email' +
            '&fields[event]=datetime,event_properties' + (q ? '&' + q : '');
  var out = { byFlow: {}, complete: true, n: 0 };
  var next = url, pages = 0;
  while (next) {
    if (pages >= NT_MAX_PAGES) { out.complete = false; out.why = 'page cap'; break; }
    if (new Date().getTime() > deadline) { out.complete = false; out.why = 'time budget'; break; }
    var j = ntGet_(next);
    var emailById = {};
    (j.included || []).forEach(function (p) {
      if (p.type === 'profile') emailById[p.id] = String((p.attributes && p.attributes.email) || '').toLowerCase();
    });
    (j.data || []).forEach(function (e) {
      var props = e.attributes.event_properties || {};
      var flow = props['$flow'] || props['$flow_id'] || '';
      if (!flow) return;                                  // campaign send, not a flow — out of scope
      var pid = e.relationships && e.relationships.profile && e.relationships.profile.data &&
                e.relationships.profile.data.id;
      var em = emailById[pid] || '';
      if (!em) return;
      if (!out.byFlow[flow]) out.byFlow[flow] = {};
      var prev = out.byFlow[flow][em];
      var ts = e.attributes.datetime;
      if (!prev || ts < prev) out.byFlow[flow][em] = ts;  // first send wins
      out.n++;
    });
    next = (j.links && j.links.next) || null;
    pages++;
  }
  return out;
}

// ------------------------------------------------------------------ cohort (Shopify)

/**
 * The cohort population: order name, customer email, destination zip. Deliberately a LIGHT query —
 * PivotAnalytics' paFetchCohort_ pulls fulfillment event trees we do not need here.
 * 🔴 Same exclusions as every other cohort cut: not cancelled, not a Reship.
 */
function ntFetchCohort_(shipWeek) {
  var q = 'query($q:String!,$cursor:String){ orders(first:50, query:$q, after:$cursor){' +
          ' pageInfo{hasNextPage endCursor} edges{node{ name email' +
          ' shippingAddress{ zip provinceCode } } } } }';
  var qs = "tag:'" + shipWeek + "' -status:cancelled -tag:'Reship'";
  var out = [], cursor = null;
  while (true) {
    var conn = shopifyGql_(q, { q: qs, cursor: cursor }).orders;
    conn.edges.forEach(function (e) {
      var n = e.node;
      out.push({
        order: n.name,
        email: String(n.email || '').toLowerCase(),
        zip: String((n.shippingAddress && n.shippingAddress.zip) || '').replace(/[^0-9]/g, '')
      });
    });
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  return out;
}

// ------------------------------------------------------------------ zip -> timezone

/**
 * ZIP3 -> IANA timezone. This is the honest weak point of the Time-of-Sending split and it is
 * written failure-first:
 *   - The grain is ZIP3. Several zip3 prefixes STRADDLE a zone line (TN, KY, IN, MI-UP, ND, SD,
 *     KS, NE, TX-El Paso, ID, NV, OR). Every one of those is listed in NT_ZIP3_AMBIG and resolves
 *     to MISSING — it is NOT bucketed into whichever side is more common.
 *   - Arizona is America/Phoenix (no DST). Hawaii, Alaska, PR, Guam get their own zones.
 *   - A missing / non-5-digit / unknown zip is MISSING. PO boxes are fine (they carry a real zip).
 *   - DST is handled by Utilities.formatDate against the IANA name, not by a fixed offset.
 * Anything MISSING is reported and BREAKS the partition assert if it is not accounted for.
 */
var NT_TZ_RANGES = [
  [6, 9, 'America/Puerto_Rico'], [10, 299, 'America/New_York'], [300, 349, 'America/New_York'],
  [350, 369, 'America/Chicago'], [386, 397, 'America/Chicago'], [398, 399, 'America/New_York'],
  [400, 418, 'America/New_York'], [430, 462, 'America/New_York'], [465, 475, 'America/New_York'],
  [478, 479, 'America/New_York'], [480, 497, 'America/New_York'],
  [500, 528, 'America/Chicago'], [530, 549, 'America/Chicago'], [550, 567, 'America/Chicago'],
  [570, 573, 'America/Chicago'], [580, 585, 'America/Chicago'], [590, 599, 'America/Denver'],
  [600, 629, 'America/Chicago'], [630, 658, 'America/Chicago'], [660, 676, 'America/Chicago'],
  [680, 690, 'America/Chicago'], [700, 714, 'America/Chicago'], [716, 729, 'America/Chicago'],
  [730, 749, 'America/Chicago'], [750, 797, 'America/Chicago'],
  [800, 816, 'America/Denver'], [820, 831, 'America/Denver'],
  [832, 834, 'America/Denver'], [836, 837, 'America/Denver'], [840, 847, 'America/Denver'],
  [850, 865, 'America/Phoenix'], [870, 884, 'America/Denver'], [885, 885, 'America/Denver'],
  [889, 897, 'America/Los_Angeles'], [900, 961, 'America/Los_Angeles'],
  [967, 968, 'Pacific/Honolulu'], [969, 969, 'Pacific/Guam'],
  [970, 978, 'America/Los_Angeles'], [980, 994, 'America/Los_Angeles'],
  [995, 999, 'America/Anchorage']
];
/** zip3s that straddle a timezone line — MISSING, never guessed. */
var NT_ZIP3_AMBIG = {};
[370, 371, 372, 373, 374, 375, 376, 377, 378, 379, 380, 381, 382, 383, 384, 385,   // TN
 419, 420, 421, 422, 423, 424, 425, 426, 427,                                       // KY
 463, 464, 476, 477,                                                                 // IN
 498, 499,                                                                           // MI UP
 574, 575, 576, 577, 586, 587, 588,                                                  // SD / ND
 677, 678, 679, 691, 692, 693,                                                       // KS / NE
 798, 799,                                                                           // TX El Paso
 835, 838, 898, 979                                                                  // ID / NV / OR
].forEach(function (z) { NT_ZIP3_AMBIG[z] = 1; });

function ntZoneForZip_(zip) {
  if (!zip || zip.length < 5) return null;
  var z3 = Number(zip.slice(0, 3));
  if (NT_ZIP3_AMBIG[z3]) return null;
  for (var i = 0; i < NT_TZ_RANGES.length; i++) {
    if (z3 >= NT_TZ_RANGES[i][0] && z3 <= NT_TZ_RANGES[i][1]) return NT_TZ_RANGES[i][2];
  }
  return null;
}

/** local hour 8..19 inclusive => 'day', else 'night'; unresolvable zone => null (MISSING). */
function ntDayNight_(iso, zone) {
  if (!zone) return null;
  var h = Number(Utilities.formatDate(new Date(iso), zone, 'H'));
  return (h >= 8 && h < 20) ? 'day' : 'night';
}

// ------------------------------------------------------------------ sheet plumbing

function ntCohortAgeDays_(shipWeek) {
  var d = shipWeek.replace('_SHIP_', '');
  var ship = new Date(d + 'T12:00:00Z');
  var today = new Date(Utilities.formatDate(new Date(), NT_TZ, 'yyyy-MM-dd') + 'T12:00:00Z');
  return Math.round((today.getTime() - ship.getTime()) / 86400000);
}

function ntCurrentShipWeek_() {
  var now = new Date();
  var dow = Number(Utilities.formatDate(now, NT_TZ, 'u'));
  for (var wk = 0; wk < 3; wk++) {
    var d = new Date(now.getTime() - ((dow - 1) + wk * 7) * 86400000);
    var tag = '_SHIP_' + Utilities.formatDate(d, NT_TZ, 'yyyy-MM-dd');
    var conn = shopifyGql_('query($q:String!){ orders(first:1, query:$q){ edges{ node{ id } } } }',
                           { q: "tag:'" + tag + "' -status:cancelled" }).orders;
    if (conn.edges.length) return tag;
  }
  throw new Error('NT_ASSERT_NO_COHORT: no _SHIP_ tag with orders in the last 3 weeks');
}

function ntCopyFormatFromPrev_(sheet, col) {
  if (col < 2) return;
  var rows = Math.max(1, sheet.getMaxRows());
  sheet.getRange(1, col - 1, rows, 1).copyTo(sheet.getRange(1, col, rows, 1), { formatOnly: true });
  sheet.setColumnWidth(col, sheet.getColumnWidth(col - 1));
}

function ntAssertColumns_(sheet) {
  var lastCol = Math.max(1, sheet.getLastColumn()), lastRow = Math.max(1, sheet.getLastRow());
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) { return String(h).trim(); });
  var grid = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var seen = {}, dup = [], headerless = [];
  for (var c = 2; c <= lastCol; c++) {
    var h = headers[c - 1];
    if (h) { if (seen[h]) dup.push(h + ' (cols ' + seen[h] + ' + ' + c + ')'); seen[h] = c; continue; }
    for (var r = 1; r < lastRow; r++) {
      if (String(grid[r][c - 1]).trim() !== '') { headerless.push(c); break; }
    }
  }
  if (headerless.length) {
    throw new Error('NT_ASSERT_HEADERLESS_COLUMN: ' + NT_TAB + ' column(s) ' + headerless.join(', ') +
                    ' hold data with no row-1 header. Refusing to write.');
  }
  if (dup.length) {
    throw new Error('NT_ASSERT_DUPLICATE_SHIP_TAG: ' + NT_TAB + ' has more than one column for ' +
                    dup.join('; ') + '. Refusing to write.');
  }
}

function ntCurrentCol_(sheet, shipWeek, allowAppend) {
  var lastCol = Math.max(1, sheet.getLastColumn());
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(String);
  var idx = headers.indexOf(shipWeek);
  if (idx < 0) {
    if (allowAppend === false) return 0;
    // ORDER IS THE RULE (Kurt 2026-08-13): format-from-previous -> header -> values -> assert.
    var col = lastCol + 1;
    ntCopyFormatFromPrev_(sheet, col);
    sheet.getRange(1, col).setValue(shipWeek);
    SpreadsheetApp.flush();
    return col;
  }
  var col = idx + 1, rightmost = 1;
  for (var i = 0; i < headers.length; i++) {
    if (/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(headers[i].trim())) rightmost = i + 1;
  }
  if (col === rightmost) return col;
  throw new Error('NT_ASSERT_NOT_RIGHTMOST: refusing column ' + col + ' (' + shipWeek +
                  ') — the rightmost cohort column is ' + rightmost);
}

/**
 * The row map. Walks column A once and returns key -> rowIndex for OUR rows only.
 * Key = section || channel || label, because "Percent of Total" appears five times and a bare
 * label (or a row index) would land a delivered-SMS percent on the placed-email row.
 * Channel comes from the nearest preceding "Email Sent" / "SMS Sent" row — NOT from the label's
 * leading whitespace, which differs by one space between sections and is a hand-edit away from
 * breaking.
 */
function ntRowMap_(sheet) {
  var lastRow = Math.max(1, sheet.getLastRow());
  var labels = sheet.getRange(1, 1, lastRow, 1).getValues()
                 .map(function (r) { return String(r[0]).replace(/\s+/g, ' ').trim(); });
  var map = {}, sec = '', chan = '';
  for (var i = 0; i < labels.length; i++) {
    var lab = labels[i];
    if (!lab) continue;
    if (Object.prototype.hasOwnProperty.call(NT_SECTIONS, lab)) { sec = NT_SECTIONS[lab]; chan = ''; continue; }
    if (lab === 'Total Shipments' || lab === 'Arrived') { map['own||' + lab] = i + 1; sec = ''; continue; }
    if (!sec) continue;
    if (lab === 'Email Sent') chan = 'email';
    else if (lab === 'SMS Sent') chan = 'sms';
    else if (lab === 'SMS Engaged') chan = 'sms';
    map[sec + '||' + chan + '||' + lab] = i + 1;
  }
  return map;
}

/** The rows this file owns. Anything not here is somebody else's and is never touched. */
function ntOwnedKeys_() {
  var keys = [];
  ['placed', 'shipped', 'delivered'].forEach(function (sec) {
    keys.push(sec + '||email||Email Sent', sec + '||email||Percent of Total');
    if (sec !== 'placed') {
      keys.push(sec + '||sms||SMS Engaged', sec + '||sms||SMS Sent', sec + '||sms||Percent of Total');
    }
  });
  keys.push('time||||From 8A and 8P - Destination Time', 'time||||From 8:01P to 7:59A - Local Time');
  return keys;
}

function ntAssertShape_(map) {
  var missing = ntOwnedKeys_().filter(function (k) { return !map[k]; });
  if (!map['own||Total Shipments']) missing.push('own||Total Shipments');
  if (missing.length) {
    throw new Error('NT_ASSERT_ROW_SHAPE: the ' + NT_TAB + ' tab is missing ' + missing.length +
                    ' expected row(s): ' + missing.join(' ; ') + '. The tab layout changed — ' +
                    'refusing to write rather than guessing which row is which.');
  }
}

/** Per-cell, owned rows only, keyed. `emptyOnly` is the frozen-column backfill mode. */
function ntWriteOwned_(sheet, col, map, values, dry, emptyOnly) {
  var wrote = 0, skipped = 0;
  ntOwnedKeys_().forEach(function (k) {
    if (!Object.prototype.hasOwnProperty.call(values, k)) return;   // blank != zero: not computed
    var row = map[k], v = values[k];
    var cell = sheet.getRange(row, col);
    if (emptyOnly && String(cell.getValue()).trim() !== '') { skipped++; return; }
    if (dry) { ntLog_('  [dry] ' + NT_TAB + '!' + cell.getA1Notation() + '  ' + k + ' = ' + v); }
    else {
      cell.setValue(v === null || v === undefined ? '' : v);
      if (typeof v === 'number') cell.setNumberFormat('0');
    }
    wrote++;
  });
  if (skipped) ntLog_('  backfill: ' + skipped + ' cell(s) already had a value — left alone (Kurt-owned)');
  return wrote;
}

// ------------------------------------------------------------------ the measurement

function ntPct_(n, d) { return (d > 0) ? ((n / d) * 100).toFixed(2) + '%' : ''; }

/**
 * Everything for ONE cohort. Returns {values, notes}. Throws only on a broken invariant; a data
 * gap comes back as an ABSENT key (blank on the sheet) plus a loud note.
 */
function ntMeasure_(shipWeek, sheetTotal, deadline) {
  var out = {}, notes = [];
  var cohort = ntFetchCohort_(shipWeek);
  var emails = {}, dupes = 0, zoneByEmail = {};
  cohort.forEach(function (o) {
    if (!o.email) return;
    if (emails[o.email]) dupes++;
    emails[o.email] = 1;
    if (!zoneByEmail[o.email]) zoneByEmail[o.email] = ntZoneForZip_(o.zip);
  });
  var nEmails = Object.keys(emails).length;
  ntLog_('  cohort ' + shipWeek + ': ' + cohort.length + ' orders, ' + nEmails +
         ' distinct emails (' + dupes + ' repeat customers — those count ONCE), ' +
         cohort.filter(function (o) { return !o.email; }).length + ' orders with no email');

  // 🔴 denominator sanity: the sheet's Total Shipments is the published number. If our cohort
  // pull disagrees materially, REPORT — do not write a percent against a denominator we distrust.
  var denomOk = true;
  if (!sheetTotal) { denomOk = false; notes.push('Total Shipments is blank on this column — no percents written'); }
  else {
    var drift = Math.abs(cohort.length - sheetTotal) / sheetTotal;
    if (drift > NT_DENOM_TOLERANCE) {
      denomOk = false;
      notes.push('DENOMINATOR DISAGREES: Shopify cohort ' + cohort.length + ' vs sheet Total Shipments ' +
                 sheetTotal + ' (' + (drift * 100).toFixed(1) + '% apart) — percents NOT written');
    }
  }

  var metrics = ntMetricIds_(deadline);
  ['email', 'sms', 'smsEngaged'].forEach(function (k) {
    if (!metrics[NT_METRIC[k]]) notes.push('metric "' + NT_METRIC[k] + '" does not exist in this Klaviyo account');
  });
  var flows = ntResolveFlows_(ntFlows_(deadline));

  var ship = shipWeek.replace('_SHIP_', '');
  var lo = new Date(new Date(ship + 'T00:00:00Z').getTime() - NT_WIN_LEAD_DAYS * 86400000)
             .toISOString().replace(/\.\d+Z$/, 'Z');
  var hi = new Date(new Date(ship + 'T00:00:00Z').getTime() + NT_WIN_TAIL_DAYS * 86400000)
             .toISOString().replace(/\.\d+Z$/, 'Z');
  ntLog_('  klaviyo window ' + lo + ' .. ' + hi);

  // pull each metric ONCE for the whole window; split by flow afterwards.
  var ev = {};
  ['email', 'sms', 'smsEngaged'].forEach(function (k) {
    var id = metrics[NT_METRIC[k]];
    if (!id) { ev[k] = null; return; }
    var r = ntEvents_(id, lo, hi, deadline);
    if (!r.complete) {
      notes.push('INCOMPLETE fetch of "' + NT_METRIC[k] + '" (' + r.why + ') — nothing derived from it');
      ev[k] = null;
    } else {
      ev[k] = r;
      ntLog_('  ' + NT_METRIC[k] + ': ' + r.n + ' events, ' + Object.keys(r.byFlow).length + ' flows');
    }
  });

  var sendTimes = [];       // {iso, email} for every SEND we counted (email + sms, all sections)
  ['placed', 'shipped', 'delivered'].forEach(function (sec) {
    var flowId = flows[sec];
    if (!flowId) return;                                    // unresolved -> blank, already logged
    var chans = (sec === 'placed') ? ['email'] : ['email', 'sms'];
    chans.forEach(function (ch) {
      var src = ev[ch];
      if (!src) return;
      var hits = src.byFlow[flowId] || {};
      var n = 0;
      Object.keys(hits).forEach(function (em) {
        if (!emails[em]) return;                            // not in this cohort
        n++;
        sendTimes.push({ iso: hits[em], email: em });
      });
      var label = (ch === 'email') ? 'Email Sent' : 'SMS Sent';
      out[sec + '||' + (ch === 'email' ? 'email' : 'sms') + '||' + label] = n;
      if (denomOk) out[sec + '||' + (ch === 'email' ? 'email' : 'sms') + '||Percent of Total'] = ntPct_(n, sheetTotal);
      ntLog_('  ' + sec + ' ' + label + ' = ' + n + (denomOk ? ' (' + ntPct_(n, sheetTotal) + ')' : ' (no %)'));
    });
    // SMS Engaged — clicks, same flow, same cohort filter.
    if (sec !== 'placed' && ev.smsEngaged) {
      var eh = ev.smsEngaged.byFlow[flowId] || {}, k2 = 0;
      Object.keys(eh).forEach(function (em) { if (emails[em]) k2++; });
      out[sec + '||sms||SMS Engaged'] = k2;
      ntLog_('  ' + sec + ' SMS Engaged (Clicked SMS) = ' + k2);
    }
  });

  // ---- Time of Sending: partition of every counted send by RECIPIENT LOCAL hour ----
  if (sendTimes.length) {
    var day = 0, night = 0, miss = 0;
    sendTimes.forEach(function (s) {
      var b = ntDayNight_(s.iso, zoneByEmail[s.email]);
      if (b === 'day') day++; else if (b === 'night') night++; else miss++;
    });
    if (day + night + miss !== sendTimes.length) {
      throw new Error('NT_ASSERT_TIME_PARTITION: ' + day + '+' + night + '+' + miss +
                      ' != ' + sendTimes.length + ' sends — the split lost messages.');
    }
    out['time||||From 8A and 8P - Destination Time'] = day;
    out['time||||From 8:01P to 7:59A - Local Time'] = night;
    if (miss) {
      notes.push('TIME SPLIT REMAINDER: ' + miss + ' of ' + sendTimes.length + ' sends (' +
                 ((miss / sendTimes.length) * 100).toFixed(1) + '%) have no resolvable destination ' +
                 'timezone (missing zip, or a zip3 that straddles a zone line) — counted in NEITHER row.');
    }
    ntLog_('  time split: day ' + day + ' / night ' + night + ' / MISSING ' + miss +
           ' of ' + sendTimes.length);
  } else {
    notes.push('no sends resolved — Time of Sending left blank');
  }

  return { values: out, notes: notes };
}

// ------------------------------------------------------------------ entry points

function ntRefreshOne_(shipWeek, dry, allowAppend, emptyOnly) {
  var sheet = SpreadsheetApp.openById(EXC_HOST_SHEET_ID).getSheetByName(NT_TAB);
  if (!sheet) throw new Error('NT_ASSERT_NO_TAB: no tab named ' + NT_TAB);
  ntAssertColumns_(sheet);
  var map = ntRowMap_(sheet);
  ntAssertShape_(map);                                 // throws BEFORE anything is written
  var col = ntCurrentCol_(sheet, shipWeek, allowAppend);
  if (!col) { ntLog_('  ' + shipWeek + ': no column and append not allowed — skipped'); return null; }

  var totalRaw = sheet.getRange(map['own||Total Shipments'], col).getValue();
  var sheetTotal = Number(String(totalRaw).replace(/[^0-9.]/g, '')) || 0;

  var deadline = new Date().getTime() + NT_TIME_BUDGET_MS;
  var res = ntMeasure_(shipWeek, sheetTotal, deadline);
  res.notes.forEach(function (n) { ntLog_('  ⚠️ ' + n); });

  var wrote = ntWriteOwned_(sheet, col, map, res.values, dry, emptyOnly);
  ntAssertColumns_(sheet);
  ntLog_('  ' + shipWeek + ' col ' + col + ': ' + wrote + ' cell(s) ' + (dry ? 'previewed' : 'written'));
  return { shipWeek: shipWeek, col: col, wrote: wrote, values: res.values, notes: res.notes };
}

/**
 * Daily entry point (its OWN time trigger — see the install note at the bottom of this file).
 * 🔴 A TIME-DRIVEN TRIGGER PASSES AN EVENT OBJECT as the first argument; only a real
 * `_SHIP_YYYY-MM-DD` string is accepted (the bug that killed paRefreshCurrentColumn_ in prod).
 */
function ntRefreshCurrentColumn(shipWeek) {
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) shipWeek = '';
  var dry = !ntWriteArmed_();
  var cur = shipWeek || ntCurrentShipWeek_();
  var age = ntCohortAgeDays_(cur);
  ntLog_('=== ntRefreshCurrentColumn ' + cur + ' (age ' + age + 'd) — ' +
         (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');
  if (age >= NT_MATURITY_DAYS) {
    ntLog_('FROZEN (age ' + age + 'd >= ' + NT_MATURITY_DAYS + 'd) — Kurt-owned, not touched. ' +
           'Use ntBackfillFrozen() to fill EMPTY cells only.');
    return { frozen: true };
  }
  if (age <= 0) { ntLog_('SKIP — cohort ships today; nothing sent yet.'); return { skipped: 'ship-day' }; }
  var t0 = new Date().getTime();
  var out = ntRefreshOne_(cur, dry, true, false);
  ntLog_('total ' + ((new Date().getTime() - t0) / 1000).toFixed(1) + 's of the 360s ceiling');
  ntLog_('=== done (' + (dry ? 'DRY RUN — nothing written' : 'written') + ') ===');
  return out;
}

/** Preview: writes NOTHING regardless of the arm switch. This is the one Kurt runs first. */
function ntPreviewCurrentColumn(shipWeek) {
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) shipWeek = '';
  var cur = shipWeek || ntCurrentShipWeek_();
  ntLog_('=== ntPreviewCurrentColumn ' + cur + ' — DRY, nothing is written ===');
  return ntRefreshOne_(cur, true, true, false);
}

/**
 * One-time fill of an already-frozen column (B–E). Only EMPTY cells are touched, so a value Kurt
 * or Dan typed is never overwritten, and it is double-gated: NOTIFICATIONS_BACKFILL=1 AND
 * NOTIFICATIONS_WRITE=1. `dry` defaults to TRUE — pass false explicitly to write.
 */
function ntBackfillFrozen(shipWeek, dry) {
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) {
    throw new Error('NT_ASSERT_BACKFILL_ARG: pass an explicit _SHIP_YYYY-MM-DD');
  }
  var wet = (dry === false);
  if (wet && !(ntBackfillArmed_() && ntWriteArmed_())) {
    throw new Error('NT_ASSERT_BACKFILL_DISARMED: set NOTIFICATIONS_BACKFILL=1 and ' +
                    'NOTIFICATIONS_WRITE=1 to write into a frozen column');
  }
  ntLog_('=== ntBackfillFrozen ' + shipWeek + ' — ' + (wet ? 'WRITING empty cells only' : 'DRY') + ' ===');
  return ntRefreshOne_(shipWeek, !wet, false, true);
}

// ------------------------------------------------------------------ diagnostics (no writes)

/** Property NAMES only — never a value. Confirms which name the Klaviyo key is under. */
function ntCheckProperties() {
  var props = PropertiesService.getScriptProperties();
  var names = props.getKeys().sort();
  ntLog_('script property NAMES: ' + names.join(', '));
  var found = NT_KEY_CANDIDATES.filter(function (n) { return !!props.getProperty(n); });
  ntLog_('klaviyo key found under: ' + (found.length ? found.join(', ') : 'NONE of ' +
         NT_KEY_CANDIDATES.join(', ')));
  ntLog_('write armed: ' + ntWriteArmed_() + ' | backfill armed: ' + ntBackfillArmed_());
  return { names: names, klaviyoKeyUnder: found };
}

/** Lists the account's real metric + flow names so nothing here is a guess. No writes. */
function ntCheckKlaviyo() {
  var deadline = new Date().getTime() + 120000;
  Object.keys(NT_PAGING).forEach(function (p) {
    ntLog_('paging ' + p + ': ' + NT_PAGING[p].mode +
           ' -> sending ' + (ntPageParam_(p) || '(no size param)'));
  });
  var metrics = ntMetricIds_(deadline);
  ntLog_('METRICS (' + Object.keys(metrics).length + '): ' + Object.keys(metrics).sort().join(' | '));
  ['email', 'sms', 'smsEngaged'].forEach(function (k) {
    ntLog_('  need "' + NT_METRIC[k] + '" -> ' + (metrics[NT_METRIC[k]] ? 'OK' : '🔴 ABSENT'));
  });
  var flows = ntFlows_(deadline);
  ntLog_('FLOWS (' + flows.length + '):');
  flows.forEach(function (f) { ntLog_('  ' + f.id + '  ' + f.name); });
  ntLog_('resolution:');
  ntResolveFlows_(flows);
  return { metrics: Object.keys(metrics).length, flows: flows.length };
}

/** Prints the row map so the label→row binding can be eyeballed before any write. No writes. */
function ntCheckRows() {
  var sheet = SpreadsheetApp.openById(EXC_HOST_SHEET_ID).getSheetByName(NT_TAB);
  var map = ntRowMap_(sheet);
  Object.keys(map).sort().forEach(function (k) { ntLog_('  row ' + map[k] + '  ' + k); });
  ntAssertShape_(map);
  ntLog_('✅ NT_ASSERT_ROW_SHAPE passes — all ' + ntOwnedKeys_().length + ' owned rows found.');
  return map;
}

/**
 * 🔴 TRIGGER — the API cannot install one. Kurt installs it by hand:
 *   Extensions → Apps Script → clock icon (Triggers) → Add Trigger
 *     function: ntRefreshCurrentColumn | deployment: Head | source: Time-driven
 *     type: Day timer | time: 1pm–2pm  (AFTER the existing refreshCurrentColumn slot)
 * It gets its OWN trigger rather than being folded into refreshCurrentColumn because a Klaviyo
 * event sweep is thousands of paged reads — the existing run is ~60s of the 360s ceiling and this
 * job's own budget is 240s. Sharing the invocation would put both over one ceiling and the
 * routing/TnT2 refresh is the one that must never be the casualty.
 */
function ntListTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    ntLog_('  ' + t.getHandlerFunction() + '  ' + t.getEventType() + '  ' + t.getTriggerSource());
  });
}
