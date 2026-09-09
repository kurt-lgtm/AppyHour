// Offline proof for D43b rule 10 — `Exceptions.gs excGorgiasCreate_` must book the Apps Script
// url-fetch DATA-quota wall as a QUOTA-WALL outcome naming GOOGLE's script-owner daily meter,
// never as a generic Gorgias `failed` carrying the raw vendor text.
//
// Loads the REAL Code.gs (for gasFetchQuotaWall_ / noteQuotaWall_ / netFetch_) and the REAL
// Exceptions.gs into one VM context, exactly as the Apps Script project shares a global scope.
// No network, no sheet, no live API — every stub throws if anything tries to reach out.
// Nothing is pushed; deploying is `gas_swap.py push Exceptions` and is Kurt's call.
const fs = require('fs'), vm = require('vm');
const DIR = 'C:\\Users\\Work\\Claude Projects\\AppyHour\\ShippingReports\\appsscript\\';

// The verbatim wording Google throws. On 2026-09-08 it carried a Gorgias URL; on 2026-09-06 a
// ParcelPanel one; on 2026-08-31 a Shopify one. One meter, three vendors' URLs.
const REAL = 'Bandwidth quota exceeded: https://appyhour.gorgias.com/api/tickets. Try reducing the rate of data transfer.';

let logs = [], fetches = 0, mode = 'wall', slack = [];
const ctx = {
  Logger: { log: m => logs.push(String(m)) },
  Utilities: {
    sleep() {}, base64Encode: s => 'B64',
    formatDate: (d, tz, fmt) => {
      const p = n => String(n).padStart(2, '0');
      const s = d.getUTCFullYear() + '-' + p(d.getUTCMonth() + 1) + '-' + p(d.getUTCDate());
      return /HH/.test(String(fmt)) ? s + 'T' + p(d.getUTCHours()) + ':' + p(d.getUTCMinutes()) + ':' + p(d.getUTCSeconds()) : s;
    },
  },
  PropertiesService: { getScriptProperties: () => ({ getProperty: k => '1', setProperty() {} }) },
  Session: { getScriptTimeZone: () => 'America/Chicago', getEffectiveUser: () => ({ getEmail: () => 'stub@example.com' }) },
  SpreadsheetApp: { openById: () => { throw new Error('no sheet in test'); }, getActive: () => { throw new Error('no sheet in test'); } },
  MailApp: { sendEmail() { throw new Error('no mail in test'); } },
  UrlFetchApp: {
    fetch(url) {
      fetches++;
      if (mode === 'wall') throw new Error(REAL);
      if (mode === 'http500') return { getResponseCode: () => 500, getContentText: () => 'boom', getAllHeaders: () => ({}) };
      return { getResponseCode: () => 201, getContentText: () => '{"id":1}', getAllHeaders: () => ({}) };
    },
    fetchAll() { throw new Error('not used'); },
  },
  console,
};
ctx.globalThis = ctx;
vm.createContext(ctx);
vm.runInContext(fs.readFileSync(DIR + 'Code.gs', 'utf8'), ctx, { filename: 'Code.gs' });
vm.runInContext(fs.readFileSync(DIR + 'Exceptions.gs', 'utf8'), ctx, { filename: 'Exceptions.gs' });

function assert(ok, msg) { console.log((ok ? 'PASS  ' : 'FAIL  ') + msg); if (!ok) process.exitCode = 1; }

// The Slack leg is stubbed at the ONE function that would post, so no channel id is ever needed
// or touched by this test.
ctx.excSlackOps_ = function (t) { slack.push(String(t)); };
ctx.EXC_DRY_RUN = false;

function reset(m) {
  mode = m; fetches = 0; logs = []; slack = [];
  ctx.RUN_QUOTA_WALL_ = null;
  ctx.EXC_GORGIAS_RUN_ = { created: 0, failed: 0, skipped_no_email: 0, disabled: 0, quota_wall: 0, errors: [] };
  // pre-seed the customer so no Shopify lookup is attempted; the Gorgias POST is the only fetch
  ctx.EXC_CUSTOMER_CACHE_ = { '170893': { email: 'a@b.com', name: 'A B', first: 'A' }, '170894': { email: 'c@d.com', name: 'C D', first: 'C' } };
}
const rec = n => ({ order: n, tracking: '1LS1234567890', carrier: 'OnTrac', hub: 'Nashville' });

// 0. the counter exists and starts at zero
reset('wall');
assert(ctx.EXC_GORGIAS_RUN_.quota_wall === 0, 'EXC_GORGIAS_RUN_ carries a quota_wall counter, separate from failed');

// 1. THE FIX: the wall is booked as quota_wall, NOT as a Gorgias failure
reset('wall');
let threw = null;
try { ctx.excGorgiasCreate_(rec('170893'), 'DELAYED', 'stuck', '2026-09-09T10:00:00Z'); } catch (e) { threw = e; }
let run = ctx.EXC_GORGIAS_RUN_;
assert(threw === null, 'excGorgiasCreate_ still NEVER THROWS (threw: ' + threw + ')');
assert(run.quota_wall === 1, 'the wall increments quota_wall, got ' + run.quota_wall);
assert(run.failed === 0, 'the wall does NOT increment failed (it is not a Gorgias failure), got ' + run.failed);
assert(run.created === 0, 'nothing is counted as created');

// 2. the recorded line names GOOGLE's meter and disclaims the vendor
const err = run.errors[0] || '';
assert(/GOOGLE Apps Script daily url-fetch DATA/.test(err), 'the error line names GOOGLE\'s script-owner daily DATA quota');
assert(/NOT Gorgias throttling/.test(err), 'the error line explicitly disclaims Gorgias');
assert(/DEFERRED/.test(err), 'the error line says the draft is deferred, not lost');
assert(/Bandwidth quota exceeded/.test(err), 'the raw vendor text is kept (truncated) for forensics');

// 3. it is recorded RUN-WIDE through the shared Code.gs helper, with its own leg name
assert(ctx.RUN_QUOTA_WALL_ && ctx.RUN_QUOTA_WALL_.leg === 'gorgias-ticket-create',
  'noteQuotaWall_ recorded the wall run-wide, leg "gorgias-ticket-create", got ' + JSON.stringify(ctx.RUN_QUOTA_WALL_ && ctx.RUN_QUOTA_WALL_.leg));

// 4. 🔴 NEVER RETRY, NEVER POST THE NEXT ONE (D43 rule 3): zero further requests
const spent = fetches;
fetches = 0;
ctx.excGorgiasCreate_(rec('170894'), 'DELAYED', 'stuck', '2026-09-09T10:00:00Z');
assert(fetches === 0, 'the next draft places ZERO further requests once the wall is up, got ' + fetches);
assert(ctx.EXC_GORGIAS_RUN_.quota_wall === 2, 'the short-circuited draft is counted as deferred too, got ' + ctx.EXC_GORGIAS_RUN_.quota_wall);
assert(ctx.EXC_GORGIAS_RUN_.failed === 0, 'and still never as a failure');
assert(spent >= 1, 'the first attempt did place its request before the wall (sanity), got ' + spent);

// 5. the ops alarm names the right system
ctx.excGorgiasFlush_();
const msg = slack.join('\n');
assert(/DEFERRED on GOOGLE's daily url-fetch DATA quota/.test(msg), 'the ops alarm names GOOGLE\'s meter');
assert(/\*not\* Gorgias throttling/.test(msg), 'the ops alarm disclaims Gorgias');
assert(/2 DEFERRED/.test(msg), 'the ops alarm reports the deferred COUNT, got: ' + msg.slice(0, 200));
assert(/0 failed/.test(msg), 'the ops alarm reports 0 failed — the wall was not laundered into that number');

// 6. 🔴 REGRESSION GUARD — a REAL Gorgias failure is still a failure, unchanged
reset('http500');
ctx.excGorgiasCreate_(rec('170893'), 'DELAYED', 'stuck', '2026-09-09T10:00:00Z');
run = ctx.EXC_GORGIAS_RUN_;
assert(run.failed === 1 && run.quota_wall === 0, 'an HTTP 500 from Gorgias is still `failed`, never quota_wall');
assert(/HTTP 500/.test(run.errors[0] || ''), 'the real failure keeps its status code');
assert(ctx.RUN_QUOTA_WALL_ === null, 'a real vendor failure does NOT set the run-level wall');

// 7. the happy path is untouched
reset('ok');
ctx.excGorgiasCreate_(rec('170893'), 'DELAYED', 'stuck', '2026-09-09T10:00:00Z');
run = ctx.EXC_GORGIAS_RUN_;
assert(run.created === 1 && run.failed === 0 && run.quota_wall === 0, 'a 201 still counts as created');
ctx.excGorgiasFlush_();
assert(slack.length === 0, 'a clean run raises no ops alarm');

// 8. the detector reached is the SHARED one, under either name (D43 rule 7)
assert(ctx.gasFetchQuotaWall_(new Error(REAL)) === true, 'gasFetchQuotaWall_ matches the wall');
assert(ctx.shopifyQuotaWall_(new Error(REAL)) === true, 'the legacy alias still delegates to it');
assert(ctx.gasFetchQuotaWall_(new Error('HTTP 429 rate limited')) === false, 'a real vendor 429 is NOT the wall');

console.log('\n--- ops alarm as it would appear ---\n' + msg);
