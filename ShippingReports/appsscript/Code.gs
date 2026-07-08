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
 *       SLACK_WEBHOOK   incoming-webhook URL (breach + failure alerts)
 *  3. Run seedStateFromSheet() once if importing the local runner's state
 *     (paste reship_report_state.json rows into a temp tab first), or just
 *     let enrichment catch up over a few runs.
 *  4. Run refresh() once manually to authorize scopes.
 *  5. Triggers -> add time-driven trigger: refresh, hourly.
 *  6. Then DISABLE the local schtask 'reship-report-refresh' (cutover step).
 */

var SHEET_ID = SpreadsheetApp.getActiveSpreadsheet().getId();
var STATE_TAB = '_state';
var MATURITY_DAYS = 14;
var LATE_REPORT_DAYS = 16;
var HIGH_VALUE = 150;
var WEEKS_BACK = 3;
var MAX_ENRICH_PER_RUN = 120; // 6-min cap guard

// ---------- entry point ----------

function refresh() {
  try {
    build_();
  } catch (e) {
    slack_('[CRITICAL] Reship report (Apps Script) FAILED: ' + e, true);
    throw e; // keep Apps Script failure emails as backup
  }
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

  // R1/R5/R6 sweep + R3/R4 incremental enrichment
  sweepAndEnrich_(state, oldest);
  saveState_(state);

  var cdf = tailCdf_(state, today);
  var tabs = buildTabs_(state, mondays, denoms, cdf, today, stamp);
  Object.keys(tabs).forEach(function (name) { writeTab_(name, tabs[name]); });

  breachAlert_(state, mondays[0], denoms, today);
}

// ---------- Shopify ----------

function shopifyGql_(query, variables) {
  var props = PropertiesService.getScriptProperties();
  var url = 'https://' + props.getProperty('SHOPIFY_STORE') + '.myshopify.com/admin/api/2026-04/graphql.json';
  for (var attempt = 0; attempt < 6; attempt++) {
    var resp = UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-Shopify-Access-Token': props.getProperty('SHOPIFY_TOKEN') },
      payload: JSON.stringify({ query: query, variables: variables || {} }),
      muteHttpExceptions: true,
    });
    if (resp.getResponseCode() === 429) { Utilities.sleep(2000); continue; }
    var d = JSON.parse(resp.getContentText());
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
      if (!rec.requested && enriched < MAX_ENRICH_PER_RUN) {
        var rq = findRequested_(cust.email || '', rec.entered);
        rec.requested = rq[0]; rec.ticket = rq[1];
        enriched++;
      }
      if (!rec.original_cohort && enriched < MAX_ENRICH_PER_RUN && cust.id) {
        // R3 + ship-Monday-precedes-complaint guard (misattribution bug 2026-07-08)
        var org = findOriginal_(cust.id, n.createdAt, n.name, rec.requested || rec.entered);
        rec.original = org[0]; rec.original_cohort = org[1]; rec.original_total = org[2];
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
  var resp = UrlFetchApp.fetch('https://appyhour.gorgias.com/api' + path + (qs ? '?' + qs : ''), {
    headers: {
      Authorization: 'Basic ' + Utilities.base64Encode(
        props.getProperty('GORGIAS_USER') + ':' + props.getProperty('GORGIAS_KEY')),
      'User-Agent': 'AppyHourReshipReport/1.0', // default UA gets Cloudflare 1010
    },
    muteHttpExceptions: true,
  });
  Utilities.sleep(1200); // ~0.8 req/s pacing
  if (resp.getResponseCode() >= 400) return null;
  return JSON.parse(resp.getContentText());
}

function findRequested_(email, entered) {
  if (!email) return ['', ''];
  var c = gorgiasGet_('/customers', { email: email });
  if (!c || !c.data || !c.data.length) return ['', ''];
  var t = gorgiasGet_('/tickets', { customer_id: c.data[0].id, limit: 30, order_by: 'created_datetime:desc' });
  if (!t || !t.data) return ['', ''];
  var floor = iso_(addDays_(new Date(entered), -14));
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

// ---------- tab builders ----------

function buildTabs_(state, mondays, denoms, cdf, today, stamp) {
  var tabs = {};
  var oldest = mondays[mondays.length - 1];

  mondays.forEach(function (mon) {
    var tag = '_SHIP_' + iso_(mon);
    var denom = denoms[tag];
    var cohortNames = Object.keys(state).filter(function (k) { return state[k].original_cohort === tag; });
    var rows = [];
    rows.push(['REFRESHED ' + stamp, 'cohort ' + tag,
      'denominator ' + denom + " orders (live Shopify, tag:'" + tag + "' -status:cancelled -tag:'Reship')",
      'day ' + Math.max(0, daysBetween_(mon, today)) + ' since ship Monday']);
    rows.push([]);
    rows.push(['ISSUE BREAKDOWN (unit = reship orders, R1/R6)']);
    rows.push(['Issue', 'Count', '% of cohort']);
    var issues = countBy_(cohortNames.map(function (k) { return state[k].issue; }));
    Object.keys(issues).sort(function (a, b) { return issues[b] - issues[a]; }).forEach(function (iss) {
      rows.push([iss, issues[iss], denom ? pct_(issues[iss] / denom) : 'n/a']);
    });
    rows.push(['TOTAL', cohortNames.length, denom ? pct_(cohortNames.length / denom) : 'n/a']);
    rows.push([]);

    var off = Math.min(daysBetween_(mon, today), MATURITY_DAYS);
    var prev = addDays_(mon, -7);
    var thisN = requestsByDay_(state, mon, off).length;
    var prevN = requestsByDay_(state, prev, off).length;
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
    var enteredWk = Object.keys(state).filter(function (k) {
      var e = state[k].entered || '';
      return e >= iso_(mon) && e <= wkEnd;
    });
    rows.push(['SHOPIFY RECONCILIATION — reship orders ENTERED this calendar week (entry date != request date, R4)']);
    rows.push(['Entered this week (deduped orders)', enteredWk.length]);
    var byCoh = countBy_(enteredWk.map(function (k) { return state[k].original_cohort || '?'; }));
    Object.keys(byCoh).sort(function (a, b) { return byCoh[b] - byCoh[a]; }).forEach(function (c) {
      rows.push(['  remediating ' + c, byCoh[c]]);
    });
    rows.push([]);
    rows.push(['DETAIL']);
    rows.push(['Reship', 'Requested', 'Entered', 'Issue', 'Original', 'Outbound week', 'Ticket', 'Status']);
    cohortNames.sort(function (a, b) { return (state[a].requested || '') < (state[b].requested || '') ? -1 : 1; })
      .forEach(function (k) {
        var r = state[k];
        rows.push([k, r.requested || 'UNKNOWN', r.entered, r.issue, r.original || '',
                   r.outbound || '', r.ticket || '', r.status || '']);
      });
    tabs['RS ' + tag] = rows;
  });

  // Summary
  var srows = [['REFRESHED ' + stamp,
    'unit = deduped reship orders; attribution = original cohort; denominators exclude reships'], [],
    ['Cohort', 'Cohort size', 'Reships to date', 'Rate', 'Day', 'Projected final', 'Projected rate', 'Maturity']];
  mondays.slice().reverse().forEach(function (mon) {
    var tag = '_SHIP_' + iso_(mon);
    var nNow = requestsByDay_(state, mon, null).length;
    var denom = denoms[tag];
    var off = daysBetween_(mon, today);
    var mature = off >= MATURITY_DAYS;
    var proj = mature ? nNow
      : (cdf[Math.min(off, MATURITY_DAYS)] ? Math.round(nNow / cdf[Math.min(off, MATURITY_DAYS)] * 10) / 10 : 'n/a');
    srows.push([tag, denom, nNow, denom ? pct_(nNow / denom) : 'n/a', off, proj,
      (denom && typeof proj === 'number') ? pct_(proj / denom) : 'n/a',
      mature ? 'FINAL' : 'maturing (day ' + off + ')']);
  });
  tabs['Summary'] = srows;

  // Pivots (Dan's 4 views)
  var winRecs = {};
  Object.keys(state).forEach(function (k) {
    if ((state[k].entered || '') >= iso_(oldest)) winRecs[k] = state[k];
  });
  var prows = [['REFRESHED ' + stamp, 'window: reship orders entered since ' + iso_(oldest) + ' (deduped, R6)'], []];
  prows = prows.concat(pivot_(countBy_(vals_(winRecs, 'entered')), 'Reship Created (Shopify entry date)'));
  prows = prows.concat(pivot_(countBy_(vals_(winRecs, 'requested', '(blank)')), 'Reship Requested (Slack/Gorgias ticket date)'));
  prows = prows.concat(pivot_(countBy_(vals_(winRecs, 'outbound', '(blank)')), 'Reship Outgoing ship week'));
  prows.push(['Reship Incoming ship week (original order cohort)', 'Count', 'Cohort size (excl. reships)', 'Reship rate']);
  var incoming = countBy_(vals_(winRecs, 'original_cohort', '(blank)'));
  Object.keys(incoming).sort().forEach(function (coh) {
    if (coh.indexOf('_SHIP_') === 0) {
      if (!(coh in denoms)) denoms[coh] = ordersCount_("tag:'" + coh + "' -status:cancelled -tag:'Reship'");
      var dn = denoms[coh];
      prows.push([coh, incoming[coh], dn, dn ? pct_(incoming[coh] / dn) : 'n/a']);
    } else {
      prows.push([coh, incoming[coh], '', '']);
    }
  });
  prows.push(['Grand Total', Object.keys(winRecs).length]);
  tabs['Pivots'] = prows;

  // Flags
  var frows = [['REFRESHED ' + stamp], [], ['Reship', 'Flag', 'Detail']];
  Object.keys(state).sort().forEach(function (k) {
    var rec = state[k];
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
  tabs['Flags'] = frows;

  return tabs;
}

// ---------- state (_state hidden tab) ----------

var STATE_COLS = ['key', 'entered', 'requested', 'ticket', 'issue', 'outbound', 'status',
                  'total', 'original', 'original_cohort', 'original_total', 'lifetime_orders'];

function loadState_() {
  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName(STATE_TAB);
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
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = ss.getSheetByName(STATE_TAB) || ss.insertSheet(STATE_TAB);
  sh.hideSheet();
  var rows = [STATE_COLS];
  Object.keys(state).sort().forEach(function (k) {
    var rec = state[k];
    rows.push([k].concat(STATE_COLS.slice(1).map(function (c) {
      return rec[c] != null ? rec[c] : '';
    })));
  });
  sh.clearContents();
  sh.getRange(1, 1, rows.length, STATE_COLS.length)
    .setNumberFormat('@') // plain text — stop Sheets from re-typing dates
    .setValues(rows);
}

// ---------- sheet writes ----------

function writeTab_(name, rows) {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
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

function slack_(text, critical) {
  var webhook = PropertiesService.getScriptProperties().getProperty('SLACK_WEBHOOK');
  if (!webhook) return; // fail-silent on alerting, loud in execution log
  try {
    UrlFetchApp.fetch(webhook, {
      method: 'post', contentType: 'application/json',
      payload: JSON.stringify({ text: (critical ? ':rotating_light: ' : ':warning: ') + text }),
    });
  } catch (e) { Logger.log('slack failed: ' + e); }
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
