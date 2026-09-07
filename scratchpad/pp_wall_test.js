// D43 harness — loads Code.gs VERBATIM into a vm context and asserts the ParcelPanel quota-wall
// behavior. No network, no ParcelPanel calls (the shared 120/min key is never touched).
const fs = require('fs'), vm = require('vm'), path = require('path');
const SRC = path.join('C:', 'Users', 'Work', 'Claude Projects', 'AppyHour', 'ShippingReports', 'appsscript', 'Code.gs');

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  PASS  ' + name); }
  else { fail++; console.log('  FAIL  ' + name + (extra ? '  :: ' + extra : '')); }
}

const WALL_PP = 'Bandwidth quota exceeded: https://open.parcelwill.com/api/v2/tracking/order?order_number=172143. Try reducing the rate of data transfer.';
const WALL_SHOPIFY = 'Bandwidth quota exceeded: https://504ac4.myshopify.com/admin/api/2026-04/graphql.json. Try reducing the rate of data transfer.';

function makeCtx(opts) {
  opts = opts || {};
  const saved = { calls: 0, rows: null };
  const cacheRows = opts.cacheRows || [];
  const sheet = {
    getLastRow: () => cacheRows.length + 1,
    getRange: () => ({ getValues: () => cacheRows, setValues: (v) => { saved.calls++; saved.rows = v; } }),
    hideSheet: () => {}, clear: () => {},
  };
  const ctx = {
    console,
    __saved: saved,
    __log: [],
    Logger: { log: (s) => ctx.__log.push(String(s)) },
    Session: { getScriptTimeZone: () => 'America/New_York' },
    Utilities: {
      sleep: () => {},
      formatDate: (d, tz, f) => '2026-09-07',
    },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (k) => (k === 'PARCELPANEL_API_KEY' ? 'KEY' : '') }) },
    SpreadsheetApp: { openById: () => ({ getSheetByName: () => sheet, insertSheet: () => sheet }) },
    UrlFetchApp: { fetchAll: opts.fetchAll || (() => { throw new Error('no stub'); }), fetch: () => { throw new Error('no stub'); } },
  };
  vm.createContext(ctx);
  vm.runInContext(fs.readFileSync(SRC, 'utf8'), ctx, { filename: 'Code.gs' });
  return ctx;
}

function resp(code, body, headers) {
  return {
    getResponseCode: () => code,
    getContentText: () => body,
    getAllHeaders: () => headers || { 'x-ratelimit-remaining': '99' },
  };
}
const OK_BODY = JSON.stringify({ order: { shipments: [{ carrier: { name: 'FedEx' }, delivery_status: 'DELIVERED', pickup_date: '2026-09-01', delivery_date: '2026-09-03' }] } });

console.log('\n-- 1. gasFetchQuotaWall_ classifies the right meter --');
{
  const c = makeCtx();
  ok('PP wall text  -> true', c.gasFetchQuotaWall_(new Error(WALL_PP)));
  ok('Shopify wall text -> true (same wall, different vendor URL)', c.gasFetchQuotaWall_(new Error(WALL_SHOPIFY)));
  ok('"service invoked too many times" -> true', c.gasFetchQuotaWall_(new Error('Service invoked too many times for one day: urlfetch.')));
  ok('HTTP 429 (PP rate limit) -> FALSE', !c.gasFetchQuotaWall_(new Error('Request failed: returned code 429')));
  ok('Address unavailable -> FALSE', !c.gasFetchQuotaWall_(new Error('Address unavailable: https://open.parcelwill.com/x')));
  ok('shopifyQuotaWall_ delegates (Exceptions.gs caller unbroken)', c.shopifyQuotaWall_(new Error(WALL_PP)) === true &&
     c.shopifyQuotaWall_(new Error('returned code 429')) === false);
}

console.log('\n-- 2. wall on the FIRST batch: cache saved, orders deferred, meter named --');
{
  let dispatches = 0;
  const c = makeCtx({ fetchAll: () => { dispatches++; throw new Error(WALL_PP); } });
  let threw = null;
  try { c.ppLookup_(['172143', '172144', '172145'], {}); } catch (e) { threw = e; }
  ok('it throws (fail-loud)', !!threw);
  ok('message names GOOGLE as the meter', /GOOGLE Apps Script daily url-fetch DATA quota/.test(String(threw)));
  ok('message explicitly says NOT ParcelPanel\'s limit', /NOT ParcelPanel's limit/.test(String(threw)));
  ok('message reports the deferred orders', /3 order\(s\) left UNSTAMPED and deferred/.test(String(threw)), String(threw));
  ok('message carries the raw text for forensics', /open\.parcelwill\.com/.test(String(threw)));
  ok('only ONE batch dispatched (no spending more of an exhausted budget)', dispatches === 1, 'dispatches=' + dispatches);
  ok('no order was stamped tried-and-done', !(c.__saved.rows || []).slice(1).some(r => r[3] === '2026-09-07'));
}

console.log('\n-- 3. wall AFTER a good batch: what was learned SURVIVES the throw --');
{
  let n = 0;
  const c = makeCtx({
    fetchAll: (reqs) => { n++; if (n === 1) return reqs.map(() => resp(200, OK_BODY)); throw new Error(WALL_PP); },
  });
  const asks = [];
  for (let i = 0; i < 15; i++) asks.push(String(180000 + i));
  let threw = null;
  try { c.ppLookup_(asks, {}); } catch (e) { threw = e; }
  ok('still throws', !!threw);
  ok('ppCacheSave_ RAN before the throw (P9 rule 6 on the report side)', c.__saved.calls === 1, 'saves=' + c.__saved.calls);
  const rows = (c.__saved.rows || []).slice(1);
  const stamped = rows.filter(r => r[3] === '2026-09-07');
  ok('the 10 served orders are stamped and persisted', stamped.length === 10, 'stamped=' + stamped.length);
  ok('the 5 unserved orders are NOT stamped (re-asked next run)', rows.length - stamped.length === 0 || stamped.length === 10);
  ok('served count and byte total are reported', /10 served before the wall/.test(String(threw)) && /KB received/.test(String(threw)), String(threw));
  ok('carrier learned before the wall is persisted', stamped.every(r => r[1] === 'FedEx'));
}

console.log('\n-- 4. a 429 is still backpressure, NOT a wall (P13 unchanged) --');
{
  let n = 0;
  const c = makeCtx({ fetchAll: (reqs) => { n++; return reqs.map(() => (n === 1 ? resp(429, '') : resp(200, OK_BODY))); } });
  const out = c.ppLookup_(['190001'], {});
  ok('retried and answered', out['190001'] && out['190001'].carrier === 'FedEx', JSON.stringify(out));
  ok('no throw, no wall', c.__saved.calls === 1);
  ok('the retried order IS stamped once served', (c.__saved.rows || []).slice(1).some(r => r[0] === '190001' && r[3] === '2026-09-07'));
}

console.log('\n-- 5. a NON-wall throw propagates unchanged (no swallowing) --');
{
  const c = makeCtx({ fetchAll: () => { throw new Error('Address unavailable: https://open.parcelwill.com/x'); } });
  let threw = null;
  try { c.ppLookup_(['190002'], {}); } catch (e) { threw = e; }
  ok('propagates verbatim', threw && /Address unavailable/.test(String(threw)) && !/GOOGLE Apps Script/.test(String(threw)), String(threw));
}

console.log('\n-- 6. ppIoSummary_ is descriptive and gates nothing --');
{
  const c = makeCtx({ fetchAll: (reqs) => reqs.map(() => resp(200, OK_BODY)) });
  c.ppLookup_(['190003', '190004'], {});
  ok('reports 2 responses', /2 response\(s\)/.test(c.ppIoSummary_()), c.ppIoSummary_());
  ok('reports non-zero KB', /[1-9]/.test(c.ppIoSummary_().split('KB')[0].split(',')[1] || ''), c.ppIoSummary_());
}

console.log('\n' + (fail === 0 ? 'ALL PASS' : 'FAILURES') + ': ' + pass + '/' + (pass + fail));
process.exit(fail === 0 ? 0 : 1);
