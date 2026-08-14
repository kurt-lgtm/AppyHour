/**
 * PivotAnalytics.gs — headless WALK-FORWARD refresh of the CURRENT ship-week column on Dan's three
 * cohort-analytics tabs: TnT2 · Lost in Transit · Routing Match.
 *
 * SSOT: AppyHour/ShippingReports/RESHIP_REPORT_RULES.md → "Cohort-analytics tabs".
 * Port of the python writer proven on _SHIP_2026-08-03 (2,305 orders).
 *
 * 🔴 GLOBAL-NAMESPACE COLLISION HAZARD. Apps Script loads every .gs into ONE global scope, so a
 * duplicated top-level name silently overrides across files — a "disabled" file is NOT inert.
 * This file previously defined `normCarrier_`, which ALSO exists in Code.gs:954 with DIFFERENT
 * behavior (Code.gs keeps OnTrac as its own bucket; this file merged it into LaserShip) — that
 * would have silently changed the live hourly reship report's carrier bucketing the moment this
 * file was first pushed. Verified 2026-08-07: the deployed project held only appsscript/Code/
 * Exceptions, so this was caught BEFORE deployment. Every symbol here is now `pa`-prefixed and this
 * file defines NOTHING that Code.gs / Exceptions.gs / PivotSheet.gs define. Do not add an
 * unprefixed function here. (Latent, pre-existing, NOT fixed here: `refresh` and `iso_` are
 * duplicated between Code.gs and the also-unpushed PivotSheet.gs — pushing that file would hijack
 * `refresh`.)
 *
 * 🔴 DRY RUN BY DEFAULT. Writes nothing until Script Property PIVOT_ANALYTICS_WRITE === '1'.
 * Dry run logs every intended cell write via Logger. Kurt reviews, then flips the property.
 *
 * Script Properties: SHOPIFY_STORE, SHOPIFY_TOKEN, PARCELPANEL_API_KEY (all already set),
 *   PIVOT_ANALYTICS_WRITE ('1' to actually write; anything else = dry run).
 *
 * NO trigger is installed from here (the API cannot). When live-ready Kurt installs a weekly
 * time-trigger on `refreshCurrentColumn` in the editor, same as Exceptions.
 */

var PA_TABS = { tnt2: 'TnT2', lost: 'Lost in Transit', routing: 'Routing Match' };
var PA_IMMATURE = 'n/a (immature)';
var PA_TZ = 'America/New_York';
var PA_SLA = 2;
var PA_ACTIVE_HRS = 24;
var PA_NO_TAG = '(no routing tag)';
var PA_PAGE = 25;
// Daily-trigger guards (Kurt 2026-08-07). PP budget is 2,500 calls/WEEK and Exceptions' hourly job
// is the dominant consumer; this leg must stay a rounding error against it (~45 × 4 ≈ 180/wk).
var PA_PP_MIN_AGE_DAYS = 3;    // Tue/Wed run Shopify-only — the rescue set is ~the whole cohort then
var PA_PP_MAX_CALLS = 200;     // hard backstop per RUN (shared across both cohort legs)
// 🔴 MATURITY (D15, Kurt 2026-08-07). A cohort column is SCRIPT-OWNED and self-heals daily from age
// 1 until it hits this age, then FREEZES — the script refuses it forever after and it becomes
// Kurt-owned. This is what makes stale wrongness recoverable: a box frozen as 3+ Day / Not Arrived
// that later proves delivered gets corrected while the column is still in-window.
var PA_MATURITY_DAYS = 10;
// 🔴 THREE OBSERVATIONS under `3+ Day Shipments` (D16, Kurt-approved 2026-08-07). Order matters —
// it is the on-sheet row order. All three are INSIDE 3+ Day, none is summed into any total, and
// together they PARTITION Not Arrived. NOTE: the words "Lost in Transit" appear NOWHERE on TnT2
// by request — that phrasing is what Dan reacts to. The Lost in Transit TAB keeps its own name.
var PA_OBS = ['still moving (4+ days)',            // undelivered, a real scan <24h — moving
              'no scan in 24h+ (investigating)',   // scanned, then silent >=24h
              'never picked up by carrier'];       // zero carrier scans ever

/** 🔴 A real carrier scan. CONFIRMED is emitted at LABEL CREATION and is NOT a scan — including it
 * makes every never-collected box look like it moved ("has events" is the wrong filter). */
var PA_MOVE = { IN_TRANSIT: 1, OUT_FOR_DELIVERY: 1, ATTEMPTED_DELIVERY: 1, READY_FOR_PICKUP: 1, PICKED_UP: 1 };

/**
 * 🔴 COLUMN-CREATION PROCEDURE (Kurt 2026-08-13), in this order and no other:
 *   1. copy the PREVIOUS column's formatting  2. stamp the header  3. write values  4. assert.
 * Formatting FIRST so a failure at any later step leaves a column that at least LOOKS like the
 * others rather than an unformatted orphan a reader can't tell from a scratch column. Copies
 * number formats (counts NUMBER "0", rate rows PERCENT "0.00%"), font/bold, fills, borders,
 * horizontal/vertical alignment, indent — plus the column WIDTH, which copyTo does not carry.
 * `formatOnly` means no value is touched: the 65 rate-row formulas in the source column are NOT
 * copied (they are per-column relative formulas and Kurt owns them), only their appearance.
 */
function paCopyFormatFromPrev_(sheet, col) {
  if (col < 2) return;
  var rows = Math.max(1, sheet.getMaxRows());
  try {
    sheet.getRange(1, col - 1, rows, 1).copyTo(sheet.getRange(1, col, rows, 1), { formatOnly: true });
    sheet.setColumnWidth(col, sheet.getColumnWidth(col - 1));
    paLog_('  format: ' + sheet.getName() + ' col ' + (col - 1) + ' -> ' + col + ' (formatOnly + width)');
  } catch (e) {
    // A format copy failing must not block the data write — but it must never be silent.
    paLog_('  ⚠️ format copy failed on ' + sheet.getName() + ' col ' + col + ': ' + e);
  }
}

/**
 * 🔴 HARD ASSERTS, run BEFORE any write to a tab. Throws a NAMED error rather than writing.
 * (a) every data column (2..lastCol) carries a row-1 header — a headerless column is invisible to
 *     `headers.indexOf(shipWeek)`, which is exactly how TnT2 grew TWO wk0810 columns on 2026-08-12;
 * (b) exactly ONE column per ship tag — a duplicate tag means a reader cannot tell which is current.
 */
function paAssertColumns_(sheet) {
  var lastCol = Math.max(1, sheet.getLastColumn()), lastRow = Math.max(1, sheet.getLastRow());
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(function (h) { return String(h).trim(); });
  var grid = sheet.getRange(1, 1, lastRow, lastCol).getValues();
  var seen = {}, dup = [], headerless = [];
  for (var c = 2; c <= lastCol; c++) {
    var h = headers[c - 1];
    if (h) {
      if (seen[h]) dup.push(h + ' (cols ' + seen[h] + ' + ' + c + ')');
      seen[h] = c;
      continue;
    }
    var hasData = false;
    for (var r = 1; r < lastRow; r++) {           // row 1 is the header row itself
      if (String(grid[r][c - 1]).trim() !== '') { hasData = true; break; }
    }
    if (hasData) headerless.push(c);
  }
  if (headerless.length) {
    throw new Error('PA_ASSERT_HEADERLESS_COLUMN: ' + sheet.getName() + ' column(s) ' +
                    headerless.join(', ') + ' hold data with no row-1 header. Refusing to write — ' +
                    'a headerless column is invisible to column lookup and the next run would append another.');
  }
  if (dup.length) {
    throw new Error('PA_ASSERT_DUPLICATE_SHIP_TAG: ' + sheet.getName() + ' has more than one column ' +
                    'for ' + dup.join('; ') + '. Refusing to write — exactly one column per ship tag.');
  }
}

function paWriteArmed_() {
  return PropertiesService.getScriptProperties().getProperty('PIVOT_ANALYTICS_WRITE') === '1';
}
function paLog_(msg) { Logger.log(msg); }

/** 🔴 Canonical carrier name is OnTrac; LaserShip is the ALIAS (Kurt 2026-08-07). Both fold to
 * OnTrac and no LaserShip bucket exists. Local to this file — see the collision note above. */
function paCarrier_(raw) {
  var s = String(raw || '').toLowerCase();
  if (s.indexOf('lasership') >= 0 || s.indexOf('ontrac') >= 0) return 'OnTrac';
  if (s.indexOf('veho') >= 0) return 'Veho';
  if (s.indexOf('fedex') >= 0) return 'FedEx';
  if (s.indexOf('ups') >= 0) return 'UPS';
  return raw ? String(raw) : 'Unknown';   // never fabricate a carrier
}

/** Box type comes from the LINE-ITEM SKUs, not from tags. */
function paBox_(skus) {
  var up = (skus || []).map(function (s) { return String(s || '').toUpperCase(); }).join('|');
  if (up.indexOf('LCUST-TRAY') >= 0) return 'Large Tray';
  if (up.indexOf('MCUST-TRAY') >= 0) return 'Medium Tray';
  return 'Regular Box';
}

/**
 * 🔴 Routing tag parse. Format is `!<Carrier> <Service> - <Hub>_AHB!` (`!ANY FedEx - <Hub>_AHB!`
 * for pins). An order carries MULTIPLE _AHB! tags: ONE assignment plus several
 * `!NO <carrier> - <Hub>_AHB!` EXCLUSIONS. A regex .search() over the joined tag string returns
 * whichever comes first and silently yields an EXCLUDED hub — on wk0803, 209 of 2,305 orders carry
 * exclusions ONLY and would have been stamped with a hub the engine explicitly ruled out.
 * So: fullmatch each tag individually, DROP `NO `, and require EXACTLY ONE assignment.
 */
var PA_AHB = /^!([^!]*?)\s*-\s*([A-Za-z]+)_AHB!$/;
var PA_RESIDUAL_HUB = 'RMFG choice (2+ hubs open)';

function paAssigned_(tags) {
  var picks = [];
  (tags || []).forEach(function (t) {
    var m = String(t).trim().match(PA_AHB);
    if (m && String(m[1]).trim().toUpperCase().indexOf('NO ') !== 0) picks.push(m);
  });
  if (picks.length !== 1) return { carrier: '', hub: PA_NO_TAG };
  return { carrier: paCarrier_(picks[0][1]), hub: String(picks[0][2]).trim() };
}

/**
 * 🔴 HUB ATTRIBUTION FOR EXCLUSION-ONLY ORDERS (D17, Kurt 2026-08-07: "if its one hub open, then
 * just put it in the hub category"). Subtract the order's `!NO` fences from the observed lane
 * universe and count the hubs left standing:
 *   exactly 1 open -> that hub. It is effectively assigned; parking it in a residual bucket hid
 *                     real volume — all 64 wk0803 cases are Dallas, and those FedEx long-hauls
 *                     were always Dallas's late boxes.
 *   >= 2 open      -> PA_RESIDUAL_HUB: RMFG genuinely chose.
 * Tag SHAPE is never special-cased — 9 orders carry an engine `!ANY - Dallas_AHB!` intent
 * expressed on Shopify as a fence stack, and open-hub counting lands them correctly anyway.
 */
function paLanesFrom_(tags, lanes) {
  (tags || []).forEach(function (t) {
    var m = String(t).trim().match(PA_AHB);
    if (!m) return;
    var c = paCarrier_(String(m[1]).replace(/^(NO|ANY)\s+/i, ''));
    // every <carrier, hub> in ANY tag — assignment OR fence — is a real lane. DERIVED, never
    // hardcoded, so a new hub (NJ) falls in automatically.
    if (c && c !== 'Unknown') lanes[c + '@' + String(m[2]).trim()] = 1;
  });
}

function paOpenHubs_(tags, lanes) {
  var fenced = {};
  (tags || []).forEach(function (t) {
    var m = String(t).trim().match(PA_AHB);
    if (!m) return;
    var pre = String(m[1]).trim();
    if (pre.toUpperCase().indexOf('NO ') !== 0) return;
    var c = paCarrier_(pre.substring(3));
    if (c && c !== 'Unknown') fenced[c + '@' + String(m[2]).trim()] = 1;
  });
  var hubs = {}, out = [];
  Object.keys(lanes).forEach(function (lane) {
    if (fenced[lane]) return;
    hubs[lane.split('@')[1]] = 1;
  });
  Object.keys(hubs).forEach(function (h) { out.push(h); });
  return out.sort();
}

/** 🔴 Shopify happenedAt is UTC. Taking the date without converting to ET adds a phantom day to
 * every evening delivery — on wk0803 that shifted 471 rows and DOUBLED the late count (146 vs 62). */
function paEtDate_(iso) {
  if (!iso) return null;
  return Utilities.formatDate(new Date(iso), PA_TZ, 'yyyy-MM-dd');
}
function paDayDiff_(a, b) {
  return Math.round((new Date(b + 'T00:00:00Z') - new Date(a + 'T00:00:00Z')) / 86400000);
}
/** Today in ET, as the same `yyyy-MM-dd` grain every other date here uses. */
function paTodayEt_() {
  return Utilities.formatDate(new Date(), PA_TZ, 'yyyy-MM-dd');
}
function paHoursSince_(iso) {
  return iso ? (new Date().getTime() - new Date(iso).getTime()) / 3600000 : null;
}

// ---------------------------------------------------------------- fetch

function paFetchCohort_(shipWeek) {
  var q =
    'query($q:String!,$cursor:String){ orders(first:' + PA_PAGE + ', query:$q, after:$cursor){' +
    ' pageInfo{hasNextPage endCursor} edges{node{ name tags shippingAddress{ provinceCode }' +
    ' lineItems(first:50){ edges{ node{ sku } } }' +
    ' fulfillments(first:10){ displayStatus trackingInfo{ company number }' +
    ' events(first:50, sortKey:HAPPENED_AT){ edges{ node{ status happenedAt } } } } } } } }';
  // 🔴 -tag:'Reship' — reships are a different population and must never enter cohort analytics.
  var qs = "tag:'" + shipWeek + "' -status:cancelled -tag:'Reship'";
  var out = [], cursor = null, lanes = {};
  while (true) {
    var conn = shopifyGql_(q, { q: qs, cursor: cursor }).orders;
    conn.edges.forEach(function (e) {
      var rec = paDerive_(e.node);
      rec.tags = e.node.tags || [];
      paLanesFrom_(rec.tags, lanes);
      out.push(rec);
    });
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
  // second pass: the lane universe is only complete once every order has been seen
  var single = 0;
  out.forEach(function (r) {
    if (r.assigned.hub !== PA_NO_TAG) return;
    var open = paOpenHubs_(r.tags, lanes);
    if (open.length === 1) { r.assigned.hub = open[0]; single += 1; }
    else r.assigned.hub = PA_RESIDUAL_HUB;
  });
  paLog_('  lane universe: ' + Object.keys(lanes).sort().join(', '));
  paLog_('  exclusion-only orders resolved to a single open hub: ' + single);
  return out;
}

/**
 * 🔴 Scan ALL fulfillments — an order can show "Fulfilled (2)" and the DELIVERED one is often not
 * fulfillments[0] (#166044 class); reading index 0 lost 26 deliveries on wk0803.
 * 🔴 displayStatus is a LAGGING ROLLUP — a DELIVERED scan event wins over it. On wk0803 three boxes
 * sat at DELAYED/OUT_FOR_DELIVERY with a DELIVERED scan, confirmed against OnTrac's own pages.
 */
function paDerive_(node) {
  var best = -1, st = 'NO_FULFILLMENT', carrierRaw = '', trk = '', dlv = null, mv = null, last = null;
  (node.fulfillments || []).forEach(function (f) {
    var fd = null, fm = null, fl = null;
    (((f.events || {}).edges) || []).forEach(function (x) {
      var s = x.node.status, t = x.node.happenedAt;
      if (PA_MOVE[s] && (fm === null || t < fm)) fm = t;
      if ((PA_MOVE[s] || s === 'DELIVERED') && (fl === null || t > fl)) fl = t;
      if (s === 'DELIVERED' && (fd === null || t > fd)) fd = t;
    });
    var ti = (f.trackingInfo && f.trackingInfo[0]) || {};
    var rank = (fd || f.displayStatus === 'DELIVERED') ? 2 : ((fm || ti.number) ? 1 : 0);
    if (rank > best) {
      best = rank; st = f.displayStatus || st;
      carrierRaw = ti.company || ''; trk = ti.number || '';
      dlv = fd; mv = fm; last = fl;
    }
  });
  var skus = (((node.lineItems || {}).edges) || []).map(function (x) { return x.node.sku; });
  return {
    order: String(node.name || '').replace(/[^0-9]/g, ''),
    shopifyStatus: st, carrier: paCarrier_(carrierRaw), tracking: trk,
    state: String((node.shippingAddress || {}).provinceCode || '??').toUpperCase(),
    box: paBox_(skus),
    assigned: paAssigned_(node.tags),
    shMove: paEtDate_(mv), shDlv: paEtDate_(dlv), lastScanIso: last,
  };
}

/**
 * ParcelPanel side of the union. Deliberately NOT `ppLookup_` from Code.gs: that helper returns
 * only {carrier, transit} and drops the delivered flag and the raw dates this needs — and it is
 * owned by the reship stream.
 * 🔴 Join on ORDER NUMBER, never tracking_number: FedEx REUSES tracking numbers (a wk0803 tracking
 * resolved to a May shipment). Dates outside the cohort window are rejected as a reused label or a
 * stale row rather than trusted.
 */
function paPpFetch_(orderNums, winLo, winHi) {
  var out = {}, key = PropertiesService.getScriptProperties().getProperty('PARCELPANEL_API_KEY');
  // 🔴 LOUD, never silent. PP only ever RESCUES boxes Shopify does not already show delivered, so a
  // total PP failure is invisible everywhere except those few orders — on the 2026-08-07 preview it
  // cost exactly 2 (#166228, #166660) out of 2,305 and looked like a rounding wobble. Count every
  // stage and shout if PP contributed nothing.
  var stats = { asked: orderNums.length, ok: 0, http: {}, delivered: 0 };
  if (!key) { paLog_('  🔴 PP SKIPPED — PARCELPANEL_API_KEY not set; union degraded to Shopify-only'); return out; }
  if (!orderNums.length) return out;
  for (var i = 0; i < orderNums.length; i += 50) {
    var slice = orderNums.slice(i, i + 50);
    var resp = UrlFetchApp.fetchAll(slice.map(function (n) {
      return { url: 'https://open.parcelwill.com/api/v2/tracking/order?order_number=' + encodeURIComponent(n),
               headers: { 'x-parcelpanel-api-key': key }, muteHttpExceptions: true };
    }));
    resp.forEach(function (r, k) {
      var code = r.getResponseCode();
      stats.http[code] = (stats.http[code] || 0) + 1;
      if (code !== 200) return;
      stats.ok++;
      try {
        var o = JSON.parse(r.getContentText());
        var ships = ((o.order || {}).shipments) || ((o.data || {}).shipments) || o.shipments || [];
        if (!ships.length) return;
        var s = ships[0];
        // ⚠️ `delivery_status` comes back NULL from this endpoint; the real field is `status`
        // (verified live 2026-08-07). Keep both — order matters only if PP ever populates the first.
        var status = String(s.delivery_status || s.status || '').toUpperCase();
        var pk = (String(s.pickup_date || '').match(/^\d{4}-\d{2}-\d{2}/) || [null])[0];
        var dl = (String(s.delivery_date || '').match(/^\d{4}-\d{2}-\d{2}/) || [null])[0];
        if (pk && (pk < winLo || pk > winHi)) pk = null;
        if (dl && (dl < winLo || dl > winHi)) dl = null;
        var del = status.indexOf('DELIVER') >= 0;
        if (del) stats.delivered++;
        out[slice[k]] = { delivered: del, pk: pk, dl: dl };
      } catch (e) {}
    });
  }
  paLog_('  PP: asked ' + stats.asked + '  http ' + JSON.stringify(stats.http) +
         '  parsed ' + Object.keys(out).length + '  delivered ' + stats.delivered);
  if (!stats.ok) paLog_('  🔴 PP RETURNED NOTHING USABLE — union degraded to Shopify-only this run');
  return out;
}

/**
 * 🔴 UNION: delivered = Shopify DELIVERED event OR PP status='delivered'. NEITHER SOURCE IS
 * COMPLETE — on wk0803 PP was hiding 224 deliveries Shopify had, and Shopify's feed was missing
 * OnTrac's final scan on 2 boxes PP had. A box is undelivered only if BOTH are silent.
 * 🔴 Late is measured over the WHOLE cohort (survivorship): an undelivered past-SLA box is a miss,
 * not "pending", and Lost in Transit is INSIDE 3+ Day — never a bucket beside it.
 */
function paUnion_(recs, pp, cohortAge) {
  var src = { pp: 0, shopify: 0, none: 0, shopifyEarlier: [] };
  recs.forEach(function (r) {
    var p = pp[r.order] || {};
    var ppD = !!(p.delivered && p.dl);
    r.arrived = !!r.shDlv || ppD;
    if (r.shDlv && r.shMove) r.tnt = paDayDiff_(r.shMove, r.shDlv);
    else if (ppD && p.pk) r.tnt = paDayDiff_(p.pk, p.dl);
    else r.tnt = null;
    // 🔴 PICKUP AUTHORITY (standing doctrine, gotcha #5). ParcelPanel `pickup_date` is CANONICAL;
    // Shopify's first movement scan is the fallback ONLY when PP has none. Both are already ET
    // (`paEtDate_` converts Shopify's UTC `happenedAt` before any date math — a raw UTC `.date()`
    // adds a phantom day to every evening event). PP is earlier than the Shopify scan on ~17% of
    // boxes and NEVER later; a box where Shopify reads earlier means an upstream feed changed —
    // logged by name, expected count zero.
    if (p.pk) {
      r.pickup = p.pk; r.pickupSrc = 'pp'; src.pp++;
      if (r.shMove && r.shMove < p.pk) src.shopifyEarlier.push(r.order + '(' + r.shMove + '<' + p.pk + ')');
    } else if (r.shMove) {
      r.pickup = r.shMove; r.pickupSrc = 'shopify'; src.shopify++;
    } else {
      r.pickup = null; r.pickupSrc = ''; src.none++;
    }
    r.moved = !!r.pickup;
    r.ontime = r.arrived && r.tnt !== null && r.tnt <= PA_SLA;
    // 🔴 THE CLOCK IS PER BOX, NOT PER COHORT (Kurt 2026-08-14: "This number is wrong. 55 from
    // Monday pickup, reviewing Tuesday pickup"). Survivorship (undelivered = late) is only valid
    // once THAT BOX's promise deadline has passed, and the deadline runs from the box's OWN pickup.
    // A ship week is MULTI-LEG — a Monday pickup plus a Tuesday (Dallas) leg is standard here — so
    // measuring every box on the cohort Monday's clock steals a full day from every Tuesday-leg box
    // and flips it to late while it is still inside its 2-day promise. On _SHIP_2026-08-10 at
    // cohort age 4d that reported 3+ Day 220 of 2,318 with Dallas (the Tuesday hub) alone at 87.
    // A box with NO pickup anywhere has no clock at all and is `pending` — never late. It cannot be
    // counted against a promise the carrier never started; it shows up on the
    // `never picked up by carrier` observation row instead.
    r.boxAge = r.pickup ? paDayDiff_(r.pickup, paTodayEt_()) : null;
    r.late = !r.ontime && (r.arrived || (r.boxAge !== null && r.boxAge > PA_SLA));
    r.pending = !r.arrived && !r.late;   // not yet due (or never picked up) — neither on-time nor late
    // lost vs active: a scan in the last 24h means the box is demonstrably MOVING (super-late, not
    // lost). Zero scans, or silent >=24h, stays LOST — unverified is not the same as known-not-lost.
    var age = paHoursSince_(r.lastScanIso);
    r.active = !r.arrived && age !== null && age < PA_ACTIVE_HRS;
    r.lost = !r.arrived && !r.active;
  });
  paLog_('  pickup clock (per box, cohort age ' + cohortAge + 'd): PP ' + src.pp +
         '  shopify-fallback ' + src.shopify + '  NO pickup anywhere ' + src.none +
         ' (pending by definition — never late)');
  if (src.shopifyEarlier.length) {
    paLog_('  🔴 Shopify first scan EARLIER than PP pickup on ' + src.shopifyEarlier.length +
           ' box(es) — expected ZERO; an upstream feed changed: ' +
           src.shopifyEarlier.slice(0, 20).join(', '));
  }
  return recs;
}

// ---------------------------------------------------------------- aggregate

/**
 * 🔴 SECTION-SCOPED KEYS. The same label appears in more than one block — `Unknown · 2 Day` exists
 * in BOTH `By Hub (assigned)` (the no-routing-tag bucket) and `By Carrier`. A flat label→value map
 * collapses them into one key, and a label-keyed writer then stamps that single value into BOTH
 * rows: the hub value 195/14 was written onto the carrier rows, double counting 209 orders whose
 * carriers were already counted under FedEx/OnTrac/UPS. Keys are therefore `SECTION||label`, and
 * the writer resolves the same key by tracking the section header as it walks rows. Same class as
 * the rate-row pairing fix — never identify a row by its bare label.
 * (There is never an unknown CARRIER — Kurt standing. The carrier section only ever receives
 * carrier-derived buckets, so a carrier `Unknown` row simply gets no key and is left alone.)
 */
// Header text → dimension. Both hub spellings are accepted so a header rename cannot orphan the
// section. 🔴 Keys are built from the DIMENSION, never the header text: keying by header emitted
// every hub bucket TWICE (once per accepted spelling), and the copy under the spelling not on the
// sheet matched nothing and was reported as "no row on the sheet — a human must add the row" for
// buckets that had just been written correctly. False missing-bucket warnings are as corrosive as
// silent failures: they train the reader to ignore the real ones.
var PA_SECTIONS = { 'By Hub (assigned)': 'hub', 'By Hub': 'hub', 'By Carrier': 'carrier',
                    'By State': 'state', 'By Box': 'box' };
var PA_DIMS = ['hub', 'carrier', 'state', 'box'];

function paKey_(dim, label) { return dim + '||' + label; }

function paValues_(recs, tab) {
  var total = recs.length, m = {};
  function n(pred) { var c = 0; recs.forEach(function (r) { if (pred(r)) c++; }); return c; }
  var PRED = (tab === PA_TABS.tnt2)
    ? { '2 Day': function (r) { return r.ontime; }, '3+ Day': function (r) { return r.late; } }
    : { 'Arrived': function (r) { return r.arrived; }, 'Not Arrived': function (r) { return !r.arrived; } };
  m[paKey_('', 'Total Shipments')] = total;
  Object.keys(PRED).forEach(function (k) {
    m[paKey_('', (tab === PA_TABS.tnt2) ? (k + ' Shipments') : k)] = n(PRED[k]);
    PA_DIMS.forEach(function (dim) {
      recs.forEach(function (r) {
        if (!PRED[k](r)) return;
        var key = (dim === 'hub') ? r.assigned.hub : r[dim];
        // the sheet's existing hub row for "no routing tag" is labelled `Unknown`
        var lab = (dim === 'hub' && key === PA_NO_TAG) ? 'Unknown' : key;
        var kk = paKey_(dim, lab + ' · ' + k);
        m[kk] = (m[kk] || 0) + 1;
      });
    });
  });
  // 🔴 THREE-ROW MODEL (D16, Kurt 2026-08-07: "fine we go with tnt3, tnt4+, still in transit").
  // Both nested rows sit INSIDE `3+ Day Shipments` and neither may be summed into any total. They
  // PARTITION Not Arrived, which is why churn between them is legitimate: a box going dark is a
  // visible migration from one row to the other with the sum unchanged.
  if (tab === PA_TABS.tnt2) {
    m[paKey_('', PA_OBS[0])] = n(function (r) { return r.active; });
    m[paKey_('', PA_OBS[1])] = n(function (r) { return r.lost && r.moved; });
    m[paKey_('', PA_OBS[2])] = n(function (r) { return r.lost && !r.moved; });
  }
  return m;
}

function paRoutingValues_(recs) {
  var m = {};
  // Hub compares assigned vs ACTUAL, and actual comes from carrier invoices (~1wk lag). Filling it
  // from the tag would compare the tag to itself and always read 100%.
  m[paKey_('', 'Routing Matched - Hub')] = PA_IMMATURE;
  var elig = 0, ok = 0;
  recs.forEach(function (r) {
    if (!r.assigned.carrier || r.carrier === 'Unknown') return;   // uncomparable, not "matched"
    elig++; if (r.assigned.carrier === r.carrier) ok++;
  });
  m[paKey_('', 'Routing Matched - Carrier')] = elig ? (Math.round(ok / elig * 1000) / 10).toFixed(1) + '%' : 'n/a';
  return m;
}

// ---------------------------------------------------------------- write

/**
 * 🔴 WALK-FORWARD FREEZE — an assert, never a convention.
 *
 * The maturity model (D15): a cohort column is SCRIPT-OWNED and self-heals daily from age 1 until
 * age `PA_MATURITY_DAYS`; at that age it FREEZES and the script refuses it forever after. So this
 * permits the rightmost column, and the one immediately left of it ONLY while that column is still
 * inside the window.
 *
 * Two rules make the loosening safe:
 *   - the age is RE-DERIVED FROM THAT COLUMN'S OWN HEADER, never from a parameter, so no caller can
 *     talk the writer into an old column by passing a friendly ship week;
 *   - the two columns must be DISTINCT and ADJACENT, so a header gap or a duplicated header cannot
 *     let the previous leg land on the current column.
 *
 * `allowAppend` is false for the previous leg: a previous cohort whose column does not exist must be
 * reported, never appended — appending would put an older cohort to the RIGHT of the current one.
 */
function paCurrentCol_(sheet, shipWeek, allowAppend) {
  var lastCol = Math.max(1, sheet.getLastColumn());
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(String);
  var idx = headers.indexOf(shipWeek);
  if (idx < 0) {
    if (allowAppend === false) return 0;              // caller logs and skips
    // 🔴 WRITE THE HEADER ON APPEND. Returning the index without stamping row 1 left the new
    // column headerless, so the NEXT run's headers.indexOf(shipWeek) missed again and appended
    // ANOTHER one — TnT2 ended up with two headerless _SHIP_2026-08-10 columns (F 1,622 / G 0)
    // and no way for the freeze assert to tell which was current. The header IS the identity of
    // the column; a column without one is invisible to every lookup here.
    // ORDER IS THE RULE (Kurt 2026-08-13): format-from-previous → header → values → assert.
    var col = lastCol + 1;
    paCopyFormatFromPrev_(sheet, col);
    sheet.getRange(1, col).setValue(shipWeek);
    SpreadsheetApp.flush();                             // header must be durable before we return
    return col;
  }
  var col = idx + 1, rightmost = 1;
  for (var i = 0; i < headers.length; i++) {
    if (/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(headers[i].trim())) rightmost = i + 1;
  }
  if (col === rightmost) return col;
  if (rightmost - col !== 1) {
    throw new Error('REFUSING column ' + col + ' (' + shipWeek + '): not adjacent to the rightmost ' +
                    'cohort column ' + rightmost);
  }
  var hdr = String(headers[col - 1]).trim();          // re-derive from the column itself
  if (!/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(hdr)) {
    throw new Error('REFUSING column ' + col + ': header ' + hdr + ' is not a cohort header');
  }
  var age = paCohortAgeDays_(hdr);
  if (age >= PA_MATURITY_DAYS) {
    throw new Error('REFUSING frozen column ' + col + ' (' + hdr + ', age ' + age + 'd >= ' +
                    PA_MATURITY_DAYS + 'd) — matured columns are Kurt-owned');
  }
  return col;
}

/**
 * 🔴 OWNED-ROW-ONLY, per-cell, keyed off the column-A label. Dan hand-edits this sheet.
 * Unrecognized labels are SKIPPED — including the blank-label rows, which are live rate formulas
 * `=IF(good+bad>0,bad/(good+bad),"")` and must never be overwritten. Never a contiguous-column
 * write. Rows are NEVER appended or inserted: adding a row shifts every formula reference below it,
 * so a missing bucket is REPORTED for a human to add (Chicago needed a row on wk0803 and got one
 * only after Kurt approved it).
 */
function paWriteOwned_(sheet, col, valuesByLabel, dry) {
  var lastRow = Math.max(1, sheet.getLastRow());
  var labels = sheet.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]).trim(); });
  var wrote = 0, missing = [], seen = {}, dim = '';
  for (var i = 0; i < labels.length; i++) {
    var lab = labels[i];
    // track the block we are inside — a bare label is ambiguous across sections. The header text is
    // resolved to a DIMENSION here, so either accepted spelling lands on the same keys.
    if (Object.prototype.hasOwnProperty.call(PA_SECTIONS, lab)) { dim = PA_SECTIONS[lab]; continue; }
    var key = paKey_(dim, lab);
    if (!lab || !Object.prototype.hasOwnProperty.call(valuesByLabel, key)) continue;
    var v = valuesByLabel[key];
    seen[key] = true;
    var cell = sheet.getRange(i + 1, col);
    if (dry) {
      paLog_('  [dry] ' + sheet.getName() + '!' + cell.getA1Notation() + '  ' + lab + '  = ' + v);
    } else {
      cell.setValue(v === null || v === undefined ? '' : v);
      if (typeof v === 'number') cell.setNumberFormat('0');
    }
    wrote++;
  }
  Object.keys(valuesByLabel).forEach(function (k) {
    if (!seen[k] && /·/.test(k)) missing.push(k.replace('||', ' → ') + '=' + valuesByLabel[k]);
  });
  if (missing.length) {
    paLog_('  ⚠️ ' + sheet.getName() + ': no row on the sheet for ' + missing.length +
           ' bucket(s) — NOT written anywhere, a human must add the row: ' + missing.join(', '));
  }
  return { wrote: wrote, missing: missing };
}

// ---------------------------------------------------------------- entry point

/**
 * Refresh ONE cohort's column. `budget` is shared across legs so both together respect the
 * per-run PP cap. `allowAppend` is false for the previous leg (see paCurrentCol_).
 */
function paRefreshOne_(shipWeek, dry, budget, allowAppend) {
  var age = paCohortAgeDays_(shipWeek);
  paLog_('-- leg ' + shipWeek + ' (age ' + age + 'd)');
  var recs = paFetchCohort_(shipWeek);
  var mon = shipWeek.replace('_SHIP_', '');
  var lo = Utilities.formatDate(new Date(new Date(mon + 'T00:00:00Z').getTime() - 2 * 86400000), PA_TZ, 'yyyy-MM-dd');
  var hi = Utilities.formatDate(new Date(new Date().getTime() + 86400000), PA_TZ, 'yyyy-MM-dd');
  // PP is the RESCUE side of the union — it can only change a box Shopify does not already show
  // delivered-with-a-pickup-scan. Asking PP for all ~2,300 orders was 47 fetchAll batches against a
  // 6-minute budget; scoping it to the handful that actually need rescuing makes the union both
  // cheap and reliable, and changes no result (the OR is unaffected for already-delivered boxes).
  var cand = recs.filter(function (r) { return !r.shDlv || !r.shMove; });
  paLog_('PP rescue candidates: ' + cand.length + ' of ' + recs.length);
  var pp = {};
  if (age < PA_PP_MIN_AGE_DAYS) {
    // 🔴 GUARD 2 — PP budget is 2,500 calls/WEEK (Kurt, standing) and Exceptions' hourly job is the
    // dominant consumer. Early in the week almost the whole cohort is still undelivered, so the
    // rescue set ≈ 2,300 and one uncapped Tuesday run could eat the week's budget. Deliveries
    // stream in via Shopify fine mid-week and PP reconciles on later runs, so skip the leg outright.
    paLog_('  PP: skipped (cohort age <' + PA_PP_MIN_AGE_DAYS + 'd) — Shopify-only this run');
  } else {
    // 🔴 GUARD 3 — hard backstop regardless of day. Oldest first: a box silent longest is the one
    // most worth rescuing. Loud when it bites; a silent truncation would read as "PP found nothing".
    cand.sort(function (a, b) { return String(a.lastScanIso || '') < String(b.lastScanIso || '') ? -1 : 1; });
    // budget is shared across legs — both cohorts together respect the per-RUN cap
    var take = cand.slice(0, Math.max(0, budget.left));
    if (cand.length > take.length) {
      paLog_('  PP: capped at ' + budget.left + ' remaining this run, skipped ' +
             (cand.length - take.length) + ' candidates (oldest-scan first)');
    }
    budget.left -= take.length;
    pp = paPpFetch_(take.map(function (r) { return r.order; }), lo, hi);
  }
  paUnion_(recs, pp, age);

  var total = recs.length;
  var ot = 0, lt = 0, arr = 0, lost = 0, active = 0;
  recs.forEach(function (r) { if (r.ontime) ot++; if (r.late) lt++; if (r.arrived) arr++; if (r.lost) lost++; if (r.active) active++; });
  // 🔴 asserts — every pass, before any write
  // Pending boxes (undelivered, cohort not yet past the 2-day promise) belong to NEITHER bucket,
  // so the partition is three-way until the cohort matures. Asserting the old two-way identity
  // would throw on every young column.
  var pend = 0;
  recs.forEach(function (r) { if (r.pending) pend++; });
  if (ot + lt + pend !== total) {
    throw new Error('PA_ASSERT_TOTAL_PARTITION: 2 Day + 3+ Day + pending (' + (ot + lt + pend) +
                    ') != Total ' + total);
  }
  if (pend) {
    paLog_('  PENDING (undelivered and inside its OWN 2-day promise, or never picked up): ' + pend +
           ' — excluded from BOTH 2 Day and 3+ Day until that box\'s deadline passes');
  }
  if (lost + active !== total - arr) {
    throw new Error('PA_ASSERT_NOTARRIVED_PARTITION: lost+active (' + (lost + active) +
                    ') != Not Arrived ' + (total - arr));
  }
  // 🔴 pending sits INSIDE the three observations (which partition Not Arrived), never as a fourth
  // bucket beside them. Under the cohort-wide clock the only pending boxes were still-moving ones,
  // so this was bounded by `active`. Under the PER-BOX clock a never-picked-up box is also pending
  // — it belongs to the `never picked up by carrier` observation, not to `still moving` — so the
  // correct bound is Not Arrived. Keeping the old `pend > active` bound would throw on every
  // multi-leg week. Both halves still hold: every pending box is undelivered, and pending can never
  // exceed the not-arrived population it is drawn from.
  var pendArrived = 0;
  recs.forEach(function (r) { if (r.pending && r.arrived) pendArrived++; });
  if (pendArrived) {
    throw new Error('PA_ASSERT_PENDING_SUBSET: ' + pendArrived + ' pending box(es) are ARRIVED — ' +
                    'pending must be a subset of Not Arrived.');
  }
  if (pend > total - arr) {
    throw new Error('PA_ASSERT_PENDING_SUBSET: pending ' + pend + ' > Not Arrived ' + (total - arr) +
                    ' — pending must sit inside the three observations, not beside them.');
  }
  paLog_('cohort ' + total + '  2Day ' + ot + '  3+Day ' + lt + '  arrived ' + arr +
         '  notArrived ' + (total - arr) + '  lost ' + lost + '  active ' + active +
         '  lateRate ' + (lt / total * 100).toFixed(2) + '%');

  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);

  // 🔴 MONOTONICITY GATE (D16, Kurt: the lost number "should go down, not up"). The invariant is on
  // the PAIR: still-in-transit + lost == Not Arrived, and that SUM is monotone non-increasing within
  // a cohort — a box leaves only by DELIVERING, and delivered cannot un-deliver. `lost` may rise
  // only when still-in-transit falls by at least as much. Refuse rather than publish a rise.
  var obs = {}, newSum = 0;
  obs[PA_OBS[0]] = active;
  obs[PA_OBS[1]] = 0; obs[PA_OBS[2]] = 0;
  recs.forEach(function (r) {
    if (!r.lost) return;
    obs[r.moved ? PA_OBS[1] : PA_OBS[2]] += 1;
  });
  PA_OBS.forEach(function (k) { newSum += obs[k]; });
  if (newSum !== total - arr) {
    throw new Error('PA_ASSERT_OBSERVATION_PARTITION: three observations sum ' + newSum +
                    ' != Not Arrived ' + (total - arr));
  }
  var prev = paReadNested_(ss, shipWeek), prevKeys = Object.keys(prev), oldSum = 0;
  prevKeys.forEach(function (k) { oldSum += prev[k]; });
  if (prevKeys.length === PA_OBS.length) {
    if (newSum > oldSum) {
      throw new Error('REFUSING to write: the three observations rose ' + oldSum + ' -> ' + newSum +
                      '. A box leaves only by DELIVERING and delivered cannot un-deliver.');
    }
    PA_OBS.forEach(function (k) {
      // migration BETWEEN the rows is expected — a box going dark moves one row to the next with
      // the sum unchanged. Log it so the movement is visible rather than a silent headline shift.
      if (obs[k] > prev[k]) paLog_('  migration: ' + k + ' ' + prev[k] + ' -> ' + obs[k]);
    });
    paLog_('  monotonicity: sum ' + oldSum + ' -> ' + newSum + ' (non-increasing) ✅');
  } else {
    paLog_('  monotonicity: partial prior row set — first write, gate skipped');
  }
  PA_OBS.forEach(function (k) { paLog_('    ' + k + ' = ' + obs[k]); });

  [PA_TABS.tnt2, PA_TABS.lost].forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (!sh) { paLog_('  ⚠️ missing tab ' + name); return; }
    paAssertColumns_(sh);                               // pre-existing damage — throw, never write onto it
    var col = paCurrentCol_(sh, shipWeek, allowAppend);
    if (!col) { paLog_('  ⚠️ ' + name + ': no column for ' + shipWeek + ' — skipped (never appended left)'); return; }
    paLog_('  ' + name + ' col ' + col);
    paWriteOwned_(sh, col, paValues_(recs, name), dry);
    if (!dry) { SpreadsheetApp.flush(); paAssertColumns_(sh); }   // post-write: still exactly one, still headed
  });
  var rm = ss.getSheetByName(PA_TABS.routing);
  if (rm) {
    paAssertColumns_(rm);
    var rc = paCurrentCol_(rm, shipWeek, allowAppend);
    if (rc) {
      paLog_('  ' + PA_TABS.routing + ' col ' + rc);
      paWriteOwned_(rm, rc, paRoutingValues_(recs), dry);
      if (!dry) { SpreadsheetApp.flush(); paAssertColumns_(rm); }
    }
  }
  return { shipWeek: shipWeek, age: age, total: total, twoDay: ot, threePlus: lt,
           arrived: arr, lost: lost, active: active };
}

/**
 * Current on-sheet values of the two nested rows for `shipWeek`, for the monotonicity gate.
 * 🔴 Matches each row by its FULL label — since the three-row change there are TWO `of which`
 * rows, and a substring match grabs the first and silently compares the wrong pair.
 */
function paReadNested_(ss, shipWeek) {
  var out = {};
  var sh = ss.getSheetByName(PA_TABS.tnt2);
  if (!sh) return out;
  var col = 0, headers = sh.getRange(1, 1, 1, Math.max(1, sh.getLastColumn())).getValues()[0];
  for (var i = 0; i < headers.length; i++) if (String(headers[i]).trim() === shipWeek) col = i + 1;
  if (!col) return out;
  sh.getRange(1, 1, Math.max(1, sh.getLastRow()), col).getValues().forEach(function (r) {
    var lab = String(r[0]).trim(), v = r[col - 1];
    // 🔴 only the OBSERVATION rows, matched by FULL label. Sweeping in any other numeric row (the
    // python twin briefly pulled `Not Arrived` into this set) inflates the baseline and the
    // monotonicity gate then compares against a number that means nothing.
    if (PA_OBS.indexOf(lab) >= 0 && typeof v === 'number') out[lab] = v;
  });
  return out;
}

/** The cohort one week before `shipWeek`, if it has orders. '' when there is none. */
function paPreviousShipWeek_(shipWeek) {
  var mon = new Date(shipWeek.replace('_SHIP_', '') + 'T12:00:00Z');
  var tag = '_SHIP_' + Utilities.formatDate(new Date(mon.getTime() - 7 * 86400000), PA_TZ, 'yyyy-MM-dd');
  return ordersCount_("tag:'" + tag + "' -status:cancelled -tag:'Reship'") > 0 ? tag : '';
}

/**
 * Daily entry point. Refreshes the current cohort, then RECONCILES the previous one while it is
 * still inside the maturity window (D15) — a box frozen as 3+ Day / Not Arrived can later prove
 * delivered, and without this the column freezes at a value we already know is wrong.
 */
function refreshCurrentColumn(shipWeek) {
  var dry = !paWriteArmed_();
  // 🔴 A TIME-DRIVEN TRIGGER PASSES AN EVENT OBJECT as the first argument. Taking it as `shipWeek`
  // made `cur` an object and killed the run at `shipWeek.replace(...)` in ~1s, before any fetch —
  // two nights straight, while the menu path (no argument) kept working, which is exactly why
  // manual testing never saw it. Only ever accept a real `_SHIP_YYYY-MM-DD` string.
  if (typeof shipWeek !== 'string' || !/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(shipWeek)) shipWeek = '';
  var cur = shipWeek || paCurrentShipWeek_();
  var age = paCohortAgeDays_(cur);
  paLog_('=== refreshCurrentColumn ' + cur + ' (age ' + age + 'd) — ' +
         (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');

  // 🔴 GUARD 1 — ship day. On a DAILY trigger the Monday run fires while the cohort is still being
  // handed to carriers: nothing has moved, so every box would read undelivered and, under the
  // survivorship rule, LATE. Anchored on cohort AGE rather than day-of-week so a shifted ship day
  // (holiday week) can't defeat it.
  if (age <= 0) {
    paLog_('SKIP — cohort ships today (age ' + age + 'd); nothing to measure yet.');
    return { skipped: 'ship-day', shipWeek: cur, age: age };
  }

  var budget = { left: PA_PP_MAX_CALLS };             // shared across both legs
  var out = { dry: dry, current: null, previous: null };
  var t0 = new Date().getTime();
  out.current = paRefreshOne_(cur, dry, budget, true);
  var tCur = new Date().getTime();
  paLog_('leg timing: current ' + ((tCur - t0) / 1000).toFixed(1) + 's');

  // ---- previous leg: SECOND, and never allowed to take the current column down with it ----
  var prev = paPreviousShipWeek_(cur);
  if (!prev) {
    paLog_('reconcile: no previous cohort found');
  } else {
    var pAge = paCohortAgeDays_(prev);
    if (pAge >= PA_MATURITY_DAYS) {
      paLog_('reconcile: ' + prev + ' is FROZEN (age ' + pAge + 'd >= ' + PA_MATURITY_DAYS +
             'd) — Kurt-owned from here, not touched');
    } else {
      try {
        out.previous = paRefreshOne_(prev, dry, budget, false);
      } catch (e) {
        // 🔴 loud, and NON-fatal: the current column is already written by this point. A previous-leg
        // failure (6-min ceiling, a refused column) must never cost us the current refresh.
        paLog_('🔴 reconcile leg FAILED for ' + prev + ' — current column is already written and is ' +
               'unaffected. Error: ' + e);
      }
      paLog_('leg timing: previous ' + ((new Date().getTime() - tCur) / 1000).toFixed(1) + 's');
    }
  }
  paLog_('total ' + ((new Date().getTime() - t0) / 1000).toFixed(1) + 's of the 360s ceiling; ' +
         'PP calls used ' + (PA_PP_MAX_CALLS - budget.left) + ' of ' + PA_PP_MAX_CALLS);
  paLog_('=== done (' + (dry ? 'DRY RUN — nothing written' : 'written') + ') ===');
  return out;
}

/**
 * Current cohort = the most recent ship Monday that actually HAS orders in Shopify.
 * 🔴 Derived from the calendar + Shopify, NOT from the sheet's rightmost header. Reading the
 * header would pin the script to whatever column already exists, so it could never discover a new
 * cohort and would refresh last week's column forever — the walk-forward would silently stall.
 * Walking back week by week (not assuming this Monday) also makes the Monday-skip safe: the first
 * touch of a new cohort is Tuesday, and `paCurrentCol_` appends the column then.
 */
function paCurrentShipWeek_() {
  var now = new Date();
  var dow = Number(Utilities.formatDate(now, PA_TZ, 'u'));    // 1=Mon .. 7=Sun
  for (var wk = 0; wk < 3; wk++) {
    var d = new Date(now.getTime() - ((dow - 1) + wk * 7) * 86400000);
    var tag = '_SHIP_' + Utilities.formatDate(d, PA_TZ, 'yyyy-MM-dd');
    if (ordersCount_("tag:'" + tag + "' -status:cancelled -tag:'Reship'") > 0) return tag;
    paLog_('  no orders for ' + tag + ' — walking back a week');
  }
  throw new Error('no _SHIP_ cohort with orders found in the last 3 weeks');
}

/** Whole days from the cohort's ship Monday to today, ET. 0 = ships today. */
function paCohortAgeDays_(shipWeek) {
  var mon = shipWeek.replace('_SHIP_', '');
  var today = Utilities.formatDate(new Date(), PA_TZ, 'yyyy-MM-dd');
  return paDayDiff_(mon, today);
}

// ------------------------------------------------- hub-row maintenance (D19, one-shot + verifier)

/**
 * 🔴 D13 STANDS: the REFRESH writer never inserts rows. A row insert shifts every reference below it,
 * and the refresh runs unattended on a daily trigger — a structural edit does not belong on that
 * path. What follows is a SEPARATE, human-invoked maintenance tool. It is generic over the hub name
 * so the NEXT new hub is one menu click rather than a hand-edit, but it is still a deliberate,
 * observed action with a verifier on both sides of it.
 *
 * Motivating gap (2026-08-14): Swedesboro (the NJ hub, live for wk0810) appeared in the lane
 * universe and in `paValues_`, but had no row in `By Hub (assigned)` on either tab — so
 * `hub → Swedesboro · 2 Day = 569` and `· 3+ Day = 12` (TnT2) and `· Arrived = 570` /
 * `· Not Arrived = 34` (Lost in Transit) were computed, warned about, and written NOWHERE.
 */

/** good/bad row suffixes per tab — the same grains `paValues_` emits. */
function paGrains_(tabName) {
  return (tabName === PA_TABS.tnt2) ? ['2 Day', '3+ Day'] : ['Arrived', 'Not Arrived'];
}

/** Rows of the `By Hub (assigned)` block: {start, end} exclusive of the header row itself. */
function paHubSection_(labels) {
  var start = -1, end = labels.length;
  for (var i = 0; i < labels.length; i++) {
    if (start < 0) {
      if (PA_SECTIONS[labels[i]] === 'hub') start = i + 1;   // first row AFTER the header
    } else if (Object.prototype.hasOwnProperty.call(PA_SECTIONS, labels[i])) {
      end = i; break;
    }
  }
  return { start: start, end: end };
}

/**
 * Parse the hub block into groups of three: `{hub} · {good}` / `{hub} · {bad}` / blank rate row.
 * 🔴 FULL-label suffix match, never a substring `indexOf` — the recurring bug family on this sheet
 * is "identify a row by a partial label match" (section collision, `of which` assert, the pair
 * finder). `3+ Day` also ENDS WITH `Day`, so a loose match pairs the wrong rows.
 */
function paHubGroups_(labels, tabName) {
  var g = paGrains_(tabName), sec = paHubSection_(labels), out = [];
  if (sec.start < 0) return out;
  for (var i = sec.start; i < sec.end; i++) {
    var lab = labels[i];
    var sufGood = ' · ' + g[0], sufBad = ' · ' + g[1];
    if (lab.length <= sufGood.length || lab.slice(-sufGood.length) !== sufGood) continue;
    var hub = lab.slice(0, lab.length - sufGood.length);
    if (labels[i + 1] !== hub + sufBad) continue;
    var rate = (labels[i + 2] === '') ? (i + 3) : 0;      // 1-based row of the rate row, 0 = none
    out.push({ hub: hub, good: i + 1, bad: i + 2, rate: rate });   // 1-based sheet rows
  }
  return out;
}

/** Residual bucket sorts LAST regardless of alphabet — it is the catch-all, not a hub. */
function paHubSortsBefore_(a, b) {
  if (a === PA_RESIDUAL_HUB) return false;
  if (b === PA_RESIDUAL_HUB) return true;
  return a < b;
}

/**
 * 🔴 INDEPENDENT RATE-ROW VERIFIER. Every blank-label rate row must reference EXACTLY the two rows
 * directly above it, in its OWN column. A row insert is the one operation that can silently
 * re-point these, so this runs before AND after any structural edit and reports a count, not a
 * feeling. Reference rows are read out of the formula text — a formula that merely LOOKS right is
 * not evidence.
 */
function paAuditRateRows_(sh) {
  var lastRow = Math.max(1, sh.getLastRow()), lastCol = Math.max(1, sh.getLastColumn());
  var labels = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]).trim(); });
  var formulas = sh.getRange(1, 1, lastRow, lastCol).getFormulas();
  var res = { rows: 0, cells: 0, ok: 0, bad: [] };
  for (var i = 2; i < lastRow; i++) {                 // 0-based; a rate row needs two rows above it
    if (labels[i] !== '' || labels[i - 1] === '' || labels[i - 2] === '') continue;
    var any = false;
    for (var c = 2; c <= lastCol; c++) {
      var f = String(formulas[i][c - 1] || '');
      if (!f) continue;
      any = true; res.cells++;
      var m = f.match(/\$?([A-Z]{1,3})\$?(\d+)/g) || [];
      var rows = {}, cols = {};
      m.forEach(function (ref) {
        var p = ref.replace(/\$/g, '').match(/^([A-Z]{1,3})(\d+)$/);
        cols[p[1]] = 1; rows[Number(p[2])] = 1;
      });
      var rk = Object.keys(rows).map(Number).sort(function (x, y) { return x - y; });
      var ck = Object.keys(cols);
      var self = sh.getRange(1, c).getA1Notation().replace(/\d+/, '');
      // labels[i] is the blank rate row = SHEET row i+1, so its pair is sheet rows i-1 (good) and i (bad).
      if (rk.length === 2 && rk[0] === i - 1 && rk[1] === i && ck.length === 1 && ck[0] === self) {
        res.ok++;
      } else {
        res.bad.push(sh.getName() + '!' + self + (i + 1) + ' expects rows ' + (i - 1) + '+' + i +
                     ' in col ' + self + ', got ' + f);
      }
    }
    if (any) res.rows++;
  }
  return res;
}

/** Menu/editor entry: audit every rate row on both tabs. Read-only. */
function auditRateRows() {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID), total = 0, bad = 0;
  [PA_TABS.tnt2, PA_TABS.lost].forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (!sh) { paLog_('⚠️ missing tab ' + name); return; }
    var r = paAuditRateRows_(sh);
    total += r.cells; bad += r.bad.length;
    paLog_(name + ': ' + r.rows + ' rate row(s), ' + r.cells + ' formula cell(s), ' +
           r.ok + ' correct, ' + r.bad.length + ' WRONG');
    r.bad.slice(0, 40).forEach(function (b) { paLog_('  🔴 ' + b); });
  });
  paLog_(bad ? ('🔴 ' + bad + ' of ' + total + ' rate formulas are mis-pointed')
             : ('✅ all ' + total + ' rate formulas point at their own pair'));
  return { cells: total, bad: bad };
}

/** Rightmost `_SHIP_` column — the only one that is not frozen history. 0 if none. */
function paRightmostCohortCol_(sh) {
  var lastCol = Math.max(1, sh.getLastColumn()), out = 0;
  var headers = sh.getRange(1, 1, 1, lastCol).getValues()[0];
  for (var i = 0; i < headers.length; i++) {
    if (/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(String(headers[i]).trim())) out = i + 1;
  }
  return out;
}

/**
 * Insert `{hub} · good` / `{hub} · bad` / blank rate row into the `By Hub (assigned)` block of ONE
 * tab, in sorted position, with formats cloned from a sibling hub group.
 *
 * 🔴 HISTORY STAYS BLANK, NEVER ZERO. A 0 in a matured column asserts "this hub shipped nothing that
 * week"; the hub did not EXIST then. Only the rightmost (live) cohort column gets the rate formula —
 * writing one into a frozen column would put a live formula inside Kurt-owned history.
 */
function paInsertHubRows_(sh, hub, dry) {
  var name = sh.getName(), g = paGrains_(name);
  var lastRow = Math.max(1, sh.getLastRow());
  var labels = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]).trim(); });
  var groups = paHubGroups_(labels, name);
  if (!groups.length) throw new Error('PA_INSERT_NO_HUB_SECTION: ' + name + ' — no By Hub groups found');
  for (var i = 0; i < groups.length; i++) {
    if (groups[i].hub === hub) {
      paLog_('  ' + name + ': ' + hub + ' already has rows (' + groups[i].good + '-' +
             (groups[i].rate || groups[i].bad) + ') — nothing to do');
      return { inserted: 0 };
    }
  }
  // sorted position: the first existing group that sorts AFTER the new hub
  var after = null;
  for (var j = 0; j < groups.length; j++) {
    if (paHubSortsBefore_(hub, groups[j].hub)) { after = groups[j]; break; }
  }
  var anchor = after ? after.good : (function () {
    var last = groups[groups.length - 1];
    return (last.rate || last.bad) + 1;
  })();
  // template = the group we are landing next to; formats are cloned from it row-for-row
  var tmpl = after ? after : groups[groups.length - 1];
  var where = after ? ('before ' + after.hub + ' (row ' + anchor + ')')
                    : ('at the end of the hub block (row ' + anchor + ')');
  paLog_('  ' + name + ': insert ' + hub + ' ' + where + ', formats from ' + tmpl.hub);
  if (!tmpl.rate) {
    paLog_('  ⚠️ ' + name + ': template group ' + tmpl.hub + ' has NO rate row — the new group ' +
           'will get one anyway (D2: a rate row follows EVERY pair).');
  }
  if (dry) {
    paLog_('  [dry] ' + name + '!A' + anchor + '  = "   ' + hub + ' · ' + g[0] + '"');
    paLog_('  [dry] ' + name + '!A' + (anchor + 1) + '  = "   ' + hub + ' · ' + g[1] + '"');
    paLog_('  [dry] ' + name + '!A' + (anchor + 2) + '  = blank-label rate row, formula in the ' +
           'rightmost cohort column only; B..(rightmost-1) left EMPTY (blank ≠ zero)');
    return { inserted: 0, anchor: anchor, dry: true };
  }
  var maxCols = Math.max(1, sh.getMaxColumns());
  sh.insertRowsBefore(anchor, 3);
  // rows at/after the anchor shifted down by 3 — re-derive the template's position
  var t = { good: tmpl.good, bad: tmpl.bad, rate: tmpl.rate };
  if (t.good >= anchor) { t.good += 3; t.bad += 3; if (t.rate) t.rate += 3; }
  sh.getRange(t.good, 1, 1, maxCols).copyTo(sh.getRange(anchor, 1, 1, maxCols), { formatOnly: true });
  sh.getRange(t.bad, 1, 1, maxCols).copyTo(sh.getRange(anchor + 1, 1, 1, maxCols), { formatOnly: true });
  if (t.rate) {
    sh.getRange(t.rate, 1, 1, maxCols).copyTo(sh.getRange(anchor + 2, 1, 1, maxCols), { formatOnly: true });
  }
  // 🔴 label style is byte-identical to its siblings: three leading spaces + ' · ' (U+00B7).
  sh.getRange(anchor, 1).setValue('   ' + hub + ' · ' + g[0]);
  sh.getRange(anchor + 1, 1).setValue('   ' + hub + ' · ' + g[1]);
  var cur = paRightmostCohortCol_(sh);
  if (cur) {
    var a1 = sh.getRange(1, cur).getA1Notation().replace(/\d+/, '');
    var good = a1 + anchor, bad = a1 + (anchor + 1);
    sh.getRange(anchor + 2, cur)
      .setFormula('=IF(' + good + '+' + bad + '>0,' + bad + '/(' + good + '+' + bad + '),"")')
      .setNumberFormat('0.00%');
    sh.getRange(anchor, cur, 2, 1).setNumberFormat('0');
    paLog_('  ' + name + ': rate formula at ' + a1 + (anchor + 2) + ' over ' + good + '/' + bad);
  } else {
    paLog_('  ⚠️ ' + name + ': no cohort column found — rate row left empty');
  }
  SpreadsheetApp.flush();
  return { inserted: 3, anchor: anchor };
}

/**
 * Add a hub's rows to BOTH cohort tabs. Verifier runs before and after; a post-insert regression in
 * ANY rate formula throws, because a silently re-pointed formula is the one way this corrupts a tab.
 */
function paAddHub_(hub, dry) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  paLog_('=== add hub rows: ' + hub + ' — ' + (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');
  var out = {};
  [PA_TABS.tnt2, PA_TABS.lost].forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (!sh) { paLog_('⚠️ missing tab ' + name); return; }
    paAssertColumns_(sh);                                  // never edit a tab that is already damaged
    var pre = paAuditRateRows_(sh);
    paLog_('  ' + name + ' BEFORE: ' + pre.rows + ' rate rows, ' + pre.cells + ' formulas, ' +
           pre.bad.length + ' wrong');
    if (pre.bad.length) {
      throw new Error('PA_INSERT_PRE_AUDIT_FAILED: ' + name + ' already has ' + pre.bad.length +
                      ' mis-pointed rate formula(s) — fix those before inserting rows: ' +
                      pre.bad.slice(0, 5).join(' | '));
    }
    out[name] = paInsertHubRows_(sh, hub, dry);
    if (dry) return;
    var post = paAuditRateRows_(sh);
    paLog_('  ' + name + ' AFTER : ' + post.rows + ' rate rows, ' + post.cells + ' formulas, ' +
           post.bad.length + ' wrong');
    if (post.bad.length) {
      throw new Error('PA_INSERT_POST_AUDIT_FAILED: ' + name + ' — the insert re-pointed ' +
                      post.bad.length + ' rate formula(s): ' + post.bad.slice(0, 5).join(' | '));
    }
    if (post.rows !== pre.rows + 1 || post.cells < pre.cells) {
      throw new Error('PA_INSERT_ROW_COUNT: ' + name + ' rate rows ' + pre.rows + ' -> ' + post.rows +
                      ', formulas ' + pre.cells + ' -> ' + post.cells + ' (expected +1 row, no loss)');
    }
    paAssertColumns_(sh);
    paLog_('  ' + name + ' ✅ rate integrity intact, +1 group');
  });
  paLog_('=== done (' + (dry ? 'DRY RUN — nothing written' : 'written') + ') ===');
  return out;
}

/** 🔴 Kurt runs THIS first: logs exactly where the rows would land. Writes nothing. */
function previewAddSwedesboroRows() { return paAddHub_('Swedesboro', true); }
/** Then THIS: inserts the rows. Idempotent — re-running finds the rows and does nothing. */
function addSwedesboroRows() { return paAddHub_('Swedesboro', false); }

/**
 * Fill MISSING rate formulas in the rightmost (live) cohort column across both tabs.
 *
 * Why this exists: `paCopyFormatFromPrev_` copies `{formatOnly:true}` on purpose (a plain copyTo
 * would clone ~65 relative formulas), so a freshly appended cohort column has the rate rows'
 * APPEARANCE and none of their formulas. Observed 2026-08-14: on column F (`_SHIP_2026-08-10`)
 * every rate row on TnT2 and Lost in Transit is empty. Only EMPTY cells in the RIGHTMOST column are
 * touched — an existing formula is never overwritten and frozen history is never entered.
 */
function fillRateFormulasCurrentColumn(dry) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID), filled = 0;
  if (dry === undefined) dry = true;
  paLog_('=== fillRateFormulasCurrentColumn — ' + (dry ? 'DRY RUN' : 'WRITING') + ' ===');
  [PA_TABS.tnt2, PA_TABS.lost].forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (!sh) return;
    var col = paRightmostCohortCol_(sh);
    if (!col) { paLog_('  ⚠️ ' + name + ': no cohort column'); return; }
    var a1 = sh.getRange(1, col).getA1Notation().replace(/\d+/, '');
    var lastRow = Math.max(1, sh.getLastRow());
    var labels = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]).trim(); });
    var f = sh.getRange(1, col, lastRow, 1).getFormulas();
    var n = 0;
    for (var i = 2; i < lastRow; i++) {
      if (labels[i] !== '' || labels[i - 1] === '' || labels[i - 2] === '') continue;
      if (String(f[i][0] || '')) continue;                 // never overwrite an existing formula
      var good = a1 + (i - 1), bad = a1 + i;               // rate row = sheet i+1; pair = i-1 / i
      if (!dry) {
        sh.getRange(i + 1, col)
          .setFormula('=IF(' + good + '+' + bad + '>0,' + bad + '/(' + good + '+' + bad + '),"")')
          .setNumberFormat('0.00%');
      }
      n++;
    }
    filled += n;
    paLog_('  ' + name + ' col ' + a1 + ': ' + n + ' empty rate cell(s) ' + (dry ? 'would be' : '') + ' filled');
  });
  paLog_('=== ' + filled + ' total (' + (dry ? 'DRY RUN — nothing written' : 'written') + ') ===');
  return filled;
}

/** Dry-run entry point for Kurt: run this, then read View → Logs. Writes nothing, ever. */
function previewCurrentColumn() {
  var saved = PropertiesService.getScriptProperties().getProperty('PIVOT_ANALYTICS_WRITE');
  PropertiesService.getScriptProperties().deleteProperty('PIVOT_ANALYTICS_WRITE');
  try { return refreshCurrentColumn(); }
  finally { if (saved) PropertiesService.getScriptProperties().setProperty('PIVOT_ANALYTICS_WRITE', saved); }
}
