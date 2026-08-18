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
 *
 *   🔴 ORDER PLACED IS NOT KLAVIYO (Kurt's ruling 2026-08-18). The order-confirmation mail is sent
 *   by SHOPIFY, not by a Klaviyo flow — and the live flow list confirms it: 26 flows, 21 live /
 *   5 draft, 0 archived, and NONE of them is an order-placed/confirmation flow. Sourcing that row
 *   from a name-regex over Klaviyo would have silently matched something else. It is therefore read
 *   from SHOPIFY ORDER EVENTS: every order carries a BasicEvent whose message is
 *       "Order confirmation email was sent to <name> (<email>)."
 *   (live-verified 2026-08-18 on _SHIP_2026-08-17 orders). That is a PER-ORDER fact, so the
 *   Order Placed row is a count of ORDERS — the same grain as Total Shipments — NOT the
 *   distinct-profile grain the two Klaviyo sections are stuck with. The two grains are labelled in
 *   the log every run so the difference is never mistaken for a discrepancy.
 *
 *   Order Shipped / Order Delivered — KLAVIYO (Kurt's ruling: in-transit and delivered mail DO go
 *   through Klaviyo). The flows are PINNED IN CODE, not regex-resolved:
 *       Order Shipped   -> XYFE5N  "Shipping Notification - In Transit (Parcel Panel)"
 *       Order Delivered -> Tu67r6  "Shipping Notification - Delivered (Shopify)"
 *   Script properties KLAVIYO_FLOW_SHIPPED / KLAVIYO_FLOW_DELIVERED still override the pin.
 *   VC8dJp "Shipping Notification - Out for Delivery (Shopify)" is deliberately NOT mapped to any
 *   row; its counts are LOGGED as an informational line for Kurt to decide on later.
 *     Email Sent      = DISTINCT cohort profiles with a Klaviyo "Received Email" event whose
 *                       $flow is that section's flow, inside the cohort window.
 *     SMS Sent        = same, metric "Received Text Message".
 *     SMS Engaged     = same, metric "Clicked Text Message"  (Klaviyo exposes no reply metric;
 *                       clicks are the only engagement signal on SMS). Stated as an ASSUMPTION.
 *
 * 🔴 THE METRIC NAMES ARE ACCOUNT-SPECIFIC AND THE OBVIOUS GUESS IS WRONG (live burn 2026-08-18).
 * This account has NO metric called "Received SMS" and NO metric called "Clicked SMS" — the first
 * version of this file looked for both and would have written a silent 0 on every SMS row. The 199
 * real metric names (from ntCheckKlaviyo) include "Received Text Message", "Sent Text Message",
 * "Relayed Text Message", "Failed to Deliver Text Message", "Clicked Text Message", "Opened Text".
 * Chosen, and WHY:
 *   SMS Sent    -> "Received Text Message", NOT "Sent Text Message". "Sent" is dispatch to the
 *                  carrier; "Received" is the handset actually taking delivery. Every other row on
 *                  this tab counts a message that reached a customer (Email Sent = "Received
 *                  Email"), and "Percent of Total" is only honest against the same basis. Choosing
 *                  "Sent Text Message" would inflate the row by every carrier-level failure.
 *   SMS Engaged -> "Clicked Text Message". CLICKS ONLY — Klaviyo publishes no reply/inbound-SMS
 *                  metric, so a customer who texts back is invisible here. This row UNDERSTATES
 *                  engagement and must never be read as "responded".
 * Changing either name is a data-definition change: re-run ntCheckKlaviyo() first and confirm the
 * name EXISTS before editing — an absent metric writes nothing, but a wrong-but-present one writes
 * a plausible lie.
 *     Percent of Total= sent / `Total Shipments` on this same column, written as a TEXT "12.34%"
 *                       string (Product Mix precedent — new columns render without hand formatting).
 *   Time of Sending   — Kurt 2026-08-18: split of messages by the RECIPIENT'S LOCAL time at the
 *                       destination, derived from the shipping address zip.
 *     "From 8A and 8P - Destination Time"   = local hour 8..19:59  (i.e. 08:00:00–19:59:59)
 *     "From 8:01P to 7:59A - Local Time"    = everything else
 *     These two PARTITION the sends. Anything whose zip cannot be resolved to ONE timezone is
 *     counted as MISSING and REPORTED — never silently bucketed (`NT_ASSERT_TIME_PARTITION`).
 *
 * 🔴 JOIN LIMITATION — APPLIES TO THE TWO KLAVIYO SECTIONS ONLY (Order Shipped / Order Delivered).
 * Order Placed does NOT have this problem: it is read per-order off the order itself.
 * Klaviyo's flow-message events
 * ("Received Email" / "Received Text Message") do NOT carry a Shopify order number — they carry a profile
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
 * 🔴 THE EMAIL ROWS CANNOT BE FILLED FROM APPS SCRIPT — AND THAT IS A PROPERTY OF THE ACCOUNT, NOT
 * A BUG (measured 2026-08-18, /metric-aggregates). /events/ has NO flow filter, so counting one
 * flow's email sends means paging EVERY "Received Email" event: 206,381 events in a 28-day window,
 * ~1,032 pages, measured at ~3.0s/page against this account = ~52 MINUTES. The Apps Script ceiling
 * is 360s. The SMS metrics are 90 and 48 pages (~4.7 and ~2.1 min) and DO fit. So:
 *     Order Shipped / Delivered  SMS Sent + SMS Engaged  -> measured, written
 *     Order Shipped / Delivered  Email Sent + its %      -> DECLINED, left BLANK, note logged
 *     Order Placed               Email Sent + its %      -> Shopify, cheap, written
 * ntMetricVolume_ makes the decline explicit and instant instead of burning 4 minutes to fail.
 * Do NOT "fix" this by raising NT_MAX_PAGES. If Kurt wants the two email rows, the honest options
 * are (a) a local/cloud job with no 360s ceiling that writes the cells, or (b) Klaviyo's
 * flow-series report, which is one cheap call but is ACCOUNT-WIDE PER WEEK and NOT cohort-joined —
 * it must never be written into a cohort column as if it were.
 *
 * MEASURED 2026-08-18 (live, read-only; the numbers this file was validated against):
 *   cohort _SHIP_2026-08-17  2324 orders / 2289 distinct emails / 34 repeats / 1 no-email
 *     Order Placed  (Shopify order events, ORDER grain)  2291  (98.58% of orders)
 *     Order Shipped   XYFE5N  SMS Sent 657   SMS Engaged 222
 *     Order Delivered Tu67r6  SMS Sent 209   SMS Engaged  64   (cohort is 1 day old — expected low)
 *   cohort _SHIP_2026-08-10  2316 orders / 2304 distinct emails
 *     Order Placed  2285 (98.66%)
 *     Order Shipped   XYFE5N  SMS Sent 938   SMS Engaged 354
 *     Order Delivered Tu67r6  SMS Sent 954   SMS Engaged 290
 *   Every value is <= its cohort size — the sends-cannot-exceed-cohort sanity check passes.
 *   Cross-check against Klaviyo's own flow-series report (account-wide, weekly): In-Transit SMS
 *   delivered 670 in the week of 08-17 vs our 657 in-cohort, and 969 in the week of 08-10 vs our
 *   938. The mapping is right.
 *   Out-for-Delivery VC8dJp: ZERO sends of any channel in August (no rows in the flow-series report,
 *   0 SMS events in either window). It is live but silent — which is why it gets no row.
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
 *   KLAVIYO_FLOW_SHIPPED / KLAVIYO_FLOW_DELIVERED — OPTIONAL overrides of the in-code flow pins
 *                                (XYFE5N / Tu67r6). Unset is the normal state. There is no
 *                                KLAVIYO_FLOW_PLACED: that row is Shopify's, not Klaviyo's.
 *                                Whichever source wins is logged, with the flow's LIVE name, every
 *                                run — and a pin that is not in the live flow list blanks its
 *                                section instead of reading as zero.
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

/**
 * Metric names EXACTLY as THIS account ships them — live-verified against /api/metrics 2026-08-18
 * (199 metrics). 🔴 "Received SMS" and "Clicked SMS" DO NOT EXIST here; both were wrong in the first
 * version of this file. See the header block for why "Received Text Message" (delivered to handset)
 * beats "Sent Text Message" (dispatched), and why SMS Engaged is CLICKS ONLY.
 * Also present and NOT used: Sent/Relayed/Failed to Deliver Text Message, Opened Text, Opened Email,
 * Clicked Email. Verify with ntCheckKlaviyo() before changing any of these.
 */
var NT_METRIC = { email: 'Received Email', sms: 'Received Text Message',
                  smsEngaged: 'Clicked Text Message' };

/**
 * 🔴 Informational only — NOT a row on this tab. Kurt 2026-08-18: the Out-for-Delivery flow is real
 * and firing, but it is not mapped to any row until he decides it deserves one. Its counts are
 * LOGGED every run so the decision is made against numbers, and a future mapping is a one-line
 * change here plus a row on the sheet — never a silent re-point of the Delivered row.
 */
var NT_FLOW_INFO = { id: 'VC8dJp', name: 'Shipping Notification - Out for Delivery (Shopify)' };

/** Section header text on the sheet -> internal section key. */
var NT_SECTIONS = { 'Order Placed': 'placed', 'Order Shipped': 'shipped',
                    'Order Delivered': 'delivered', 'Time of Sending': 'time' };

/**
 * Flow resolution for the two KLAVIYO sections. Resolution order: script-property pin -> the
 * PINNED-IN-CODE id below -> nothing. There is deliberately NO name regex any more.
 *
 * 🔴 WHY THE REGEX IS GONE. /ship(ped|ping|ment)/ matches FOUR live flows in this account
 * ("… In Transit (Parcel Panel)", "… Delivered (Shopify)", "… Out for Delivery (Shopify)", and
 * more), and /deliver/ matches both the Delivered and the Out-for-Delivery flow. A regex here is a
 * coin flip that looks like a resolution, and the wrong side of it writes a number that is off by a
 * whole notification stage without ever erroring. Kurt named the mapping; the mapping is the code.
 *
 * `placed` is absent ON PURPOSE — that row comes from Shopify (see header). Do not add it back
 * hoping a flow will turn up; there is no order-placed flow in this account.
 */
var NT_FLOW_CFG = {
  shipped:   { prop: 'KLAVIYO_FLOW_SHIPPED',   pin: 'XYFE5N',
               name: 'Shipping Notification - In Transit (Parcel Panel)' },
  delivered: { prop: 'KLAVIYO_FLOW_DELIVERED', pin: 'Tu67r6',
               name: 'Shipping Notification - Delivered (Shopify)' }
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
  // status/archived are carried so ntCheckKlaviyo can PROVE the list is not status-filtered — the
  // "there is no order-placed flow" conclusion rests on seeing drafts and archives too.
  r.data.forEach(function (f) {
    out.push({ id: f.id, name: String(f.attributes.name),
               status: String(f.attributes.status), archived: !!f.attributes.archived });
  });
  return out;
}

/**
 * section -> flow id, for the KLAVIYO sections only. Property pin wins, else the in-code pin.
 * The resolved id is CHECKED AGAINST THE LIVE FLOW LIST and its live name is logged: a pin that no
 * longer exists (flow deleted / account switched) must not quietly return zero rows, so it
 * unresolves the section and the section writes NOTHING. The name is logged, never matched on.
 */
function ntResolveFlows_(flows) {
  var props = PropertiesService.getScriptProperties(), out = {};
  var nameById = {};
  flows.forEach(function (f) { nameById[f.id] = f.name; });
  Object.keys(NT_FLOW_CFG).forEach(function (sec) {
    var cfg = NT_FLOW_CFG[sec], pinned = props.getProperty(cfg.prop);
    var id = (pinned && String(pinned).trim()) ? String(pinned).trim() : cfg.pin;
    var src = (pinned && String(pinned).trim()) ? 'script property ' + cfg.prop : 'code pin';
    if (!nameById.hasOwnProperty(id)) {
      out[sec] = null;
      ntLog_('  ⚠️ flow ' + sec + ': ' + id + ' (from ' + src + ') IS NOT IN THE LIVE FLOW LIST (' +
             flows.length + ' flows). Expected "' + cfg.name + '". This section writes NOTHING — ' +
             'a missing flow must not read as zero sends.');
      return;
    }
    out[sec] = id;
    ntLog_('  flow ' + sec + ': ' + id + ' "' + nameById[id] + '" (from ' + src + ')' +
           (nameById[id] === cfg.name ? '' : '  ⚠️ live name differs from the documented "' +
            cfg.name + '" — the flow may have been renamed or re-pointed; verify with Kurt'));
  });
  // Not a row (Kurt 2026-08-18) — presence is logged so the informational counts can be trusted.
  ntLog_('  flow (INFO, no row) ' + NT_FLOW_INFO.id + ': ' +
         (nameById.hasOwnProperty(NT_FLOW_INFO.id) ? '"' + nameById[NT_FLOW_INFO.id] + '"'
                                                   : 'NOT IN THE LIVE FLOW LIST'));
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

/**
 * 🔴 HOW BIG IS THE SWEEP? Ask BEFORE paging, not after the budget is gone (live burn 2026-08-18).
 * /events/ has no flow filter, so counting "how many cohort profiles got the In-Transit email" means
 * paging EVERY "Received Email" event in the account for the window. Measured on this account:
 *     Received Email          206,381 events / 28d   -> ~1,032 pages   INFEASIBLE in 240s
 *     Received Text Message    17,922 events / 28d   ->    ~90 pages   fine
 *     Clicked Text Message      9,442 events / 28d   ->    ~48 pages   fine
 * Marketing campaigns dominate the email metric; the handful we want is buried in them. A blind
 * sweep therefore burns the whole budget, returns INCOMPLETE, and writes nothing — but only AFTER
 * four minutes, every single run, forever.
 * ONE cheap POST to /metric-aggregates/ gives the exact count first, so an impossible sweep is
 * declined up front with a note that says WHY and WHAT the number would have cost.
 * (This endpoint is a POST and is deliberately NOT in NT_PAGING — it returns one aggregate, no
 * collection, no paging.)
 */
function ntMetricVolume_(metricId, lo, hi) {
  var body = { data: { type: 'metric-aggregate', attributes: {
    metric_id: metricId, measurements: ['count'], interval: 'week', timezone: 'UTC',
    filter: ['greater-or-equal(datetime,' + lo.replace('Z', '') + ')',
             'less-than(datetime,' + hi.replace('Z', '') + ')'] } } };
  var resp = UrlFetchApp.fetch(NT_BASE + '/metric-aggregates/', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(body),
    muteHttpExceptions: true,
    headers: { Authorization: 'Klaviyo-API-Key ' + ntKey_(), revision: NT_REV, accept: 'application/json' }
  });
  if (resp.getResponseCode() !== 200) {
    ntLog_('  ⚠️ volume precheck failed (' + resp.getResponseCode() + ') — ' +
           resp.getContentText().slice(0, 200) + '. Sweeping blind.');
    return null;                                   // null = unknown, NOT zero
  }
  var rows = JSON.parse(resp.getContentText()).data.attributes.data || [], tot = 0;
  rows.forEach(function (d) {
    (d.measurements.count || []).forEach(function (v) { tot += Number(v) || 0; });
  });
  return tot;
}

// ------------------------------------------------------------------ cohort (Shopify)

/**
 * 🔴 The Shopify order-confirmation send, per order. Matches the BasicEvent message Shopify writes
 * seconds after checkout: "Order confirmation email was sent to <name> (<email>)."
 * Anchored to the START of the message so it can never catch the OTHER confirmation events on the
 * same order — "Confirmation #ABC was generated for this order." and, from the RMFG app, "… sent a
 * shipping confirmation email to …" (that one belongs to the SHIPPED stage and would double-count).
 */
var NT_PLACED_RE = /^order confirmation email was sent/i;

/**
 * The cohort population: order name, customer email, destination zip, AND whether Shopify sent the
 * order-confirmation email. Deliberately a LIGHT query — PivotAnalytics' paFetchCohort_ pulls
 * fulfillment event trees we do not need here.
 * 🔴 Same exclusions as every other cohort cut: not cancelled, not a Reship.
 *
 * 🔴 events() MUST BE ASKED FOR IN CREATED_AT ASCENDING ORDER (live burn 2026-08-18). The default
 * ordering returns the NEWEST events first, so a `first:6` window on a months-old subscription
 * order was filled with August fulfillment chatter and the June order-confirmation event fell off
 * the end — the count came back 271/400 (68%) and looked like a real deliverability problem. With
 * sortKey CREATED_AT ascending the same 400 orders answer 397. The order-confirmation event is
 * always among the first few events of an order, so ascending + a small page is both correct and
 * cheap. `query:"confirmation"` narrows the payload; the REGEX, not the search, decides the match.
 * page size is 25 (not 50) because each node now carries an event list.
 */
function ntFetchCohort_(shipWeek) {
  var q = 'query($q:String!,$cursor:String){ orders(first:25, query:$q, after:$cursor){' +
          ' pageInfo{hasNextPage endCursor} edges{node{ name email' +
          ' shippingAddress{ zip provinceCode }' +
          ' events(first:10, sortKey:CREATED_AT, reverse:false, query:"confirmation")' +
          '   { edges{ node{ createdAt message } } } } } } }';
  var qs = "tag:'" + shipWeek + "' -status:cancelled -tag:'Reship'";
  var out = [], cursor = null;
  while (true) {
    var conn = shopifyGql_(q, { q: qs, cursor: cursor }).orders;
    conn.edges.forEach(function (e) {
      var n = e.node;
      var confirmAt = '';
      ((n.events && n.events.edges) || []).forEach(function (ee) {
        var msg = String((ee.node && ee.node.message) || '').trim();
        if (!confirmAt && NT_PLACED_RE.test(msg)) confirmAt = ee.node.createdAt;
      });
      out.push({
        order: n.name,
        email: String(n.email || '').toLowerCase(),
        zip: String((n.shippingAddress && n.shippingAddress.zip) || '').replace(/[^0-9]/g, ''),
        confirmAt: confirmAt                      // '' = Shopify has no such event on this order
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
  var emails = {}, dupes = 0, zoneByEmail = {}, placedN = 0;
  cohort.forEach(function (o) {
    if (o.confirmAt) placedN++;
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
    // volume precheck — decline an impossible sweep instead of spending the budget discovering it.
    var vol = ntMetricVolume_(id, lo, hi);
    if (vol !== null) {
      var pagesNeeded = Math.ceil(vol / NT_PAGE);
      ntLog_('  volume "' + NT_METRIC[k] + '" in window: ' + vol + ' events -> ~' + pagesNeeded +
             ' pages (cap ' + NT_MAX_PAGES + ')');
      if (pagesNeeded > NT_MAX_PAGES) {
        notes.push('SWEEP DECLINED for "' + NT_METRIC[k] + '": ' + vol + ' events in the window ' +
                   'need ~' + pagesNeeded + ' pages, over the ' + NT_MAX_PAGES + '-page cap. ' +
                   'Klaviyo /events has NO flow filter, so there is no cheaper cut — every row fed ' +
                   'by this metric is left BLANK (blank != zero). Raising NT_MAX_PAGES does not fix ' +
                   'it; the 360s Apps Script ceiling does not fit the sweep.');
        ev[k] = null;
        return;
      }
    }
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

  // ---- Order Placed: SHOPIFY, per ORDER (Kurt 2026-08-18). Not Klaviyo, not a flow. ----
  // 🔴 GRAIN WARNING, stated every run: this row counts ORDERS; the two Klaviyo rows below count
  // DISTINCT PROFILES. They are not comparable line-for-line and the gap is the repeat-customer
  // count logged above — never "reconcile" one to the other.
  out['placed||email||Email Sent'] = placedN;
  if (denomOk) out['placed||email||Percent of Total'] = ntPct_(placedN, sheetTotal);
  ntLog_('  placed Email Sent = ' + placedN + ' of ' + cohort.length + ' orders (SHOPIFY order ' +
         'events, ORDER grain)' + (denomOk ? ' (' + ntPct_(placedN, sheetTotal) + ')' : ' (no %)'));
  if (placedN > cohort.length) {
    throw new Error('NT_ASSERT_PLACED_OVERCOUNT: ' + placedN + ' confirmation events on ' +
                    cohort.length + ' orders — the per-order match is double-counting.');
  }
  if (cohort.length && (cohort.length - placedN) / cohort.length > 0.05) {
    notes.push('ORDER PLACED GAP: ' + (cohort.length - placedN) + ' of ' + cohort.length +
               ' cohort orders carry NO Shopify order-confirmation event. A few percent is normal ' +
               '(POS/manual/no-email orders); a large gap usually means the events page window is ' +
               'too small or the message wording changed — check before trusting this row.');
  }

  ['shipped', 'delivered'].forEach(function (sec) {
    var flowId = flows[sec];
    if (!flowId) return;                                    // unresolved -> blank, already logged
    var chans = ['email', 'sms'];
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
    // SMS Engaged — CLICKS ONLY, same flow, same cohort filter. Not replies: none exist.
    if (ev.smsEngaged) {
      var eh = ev.smsEngaged.byFlow[flowId] || {}, k2 = 0;
      Object.keys(eh).forEach(function (em) { if (emails[em]) k2++; });
      out[sec + '||sms||SMS Engaged'] = k2;
      ntLog_('  ' + sec + ' SMS Engaged ("' + NT_METRIC.smsEngaged + '" — clicks only) = ' + k2);
    }
  });

  // ---- Out-for-Delivery: INFORMATIONAL, no row (Kurt 2026-08-18). Logged so the decision to give
  // it a row (or not) is made against real numbers rather than a guess. NOTHING is written. ----
  ['email', 'sms', 'smsEngaged'].forEach(function (ch) {
    if (!ev[ch]) return;
    var h = ev[ch].byFlow[NT_FLOW_INFO.id] || {}, c = 0;
    Object.keys(h).forEach(function (em) { if (emails[em]) c++; });
    ntLog_('  [INFO — no row] ' + NT_FLOW_INFO.name + ' (' + NT_FLOW_INFO.id + ') ' +
           NT_METRIC[ch] + ' = ' + c + ' cohort profiles (' + Object.keys(h).length + ' account-wide)');
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
  // The names that were WRONG before 2026-08-18 — asserted absent so nobody "restores" them.
  ['Received SMS', 'Clicked SMS'].forEach(function (bad) {
    ntLog_('  legacy guess "' + bad + '" -> ' + (metrics[bad] ? 'exists (!) — re-read the header'
                                                             : 'correctly ABSENT in this account'));
  });
  // What a sweep would COST right now (last 28 days) — the reason the email rows may be blank.
  var vhi = new Date().toISOString().replace(/\.\d+Z$/, 'Z');
  var vlo = new Date(new Date().getTime() - 28 * 86400000).toISOString().replace(/\.\d+Z$/, 'Z');
  ['email', 'sms', 'smsEngaged'].forEach(function (k) {
    var id = metrics[NT_METRIC[k]];
    if (!id) return;
    var v = ntMetricVolume_(id, vlo, vhi);
    ntLog_('  volume(28d) "' + NT_METRIC[k] + '" = ' + (v === null ? 'UNKNOWN' : v) +
           (v === null ? '' : ' events -> ~' + Math.ceil(v / NT_PAGE) + ' pages, cap ' +
                              NT_MAX_PAGES + (Math.ceil(v / NT_PAGE) > NT_MAX_PAGES
                                              ? '  🔴 SWEEP INFEASIBLE — rows stay BLANK' : '  OK')));
  });
  var flows = ntFlows_(deadline);
  ntLog_('FLOWS (' + flows.length + ') — 2026-08-18 live: 26 total, 21 live / 5 draft, 0 archived. ' +
         '/flows returns EVERY status, so "no order-placed flow" is a fact about the account, not a ' +
         'filter artifact:');
  var byStatus = {};
  flows.forEach(function (f) { byStatus[f.status] = (byStatus[f.status] || 0) + 1; });
  ntLog_('  status mix: ' + JSON.stringify(byStatus) + ' | archived: ' +
         flows.filter(function (f) { return f.archived; }).length);
  flows.forEach(function (f) {
    ntLog_('  ' + f.id + '  ' + f.status + (f.archived ? ' ARCHIVED' : '') + '  ' + f.name);
  });
  var placedish = flows.filter(function (f) { return /order\s*(placed|confirm)/i.test(f.name); });
  ntLog_('  flows matching /order (placed|confirm)/i : ' + placedish.length +
         ' — 0 is EXPECTED (Order Placed is a Shopify email, see header).');
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
