// Offline proof for D43 gorgias guard. Loads the REAL Code.gs into a VM with Apps Script stubs.
// No network, no sheet, no live API — every stub throws if anything tries to reach out.
const fs = require('fs'), vm = require('vm'), path = require('path');
const SRC = 'C:\\Users\\Work\\Claude Projects\\AppyHour\\ShippingReports\\appsscript\\Code.gs';

// The verbatim string Google threw at 7:08:40 AM CDT on 2026-09-08.
const REAL = 'Bandwidth quota exceeded: https://appyhour.gorgias.com/api/customers?email=redacted%40example.com. Try reducing the rate of data transfer.';

let logs = [], fetches = 0, mode = 'wall';
const ctx = {
  Logger: { log: m => logs.push(String(m)) },
  Utilities: {
    sleep(){}, base64Encode: s => 'B64',
    // real enough for iso_() / stamps: yyyy-MM-dd[THH:mm:ss] off the Date, UTC
    formatDate: (d, tz, fmt) => {
      const p = n => String(n).padStart(2, '0');
      const s = d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate());
      return /HH/.test(String(fmt)) ? s + 'T' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds()) : s;
    },
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => 'STUB', setProperty(){} }) },
  Session: { getScriptTimeZone: () => 'America/Chicago' },
  SpreadsheetApp: { openById: () => { throw new Error('no sheet in test'); } },
  UrlFetchApp: {
    fetch(url) {
      fetches++;
      if (mode === 'wall') { const e = new Error(REAL); throw e; }
      // "healthy" mode: tiny canned Gorgias payloads
      const body = /\/customers/.test(url)
        ? JSON.stringify({ data: [{ id: 4242 }] })
        : JSON.stringify({ data: [
            { id: 900, created_datetime: '2026-09-05T10:00:00' },
            { id: 901, created_datetime: '2026-09-03T10:00:00' },
            { id: 902, created_datetime: '2026-08-01T10:00:00' }, // older than floor -> covered
          ], meta: {} });
      return { getResponseCode: () => 200, getContentText: () => body, getAllHeaders: () => ({}) };
    },
    fetchAll(){ throw new Error('not used'); },
  },
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(SRC, 'utf8'), ctx, { filename: 'Code.gs' });

function assert(ok, msg) { console.log((ok ? 'PASS  ' : 'FAIL  ') + msg); if (!ok) process.exitCode = 1; }

// 1. the detector sees the real string, through both names
assert(ctx.gasFetchQuotaWall_(new Error(REAL)) === true, 'gasFetchQuotaWall_ matches the verbatim 2026-09-08 Gorgias-URL exception');
assert(ctx.shopifyQuotaWall_(new Error(REAL)) === true, 'shopifyQuotaWall_ alias delegates to the same detector');
assert(ctx.gasFetchQuotaWall_(new Error('HTTP 429 rate limited')) === false, 'a real vendor 429 is NOT mistaken for the wall');
assert(ctx.gasFetchQuotaWall_(new Error('Service invoked too many times for one day: urlfetch.')) === true, 'sibling daily-limit wording also matches');

// 2. healthy path still resolves the earliest in-window ticket, and pages ONCE
mode = 'healthy'; fetches = 0;
let r = ctx.findRequested_('a@b.com', '2026-09-06', '');
assert(r[0] === '2026-09-03' && r[1] === '901', 'healthy: earliest in-window ticket resolved (' + JSON.stringify(r) + ')');
assert(fetches === 2, 'healthy: exactly 2 requests (customers + ONE ticket page), got ' + fetches);

// 3. the wall degrades instead of throwing
mode = 'wall'; fetches = 0; logs = [];
let threw = null, out = null;
try { out = ctx.findRequested_('a@b.com', '2026-09-06', ''); } catch (e) { threw = e; }
assert(threw === null, 'findRequested_ does NOT throw on the wall (it threw: ' + threw + ')');
assert(JSON.stringify(out) === '["",""]', 'findRequested_ returns a blank pair, never a guessed date');
assert(ctx.RUN_QUOTA_WALL_ && ctx.RUN_QUOTA_WALL_.leg === 'gorgias', 'run-level wall recorded, leg named "gorgias"');
assert(fetches === 1, 'only ONE request was placed before the stop, got ' + fetches);

// 4. every later call is refused WITHOUT spending another byte
fetches = 0;
out = ctx.findRequested_('c@d.com', '2026-09-06', '');
assert(fetches === 0 && JSON.stringify(out) === '["",""]', 'post-wall calls place ZERO further requests');
assert(ctx.gorgiasForOrder_ && (function(){ let f0 = fetches; ctx.gorgiasGet_('/tickets', {}); return fetches === f0; })(), 'gorgiasGet_ itself short-circuits after the wall');

// 5. the run says it stopped, and how much is unenriched
const note = ctx.quotaPartialNote_();
assert(/PARTIAL/.test(note) && /url-fetch DATA quota/.test(note), 'partial note is explicit about the meter');
assert(/NOT the vendor's rate limit/.test(note), 'partial note re-labels the meter away from Gorgias');
assert(/row\(s\) left UNENRICHED/.test(note), 'partial note reports the unenriched count');
assert(/gorgias leg/.test(note), 'partial note names the leg that met the wall');
// 6. 🔴 the sweep STOPS CLEANLY and the caller keeps everything already accumulated
ctx.RUN_QUOTA_WALL_ = null; ctx.GORGIAS_ENRICH_SKIPPED_ = 0;
mode = 'mixed'; fetches = 0;
const order = n => ({ node: { name: '#' + n, createdAt: '2026-09-06T12:00:00Z', tags: ['Reship', '_SHIP_2026-08-31'],
  displayFulfillmentStatus: 'UNFULFILLED', totalPriceSet: { shopMoney: { amount: '80.00' } },
  customer: { id: 'gid://shopify/Customer/1', email: 'a@b.com', numberOfOrders: 3 } } });
ctx.UrlFetchApp.fetch = function (url) {
  fetches++;
  if (/gorgias/.test(url)) throw new Error(REAL);            // the wall, on the Gorgias leg
  const body = JSON.stringify({ data: { orders: { pageInfo: { hasNextPage: false, endCursor: null },
    edges: [order(1001), order(1002)] } }, extensions: { cost: { actualQueryCost: 20 } } });
  return { getResponseCode: () => 200, getContentText: () => body, getAllHeaders: () => ({}) };
};
const state = {};
let sweepThrew = null;
try { ctx.sweepAndEnrich_(state, new Date('2026-08-24')); } catch (e) { sweepThrew = e; }
assert(sweepThrew === null, 'sweepAndEnrich_ returns instead of throwing when the wall hits mid-sweep (threw: ' + sweepThrew + ')');
assert(Object.keys(state).length >= 1, 'state still holds the rows swept before the wall: ' + JSON.stringify(Object.keys(state)));
assert(state['#1001'] && state['#1001'].entered === '2026-09-06', 'the pre-wall row kept its swept fields — build_ saves this');
assert(ctx.RUN_QUOTA_WALL_ && ctx.RUN_QUOTA_WALL_.leg === 'gorgias', 'wall still attributed to the gorgias leg');

console.log('\n--- note as it appears on every tab + in Slack ---\n' + note);
console.log('\n--- Logger lines ---\n' + logs.join('\n'));
