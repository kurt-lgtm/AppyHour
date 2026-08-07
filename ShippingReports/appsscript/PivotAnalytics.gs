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

/** 🔴 A real carrier scan. CONFIRMED is emitted at LABEL CREATION and is NOT a scan — including it
 * makes every never-collected box look like it moved ("has events" is the wrong filter). */
var PA_MOVE = { IN_TRANSIT: 1, OUT_FOR_DELIVERY: 1, ATTEMPTED_DELIVERY: 1, READY_FOR_PICKUP: 1, PICKED_UP: 1 };

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
function paAssigned_(tags) {
  var picks = [];
  (tags || []).forEach(function (t) {
    var m = String(t).trim().match(PA_AHB);
    if (m && String(m[1]).trim().toUpperCase().indexOf('NO ') !== 0) picks.push(m);
  });
  if (picks.length !== 1) return { carrier: '', hub: PA_NO_TAG };
  return { carrier: paCarrier_(picks[0][1]), hub: String(picks[0][2]).trim() };
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
  var out = [], cursor = null;
  while (true) {
    var conn = shopifyGql_(q, { q: qs, cursor: cursor }).orders;
    conn.edges.forEach(function (e) { out.push(paDerive_(e.node)); });
    if (!conn.pageInfo.hasNextPage) break;
    cursor = conn.pageInfo.endCursor;
  }
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
  if (!key || !orderNums.length) return out;
  for (var i = 0; i < orderNums.length; i += 50) {
    var slice = orderNums.slice(i, i + 50);
    var resp = UrlFetchApp.fetchAll(slice.map(function (n) {
      return { url: 'https://open.parcelwill.com/api/v2/tracking/order?order_number=' + encodeURIComponent(n),
               headers: { 'x-parcelpanel-api-key': key }, muteHttpExceptions: true };
    }));
    resp.forEach(function (r, k) {
      if (r.getResponseCode() !== 200) return;
      try {
        var o = JSON.parse(r.getContentText());
        var ships = ((o.order || {}).shipments) || ((o.data || {}).shipments) || o.shipments || [];
        if (!ships.length) return;
        var s = ships[0];
        var status = String(s.delivery_status || s.status || '').toUpperCase();
        var pk = (String(s.pickup_date || '').match(/^\d{4}-\d{2}-\d{2}/) || [null])[0];
        var dl = (String(s.delivery_date || '').match(/^\d{4}-\d{2}-\d{2}/) || [null])[0];
        if (pk && (pk < winLo || pk > winHi)) pk = null;
        if (dl && (dl < winLo || dl > winHi)) dl = null;
        out[slice[k]] = { delivered: status.indexOf('DELIVER') >= 0, pk: pk, dl: dl };
      } catch (e) {}
    });
  }
  return out;
}

/**
 * 🔴 UNION: delivered = Shopify DELIVERED event OR PP status='delivered'. NEITHER SOURCE IS
 * COMPLETE — on wk0803 PP was hiding 224 deliveries Shopify had, and Shopify's feed was missing
 * OnTrac's final scan on 2 boxes PP had. A box is undelivered only if BOTH are silent.
 * 🔴 Late is measured over the WHOLE cohort (survivorship): an undelivered past-SLA box is a miss,
 * not "pending", and Lost in Transit is INSIDE 3+ Day — never a bucket beside it.
 */
function paUnion_(recs, pp) {
  recs.forEach(function (r) {
    var p = pp[r.order] || {};
    var ppD = !!(p.delivered && p.dl);
    r.arrived = !!r.shDlv || ppD;
    if (r.shDlv && r.shMove) r.tnt = paDayDiff_(r.shMove, r.shDlv);
    else if (ppD && p.pk) r.tnt = paDayDiff_(p.pk, p.dl);
    else r.tnt = null;
    r.moved = !!r.shMove || !!p.pk;
    r.ontime = r.arrived && r.tnt !== null && r.tnt <= PA_SLA;
    r.late = !r.ontime;
    // lost vs active: a scan in the last 24h means the box is demonstrably MOVING (super-late, not
    // lost). Zero scans, or silent >=24h, stays LOST — unverified is not the same as known-not-lost.
    var age = paHoursSince_(r.lastScanIso);
    r.active = !r.arrived && age !== null && age < PA_ACTIVE_HRS;
    r.lost = !r.arrived && !r.active;
  });
  return recs;
}

// ---------------------------------------------------------------- aggregate

function paValues_(recs, tab) {
  var total = recs.length, m = {}, i;
  function n(pred) { var c = 0; recs.forEach(function (r) { if (pred(r)) c++; }); return c; }
  var PRED = (tab === PA_TABS.tnt2)
    ? { '2 Day': function (r) { return r.ontime; }, '3+ Day': function (r) { return r.late; } }
    : { 'Arrived': function (r) { return r.arrived; }, 'Not Arrived': function (r) { return !r.arrived; } };
  m['Total Shipments'] = total;
  Object.keys(PRED).forEach(function (k) {
    m[(tab === PA_TABS.tnt2) ? (k + ' Shipments') : k] = n(PRED[k]);
    ['hub', 'carrier', 'state', 'box'].forEach(function (dim) {
      recs.forEach(function (r) {
        if (!PRED[k](r)) return;
        var key = (dim === 'hub') ? r.assigned.hub : r[dim];
        var lab = (dim === 'hub' && key === PA_NO_TAG) ? 'Unknown' : key;
        m[lab + ' · ' + k] = (m[lab + ' · ' + k] || 0) + 1;
      });
    });
  });
  if (tab === PA_TABS.tnt2) m['of which: Lost in Transit'] = n(function (r) { return r.lost; });
  return m;
}

function paRoutingValues_(recs) {
  var m = {};
  // Hub compares assigned vs ACTUAL, and actual comes from carrier invoices (~1wk lag). Filling it
  // from the tag would compare the tag to itself and always read 100%.
  m['Routing Matched - Hub'] = PA_IMMATURE;
  var elig = 0, ok = 0;
  recs.forEach(function (r) {
    if (!r.assigned.carrier || r.carrier === 'Unknown') return;   // uncomparable, not "matched"
    elig++; if (r.assigned.carrier === r.carrier) ok++;
  });
  m['Routing Matched - Carrier'] = elig ? (Math.round(ok / elig * 1000) / 10).toFixed(1) + '%' : 'n/a';
  return m;
}

// ---------------------------------------------------------------- write

/**
 * 🔴 WALK-FORWARD FREEZE, enforced as an assert not a convention: this resolves the current
 * ship-week column and REFUSES to return anything left of the rightmost header. Matured columns
 * are frozen.
 */
function paCurrentCol_(sheet, shipWeek) {
  var lastCol = Math.max(1, sheet.getLastColumn());
  var headers = sheet.getRange(1, 1, 1, lastCol).getValues()[0].map(String);
  var idx = headers.indexOf(shipWeek);
  if (idx >= 0) {
    var col = idx + 1;
    var rightmost = 1;
    for (var i = 0; i < headers.length; i++) if (/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(headers[i].trim())) rightmost = i + 1;
    if (col < rightmost) {
      throw new Error('REFUSING to write matured column ' + col + ' (' + shipWeek +
                      '); rightmost cohort column is ' + rightmost);
    }
    return col;
  }
  return lastCol + 1;   // new week rolled → append to the right
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
  var wrote = 0, missing = [], seen = {};
  for (var i = 0; i < labels.length; i++) {
    var lab = labels[i];
    if (!lab || !Object.prototype.hasOwnProperty.call(valuesByLabel, lab)) continue;
    var v = valuesByLabel[lab];
    seen[lab] = true;
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
    if (!seen[k] && /·/.test(k)) missing.push(k + '=' + valuesByLabel[k]);
  });
  if (missing.length) {
    paLog_('  ⚠️ ' + sheet.getName() + ': no row on the sheet for ' + missing.length +
           ' bucket(s) — NOT written anywhere, a human must add the row: ' + missing.join(', '));
  }
  return { wrote: wrote, missing: missing };
}

// ---------------------------------------------------------------- entry point

function refreshCurrentColumn(shipWeek) {
  var dry = !paWriteArmed_();
  if (!shipWeek) shipWeek = paCurrentShipWeek_();
  paLog_('=== refreshCurrentColumn ' + shipWeek + ' — ' + (dry ? 'DRY RUN (no writes)' : 'WRITING') + ' ===');

  var recs = paFetchCohort_(shipWeek);
  var mon = shipWeek.replace('_SHIP_', '');
  var lo = Utilities.formatDate(new Date(new Date(mon + 'T00:00:00Z').getTime() - 2 * 86400000), PA_TZ, 'yyyy-MM-dd');
  var hi = Utilities.formatDate(new Date(new Date().getTime() + 86400000), PA_TZ, 'yyyy-MM-dd');
  paUnion_(recs, paPpFetch_(recs.map(function (r) { return r.order; }), lo, hi));

  var total = recs.length;
  var ot = 0, lt = 0, arr = 0, lost = 0, active = 0;
  recs.forEach(function (r) { if (r.ontime) ot++; if (r.late) lt++; if (r.arrived) arr++; if (r.lost) lost++; if (r.active) active++; });
  // 🔴 asserts — every pass, before any write
  if (ot + lt !== total) throw new Error('2 Day + 3+ Day (' + (ot + lt) + ') != Total ' + total);
  if (lost + active !== total - arr) throw new Error('lost+active (' + (lost + active) + ') != Not Arrived ' + (total - arr));
  paLog_('cohort ' + total + '  2Day ' + ot + '  3+Day ' + lt + '  arrived ' + arr +
         '  notArrived ' + (total - arr) + '  lost ' + lost + '  active ' + active +
         '  lateRate ' + (lt / total * 100).toFixed(2) + '%');

  var ss = SpreadsheetApp.openById(PIVOT_SHEET_ID);
  [PA_TABS.tnt2, PA_TABS.lost].forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (!sh) { paLog_('  ⚠️ missing tab ' + name); return; }
    var col = paCurrentCol_(sh, shipWeek);
    paLog_('-- ' + name + ' col ' + col);
    paWriteOwned_(sh, col, paValues_(recs, name), dry);
  });
  var rm = ss.getSheetByName(PA_TABS.routing);
  if (rm) {
    var rc = paCurrentCol_(rm, shipWeek);
    paLog_('-- ' + PA_TABS.routing + ' col ' + rc);
    paWriteOwned_(rm, rc, paRoutingValues_(recs), dry);
  }
  paLog_('=== done (' + (dry ? 'DRY RUN — nothing written' : 'written') + ') ===');
  return { total: total, twoDay: ot, threePlus: lt, arrived: arr, lost: lost, active: active, dry: dry };
}

/** Rightmost `_SHIP_` header already on the TnT2 tab = the cohort currently being tracked. */
function paCurrentShipWeek_() {
  var sh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName(PA_TABS.tnt2);
  var headers = sh.getRange(1, 1, 1, Math.max(1, sh.getLastColumn())).getValues()[0].map(String);
  var week = '';
  headers.forEach(function (h) { if (/^_SHIP_\d{4}-\d{2}-\d{2}$/.test(h.trim())) week = h.trim(); });
  if (!week) throw new Error('no _SHIP_ column header found on ' + PA_TABS.tnt2);
  return week;
}

/** Dry-run entry point for Kurt: run this, then read View → Logs. Writes nothing, ever. */
function previewCurrentColumn() {
  var saved = PropertiesService.getScriptProperties().getProperty('PIVOT_ANALYTICS_WRITE');
  PropertiesService.getScriptProperties().deleteProperty('PIVOT_ANALYTICS_WRITE');
  try { return refreshCurrentColumn(); }
  finally { if (saved) PropertiesService.getScriptProperties().setProperty('PIVOT_ANALYTICS_WRITE', saved); }
}
