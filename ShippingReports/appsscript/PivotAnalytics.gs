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

/**
 * 🔴 TNT1 (D22, Kurt 2026-08-17: "I want to get TNT0 and TNT1 shipments in there (just label the
 * row tnt1)"). ONE nested row under `2 Day Shipments` counting delivered boxes whose TNT is
 * <= 1 CALENDAR DAY measured from THAT BOX's OWN pickup (D18 per-box clock) — so same-day (TNT 0,
 * ~42 boxes on wk0810, short intra-region lanes) is INCLUDED, not dropped and not given its own row.
 *
 * 🔴 SUBSET, NOT A SIBLING. `2 Day Shipments` is TNT <= 2 and KEEPS counting these; TNT1 is a strict
 * subset of it and is summed into NOTHING. Same discipline as the D16 observations: a nested row
 * partitions or refines its parent, it never adds to a total. Asserted (`PA_ASSERT_TNT1_SUBSET`).
 */
var PA_TNT1_LABEL = 'TNT1';
var PA_TNT1_SLA = 1;
/** Labels that are NESTED refinements of a grain row — skipped when resolving a rate-row pair. */
var PA_NESTED = PA_OBS.concat([PA_TNT1_LABEL]);

/**
 * 🔴 D22b (Kurt 2026-08-17: "we want tnt1 rows for these hubs too") — the same nesting exists per
 * hub as `{hub} · TNT1`, sitting between `{hub} · 2 Day` and `{hub} · 3+ Day`. The nested test is
 * therefore a PREDICATE, not a flat allowlist lookup: exact match on the top-block labels, or the
 * explicit ` · TNT1` suffix. It is still an ALLOWLIST — "anything that is not a grain row" was
 * rejected in D19a because it lets the pair resolver stroll out of its own block.
 */
function paIsNested_(lab) {
  if (PA_NESTED.indexOf(lab) >= 0) return true;
  var suf = ' · ' + PA_TNT1_LABEL;
  return lab.length > suf.length && lab.slice(-suf.length) === suf;
}

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
    // 🔴 D22 — TNT <= 1 on the SAME per-box clock as `ontime`; TNT 0 is genuine and counts here.
    // Strict subset of `ontime` by construction (PA_TNT1_SLA < PA_SLA); asserted before any write.
    r.tnt1 = r.arrived && r.tnt !== null && r.tnt <= PA_TNT1_SLA;
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
    // 🔴 D22 — nested UNDER `2 Day Shipments`, summed into nothing. Top-block only by decision:
    // emitting it per hub/carrier/state/box would need ~100 inserted rows on two tabs for a number
    // Kurt did not ask for. Adding the dimension later is a one-line change here plus the rows.
    m[paKey_('', PA_TNT1_LABEL)] = n(function (r) { return r.tnt1; });
    // 🔴 D22b (Kurt 2026-08-17: "we want tnt1 rows for these hubs too") — the SAME subset, at hub
    // grain, under the existing `hub||{label} · {grain}` key convention so the ordinary refresh fills
    // it with no further change. Only the HUB dimension: carrier/state/box were not asked for and
    // would cost ~100 inserted rows (see D22b "Scope" in the rules doc).
    //
    // 🔴 Zero-filled for every hub that emitted a `· 2 Day` key, unlike the sibling dim rows which
    // emit only non-zero. A count row that silently keeps LAST run's value is the stale-number bug;
    // and a per-hub TNT1 with no parent 2-Day key is meaningless, so the parent set is the honest
    // denominator. Hubs with no 2-Day boxes at all (Indianapolis, closed) emit nothing and their
    // cells stay BLANK — never 0.
    var hubTnt1 = {};
    recs.forEach(function (r) {
      if (!r.tnt1) return;
      var hk = r.assigned.hub;
      var hl = (hk === PA_NO_TAG) ? 'Unknown' : hk;
      hubTnt1[hl] = (hubTnt1[hl] || 0) + 1;
    });
    Object.keys(m).forEach(function (k) {
      if (k.indexOf('hub||') !== 0 || k.slice(-(' · 2 Day').length) !== ' · 2 Day') return;
      var hl = k.slice('hub||'.length, k.length - ' · 2 Day'.length);
      var v = hubTnt1[hl] || 0;
      // 🔴 subset assert at HUB grain (D22b). The top-block assert can hold while a single hub's
      // TNT1 exceeds its own 2 Day — that would mean the clock or the hub attribution drifted for
      // that hub, and the row would misrepresent that hub's headline. Refuse rather than publish.
      if (v > m[k]) {
        throw new Error('PA_ASSERT_TNT1_SUBSET: hub ' + hl + ' TNT1 ' + v + ' > 2 Day ' + m[k] +
                        ' — TNT<=1 must be a subset of TNT<=2 at every grain.');
      }
      m[paKey_('hub', hl + ' · ' + PA_TNT1_LABEL)] = v;
    });
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

/**
 * 🔴 D23 — `Routing Match` IS WRITE-ONCE, NOT 10-DAY-MATURING (Kurt 2026-08-17:
 * "for Routing match, let's do this walk forward or something 8/10 is already matured. we shouldn't
 * refresh this." … "matured on the carrier end. Shopify had the wrong tags so the data is wrong.").
 *
 * WHY THIS TAB DIFFERS FROM THE DELIVERY TABS. TnT2 / Lost in Transit measure the box against the
 * WORLD (did it arrive, when) — the world only gets more known with age, so re-running at day 5
 * CONVERGES and D15's 10-day window is right. Routing Match measures ROUTING TAG vs EXECUTED
 * CARRIER, and the TAG is mutable after ship: corrective retagging (wk0810 alone logged 376 tag
 * writes in `_outputs/logs/wk0810_corrective_delta.jsonl`) plus later RMFG/drift-in fixes rewrite
 * the very thing the measurement compares against. So a recompute days later scores the actuals
 * against tags that are no longer what the engine assigned at ship time: this number DEGRADES with
 * age instead of converging. It is only valid as a SHIP-TIME SNAPSHOT.
 *
 * THE RULE: first measurement wins, forever. `PA_MATURITY_DAYS` is untouched (still 10, still the
 * law on TnT2 / Lost in Transit).
 *
 * PLACEHOLDER vs MEASUREMENT — the distinction the freeze turns on. A cell freezes only if it holds
 * a MEASUREMENT: a number (these cells are percent-formatted, so `98.0%` lands as 0.98) or a bare
 * numeric/percent string. Blank, `n/a (immature)` (the Hub row's deliberate state — hub actuals need
 * carrier invoices, ~1wk lag) and `n/a` (nothing eligible to compare) are PLACEHOLDERS and stay
 * writable forever. Freezing on "non-empty" would nail the Hub row to `n/a (immature)` for all time,
 * which is the opposite of what it is there to say.
 */
function paRoutingIsMeasured_(v) {
  if (typeof v === 'number') return true;                  // percent-formatted cell holds a number
  var s = String(v === null || v === undefined ? '' : v).trim();
  if (!s) return false;                                    // never measured
  if (s === PA_IMMATURE) return false;                     // deliberate placeholder
  if (s.toLowerCase() === 'n/a') return false;             // "nothing eligible" placeholder
  return /^-?\d+(\.\d+)?\s*%?$/.test(s);                   // a measurement, and nothing else is
}

/** Current values of column `col` keyed EXACTLY as `paWriteOwned_` keys its writes (section-aware,
 *  so a future sectioned Routing Match cannot silently key differently from the writer). */
function paColumnByKey_(sheet, col) {
  var lastRow = Math.max(1, sheet.getLastRow());
  var grid = sheet.getRange(1, 1, lastRow, col).getValues();
  var out = {}, dim = '';
  for (var i = 0; i < grid.length; i++) {
    var lab = String(grid[i][0]).trim();
    if (Object.prototype.hasOwnProperty.call(PA_SECTIONS, lab)) { dim = PA_SECTIONS[lab]; continue; }
    if (!lab) continue;
    out[paKey_(dim, lab)] = grid[i][col - 1];
  }
  return out;
}

/**
 * D23 write path for `Routing Match`: filter the value map down to cells that are NOT already
 * measured, log write-vs-frozen per run, and verify after the flush that nothing measured moved.
 * The guard throws `PA_ASSERT_ROUTING_FROZEN` rather than repairing — an overwrite here destroys the
 * only ship-time reading that will ever exist for that cohort; it cannot be recomputed later.
 */
function paWriteRoutingFrozen_(sheet, col, vals, dry) {
  var cur = paColumnByKey_(sheet, col);
  var open = {}, frozen = [], writing = [];
  Object.keys(vals).forEach(function (k) {
    var name = k.replace('||', ' → ');
    if (Object.prototype.hasOwnProperty.call(cur, k) && paRoutingIsMeasured_(cur[k])) {
      frozen.push(name + ' held at ' + cur[k]);
      return;
    }
    open[k] = vals[k];
    writing.push(name + ': ' + JSON.stringify(cur[k] === undefined ? '' : cur[k]) + ' -> ' + vals[k]);
  });
  paLog_('  ' + PA_TABS.routing + ' D23 freeze — WRITE ' + writing.length +
         (writing.length ? ' [' + writing.join('; ') + ']' : '') + ' | SKIPPED-AS-FROZEN ' +
         frozen.length + (frozen.length ? ' [' + frozen.join('; ') + ']' : ''));
  // belt-and-braces: nothing measured may be in the write set (unreachable by the filter above).
  Object.keys(open).forEach(function (k) {
    if (Object.prototype.hasOwnProperty.call(cur, k) && paRoutingIsMeasured_(cur[k])) {
      throw new Error('PA_ASSERT_ROUTING_FROZEN: ' + k.replace('||', ' → ') + ' already holds the ' +
                      'measured value ' + cur[k] + ' — Routing Match is a ship-time snapshot (D23) ' +
                      'and is written exactly once per cohort.');
    }
  });
  var res = paWriteOwned_(sheet, col, open, dry);
  if (!dry) {
    SpreadsheetApp.flush();
    var after = paColumnByKey_(sheet, col);
    Object.keys(cur).forEach(function (k) {
      if (!paRoutingIsMeasured_(cur[k])) return;
      if (String(after[k]) !== String(cur[k])) {
        throw new Error('PA_ASSERT_ROUTING_FROZEN: ' + k.replace('||', ' → ') + ' changed ' + cur[k] +
                        ' -> ' + after[k] + ' during this run. A measured Routing Match cell is ' +
                        'immutable (D23).');
      }
    });
  }
  return res;
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
  // 🔴 D22 — TNT1 is a strict SUBSET of `2 Day Shipments`, never a sibling bucket. If this ever
  // exceeds the 2-Day count the clock or the threshold has drifted and the row would misrepresent
  // the headline; refuse rather than publish it.
  var tnt1 = 0;
  recs.forEach(function (r) { if (r.tnt1) tnt1++; });
  if (tnt1 > ot) {
    throw new Error('PA_ASSERT_TNT1_SUBSET: TNT1 ' + tnt1 + ' > 2 Day ' + ot +
                    ' — TNT<=1 must be a subset of TNT<=2.');
  }
  paLog_('  TNT1 (delivered, TNT<=' + PA_TNT1_SLA + ' from its OWN pickup): ' + tnt1 +
         ' of 2 Day ' + ot + ' — subset, summed into nothing');

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
      // 🔴 D23: write-once. NOT paWriteOwned_ directly — a measured cell here never gets rewritten.
      paWriteRoutingFrozen_(rm, rc, paRoutingValues_(recs), dry);
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
 * 🔴 D20 — A REFUSED RUN MUST BE LOUD. The asserts THROW before any write, which is correct: a
 * column that cannot be trusted is not written. But a throw on an unattended daily trigger is
 * INVISIBLE — the column simply keeps last night's numbers and looks fine. That is how
 * `_SHIP_2026-08-10` sat at Friday's figures through the weekend (Kurt had typed "investigate" into
 * `TnT2!G7`, a cell in a column with no row-1 header, so `PA_ASSERT_HEADERLESS_COLUMN` refused every
 * run). Staleness has no visual signature — a frozen number and a fresh one look identical.
 *
 * So the entry point is wrapped: ANY throw DMs Kurt privately (`slack_`, Code.gs — bot DM with an
 * email fallback) naming the assert and the run, then RETHROWS so the failure still registers as a
 * failed execution. This does not weaken a single assert; it only removes the silence around one.
 */
function refreshCurrentColumn(shipWeek) {
  try {
    return paRefreshCurrentColumn_(shipWeek);
  } catch (e) {
    var msg = String(e && e.message ? e.message : e);
    // The assert names are PA_ASSERT_* / PA_INSERT_* precisely so an alert can name the invariant.
    var named = (msg.match(/PA_[A-Z_]+/) || ['(unnamed failure)'])[0];
    var txt = 'Reship pivot refresh REFUSED TO WRITE — ' + named + '. The cohort column still holds ' +
              'the PREVIOUS run\'s numbers and will keep doing so until this clears. ' +
              (named === 'PA_ASSERT_HEADERLESS_COLUMN'
                 ? 'Most common cause: a note typed into a cell in a column that has no header in ' +
                   'row 1. Clear that cell (or give the column a row-1 header) and re-run. '
                 : '') +
              'Detail: ' + msg;
    try {
      if (typeof slack_ === 'function') slack_(txt, true);
      else Logger.log('🔴 slack_ unavailable — ' + txt);
    } catch (e2) { Logger.log('🔴 alert failed: ' + e2 + ' — original: ' + txt); }
    paLog_('🔴 ' + txt);
    throw e;                                   // still a failed execution; nothing is swallowed
  }
}

/**
 * Daily entry point. Refreshes the current cohort, then RECONCILES the previous one while it is
 * still inside the maturity window (D15) — a box frozen as 3+ Day / Not Arrived can later prove
 * delivered, and without this the column freezes at a value we already know is wrong.
 */
function paRefreshCurrentColumn_(shipWeek) {
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
  //
  // 🔴 THE GUARD SKIPS THE CURRENT LEG ONLY — NEVER THE INVOCATION. It used to `return` here, which
  // took the PREVIOUS-week reconcile leg down with it: on Monday 2026-08-17 the run logged
  // "SKIP — cohort ships today (age 0d)" and exited, so `_SHIP_2026-08-10` (age 7d, still inside
  // the 10-day maturity window and therefore still script-owned) never refreshed and sat frozen at
  // Friday's numbers for a full extra day. Every Monday silently froze last week's column. The
  // reconcile leg is exactly what the maturity model exists for, and a brand-new cohort having
  // nothing to measure says nothing about a 7-day-old one.
  var skipCurrent = (age <= 0);
  if (skipCurrent) {
    paLog_('SKIP CURRENT LEG — cohort ships today (age ' + age + 'd); nothing to measure yet. ' +
           'Continuing to the previous-week reconcile leg.');
  }

  var budget = { left: PA_PP_MAX_CALLS };             // shared across both legs
  var out = { dry: dry, current: null, previous: null };
  if (skipCurrent) out.skipped = 'ship-day';
  var t0 = new Date().getTime();
  if (!skipCurrent) {
    out.current = paRefreshOne_(cur, dry, budget, true);
  }
  var tCur = new Date().getTime();
  paLog_('leg timing: current ' + (skipCurrent ? 'skipped (ship day)'
                                               : ((tCur - t0) / 1000).toFixed(1) + 's'));

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
        // 🔴 loud, and NON-fatal: the current column is already written by this point (or was
        // deliberately skipped on a ship day). A previous-leg failure (6-min ceiling, a refused
        // column) must never cost us the current refresh.
        var pmsg = '🔴 reconcile leg FAILED for ' + prev + ' — current column is ' +
                   (skipCurrent ? 'not written on a ship day by design' : 'already written') +
                   ' and is unaffected. Error: ' + e;
        paLog_(pmsg);
        // 🔴 D20 — this branch SWALLOWS the error by design, so the wrapper's alert never sees it.
        // Alert here too, or a previous column can freeze for its whole remaining window in silence.
        try { if (typeof slack_ === 'function') slack_(pmsg, true); } catch (e2) { paLog_('alert failed: ' + e2); }
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
    // 🔴 D22b — the group is no longer always three contiguous rows: `{hub} · TNT1` may sit between
    // the good and bad rows (nested subset of `· 2 Day`, TnT2 only). Tolerating it here is not
    // cosmetic — `paInsertHubRows_` (adding the NEXT new hub) reads these groups, and a strict
    // "bad is directly below good" test would report ZERO groups and throw PA_INSERT_NO_HUB_SECTION
    // on a tab that is perfectly healthy.
    var tnt1 = (labels[i + 1] === hub + ' · ' + PA_TNT1_LABEL) ? (i + 2) : 0;
    var badI = tnt1 ? (i + 2) : (i + 1);
    if (labels[badI] !== hub + sufBad) continue;
    var rate = (labels[badI + 1] === '') ? (badI + 2) : 0;   // 1-based row of the rate row, 0 = none
    out.push({ hub: hub, good: i + 1, tnt1: tnt1, bad: badI + 1, rate: rate });   // 1-based sheet rows
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
 * 🔴 RESOLVE A BLANK-LABEL RATE ROW'S PAIR BY LABEL, NEVER BY POSITION (2026-08-17).
 *
 * The old rule — "the pair is the two rows directly above" — is true for every By-Hub / By-Carrier /
 * By-State / By-Box block, and FALSE for the TnT2 top block, because D16 inserted the three
 * observation rows between `3+ Day Shipments` and its rate row:
 *
 *     3  2 Day Shipments          <- good
 *     4  3+ Day Shipments         <- bad
 *     5     still moving (4+ days)          } D16 observations: a nested PARTITION of Not Arrived,
 *     6     no scan in 24h+ (investigating) } NOT a rate pair. Nothing may be summed from them.
 *     7     never picked up by carrier      }
 *     8  (blank label)            <- rate row; its pair is 3+4, not 6+7
 *
 * That mis-assumption made `paAuditRateRows_` report 5 FALSE POSITIVES (TnT2!B8..F8, all correct)
 * and refuse the D19 hub-row insert with PA_INSERT_PRE_AUDIT_FAILED — blocking a real maintenance
 * action on a phantom. Worse, `fillRateFormulasCurrentColumn` carried the SAME assumption on the
 * WRITE side: a freshly appended cohort column has no rate formulas at all (D19), so its top-block
 * cell is empty and eligible, and it would have written the headline late rate as
 * `no-scan-24h / (no-scan-24h + never-picked-up)` — a wrong HEADLINE number, silently.
 *
 * The resolver walks UP from the rate row past nested rows and stops at the nearest BAD-grain row,
 * requiring a GOOD-grain row directly above it. Grain is matched on the LABEL (`3+ Day Shipments`,
 * `Not Arrived`, or any `{key} · 3+ Day` / `{key} · Not Arrived`), so it is identical for the top
 * block and every dimension block and needs no special case per tab. Returns 0-based
 * `{good, bad}` indices, or null when it cannot resolve — and unresolvable stays a REPORTED
 * failure, never a silent pass.
 */
var PA_PAIR_WALK_MAX = 6;      // observation rows are 3; a longer gap means the tab shape changed

function paRatePairFor_(labels, i, tabName) {
  var g = paGrains_(tabName);                       // ['2 Day','3+ Day'] | ['Arrived','Not Arrived']
  function is(lab, grain) {
    // top block: `3+ Day Shipments` (TnT2) or bare `Not Arrived` (Lost). dim block: `{key} · {grain}`.
    return lab === grain || lab === grain + ' Shipments' || lab.slice(-(grain.length + 3)) === ' · ' + grain;
  }
  for (var b = i - 1, steps = 0; b >= 1 && steps < PA_PAIR_WALK_MAX; b--, steps++) {
    if (labels[b] === '') return null;              // hit another rate row / a gap — shape is wrong
    if (!is(labels[b], g[1])) continue;             // a nested observation row: skip it
    // 🔴 D22 — the GOOD row is no longer guaranteed to sit directly above the BAD row either: the
    // TNT1 row is nested UNDER `2 Day Shipments`, so the top block reads good / TNT1 / bad. Walk up
    // past nested rows here too, but only past labels on the EXPLICIT nested allowlist (PA_NESTED) —
    // skipping "anything that isn't a grain row" would let this stroll out of the block and pair
    // rows from two different sections, which is the exact bug family this resolver exists to kill.
    for (var gI = b - 1; gI >= 0 && b - gI <= PA_NESTED.length + 1; gI--) {
      if (is(labels[gI], g[0])) return { good: gI, bad: b };
      if (!paIsNested_(labels[gI])) return null;            // not a known nested row — shape is wrong
    }
    return null;
  }
  return null;
}

/**
 * 🔴 INDEPENDENT RATE-ROW VERIFIER. Every blank-label rate row must reference EXACTLY its own
 * good/bad pair (resolved by LABEL — see `paRatePairFor_`), in its OWN column. A row insert is the
 * one operation that can silently re-point these, so this runs before AND after any structural edit
 * and reports a count, not a feeling. Reference rows are read out of the formula text — a formula
 * that merely LOOKS right is not evidence.
 */
function paAuditRateRows_(sh) {
  var lastRow = Math.max(1, sh.getLastRow()), lastCol = Math.max(1, sh.getLastColumn());
  var labels = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]).trim(); });
  var formulas = sh.getRange(1, 1, lastRow, lastCol).getFormulas();
  var res = { rows: 0, cells: 0, ok: 0, bad: [] };
  for (var i = 2; i < lastRow; i++) {                 // 0-based; a rate row needs two rows above it
    if (labels[i] !== '' || labels[i - 1] === '' || labels[i - 2] === '') continue;
    var pair = paRatePairFor_(labels, i, sh.getName());
    if (!pair) {
      res.bad.push(sh.getName() + ' row ' + (i + 1) + ': cannot resolve a good/bad pair above this ' +
                   'blank-label rate row (nearest labels: ' + labels.slice(Math.max(0, i - 4), i).join(' | ') + ')');
      continue;
    }
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
      // 0-based pair indices -> SHEET rows are +1 each.
      var gRow = pair.good + 1, bRow = pair.bad + 1;
      if (rk.length === 2 && rk[0] === gRow && rk[1] === bRow && ck.length === 1 && ck[0] === self) {
        res.ok++;
      } else {
        res.bad.push(sh.getName() + '!' + self + (i + 1) + ' expects rows ' + gRow + '+' + bRow +
                     ' (' + labels[pair.good] + ' / ' + labels[pair.bad] + ') in col ' + self +
                     ', got ' + f);
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

// ------------------------------------------------------ TNT1 row maintenance (D22, human-invoked)

/**
 * 🔴 D22 — insert the ONE `TNT1` row on TnT2, directly BENEATH `2 Day Shipments`, so the block reads
 *     2 Day Shipments  /  TNT1 (its subset)  /  3+ Day Shipments  /  the three observations  /  rate.
 *
 * Same discipline as D19's hub insert and for the same reason: the unattended refresh writer NEVER
 * inserts rows (D13) — a structural edit re-points every reference below it. This is a deliberate,
 * human-invoked, idempotent maintenance action with the independent rate-row verifier on BOTH sides.
 *
 * 🔴 The insert lands BETWEEN the top block's good and bad rows, which is exactly what
 * `paRatePairFor_` had to be taught (see the D22 note there). Sheets re-points the existing rate
 * formula itself (B4 -> B5); the pre/post audit is what PROVES it, per cell, from the formula text.
 *
 * 🔴 HISTORY STAYS BLANK, NEVER ZERO. Only the current/non-frozen cohort columns are ever filled,
 * and they are filled by the normal refresh — not here. A 0 in a matured column would assert "no box
 * arrived in <=1 day that week"; we simply never measured it. Frozen columns (age >=
 * PA_MATURITY_DAYS) are Kurt-owned and stay empty forever.
 */
function paAddTnt1_(dry) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  var name = PA_TABS.tnt2, sh = ss.getSheetByName(name);
  paLog_('=== add TNT1 row on ' + name + ' — ' + (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');
  if (!sh) throw new Error('PA_INSERT_NO_TAB: ' + name + ' not found');
  paAssertColumns_(sh);                                  // never edit a tab that is already damaged

  var lastRow = Math.max(1, sh.getLastRow());
  var raw = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]); });
  var labels = raw.map(function (s) { return s.trim(); });
  if (labels.indexOf(PA_TNT1_LABEL) >= 0) {
    paLog_('  ' + name + ': ' + PA_TNT1_LABEL + ' already present at row ' +
           (labels.indexOf(PA_TNT1_LABEL) + 1) + ' — nothing to do');
    return { inserted: 0 };
  }
  var g = paGrains_(name);                               // ['2 Day','3+ Day']
  var good = labels.indexOf(g[0] + ' Shipments'), bad = labels.indexOf(g[1] + ' Shipments');
  if (good < 0 || bad < 0) {
    throw new Error('PA_INSERT_NO_TOP_BLOCK: ' + name + ' — could not find both "' + g[0] +
                    ' Shipments" and "' + g[1] + ' Shipments" in column A');
  }
  if (bad !== good + 1) {
    throw new Error('PA_INSERT_TOP_BLOCK_SHAPE: ' + name + ' — "' + g[1] + ' Shipments" (row ' +
                    (bad + 1) + ') is not directly below "' + g[0] + ' Shipments" (row ' +
                    (good + 1) + '); refusing to guess where TNT1 belongs');
  }
  // Format + indent template = the first D16 observation row: the SAME nesting level (a refinement
  // of a grain row), so TNT1 matches its siblings byte-for-byte rather than by a guessed space count.
  var tmpl = labels.indexOf(PA_OBS[0]);
  if (tmpl < 0) throw new Error('PA_INSERT_NO_TEMPLATE: ' + name + ' — observation row "' + PA_OBS[0] +
                                '" not found to clone formatting/indent from');
  var indent = (raw[tmpl].match(/^\s*/) || [''])[0];
  var anchor = bad + 1;                                  // 1-based sheet row to insert BEFORE

  var pre = paAuditRateRows_(sh);
  paLog_('  ' + name + ' BEFORE: ' + pre.rows + ' rate rows, ' + pre.cells + ' formulas, ' +
         pre.bad.length + ' wrong');
  if (pre.bad.length) {
    throw new Error('PA_INSERT_PRE_AUDIT_FAILED: ' + name + ' already has ' + pre.bad.length +
                    ' mis-pointed rate formula(s) — fix those before inserting rows: ' +
                    pre.bad.slice(0, 5).join(' | '));
  }
  if (dry) {
    paLog_('  [dry] insert 1 row before ' + name + '!A' + anchor + ' (directly under "' +
           labels[good] + '", above "' + labels[bad] + '")');
    paLog_('  [dry] ' + name + '!A' + anchor + ' = "' + indent + PA_TNT1_LABEL +
           '"  (formats cloned from row ' + (tmpl + 1) + ': "' + labels[tmpl] + '")');
    var rateRow = -1;                                    // first blank-label row BELOW the bad row
    for (var q = bad + 1; q < labels.length; q++) { if (labels[q] === '') { rateRow = q; break; } }
    paLog_('  [dry] the top-block rate row moves ' + (rateRow + 1) + ' -> ' + (rateRow + 2) +
           '; Sheets re-points its own formula and the POST audit proves it, per cell, from the ' +
           'formula text (expected pair after the insert: rows ' + (good + 1) + ' + ' + (bad + 2) + ')');
    paLog_('  [dry] values: written by the normal refresh into non-frozen cohort columns ONLY; ' +
           'frozen/matured columns stay BLANK (blank != zero)');
    return { inserted: 0, anchor: anchor, dry: true };
  }

  var maxCols = Math.max(1, sh.getMaxColumns());
  sh.insertRowsBefore(anchor, 1);
  var t = (tmpl + 1) >= anchor ? tmpl + 2 : tmpl + 1;    // 1-based template row after the shift
  sh.getRange(t, 1, 1, maxCols).copyTo(sh.getRange(anchor, 1, 1, maxCols), { formatOnly: true });
  sh.getRange(anchor, 1).setValue(indent + PA_TNT1_LABEL);
  var cur = paRightmostCohortCol_(sh);
  if (cur) sh.getRange(anchor, cur).setNumberFormat('0');   // count, not a rate
  SpreadsheetApp.flush();

  var post = paAuditRateRows_(sh);
  paLog_('  ' + name + ' AFTER : ' + post.rows + ' rate rows, ' + post.cells + ' formulas, ' +
         post.bad.length + ' wrong');
  if (post.bad.length) {
    throw new Error('PA_INSERT_POST_AUDIT_FAILED: ' + name + ' — the insert re-pointed ' +
                    post.bad.length + ' rate formula(s): ' + post.bad.slice(0, 5).join(' | '));
  }
  // A row insert must not create, destroy, or empty a rate row — TNT1 adds a COUNT row, not a pair.
  if (post.rows !== pre.rows || post.cells !== pre.cells) {
    throw new Error('PA_INSERT_ROW_COUNT: ' + name + ' rate rows ' + pre.rows + ' -> ' + post.rows +
                    ', formulas ' + pre.cells + ' -> ' + post.cells + ' (expected both unchanged)');
  }
  paAssertColumns_(sh);
  paLog_('  ' + name + ' ✅ TNT1 row at ' + anchor + ', rate integrity intact');
  paLog_('=== done (written) ===');
  return { inserted: 1, anchor: anchor };
}

/** 🔴 Kurt runs THIS first: logs exactly where the TNT1 row would land. Writes nothing. */
function previewAddTnt1Row() { return paAddTnt1_(true); }
/** Then THIS: inserts the row. Idempotent — re-running finds TNT1 and does nothing. */
function addTnt1Row() { return paAddTnt1_(false); }

// -------------------------------------------- PER-HUB TNT1 rows (D22b, human-invoked, TnT2 only)

/**
 * 🔴 D22b — a `{hub} · TNT1` row inside EVERY `By Hub (assigned)` group on TnT2, directly under that
 * hub's `· 2 Day` row (mirroring the top block, where TNT1 sits under `2 Day Shipments` and above
 * `3+ Day`). Kurt, on the hub block: **"we want tnt1 rows for these hubs too"**.
 *
 * 🔴 GENERIC OVER HUB NAME AND DRIVEN BY THE SHEET. The hub list comes from `paHubGroups_` — the rows
 * actually present — never from a hardcoded roster, so the next new hub needs no code change here
 * (it needs its 3 rows via `paAddHub_` first, then a re-run of this). `RMFG choice (2+ hubs open)`
 * is included: it is a real bucket of boxes. Indianapolis is included too — the hub is closed, so its
 * row simply stays empty, which is the honest rendering (blank ≠ 0).
 *
 * 🔴 THE RISK IS THE INSERT, NOT THE NUMBER. ~6-7 inserts each shift every reference below them.
 * Three defences, all of which must hold:
 *   1. **Bottom-up.** Groups are processed in DESCENDING row order, so an earlier insert can never
 *      invalidate a later group's already-computed row numbers. (Top-down would silently write the
 *      label into the wrong group by the third insert.)
 *   2. **Rate-row verifier before AND after**, reading FORMULA TEXT — a re-pointed formula still
 *      LOOKS right. A pre-existing mis-point REFUSES the whole run (`PA_INSERT_PRE_AUDIT_FAILED`);
 *      a regression after THROWS. Rate-row count and formula-cell count must be UNCHANGED: this adds
 *      COUNT rows, never a pair.
 *   3. **Idempotent.** A group that already has its TNT1 row is skipped, so a re-run cannot
 *      double-insert.
 *
 * TnT2 ONLY. The Lost in Transit tab's grains are Arrived / Not Arrived — arrival measures, with no
 * transit-time meaning — so a TNT1 row there would be a category error.
 *
 * 🔴 HISTORY STAYS BLANK, NEVER ZERO. Values arrive from the ordinary refresh into non-frozen cohort
 * columns only. A 0 in a matured column would assert "no box reached this hub's customers in <=1 day
 * that week"; we never measured it.
 */
function paAddHubTnt1_(dry) {
  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  var name = PA_TABS.tnt2, sh = ss.getSheetByName(name);
  paLog_('=== add per-hub TNT1 rows on ' + name + ' — ' + (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');
  if (!sh) throw new Error('PA_INSERT_NO_TAB: ' + name + ' not found');
  paAssertColumns_(sh);                                  // never edit a tab that is already damaged

  var lastRow = Math.max(1, sh.getLastRow());
  var raw = sh.getRange(1, 1, lastRow, 1).getValues().map(function (r) { return String(r[0]); });
  var labels = raw.map(function (s) { return s.trim(); });
  var groups = paHubGroups_(labels, name);
  if (!groups.length) throw new Error('PA_INSERT_NO_HUB_SECTION: ' + name + ' — no By Hub groups found');

  var pre = paAuditRateRows_(sh);
  paLog_('  ' + name + ' BEFORE: ' + pre.rows + ' rate rows, ' + pre.cells + ' formulas, ' +
         pre.bad.length + ' wrong');
  if (pre.bad.length) {
    throw new Error('PA_INSERT_PRE_AUDIT_FAILED: ' + name + ' already has ' + pre.bad.length +
                    ' mis-pointed rate formula(s) — fix those before inserting rows: ' +
                    pre.bad.slice(0, 5).join(' | '));
  }

  var todo = groups.filter(function (gr) { return !gr.tnt1; });
  groups.forEach(function (gr) {
    if (gr.tnt1) paLog_('  ' + name + ': ' + gr.hub + ' already has its TNT1 row (' + gr.tnt1 + ') — skip');
  });
  if (!todo.length) {
    paLog_('  ' + name + ': every hub group already has a TNT1 row — nothing to do');
    paLog_('=== done (idempotent no-op) ===');
    return { inserted: 0, hubs: [] };
  }
  // 🔴 BOTTOM-UP: descending anchor, so an insert never invalidates a not-yet-processed row number.
  todo.sort(function (a, b) { return b.good - a.good; });

  var cur = paRightmostCohortCol_(sh);
  var hubs = [];
  todo.forEach(function (gr) {
    var anchor = gr.good + 1;                            // insert BEFORE this row = directly under `· 2 Day`
    // label style byte-identical to its own siblings: the group's OWN indent, ' · ' = U+00B7.
    var indent = (raw[gr.good - 1].match(/^\s*/) || [''])[0];
    var label = indent + gr.hub + ' · ' + PA_TNT1_LABEL;
    hubs.push({ hub: gr.hub, row: anchor, label: label });
    if (dry) {
      paLog_('  [dry] ' + name + '!A' + anchor + ' = "' + label + '"  (under "' + labels[gr.good - 1] +
             '", above "' + labels[gr.bad - 1] + '"; formats cloned from row ' + gr.bad +
             ' = "' + labels[gr.bad - 1] + '")');
      return;
    }
    var maxCols = Math.max(1, sh.getMaxColumns());
    sh.insertRowsBefore(anchor, 1);
    // format template = this group's OWN `· 3+ Day` sibling (a count row at the same indent level);
    // it sat at gr.bad and the insert pushed it down by one.
    sh.getRange(gr.bad + 1, 1, 1, maxCols).copyTo(sh.getRange(anchor, 1, 1, maxCols), { formatOnly: true });
    sh.getRange(anchor, 1).setValue(label);
    if (cur) sh.getRange(anchor, cur).setNumberFormat('0');   // a count, not a rate
  });
  if (dry) {
    paLog_('  [dry] ' + todo.length + ' row(s) would be inserted, bottom-up (highest row first)');
    paLog_('  [dry] values: written by the normal refresh into non-frozen cohort columns ONLY; ' +
           'frozen/matured columns stay BLANK (blank != zero)');
    paLog_('=== done (DRY RUN — nothing written) ===');
    return { inserted: 0, hubs: hubs, dry: true };
  }
  SpreadsheetApp.flush();

  var post = paAuditRateRows_(sh);
  paLog_('  ' + name + ' AFTER : ' + post.rows + ' rate rows, ' + post.cells + ' formulas, ' +
         post.bad.length + ' wrong');
  if (post.bad.length) {
    throw new Error('PA_INSERT_POST_AUDIT_FAILED: ' + name + ' — the insert re-pointed ' +
                    post.bad.length + ' rate formula(s): ' + post.bad.slice(0, 5).join(' | '));
  }
  if (post.rows !== pre.rows || post.cells !== pre.cells) {
    throw new Error('PA_INSERT_ROW_COUNT: ' + name + ' rate rows ' + pre.rows + ' -> ' + post.rows +
                    ', formulas ' + pre.cells + ' -> ' + post.cells + ' (expected both unchanged — ' +
                    'TNT1 adds count rows, not pairs)');
  }
  paAssertColumns_(sh);
  hubs.forEach(function (h) { paLog_('  ' + name + ' ✅ ' + h.hub + ' TNT1 row at ' + h.row); });
  paLog_('=== done (' + todo.length + ' row(s) written, rate integrity intact) ===');
  return { inserted: todo.length, hubs: hubs };
}

/** 🔴 Kurt runs THIS first: logs exactly where every per-hub TNT1 row lands. Writes nothing. */
function previewAddHubTnt1Rows() { return paAddHubTnt1_(true); }
/** Then THIS: inserts them. Idempotent — a group that already has its TNT1 row is skipped. */
function addHubTnt1Rows() { return paAddHubTnt1_(false); }

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
      // 🔴 Pair resolved BY LABEL, never by position. This used to be `i-1 / i`, which is right for
      // every dimension block and WRONG for the TnT2 top block (D16's three observation rows sit
      // between `3+ Day Shipments` and its rate row). Because a freshly appended column has NO rate
      // formulas (D19), that cell is empty and eligible here — so the old code would have written
      // the HEADLINE late rate as no-scan-24h/(no-scan-24h + never-picked-up). Skip loudly rather
      // than write a guess.
      var pair = paRatePairFor_(labels, i, name);
      if (!pair) { paLog_('  ⚠️ ' + name + ' row ' + (i + 1) + ': unresolvable rate pair — SKIPPED'); continue; }
      var good = a1 + (pair.good + 1), bad = a1 + (pair.bad + 1);
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
