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
var SINCE = '2026-06-30'; // fixed window start (Kurt 2026-07-09)

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
  if (sh.getLastRow() >= 3) {
    sh.getRange('A3:K' + sh.getLastRow()).getValues().forEach(function (row) {
      if (row[0]) prev[row[0]] = [row[8] || '', row[9] || '', row[10] || ''];
    });
  }

  var keys = Object.keys(state).filter(function (k) {
    return (state[k].entered || '') >= SINCE || k === '#135175';
  }).sort(function (a, b) {
    var x = (state[a].entered || '') + a, y = (state[b].entered || '') + b;
    return x < y ? -1 : 1;
  });

  var rows = [
    ['Reships entered since ' + SINCE + ' (+#135175 _HOLD) — refreshed ' +
       Utilities.formatDate(new Date(), Session.getScriptTimeZone(), "yyyy-MM-dd'T'HH:mm"),
     'cols A-H source (hourly, from main sheet _state)',
     'cols I-K yours: Override Requested / Override Created / Exclude(x)', ''],
    ['Order', 'Requested', 'Created', 'Issue', 'Incoming week', 'Outgoing week', 'Status', 'Original',
     'Override Requested', 'Override Created', 'Exclude', 'Eff Requested', 'Eff Created'],
  ];
  keys.forEach(function (k, i) {
    var r = state[k], o = prev[k] || ['', '', ''];
    var rn = i + 3;
    rows.push([k, r.requested || '', r.entered || '', r.issue || '', r.original_cohort || '',
      r.outbound || '', r.status || '', r.original || '', o[0], o[1], o[2],
      '=IF($I' + rn + '<>"",$I' + rn + ',$B' + rn + ')',
      '=IF($J' + rn + '<>"",$J' + rn + ',$C' + rn + ')']);
  });

  sh.clearContents();
  var width = 13;
  sh.getRange(1, 1, rows.length, width).setValues(rows.map(function (r) {
    return r.concat(new Array(width - r.length).fill(''));
  }));
  sh.getRange('B:C').setNumberFormat('@');
}

function iso_(d) { return Utilities.formatDate(d, Session.getScriptTimeZone(), 'yyyy-MM-dd'); }
