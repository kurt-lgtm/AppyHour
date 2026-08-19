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
 *   Total Shipments / Arrived   — 🔴 SCRIPT-OWNED as of Kurt's ruling 2026-08-19. This REPLACES the
 *   "NOT OURS / owned elsewhere / read-only here" note that stood here before, and supersedes the
 *   same phrasing in D24. Both rows were hand-typed constants that no code wrote and that stopped
 *   being updated after `_SHIP_2026-08-03` — and `Arrived` had already drifted 193 low on 08-03,
 *   copied mid-flight and never refreshed. See ntMirrorCell_ for the full finding.
 *     Total Shipments = RECOMPUTED here from `cohort.length`; `ntFetchCohort_` runs the identical
 *                       query TnT2 is built from, so it is the same population at no extra cost.
 *                       TnT2 is read only as an independent CROSS-CHECK.
 *     Arrived         = MIRRORED from `Lost in Transit` by header + label (never index). It is a
 *                       delivery fact derived from fulfillment trees + ParcelPanel, and this file's
 *                       cohort query is deliberately light — recomputing it would duplicate that
 *                       pipeline and spend the shared PP quota twice.
 *   🔴 BOTH ROWS FILL BLANKS ONLY. A value a human typed is never overwritten on any column; if the
 *   computed value disagrees it is REPORTED and the typed value kept, because the disagreement is
 *   the evidence that the mirror drifted.
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
 * 🔴 THE SWEEP IS RESUMABLE — A RUN THAT DOES NOT FINISH BANKS ITS PROGRESS (Kurt 2026-08-19:
 * "can't we just slow it down?"). The 2026-08-19 13:40 live run is the motivating burn: 242.7s of
 * the 360s ceiling spent, and the ONLY cell written was the Shopify-sourced `placed` one. Both SMS
 * metrics — 79 and 40 pages, comfortably UNDER the 120-page cap — died on TIME, threw their pages
 * away, and reported INCOMPLETE. Every subsequent run repeated the same 242.7s and threw it away
 * again. Nothing was wrong with the cap; the run had no memory.
 *   🔴 AND ONE RUN CANNOT BE MADE TO FINISH. Measured live 2026-08-19 on this cohort's real window:
 *   `Received Text Message` is 79 pages at 4.59s/page = **363s**, which is more than the entire 360s
 *   ceiling before a single other call. No budget, cap, or tuning makes that fit in one invocation —
 *   resumption is not an optimisation here, it is the only way that metric ever completes.
 *   - Progress is CHECKPOINTED to the hidden `_nt_sweep` tab after every run: per (cohort, metric)
 *     the Klaviyo `links.next` cursor, the page count, and the accumulated per-flow distinct-profile
 *     sets. The next run picks the cursor up and keeps going.
 *   - 🔴 WHY A SHEET TAB AND NOT A SCRIPT PROPERTY. A Script Property value is capped at 9 KB and
 *     the whole store at 500 KB; one cohort's set is ~900–2,300 email/timestamp pairs ≈ 40–100 KB,
 *     so it does not fit in one property and sharding it across properties would burn the store the
 *     other three files share. CacheService is 100 KB/key and EXPIRES (6h max, evictable) — a cache
 *     that can silently vanish mid-sweep is the same failure with extra steps. A hidden tab is
 *     durable, inspectable by hand, and already the house pattern (`_exc_state`, `_state`).
 *   - 🔴 A PARTIALLY SWEPT METRIC WRITES NOTHING. Its rows stay BLANK and the log says how far it
 *     got ("pages 62/79, 78% — resuming next run"). A half-swept number looks finished and is a lie;
 *     blank is honest. Only `complete` sweeps produce cells.
 *   - The run is bounded by TIME first (NT_TIME_BUDGET_MS minus NT_SAVE_RESERVE_MS, so there is
 *     always room to checkpoint) and by pages second, so it exits cleanly instead of being killed
 *     mid-page by the 360s ceiling.
 *   - INVALIDATION: the state carries a signature of {version, cohort tag, window lo/hi, sorted
 *     tracked flow ids, metric names}. Any change discards the state and restarts — a set
 *     accumulated under different pins is not a partial answer to the new question, it is a wrong
 *     one. A COMPLETE result is reused for the rest of the SAME America/New_York calendar day and
 *     re-measured the next day: this tab is a daily walk-forward artifact, so "same day" is its
 *     natural grain, and a duration TTL would let a 23:50 sweep be reused at 01:10 and silently
 *     freeze a day's movement.
 *   - Only the THREE tracked flows (the two pinned + the informational one) are retained in state.
 *     Events belonging to any other flow are dropped as they are read — that is what keeps the state
 *     bounded and it is why a change to the pins MUST invalidate it.
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
/**
 * 🔴 NT_MAX_PAGES IS THE FEASIBILITY CAP, AND IT IS NOW A TOTAL ACROSS RUNS — not a per-run limit.
 * Resumption exists to make a sweep that is ALREADY under this cap actually FINISH; it is not a
 * licence to raise the cap. At ~2s/page a 120-page metric is ~2 runs, which is a number of clicks
 * Kurt can live with. The 830-page email metric is still DECLINED up front by the volume precheck
 * and still leaves its rows blank — see the email block in the header. Raising this number is a
 * decision about how many runs a cohort costs, so it is Kurt's, not a tuning knob.
 */
var NT_MAX_PAGES = 120;                  // TOTAL pages per metric per cohort, across runs
var NT_MAX_PAGES_PER_RUN = 90;           // second bound inside ONE run (time is the first)
var NT_TIME_BUDGET_MS = 240000;          // 4 min of fetching, leaving 2 min of the 360s ceiling
/**
 * Held back from the fetch budget so a run ALWAYS has room to serialize + write its checkpoint.
 * Without this the paging loop runs to the deadline and the checkpoint write is what gets killed by
 * the 360s ceiling — losing exactly the progress the checkpoint exists to keep.
 */
var NT_SAVE_RESERVE_MS = 25000;
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

// ------------------------------------------------------------------ resumable sweep state

/**
 * 🔴 THE CHECKPOINT STORE. Hidden tab, one row per record, `write-then-trim` like `_exc_state`
 * (clearing first means any failure below destroys the state outright).
 *
 * Layout:  key | kind | seq | payload
 *   key      the cohort tag, so two legs never collide and a stale cohort is trivially droppable
 *   kind     'sig' | 'vol:<metricKey>' | 'meta:<metricKey>' | 'data:<metricKey>'
 *   seq      chunk index for `data:` rows, 0 otherwise
 *   payload  a '#'-prefixed string. 🔴 THE '#' IS LOAD-BEARING: setValue() on a string beginning
 *            with '=' makes it a FORMULA, and a serialized blob is not something to hand the
 *            formula parser. One char, stripped on read, and the class cannot recur.
 *
 * A cell holds 50,000 characters, so blobs are chunked at NT_CHUNK_CHARS with headroom and
 * reassembled by seq. Entries are `flowId\temail\tepochSeconds` per line — tab and newline are both
 * illegal in an email address and both survive a sheet round-trip, so the delimiter can never
 * collide with the data (a comma or semicolon eventually would).
 */
var NT_STATE_TAB = '_nt_sweep';
var NT_STATE_VER = 2;                    // bump = every stored state is discarded on sight
var NT_STATE_COLS = ['key', 'kind', 'seq', 'payload'];
var NT_CHUNK_CHARS = 40000;              // cell limit is 50,000 — headroom for the '#' and for slack

function ntLog_(m) { Logger.log(m); }

/** ET calendar date, the grain a COMPLETE result is reused for. */
function ntToday_() { return Utilities.formatDate(new Date(), NT_TZ, 'yyyy-MM-dd'); }

function ntJson_(s) { try { return JSON.parse(s); } catch (e) { return null; } }

function ntStateSheet_() {
  var ss = SpreadsheetApp.openById(EXC_HOST_SHEET_ID);
  var sh = ss.getSheetByName(NT_STATE_TAB);
  if (!sh) {
    sh = ss.insertSheet(NT_STATE_TAB);
    sh.getRange(1, 1, 1, NT_STATE_COLS.length).setValues([NT_STATE_COLS.slice()]);
  }
  try { sh.hideSheet(); } catch (e) { /* already hidden, or it is the only visible sheet */ }
  return sh;
}

/** `flowId\temail\tepochSec` per line. Sorted only incidentally; order carries no meaning. */
function ntSerializeSets_(sets) {
  var lines = [];
  Object.keys(sets).forEach(function (flow) {
    var d = sets[flow] || {};
    Object.keys(d).forEach(function (em) { lines.push(flow + '\t' + em + '\t' + d[em]); });
  });
  return lines.join('\n');
}

function ntParseSets_(blob) {
  var out = {};
  if (!blob) return out;
  blob.split('\n').forEach(function (ln) {
    if (!ln) return;
    var p = ln.replace(/\r$/, '').split('\t');
    if (p.length !== 3 || !p[0] || !p[1]) return;
    if (!out[p[0]]) out[p[0]] = {};
    out[p[0]][p[1]] = Number(p[2]) || 0;
  });
  return out;
}

function ntSetSize_(sets) {
  var n = 0;
  Object.keys(sets || {}).forEach(function (f) { n += Object.keys(sets[f] || {}).length; });
  return n;
}

/**
 * 🔴 THE INVALIDATION KEY. Everything the accumulated sets are an answer TO. If any of it moved,
 * the stored pages answer a different question and are discarded rather than continued — a set
 * gathered under the old flow pins is not a partial answer to the new pins, it is a wrong one.
 * The tracked flow list is in here precisely because the sweep DROPS every untracked flow as it
 * reads: re-pointing a pin cannot be repaired by continuing, only by restarting.
 */
function ntSig_(shipWeek, lo, hi, flowIds) {
  return { ver: NT_STATE_VER, shipWeek: shipWeek, lo: lo, hi: hi,
           flows: flowIds.slice().sort().join(','),
           metrics: NT_METRIC.email + '|' + NT_METRIC.sms + '|' + NT_METRIC.smsEngaged };
}

function ntSigEq_(a, b) {
  if (!a || !b) return false;
  return a.ver === b.ver && a.shipWeek === b.shipWeek && a.lo === b.lo && a.hi === b.hi &&
         a.flows === b.flows && a.metrics === b.metrics;
}

function ntSigWhy_(a, b) {
  if (!a) return 'no stored state';
  if (!b) return 'no signature computed';
  var d = [];
  ['ver', 'shipWeek', 'lo', 'hi', 'flows', 'metrics'].forEach(function (k) {
    if (a[k] !== b[k]) d.push(k + ' ' + a[k] + ' -> ' + b[k]);
  });
  return d.length ? d.join('; ') : 'unchanged';
}

/** Everything stored for ONE cohort. Returns {sig, vol:{}, metrics:{k:{...,sets}}}. */
function ntLoadState_(shipWeek) {
  var st = { sig: null, vol: {}, metrics: {} };
  var sh = ntStateSheet_();
  var last = sh.getLastRow();
  if (last < 2) return st;
  var rows = sh.getRange(2, 1, last - 1, NT_STATE_COLS.length).getValues();
  var blobs = {};
  rows.forEach(function (r) {
    if (String(r[0]) !== shipWeek) return;
    var kind = String(r[1]), seq = Number(r[2]) || 0, pay = String(r[3] == null ? '' : r[3]);
    if (pay.charAt(0) === '#') pay = pay.slice(1);
    if (kind === 'sig') { st.sig = ntJson_(pay); return; }
    if (kind.indexOf('vol:') === 0) { st.vol[kind.slice(4)] = ntJson_(pay); return; }
    if (kind.indexOf('meta:') === 0) { st.metrics[kind.slice(5)] = ntJson_(pay); return; }
    if (kind.indexOf('data:') === 0) {
      var k = kind.slice(5);
      if (!blobs[k]) blobs[k] = [];
      blobs[k][seq] = pay;
    }
  });
  Object.keys(st.metrics).forEach(function (k) {
    var m = st.metrics[k];
    if (!m) { delete st.metrics[k]; return; }
    var parts = blobs[k] || [];
    // 🔴 A MISSING CHUNK IS CORRUPTION, NOT AN EMPTY SET. Half a blob parses cleanly into a
    // smaller-but-plausible set, which would then be written as a finished count. Drop the record.
    var have = 0;
    for (var i = 0; i < (m.chunks || 0); i++) { if (typeof parts[i] === 'string') have++; }
    if (have !== (m.chunks || 0)) {
      ntLog_('  ⚠️ sweep state for "' + k + '" is missing ' + ((m.chunks || 0) - have) + ' of ' +
             m.chunks + ' chunk(s) — DISCARDED (a partial blob would parse as a smaller valid set)');
      delete st.metrics[k];
      return;
    }
    m.sets = ntParseSets_(parts.join(''));
  });
  return st;
}

/** Rewrites this cohort's rows; every OTHER cohort's rows are preserved untouched. */
function ntSaveState_(shipWeek, st) {
  var sh = ntStateSheet_();
  var keep = [], last = sh.getLastRow();
  if (last >= 2) {
    sh.getRange(2, 1, last - 1, NT_STATE_COLS.length).getValues().forEach(function (r) {
      if (String(r[0]) && String(r[0]) !== shipWeek) keep.push(r);
    });
  }
  var rows = [NT_STATE_COLS.slice()].concat(keep);
  rows.push([shipWeek, 'sig', 0, '#' + JSON.stringify(st.sig)]);
  Object.keys(st.vol).forEach(function (k) {
    rows.push([shipWeek, 'vol:' + k, 0, '#' + JSON.stringify(st.vol[k])]);
  });
  Object.keys(st.metrics).forEach(function (k) {
    var m = st.metrics[k];
    if (!m) return;
    var blob = ntSerializeSets_(m.sets || {});
    var chunks = [];
    for (var i = 0; i < blob.length; i += NT_CHUNK_CHARS) chunks.push(blob.slice(i, i + NT_CHUNK_CHARS));
    if (!chunks.length) chunks = [''];
    rows.push([shipWeek, 'meta:' + k, 0, '#' + JSON.stringify({
      next: m.next || '', pages: m.pages || 0, events: m.events || 0,
      total: m.total || 0, complete: !!m.complete, declined: !!m.declined,
      why: m.why || '', profiles: ntSetSize_(m.sets || {}), chunks: chunks.length,
      day: m.day || ntToday_(), updatedAt: new Date().toISOString()
    })]);
    chunks.forEach(function (c, i) { rows.push([shipWeek, 'data:' + k, i, '#' + c]); });
  });
  sh.getRange(1, 1, rows.length, NT_STATE_COLS.length).setValues(rows);
  if (sh.getLastRow() > rows.length) {
    sh.getRange(rows.length + 1, 1, sh.getLastRow() - rows.length, NT_STATE_COLS.length).clearContent();
  }
  try { sh.hideSheet(); } catch (e) { /* fine */ }
}

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
    // netFetch_ (Code.gs) absorbs connection-class blips + 5xx before this loop's own 429 handling
    // sees the response; a 4xx still arrives here untouched and throws loudly below.
    var resp = netFetch_(url, {
      method: 'get',
      muteHttpExceptions: true,
      headers: { Authorization: 'Klaviyo-API-Key ' + ntKey_(), revision: NT_REV, accept: 'application/json' }
    }, 'klaviyo get');
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
 * The FIRST page url of a metric's event sweep. Split out from the sweep because a resumed run
 * uses the stored `links.next` instead and must never rebuild this by hand.
 *
 * 🔴 DATETIME LITERALS ARE UNQUOTED IN A KLAVIYO FILTER; a metric_id is a QUOTED string. Quoting
 * the datetimes returns 400 "Invalid filter provided. Verify your datetimes are not in quotes."
 * (verified live 2026-08-18, revision 2024-10-15 — this was the next 400 after the paging one).
 *
 * 🔴 `sort=datetime` (ASCENDING) IS WHAT MAKES RESUMPTION SAFE ACROSS RUNS. The default order is
 * newest-first, so an event created between run 1 and run 2 is inserted AHEAD of the stored cursor
 * and is never seen — the sweep completes and silently misses it. Ascending appends new events
 * BEHIND the cursor, so a resumed sweep walks all the way to the true end of the window. The set is
 * a union either way, so the sort never double-counts; it only decides what a resumed run can miss.
 */
function ntEventsUrl_(metricId, lo, hi) {
  var q = ntPageParam_('/events/');
  return NT_BASE + '/events/?filter=' + encodeURIComponent(
           'and(equals(metric_id,"' + metricId + '"),greater-or-equal(datetime,' + lo +
           '),less-than(datetime,' + hi + '))') +
         '&include=profile&fields[profile]=email' +
         '&fields[event]=datetime,event_properties&sort=datetime' + (q ? '&' + q : '');
}

/**
 * RESUMABLE sweep of one metric over [lo,hi). Continues from `prev` (a checkpoint) or starts fresh,
 * pages until it finishes / runs out of time / hits a page bound, and returns a checkpoint either
 * way. Per flow, per profile-email it keeps the FIRST send — that is the message whose local hour
 * the Time-of-Sending split is measured on.
 *
 * 🔴 ONLY `tracked` FLOWS ARE RETAINED. Everything else is dropped as it is read: that is what keeps
 * the checkpoint bounded (an account-wide email metric is 206k events; the three tracked flows are
 * ~1k profiles), and it is exactly why the tracked ids are part of the state signature — re-pointing
 * a pin cannot be repaired by continuing a sweep that already threw the new flow's events away.
 *
 * 🔴 `complete:false` IS NOT A SMALLER ANSWER. The caller must write NOTHING from it. It carries the
 * cursor so the next run continues; deriving a count from it would publish a number that is missing
 * however many pages are left, indistinguishable on the sheet from a finished one.
 */
function ntSweepMetric_(metricId, lo, hi, tracked, prev, deadline) {
  var st = {
    sets: (prev && prev.sets) || {},
    pages: (prev && prev.pages) || 0,
    events: (prev && prev.events) || 0,
    next: (prev && prev.next) || '',
    complete: false, why: ''
  };
  var next = st.next || ntEventsUrl_(metricId, lo, hi);
  var thisRun = 0;
  while (next) {
    if (st.pages >= NT_MAX_PAGES) { st.next = next; st.why = 'total page cap ' + NT_MAX_PAGES; return st; }
    if (thisRun >= NT_MAX_PAGES_PER_RUN) { st.next = next; st.why = 'per-run page cap ' + NT_MAX_PAGES_PER_RUN; return st; }
    if (new Date().getTime() > deadline) { st.next = next; st.why = 'time budget'; return st; }
    var j = ntGet_(next);
    var emailById = {};
    (j.included || []).forEach(function (p) {
      if (p.type === 'profile') emailById[p.id] = String((p.attributes && p.attributes.email) || '').toLowerCase();
    });
    (j.data || []).forEach(function (e) {
      var props = e.attributes.event_properties || {};
      var flow = props['$flow'] || props['$flow_id'] || '';
      if (!flow || !tracked[flow]) return;      // campaign send, or a flow no row depends on
      var pid = e.relationships && e.relationships.profile && e.relationships.profile.data &&
                e.relationships.profile.data.id;
      var em = emailById[pid] || '';
      if (!em) return;
      if (!st.sets[flow]) st.sets[flow] = {};
      var sec = Math.floor(new Date(e.attributes.datetime).getTime() / 1000);
      var prevSec = st.sets[flow][em];
      if (!prevSec || sec < prevSec) st.sets[flow][em] = sec;   // first send wins
      st.events++;
    });
    next = (j.links && j.links.next) || null;
    st.pages++;
    thisRun++;
  }
  st.next = '';
  st.complete = true;
  return st;
}

/** epoch seconds (how the checkpoint stores a send) -> the ISO string ntDayNight_ parses. */
function ntIsoFromSec_(sec) { return new Date(Number(sec) * 1000).toISOString(); }

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
  var resp = netFetch_(NT_BASE + '/metric-aggregates/', {
    method: 'post', contentType: 'application/json', payload: JSON.stringify(body),
    muteHttpExceptions: true,
    headers: { Authorization: 'Klaviyo-API-Key ' + ntKey_(), revision: NT_REV, accept: 'application/json' }
  }, 'klaviyo metric-aggregates');
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

/**
 * 🔴 THE VOLUME PROBES WERE **NOT** THE COST — THAT WAS AN INFERENCE, AND IT WAS WRONG (measured
 * 2026-08-19, live). The three `/metric-aggregates/` POSTs were assumed to be ~129s of the 242.7s
 * run because they are the only thing logged between the window line and the sweep. Timed directly
 * they are **0.5s, 0.5s and 0.6s — 1.6s for all three**. The budget went where the pages are:
 * `Received Text Message` is 79 pages at **4.59s/page = 363s**, which exceeds the entire 360s Apps
 * Script ceiling on its own. Caching the probe is still worth doing — it is free and it makes the
 * feasibility decision once per cohort instead of once per run — but it saves ~1.6s, not ~129s, and
 * anyone optimising this file should attack pages, not probes. (Same class as the stage-timer burn:
 * a cost attributed by elimination is not a measurement.) Now:
 *   - ONE probe per (cohort, metric), CACHED in the sweep state. The cohort's window is fixed and
 *     historical and the signature already discards the cache when the window, the pins, or the
 *     metric names move, so the cached number is never answering a stale question. There is no
 *     separate TTL because there is no separate way for it to go stale.
 *   - SKIPPED ENTIRELY once that metric's sweep is under way or finished. The volume's only job is
 *     the feasibility decision (pages needed vs NT_MAX_PAGES); once pages are banked the decision
 *     has already been taken and re-taking it can only cost time or, worse, flip mid-sweep.
 *   - A probe that FAILS caches nothing (null = unknown, not zero) and the sweep proceeds blind,
 *     exactly as before — bounded now by the caps rather than by the unknown.
 */
function ntVolumeCached_(key, metricId, lo, hi, state) {
  if (state.vol && state.vol[key] && typeof state.vol[key].events === 'number') {
    ntLog_('  volume "' + NT_METRIC[key] + '": ' + state.vol[key].events +
           ' events (CACHED for this cohort, probed ' + state.vol[key].at + ' — no POST this run)');
    return state.vol[key].events;
  }
  var v = ntMetricVolume_(metricId, lo, hi);
  if (v !== null) state.vol[key] = { events: v, at: new Date().toISOString() };
  return v;
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

/**
 * 🔴 `Total Shipments` AND `Arrived` ARE SCRIPT-OWNED (Kurt 2026-08-19). This SUPERSEDES the old
 * "NOT OURS / read-only here" note that stood at the top of this file and in D24.
 *
 * WHAT WAS FOUND (2026-08-19). Both rows were HAND-TYPED CONSTANTS — read back with
 * valueRenderOption=FORMULA they are literal numbers, not formulas — and NOTHING in the script
 * project wrote them: `paValues_` emits `Total Shipments` / `Arrived` only onto `TnT2` and
 * `Lost in Transit` (`PA_TABS`), and neither string occurs in Code.gs or Exceptions.gs at all. They
 * were a manual mirror that stopped after `_SHIP_2026-08-03`, and one of them had already GONE WRONG:
 *     Notifications Arrived  08-03 = 2075
 *     Lost in Transit Arrived 08-03 = 2268   (+193)
 * — copied mid-flight and never refreshed as the cohort finished delivering, while `Total Shipments`
 * matched on every overlapping column. That is the whole argument for owning these rows: a hand
 * mirror of a STILL-MOVING number goes stale silently and nothing ever says so.
 *
 * 🔴 THE TWO ROWS GET DIFFERENT TREATMENT, AND THE REASON IS WHERE THE FACT LIVES.
 *
 * `Total Shipments` is RECOMPUTED, not mirrored. `ntFetchCohort_` already runs the IDENTICAL Shopify
 * query TnT2 is built from — `tag:'<ship>' -status:cancelled -tag:'Reship'` (PivotAnalytics.gs
 * `paFetchCohort_`, same string) — so `cohort.length` IS that population, in hand, at zero extra
 * cost. Live proof: this file measured 2324 / 2316 and TnT2 published 2324 / 2316. Copying TnT2's
 * cell would inherit that tab's write timing and break if its row ever moves; recomputing cannot.
 * TnT2 is still READ, purely as an independent CROSS-CHECK, and a mismatch is reported.
 *
 * `Arrived` is MIRRORED from `Lost in Transit`, and mirroring is the SAFER choice for this row
 * specifically: `arrived` is not a Shopify-only fact. PivotAnalytics derives it from fulfillment
 * event trees plus ParcelPanel, whose quota is 2,500 calls/week ACCOUNT-WIDE (D28). This file's
 * cohort query is deliberately light and fetches no fulfillments, so recomputing here would mean
 * rebuilding that pipeline AND spending the shared PP quota a second time — and any drift between
 * the two derivations would put two different numbers under the same label on two tabs. One
 * derivation, published once, mirrored. `Lost in Transit` is refreshed by the same daily
 * walk-forward job, so the mirror inherits a fresh value rather than a stale one.
 *
 * 🔴 THE MIRROR IS KEYED BY HEADER TEXT AND COLUMN-A LABEL, NEVER BY INDEX. Both the ship-week
 * column and the row are looked up by name on every read, so inserting a row or a cohort column on
 * the source tab cannot silently re-point this at the wrong number — it just fails to find it and
 * writes nothing. A missing source, a missing column, or a blank source cell all return null and
 * leave the cell BLANK: blank ≠ zero, and cohorts that predate the source tab must stay empty.
 */
var NT_MIRROR = {
  total: { tab: 'TnT2', label: 'Total Shipments' },
  arrived: { tab: 'Lost in Transit', label: 'Arrived' }
};

/** One cell from another tab, resolved by ship-week HEADER + column-A LABEL. null = not available. */
function ntMirrorCell_(tabName, label, shipWeek) {
  var sh = SpreadsheetApp.openById(EXC_HOST_SHEET_ID).getSheetByName(tabName);
  if (!sh) return null;
  var lastCol = Math.max(1, sh.getLastColumn()), lastRow = Math.max(1, sh.getLastRow());
  var headers = sh.getRange(1, 1, 1, lastCol).getValues()[0]
                  .map(function (h) { return String(h).trim(); });
  var col = headers.indexOf(shipWeek) + 1;
  if (col < 1) return null;                       // no such cohort column on the source tab
  var labels = sh.getRange(1, 1, lastRow, 1).getValues()
                 .map(function (r) { return String(r[0]).replace(/\s+/g, ' ').trim(); });
  var row = labels.indexOf(label) + 1;
  if (row < 1) return null;                       // the source tab's layout moved — fail, do not guess
  var raw = String(sh.getRange(row, col).getValue()).trim();
  if (raw === '') return null;                    // blank source stays blank here (blank != zero)
  var n = Number(raw.replace(/[^0-9.]/g, ''));
  return isFinite(n) ? n : null;
}

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

/**
 * 🔴 `allowOlder` EXISTS BECAUSE THE BACKFILL ENTRY POINT COULD NOT REACH A BACKFILL TARGET (found
 * 2026-08-19). `ntBackfillFrozen` documents itself as "one-time fill of an already-frozen column
 * (B–E)", but every one of those columns is by definition NOT the rightmost, so the
 * NT_ASSERT_NOT_RIGHTMOST guard below rejected all of them — the function could only ever target the
 * column it was written to avoid. It was unusable as shipped and nothing said so.
 * The guard's real job is stopping the DAILY REFRESH from landing on an old column, and that job is
 * untouched: `allowOlder` is passed ONLY by `ntBackfillFrozen`, which names its target explicitly,
 * is double-gated on NOTIFICATIONS_BACKFILL=1 AND NOTIFICATIONS_WRITE=1, defaults to dry, and writes
 * EMPTY cells only. On the refresh path the assert still fires exactly as before.
 */
function ntCurrentCol_(sheet, shipWeek, allowAppend, allowOlder) {
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
  if (col === rightmost || allowOlder === true) return col;
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
  var keys = ['own||Total Shipments', 'own||Arrived'];
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
  if (missing.length) {
    throw new Error('NT_ASSERT_ROW_SHAPE: the ' + NT_TAB + ' tab is missing ' + missing.length +
                    ' expected row(s): ' + missing.join(' ; ') + '. The tab layout changed — ' +
                    'refusing to write rather than guessing which row is which.');
  }
}

/**
 * 🔴 THE ROWS WHOSE DISAGREEMENT WITH A PRE-EXISTING CELL IS WORTH REPORTING (Kurt 2026-08-19).
 * Both were hand-typed mirrors before the ownership handover, so an existing value in a MATURED
 * column may be a human's — it is left alone, and if what we compute differs, that is REPORTED
 * rather than corrected. The disagreement is the evidence that the mirror drifted; overwriting it
 * hides how far.
 *
 * 🔴 THIS APPLIES IN BACKFILL MODE ONLY, AND THAT IS DELIBERATE. Holding these cells on the CURRENT
 * column would freeze a still-moving number the first time it is written — `Arrived` on a 2-day-old
 * cohort is mid-flight (1063 of 2324 on 08-17), so a write-once-then-never-touch rule would stamp a
 * mid-flight value and leave it there forever. That is EXACTLY the bug this whole change exists to
 * fix: the typed 2075 on 08-03 was mid-flight when it was copied and never refreshed, and the
 * authority now says 2268. So the current, un-matured column REFRESHES in place like every other row
 * here (walk-forward, the tab's existing doctrine), and only a matured column — reachable solely
 * through `ntBackfillFrozen`, which is double-gated and empty-only — holds what it already has.
 */
var NT_BLANK_ONLY_KEYS = { 'own||Total Shipments': 1, 'own||Arrived': 1 };

/** Per-cell, owned rows only, keyed. `emptyOnly` is the frozen-column backfill mode. */
function ntWriteOwned_(sheet, col, map, values, dry, emptyOnly) {
  var wrote = 0, skipped = 0, held = 0, notes = [];
  ntOwnedKeys_().forEach(function (k) {
    if (!Object.prototype.hasOwnProperty.call(values, k)) return;   // blank != zero: not computed
    var row = map[k], v = values[k];
    var cell = sheet.getRange(row, col);
    var existing = String(cell.getValue()).trim();

    if (emptyOnly && existing !== '') {
      // A matured column keeps what it has. For the two formerly hand-mirrored rows, say so loudly
      // when our value differs — that gap is the only signal that the old manual mirror drifted.
      if (NT_BLANK_ONLY_KEYS[k]) {
        var have = Number(existing.replace(/[^0-9.]/g, ''));
        if (typeof v === 'number' && isFinite(have) && have !== v) {
          held++;
          notes.push('DISAGREEMENT on "' + k.replace('own||', '') + '" (column ' + col + '): the ' +
                     'sheet holds ' + have + ', this run computes ' + v + ' (' +
                     (v - have > 0 ? '+' : '') + (v - have) + '). The existing value is KEPT — this ' +
                     'is a matured column and a human may have typed it. Reported, not corrected: a ' +
                     'hand mirror that drifted is evidence, and overwriting it hides how far. Clear ' +
                     'the cell to hand it to the script.');
        }
      }
      skipped++;
      return;
    }

    if (dry) { ntLog_('  [dry] ' + NT_TAB + '!' + cell.getA1Notation() + '  ' + k + ' = ' + v); }
    else {
      cell.setValue(v === null || v === undefined ? '' : v);
      if (typeof v === 'number') cell.setNumberFormat('0');
    }
    wrote++;
  });
  if (skipped) ntLog_('  backfill: ' + skipped + ' cell(s) already had a value — left alone (Kurt-owned)');
  if (held) ntLog_('  fill-blanks-only: ' + held + ' cell(s) already had a value — left alone');
  return { wrote: wrote, notes: notes };
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
  // ---- Total Shipments / Arrived: SCRIPT-OWNED as of Kurt 2026-08-19 -------------------------
  // 🔴 blank != zero — a cohort that returns no orders writes NOTHING rather than a 0 that would
  // then serve as a denominator and make every percent read 0.00%.
  if (cohort.length > 0) out['own||Total Shipments'] = cohort.length;

  // Independent cross-check. The denominator must not come from the thing being measured, so the
  // published TnT2 value is read and COMPARED rather than trusted or copied.
  var xcheck = ntMirrorCell_(NT_MIRROR.total.tab, NT_MIRROR.total.label, shipWeek);
  if (xcheck !== null && cohort.length > 0 && xcheck !== cohort.length) {
    notes.push('CROSS-CHECK: this run computes Total Shipments ' + cohort.length + ' but ' +
               NT_MIRROR.total.tab + ' publishes ' + xcheck + ' for the same cohort tag from the ' +
               'same Shopify query. They should be identical; a gap means one of the two tabs was ' +
               'measured at a different moment or the cohort query drifted. Investigate before ' +
               'trusting either percent.');
  } else if (xcheck !== null && xcheck === cohort.length) {
    ntLog_('  cross-check OK: ' + NT_MIRROR.total.tab + ' publishes the same Total Shipments (' + xcheck + ')');
  }

  // `Arrived` is a delivery fact this file does not compute — mirrored by name from its authority.
  var arrived = ntMirrorCell_(NT_MIRROR.arrived.tab, NT_MIRROR.arrived.label, shipWeek);
  if (arrived !== null) {
    out['own||Arrived'] = arrived;
    ntLog_('  Arrived ' + arrived + ' mirrored from ' + NT_MIRROR.arrived.tab + ' (by header + label)');
  } else {
    notes.push('Arrived left BLANK: ' + NT_MIRROR.arrived.tab + ' has no value for ' + shipWeek +
               ' (no such column, no such row, or the source cell is empty). Blank != zero.');
  }

  // 🔴 denominator sanity: prefer what is PUBLISHED on this tab; fall back to what we just computed
  // for it. If a human typed a value we distrust, percents are refused rather than written against a
  // denominator we would not stand behind.
  var denomOk = true, denomSrc = NT_TAB + '!Total Shipments (published)';
  if (!sheetTotal && cohort.length > 0) {
    sheetTotal = cohort.length;
    denomSrc = 'computed this run (same cohort query as ' + NT_MIRROR.total.tab + ') and written to the row';
    ntLog_('  denominator ' + sheetTotal + ' — ' + denomSrc);
  }
  if (!sheetTotal) { denomOk = false; notes.push('Total Shipments is blank and the cohort is empty — no percents written'); }
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

  // ---- resumable sweep: load the checkpoint, validate it, continue or restart ----------------
  // 🔴 The tracked flow set is the two PINNED flows plus the informational one. Nothing else is
  // retained (see ntSweepMetric_), and the same list is what the state signature is taken over.
  var tracked = {};
  ['shipped', 'delivered'].forEach(function (s) { if (flows[s]) tracked[flows[s]] = s; });
  tracked[NT_FLOW_INFO.id] = 'info';
  var sig = ntSig_(shipWeek, lo, hi, Object.keys(tracked));

  var state = ntLoadState_(shipWeek);
  if (!ntSigEq_(state.sig, sig)) {
    if (state.sig) {
      ntLog_('  ⚠️ sweep state DISCARDED — ' + ntSigWhy_(state.sig, sig) + '. A set accumulated ' +
             'under different pins/window is not a partial answer to this question, it is a wrong ' +
             'one. Restarting from page 0.');
    }
    state = { sig: sig, vol: {}, metrics: {} };
  } else {
    state.sig = sig;
  }

  // 🔴 A COMPLETE result is reused only for the REST OF THE SAME ET CALENDAR DAY. This tab is a
  // daily walk-forward artifact, so the day is its natural grain: a duration TTL would let a 23:50
  // sweep be reused at 01:10 and freeze a day's movement into the next column refresh.
  var today = ntToday_();
  Object.keys(state.metrics).forEach(function (k) {
    var m = state.metrics[k];
    if (m && m.complete && m.day !== today) {
      ntLog_('  sweep of "' + NT_METRIC[k] + '" completed on ' + m.day + ', not today (' + today +
             ') — re-measuring from page 0 so the column moves forward');
      delete state.metrics[k];
    }
  });

  // 🔴 ORDER BY COST, CHEAPEST FIRST. With a hard budget, sweeping the 830-page metric first would
  // starve two 40/79-page metrics that fit — the whole point of resuming is that the runs that can
  // finish, do. Unknown volume sorts last: it might be the expensive one.
  var order = ['email', 'sms', 'smsEngaged'].filter(function (k) { return !!metrics[NT_METRIC[k]]; });
  var costOf = {};
  order.forEach(function (k) {
    var m = state.metrics[k];
    // 🔴 the probe is SKIPPED once a sweep is under way or done — the decision it feeds is taken.
    if (m && (m.pages > 0 || m.complete || m.declined)) {
      costOf[k] = m.total || (m.pages || 0);
      return;
    }
    var vol = ntVolumeCached_(k, metrics[NT_METRIC[k]], lo, hi, state);
    costOf[k] = (vol === null) ? 1e9 : Math.ceil(vol / NT_PAGE);
    if (vol !== null) {
      ntLog_('  volume "' + NT_METRIC[k] + '" in window: ' + vol + ' events -> ~' + costOf[k] +
             ' pages (cap ' + NT_MAX_PAGES + ' total, ' + NT_MAX_PAGES_PER_RUN + '/run)');
    }
  });
  order.sort(function (a, b) { return costOf[a] - costOf[b]; });
  ntLog_('  sweep order (cheapest first): ' + order.map(function (k) {
    return NT_METRIC[k] + ' ~' + (costOf[k] >= 1e9 ? '?' : costOf[k]) + 'p';
  }).join(' -> '));

  // ---- work the metrics in order, banking progress; TIME is the first bound ------------------
  var ev = {}, resumed = [];
  var sweepDeadline = deadline - NT_SAVE_RESERVE_MS;
  order.forEach(function (k) {
    var id = metrics[NT_METRIC[k]];
    var m = state.metrics[k] || null;

    if (m && m.complete) {
      ev[k] = { byFlow: m.sets, n: m.events };
      ntLog_('  "' + NT_METRIC[k] + '": COMPLETE from state (' + m.pages + ' pages, ' +
             ntSetSize_(m.sets) + ' profiles, swept ' + m.updatedAt + ') — no fetch this run');
      return;
    }

    // feasibility decline — unchanged semantics, now recorded so it is decided once per cohort.
    if (!m && costOf[k] < 1e9 && costOf[k] > NT_MAX_PAGES) {
      state.metrics[k] = { declined: true, complete: false, total: costOf[k], pages: 0, events: 0,
                           sets: {}, day: today, why: 'over the ' + NT_MAX_PAGES + '-page cap' };
      notes.push('SWEEP DECLINED for "' + NT_METRIC[k] + '": ~' + costOf[k] + ' pages, over the ' +
                 NT_MAX_PAGES + '-page total cap. Klaviyo /events has NO flow filter, so there is ' +
                 'no cheaper cut — every row fed by this metric is left BLANK (blank != zero). ' +
                 'Resuming does not fix this either: it would take ~' +
                 Math.ceil(costOf[k] / NT_MAX_PAGES_PER_RUN) + ' runs. Raising NT_MAX_PAGES is a ' +
                 'decision about how many runs a cohort costs — Kurt\'s, not a tuning knob.');
      ev[k] = null;
      return;
    }
    // 🔴 RE-STATE THE DECLINE EVERY RUN. Recording it in state so the probe can be skipped must not
    // also silence it: a blank row whose reason was logged once, days ago, reads as an unexplained
    // gap — which is how a deliberate decline gets mistaken for a broken job.
    if (m && m.declined) {
      notes.push('SWEEP DECLINED for "' + NT_METRIC[k] + '" (decided for this cohort on ' +
                 m.day + '): ~' + m.total + ' pages, over the ' + NT_MAX_PAGES + '-page total cap. ' +
                 'Rows fed by this metric stay BLANK (blank != zero).');
      ev[k] = null;
      return;
    }

    if (new Date().getTime() > sweepDeadline) {
      notes.push('NOT STARTED this run: "' + NT_METRIC[k] + '" — the budget went to the metrics ' +
                 'ahead of it. Its rows stay BLANK; run again to continue.');
      ev[k] = null;
      return;
    }

    var before = (m && m.pages) || 0;
    var r = ntSweepMetric_(id, lo, hi, tracked, m, sweepDeadline);
    r.total = (m && m.total) || costOf[k];
    r.day = today;
    state.metrics[k] = r;

    var tot = (r.total >= 1e9) ? 0 : r.total;
    var pctDone = tot ? Math.min(100, Math.round((r.pages / tot) * 100)) : 0;
    if (r.complete) {
      ev[k] = { byFlow: r.sets, n: r.events };
      ntLog_('  "' + NT_METRIC[k] + '": COMPLETE — ' + r.pages + ' pages (' + (r.pages - before) +
             ' this run), ' + r.events + ' tracked-flow events, ' + ntSetSize_(r.sets) + ' profiles');
    } else {
      ev[k] = null;
      resumed.push(k);
      notes.push('IN PROGRESS "' + NT_METRIC[k] + '": pages ' + r.pages + '/' +
                 (tot ? tot : '?') + (tot ? ', ' + pctDone + '%' : '') + ' — stopped on ' + r.why +
                 ', checkpointed, resuming next run. Its rows stay BLANK (blank != zero); a ' +
                 'partial count would look finished.');
      ntLog_('  "' + NT_METRIC[k] + '": pages ' + r.pages + '/' + (tot ? tot : '?') +
             (tot ? ', ' + pctDone + '%' : '') + ' — resuming next run (' + r.why + ')');
    }
  });

  // 🔴 CHECKPOINT BEFORE ANY SHEET WRITE and before any assert can throw. The whole point is that a
  // run which dies later still banks its pages; saving at the end of ntMeasure_ would lose them to
  // the very partition/overcount asserts that exist to stop a bad write.
  ntSaveState_(shipWeek, state);
  ntLog_('  checkpoint saved to ' + NT_STATE_TAB + ' (' + Object.keys(state.metrics).length +
         ' metric record(s))');

  var sendTimes = [];       // {sec, email} for every SEND we counted (email + sms, all sections)

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
        sendTimes.push({ sec: hits[em], email: em });       // epoch seconds — the checkpoint's grain
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
      var b = ntDayNight_(ntIsoFromSec_(s.sec), zoneByEmail[s.email]);
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

function ntRefreshOne_(shipWeek, dry, allowAppend, emptyOnly, allowOlder) {
  var sheet = SpreadsheetApp.openById(EXC_HOST_SHEET_ID).getSheetByName(NT_TAB);
  if (!sheet) throw new Error('NT_ASSERT_NO_TAB: no tab named ' + NT_TAB);
  ntAssertColumns_(sheet);
  var map = ntRowMap_(sheet);
  ntAssertShape_(map);                                 // throws BEFORE anything is written
  var col = ntCurrentCol_(sheet, shipWeek, allowAppend, allowOlder);
  if (!col) { ntLog_('  ' + shipWeek + ': no column and append not allowed — skipped'); return null; }

  var totalRaw = sheet.getRange(map['own||Total Shipments'], col).getValue();
  var sheetTotal = Number(String(totalRaw).replace(/[^0-9.]/g, '')) || 0;

  var deadline = new Date().getTime() + NT_TIME_BUDGET_MS;
  var res = ntMeasure_(shipWeek, sheetTotal, deadline);
  res.notes.forEach(function (n) { ntLog_('  ⚠️ ' + n); });

  var w = ntWriteOwned_(sheet, col, map, res.values, dry, emptyOnly);
  // 🔴 A disagreement between a hand-typed cell and the computed value is surfaced with the same
  // ⚠️ prefix as every other note. It is found DURING the write, so it cannot be reported earlier.
  w.notes.forEach(function (n) { ntLog_('  ⚠️ ' + n); });
  var notes = res.notes.concat(w.notes);
  ntAssertColumns_(sheet);
  ntLog_('  ' + shipWeek + ' col ' + col + ': ' + w.wrote + ' cell(s) ' + (dry ? 'previewed' : 'written'));
  return { shipWeek: shipWeek, col: col, wrote: w.wrote, values: res.values, notes: notes };
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
  // allowOlder=true: a frozen column is never the rightmost one — see ntCurrentCol_.
  return ntRefreshOne_(shipWeek, !wet, false, true, true);
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

/**
 * How far each metric's sweep has got, and why it is where it is. NO writes, no fetches — reads the
 * checkpoint tab only, so it is safe to run at any point including mid-sweep.
 */
function ntCheckSweepState(shipWeek) {
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) shipWeek = '';
  var cur = shipWeek || ntCurrentShipWeek_();
  var st = ntLoadState_(cur);
  ntLog_('=== sweep state for ' + cur + ' (tab ' + NT_STATE_TAB + ') ===');
  if (!st.sig) { ntLog_('  no stored state — the next run starts from page 0'); return st; }
  ntLog_('  signature: ver ' + st.sig.ver + ' | window ' + st.sig.lo + ' .. ' + st.sig.hi +
         ' | flows ' + st.sig.flows);
  Object.keys(st.vol).forEach(function (k) {
    ntLog_('  volume cache "' + NT_METRIC[k] + '": ' + st.vol[k].events + ' events (probed ' +
           st.vol[k].at + ')');
  });
  ['email', 'sms', 'smsEngaged'].forEach(function (k) {
    var m = st.metrics[k];
    if (!m) { ntLog_('  "' + NT_METRIC[k] + '": nothing stored'); return; }
    var tot = m.total || 0;
    ntLog_('  "' + NT_METRIC[k] + '": ' + (m.complete ? 'COMPLETE' : m.declined ? 'DECLINED' : 'IN PROGRESS') +
           '  pages ' + m.pages + '/' + (tot || '?') +
           (tot ? ' (' + Math.min(100, Math.round((m.pages / tot) * 100)) + '%)' : '') +
           '  profiles ' + ntSetSize_(m.sets || {}) + '  chunks ' + m.chunks +
           '  day ' + m.day + '  ' + (m.why || '') + (m.next ? '  [cursor stored]' : ''));
  });
  return st;
}

/**
 * Throw the checkpoint away for one cohort so the next run re-sweeps from page 0. The ordinary way
 * to invalidate is to change something the SIGNATURE covers — this is the manual escape hatch for
 * the case where the stored pages are suspect but nothing in the signature moved. Writes only to
 * the hidden state tab; the Notifications tab is never touched.
 */
function ntResetSweepState(shipWeek) {
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) {
    throw new Error('NT_ASSERT_RESET_ARG: pass an explicit _SHIP_YYYY-MM-DD — this discards ' +
                    'measured pages and must never be aimed by accident');
  }
  var sh = ntStateSheet_();
  var last = sh.getLastRow();
  if (last < 2) { ntLog_('  nothing stored'); return 0; }
  var rows = sh.getRange(2, 1, last - 1, NT_STATE_COLS.length).getValues();
  var keep = rows.filter(function (r) { return String(r[0]) && String(r[0]) !== shipWeek; });
  var dropped = rows.filter(function (r) { return String(r[0]) === shipWeek; }).length;
  var outRows = [NT_STATE_COLS.slice()].concat(keep);
  sh.getRange(1, 1, outRows.length, NT_STATE_COLS.length).setValues(outRows);
  if (sh.getLastRow() > outRows.length) {
    sh.getRange(outRows.length + 1, 1, sh.getLastRow() - outRows.length, NT_STATE_COLS.length).clearContent();
  }
  ntLog_('  dropped ' + dropped + ' state row(s) for ' + shipWeek + ' — the next run restarts it');
  return dropped;
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
 *
 * 🔴 ONE RUN NO LONGER FINISHES A COHORT, AND IT IS NOT SUPPOSED TO (measured 2026-08-19, live,
 * read-only, against `_SHIP_2026-08-17`'s real window):
 *     Clicked Text Message    40 pages @ 2.75s/page = 110s   -> fits in ONE run
 *     Received Text Message   79 pages @ 4.59s/page = 363s   -> EXCEEDS the whole 360s ceiling alone
 *     Received Email         830 pages                       -> declined, unchanged
 * That is why the 08-19 run wrote nothing: the cheaper SMS metric was never the problem, the
 * expensive one cannot be done in one invocation at any budget. With the sweep ordered cheapest-first
 * and checkpointed, run 1 finishes `Clicked Text Message` and both `SMS Engaged` rows land; the
 * `SMS Sent` rows and `Time of Sending` land when `Received Text Message` finishes a few runs later.
 * The daily trigger gets there on its own; Kurt can also just press ntRefreshCurrentColumn again and
 * each press picks up exactly where the last stopped. `ntCheckSweepState()` says how far it has got.
 *
 * ⚠️ UNVERIFIED: the per-run fixed overhead (the Shopify cohort pull — 2,324 orders at 25/page ≈ 93
 * GraphQL calls — plus /metrics/ and /flows/) has NOT been measured; the Shopify credentials live in
 * Script Properties and are not reachable from a local probe. It comes off the same 240s budget, so
 * the number of runs `Received Text Message` needs is 3 at 60s of overhead and 5 at 120s. The first
 * live run settles it — read `total …s of the 360s ceiling` and the `pages n/79` line together.
 */
function ntListTriggers() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    ntLog_('  ' + t.getHandlerFunction() + '  ' + t.getEventType() + '  ' + t.getTriggerSource());
  });
}
