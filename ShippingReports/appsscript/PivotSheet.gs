/**
 * Reship Pivots sheet — standalone hourly refresh.
 * Bind this to the PIVOT sheet (1weQz0AO...): Extensions -> Apps Script, paste,
 * Run refresh() once (authorize), then add an hourly time-driven trigger.
 *
 * No credentials needed: it reads the MAIN Reship Sheet's hidden `_state` tab
 * (which the main sheet's script maintains from Shopify/Gorgias) and rewrites
 * Raw Data here. Your override cols I-K survive; pivot tabs are formulas and
 * update on their own.
 *
 * NOTE: if you use this, remove the refreshPivotSheet_ call from the MAIN
 * sheet's script (or keep only one owner — two writers just race each other).
 */

var MAIN_SHEET_ID = '1JgyYknIxJ3-UJxJOX-y78rf8cPNhT0uPy5FUw2zO9wE';
var CUTOVER = '2026-07-08'; // membership = frozen _seed tab UNION entered >= cutover; rows persist once fulfilled

var STATE_COLS = ['key', 'entered', 'requested', 'ticket', 'issue', 'outbound', 'status',
                  'total', 'original', 'original_cohort', 'original_total', 'lifetime_orders'];

function refresh() {
  // load state from the main sheet's hidden _state tab
  var src = SpreadsheetApp.openById(MAIN_SHEET_ID).getSheetByName('_state');
  if (!src) throw new Error('_state tab missing on main Reship Sheet');
  var values = src.getDataRange().getValues();
  var state = {};
  for (var i = 1; i < values.length; i++) {
    if (!values[i][0]) continue;
    var rec = {};
    for (var c = 1; c < STATE_COLS.length; c++) {
      var v = values[i][c];
      if (v !== '' && v != null) rec[STATE_COLS[c]] = (v instanceof Date) ? iso_(v) : String(v);
    }
    state[values[i][0]] = rec;
  }

  var sh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('Raw Data')
        || SpreadsheetApp.getActiveSpreadsheet().insertSheet('Raw Data');

  // preserve user override cols I-K keyed by order
  var prev = {};
  if (sh.getLastRow() >= 2) {
    sh.getRange('A2:K' + sh.getLastRow()).getValues().forEach(function (row) {
      if (row[0]) prev[row[0]] = [row[8] || '', row[9] || '', row[10] || ''];
    });
  }

  var seed = {};
  var seedSh = SpreadsheetApp.getActiveSpreadsheet().getSheetByName('_seed');
  if (seedSh) {
    seedSh.getRange('A1:A' + Math.max(1, seedSh.getLastRow())).getValues().forEach(function (row) {
      if (row[0]) seed[row[0]] = true;
    });
  }
  var keys = Object.keys(state).filter(function (k) {
    return seed[k] || (state[k].entered || '') >= CUTOVER;
  }).sort(function (a, b) {
    var x = (state[a].entered || '') + a, y = (state[b].entered || '') + b;
    return x < y ? -1 : 1;
  });

  // headers in ROW 1 (sortable); Eff cols = anchored ARRAYFORMULAs (sort-safe)
  var rows = [
    ['Order', 'Requested', 'Created', 'Issue', 'Incoming week', 'Outgoing week', 'Status', 'Original',
     'Override Requested', 'Override Created', 'Exclude'],
  ];
  keys.forEach(function (k) {
    var r = state[k], o = prev[k] || ['', '', ''];
    rows.push([k, r.requested || '', r.entered || '', r.issue || '', r.original_cohort || '',
      r.outbound || '', r.status || '', r.original || '', o[0], o[1], o[2]]);
  });

  sh.clearContents();
  var width = 11;
  sh.getRange(1, 1, rows.length, width).setValues(rows.map(function (r) {
    return r.concat(new Array(width - r.length).fill(''));
  }));
  sh.getRange('L1:M1').setFormulas([[
    '=ARRAYFORMULA({"Eff Requested"; IF(A2:A="",,IF(I2:I<>"",I2:I,B2:B))})',
    '=ARRAYFORMULA({"Eff Created"; IF(A2:A="",,IF(J2:J<>"",J2:J,C2:C))})']]);
  sh.getRange('B:C').setNumberFormat('@');
}

function iso_(d) { return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd'); }
