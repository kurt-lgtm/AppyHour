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
  // ALL Slack alerts (error + breach) go to the single SLACK_WEBHOOK = Kurt's
  // channel only (Kurt 2026-07-13). Apps Script failure emails are the backup.
  try {
    build_();
  } catch (e) {
    slack_(':rotating_light: Reship report (Apps Script) FAILED: ' + e, true);
    throw e;
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

  // R1/R5/R6 sweep + R3/R4 incremental enrichment. Sweep 92d back so the Daily
  // tab has history; enrichment stays incremental under the 6-min GAS cap.
  var histSince = iso_(addDays_(today, -HISTORY_DAYS));
  var sweepFrom = histSince < iso_(oldest) ? new Date(histSince) : oldest;
  sweepAndEnrich_(state, sweepFrom);
  enrichBoxTypes_(state, mondays);
  fillRequestedFromSlack_(state, histSince);
  saveState_(state);

  refreshPivotSheet_(state, mondays);
  writeProductMix_(mondays, denoms, stamp);
  try { writeTriage_(state, oldest, stamp); }
  catch (e) { Logger.log('triage failed (non-fatal): ' + e); }
  writeDaily_(state, stamp);
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
  sh.clearContents();
  var width = 9;
  sh.getRange(1, 1, rows.length, width).setValues(rows.map(function (r) {
    return r.slice(0, width).concat(new Array(Math.max(0, width - r.length)).fill(''));
  }));
  sh.getRange('B:C').setNumberFormat('@');
  props.setProperty('PIVOT_WATERMARK', String(maxNum));
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
  var resp = UrlFetchApp.fetch('https://appyhour.gorgias.com/api' + path + (qs ? '?' + qs : ''), {
    headers: {
      Authorization: 'Basic ' + Utilities.base64Encode(
        props.getProperty('GORGIAS_USER') + ':' + (props.getProperty('GORGIAS_KEY') || props.getProperty('GORGIAS_API_KEY'))),
      'User-Agent': 'AppyHourReshipReport/1.0', // default UA gets Cloudflare 1010
    },
    muteHttpExceptions: true,
  });
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
                  'total', 'original', 'original_cohort', 'original_total', 'lifetime_orders', 'original_boxtype'];

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
  sh.clearContents();
  sh.getRange(1, 1, rows.length, STATE_COLS.length)
    .setNumberFormat('@') // plain text — stop Sheets from re-typing dates
    .setValues(rows);
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

// Alert Kurt PRIVATELY only (Kurt 2026-07-13: never a public channel). Bot DM
// via chat.postMessage (SLACK_BOT_TOKEN prop, needs Bot chat:write); email
// fallback. NEVER the SLACK_WEBHOOK — that's bound to public #reships.
function slack_(text, critical) {
  var pfx = (critical ? ':rotating_light: ' : ':warning: ') + text;
  var token = PropertiesService.getScriptProperties().getProperty('SLACK_BOT_TOKEN');
  if (token) {
    try {
      var r = UrlFetchApp.fetch('https://slack.com/api/chat.postMessage', {
        method: 'post', contentType: 'application/json',
        headers: { Authorization: 'Bearer ' + token },
        payload: JSON.stringify({ channel: KURT_SLACK_ID, text: pfx }),
        muteHttpExceptions: true,
      });
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
function classifyReship_(text) {
  var t = String(text).toLowerCase();
  for (var i = 0; i < ISSUE_RULES_.length; i++) {
    if (ISSUE_RULES_[i][0].test(t)) return [ISSUE_RULES_[i][1], ISSUE_RULES_[i][2]];
  }
  return [null, null];
}
function parseReshipMsg_(text, createdIso) {
  var c = classifyReship_(text);
  if (!c[0]) return null;
  var onum = (String(text).match(/#\s*(\d{5,6})\b/) || [])[1] || null;
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
    var r = UrlFetchApp.fetch(url, { headers: { Authorization: 'Bearer ' + token }, muteHttpExceptions: true });
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
     'sizes = live Shopify; reship counts = COUNTIFS over Raw Data (same rows as Count-of-incoming-week); blank box-type = Regular'],
    ['Cohort', 'Cohort size',
     'Regular Box', 'Regular Box Reship discrete', 'Regular Box Reship %',
     'Medium Tray', 'Medium Tray Reship discrete', 'Medium Tray Reship %',
     'Large Tray', 'Large Tray Reship discrete', 'Large Tray Reship %']];
  var RD = "'Raw Data'";  // E=incoming, I=box type
  // WALK-FORWARD cohort list (Kurt 2026-07-20): every ship-week present in the Raw
  // Data ledger, UNION the current window — accumulates, never drops old weeks.
  var cohortSet = {};
  mondays.forEach(function (m) { cohortSet['_SHIP_' + iso_(m)] = true; });
  try {
    SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Raw Data')
      .getRange('E2:E' + Math.max(2, SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Raw Data').getLastRow()))
      .getValues().forEach(function (r) { if (String(r[0]).indexOf('_SHIP_') === 0) cohortSet[r[0]] = true; });
  } catch (e) {}
  Object.keys(cohortSet).sort().forEach(function (tag, i) {
    var base = "tag:'" + tag + "' -status:cancelled -tag:'Reship'";
    var total = ordersCount_(base), med = ordersCount_(base + ' sku:AHB-MCUST-TRAY*'),
        lge = ordersCount_(base + ' sku:AHB-LCUST-TRAY*'), r = i + 3;
    var medC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ',' + RD + '!$I:$I,"Medium Tray")';
    var lgeC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ',' + RD + '!$I:$I,"Large Tray")';
    var regC = '=COUNTIFS(' + RD + '!$E:$E,$A' + r + ')-G' + r + '-J' + r;
    rows.push([tag, total,
      total - med - lge, regC, '=IF(C' + r + '>0,TEXT(D' + r + '/C' + r + ',"0.00%"),"n/a")',
      med, medC, '=IF(F' + r + '>0,TEXT(G' + r + '/F' + r + ',"0.00%"),"n/a")',
      lge, lgeC, '=IF(I' + r + '>0,TEXT(J' + r + '/I' + r + ',"0.00%"),"n/a")']);
  });
  writeTabTo_(PIVOT_SHEET_ID, 'Product Mix', rows, true);
}

// ---- Triage (Slack-only feed, requested-not-entered) ----
function writeTriage_(state, oldest, stamp) {
  var originals = {};
  Object.keys(state).forEach(function (k) {
    var o = String(state[k].original || '').replace(/^#/, ''); if (o) originals[o] = true;
  });
  var prev = {};
  try {
    var psh = SpreadsheetApp.openById(PIVOT_SHEET_ID).getSheetByName('Triage');
    if (psh && psh.getLastRow() >= 3) {
      psh.getRange('A3:F' + psh.getLastRow()).getValues().forEach(function (row) {
        if (row[0]) prev[String(row[0])] = row[5];
      });
    }
  } catch (e) {}
  var recs = fetchSlackReship_(Math.floor(new Date(oldest).getTime() / 1000));
  var rows = [['REFRESHED ' + stamp,
    'Slack #reship-and-order-requests posts w/o an entered reship order — NOT counted anywhere. Col F is YOURS: reship / refund / no action', '', '', '', 'Decision'],
    ['Key', 'Posted', 'Issue', 'Order', 'Gorgias', 'Decision']];
  recs.forEach(function (r) {
    var onum = String(r.order_number || '');
    if (onum && originals[onum]) return;  // already remediated
    var key = String(r.gorgias_id || onum || (r.created_ts || ''));
    rows.push([key, (r.created_ts || '').slice(0, 16), r.issue || '',
               onum ? '#' + onum : '', String(r.gorgias_id || ''), prev[key] || '']);
  });
  writeTabTo_(PIVOT_SHEET_ID, 'Triage', rows, false);
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
  var sh = ss.getSheetByName(name) || ss.insertSheet(name);
  sh.clearContents();
  var w = Math.max.apply(null, rows.map(function (r) { return r.length; }).concat([1]));
  var padded = rows.map(function (r) { return r.concat(new Array(w - r.length).fill('')); });
  var rng = sh.getRange(1, 1, padded.length, w);
  if (userEntered) rng.setValues(padded); else rng.setValues(padded);
}
