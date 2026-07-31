/**
 * #exceptions alerter — hourly ParcelPanel exception sweep -> private Slack channel.
 * Constraints SSOT: ShippingReports/EXCEPTIONS_ALERT_RULES.md (repo). Read it before
 * changing anything here; the rules are authored there first.
 *
 * HOST: Kurt's copy of the reship sheet, 1kk1Qld-7QDIkhIKL93EIc6NGmOhnD_PZcJdTNX9m8Pg.
 * 🔴 That copy still ships with Code.gs's PIVOT_SHEET_ID aimed at Dan's LIVE reship report
 * (1weQz0AOAZJu7-I2reZ8fIqQ_b10BKWd4sYHn5HAUkGU). Triggers do not copy, so nothing runs by
 * default — but do NOT add a trigger for anything in Code.gs on this copy. The only trigger
 * this host should carry is hourlyExceptionSweep below.
 *
 * SETUP (one-time):
 *  1. Script Properties already needed by Code.gs are reused: SHOPIFY_STORE, SHOPIFY_TOKEN,
 *     PARCELPANEL_API_KEY, SLACK_BOT_TOKEN.
 *  2. Run cleanupHostSheet() once from the Exceptions menu to drop the inherited reship tabs.
 *  3. Run hourlyExceptionSweep() once manually to authorize scopes and seed the cohort.
 *  4. Triggers -> time-driven, hourly, hourlyExceptionSweep.
 */

var EXC_HOST_SHEET_ID = '1kk1Qld-7QDIkhIKL93EIc6NGmOhnD_PZcJdTNX9m8Pg';
var EXC_CHANNEL = 'C0BLKKPAW8P';          // private #exceptions. NEVER SLACK_WEBHOOK (public #reships).
var EXC_LOG_TAB = 'Exceptions';
var EXC_STATE_TAB = '_exc_state';
var EXC_MAX_POLL_PER_RUN = 900;           // 6-min Apps Script ceiling; PP is 1 GET/order, 50/batch
var EXC_PP_FAIL_RATIO = 0.2;              // above this share of failed fetches -> CRITICAL, not "all clear"
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

  function r(cls, ping) { return { cls: cls, detail: detail, ping: ping, status: status }; }

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

/** Fetch raw PP shipments for order numbers. Returns {orderNum: shipment}, plus a failure count. */
function excPpFetch_(orderNums) {
  var out = { ships: {}, failed: 0, attempted: 0 };
  var key = PropertiesService.getScriptProperties().getProperty('PARCELPANEL_API_KEY');
  if (!key || !orderNums.length) return out;
  var uniq = orderNums.filter(function (n, i) { return n && orderNums.indexOf(n) === i; });
  out.attempted = uniq.length;
  var reqs = uniq.map(function (n) {
    return {
      url: 'https://open.parcelwill.com/api/v2/tracking/order?order_number=' + encodeURIComponent(n),
      headers: { 'x-parcelpanel-api-key': key },
      muteHttpExceptions: true,
    };
  });
  for (var i = 0; i < reqs.length; i += 50) {
    var resp;
    try {
      resp = UrlFetchApp.fetchAll(reqs.slice(i, i + 50));
    } catch (err) {
      out.failed += Math.min(50, reqs.length - i);
      continue;
    }
    resp.forEach(function (rp, k) {
      if (rp.getResponseCode() !== 200) { out.failed++; return; }
      try {
        var o = JSON.parse(rp.getContentText());
        var ships = ((o.order || {}).shipments) || ((o.data || {}).shipments) || o.shipments || [];
        if (ships.length) out.ships[uniq[i + k]] = ships[0];
      } catch (e) { out.failed++; }
    });
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

function excMessage_(rec, cls, detail) {
  // Verbatim carrier text is non-negotiable — it's what lets Dan judge in 2s without opening
  // anything. Order link last so Slack doesn't unfurl over the detail.
  return (EXC_EMOJI_[cls] || ':warning:') + ' *' + cls.replace(/_/g, ' ') + '* — #' + rec.order +
         (rec.customer ? ' · ' + rec.customer : '') +
         (rec.carrier ? ' · ' + rec.carrier : '') +
         (rec.state ? ' · ' + rec.state : '') +
         '\n> ' + (detail || '(no carrier text)') +
         '\nhttps://admin.shopify.com/store/' +
         PropertiesService.getScriptProperties().getProperty('SHOPIFY_STORE') +
         '/orders?query=' + encodeURIComponent(rec.order);
}

function excLog_(stamp, rec, cls, detail) {
  var ss = excSS_();
  var sh = ss.getSheetByName(EXC_LOG_TAB);
  if (!sh) {
    sh = ss.insertSheet(EXC_LOG_TAB);
    sh.getRange(1, 1, 1, 7)
      .setValues([['when', 'order', 'customer', 'carrier', 'state', 'class', 'carrier event']])
      .setFontWeight('bold');
    sh.setFrozenRows(1);
  }
  sh.appendRow([stamp, '#' + rec.order, rec.customer, rec.carrier, rec.state, cls, detail]);
}

// ---------------------------------------------------------------- entry point

function hourlyExceptionSweep() {
  try {
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

    // 🔴 A PP outage must not read as "no exceptions" — silence has to fail loudly.
    if (pp.attempted && pp.failed / pp.attempted > EXC_PP_FAIL_RATIO) {
      throw new Error('ParcelPanel fetch failing: ' + pp.failed + '/' + pp.attempted +
                      ' — results suppressed rather than reported as all-clear');
    }

    var stamp = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
    var posted = 0;
    batch.forEach(function (on) {
      var rec = st[on], ship = pp.ships[on];
      rec.last_seen = stamp;
      if (!ship) return;
      var c = ship.carrier;
      rec.carrier = (c && (c.name || c.code)) || rec.carrier || '';
      var v = excClassify_(ship);
      if (v.cls === 'DELIVERED') { rec.open = false; return; }
      if (!v.ping) return;
      if (rec.alerted.indexOf(v.cls) >= 0) return;   // dedup on (order, class)
      excSlackPost_(excMessage_(rec, v.cls, v.detail));
      excLog_(stamp, rec, v.cls, v.detail);
      rec.alerted.push(v.cls);
      rec.open = false;                              // notified once; a human owns it now
      posted++;
    });

    excSaveState_(st);
    Logger.log('exceptions sweep: polled ' + batch.length + '/' + open.length + ' open, posted ' + posted);
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

// Named onOpen so the menu installs on the host copy, where Code.gs (which owned the reship
// onOpen) is deleted. If Code.gs is ever restored alongside this file, rename one of them —
// two onOpen definitions in one project means the later one silently wins.
function onOpen() {
  SpreadsheetApp.getUi().createMenu('Exceptions')
    .addItem('Run sweep now', 'hourlyExceptionSweep')
    .addItem('Replay classifier self-test', 'excSelfTest')
    .addItem('Remove inherited reship tabs', 'cleanupHostSheet')
    .addToUi();
}

/** Manual only. Drops the reship tabs this copy inherited. Never called by the hourly job. */
function cleanupHostSheet() {
  var ss = excSS_();
  if (ss.getId() !== EXC_HOST_SHEET_ID) throw new Error('refusing to clean a sheet that is not the host copy');
  var drop = ['Product Mix (T)', 'Product Mix', 'Count of requested', 'Count of created',
              'Count of incoming week', 'Count of outgoing week', 'Triage', 'Raw Data', '_seed', '_state'];
  var removed = [];
  drop.forEach(function (name) {
    var sh = ss.getSheetByName(name);
    if (sh) { ss.deleteSheet(sh); removed.push(name); }
  });
  Logger.log('removed: ' + removed.join(', '));
}

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
