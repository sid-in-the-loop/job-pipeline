ObjC.import('Foundation');
function readFile(p) {
  return $.NSString.stringWithContentsOfFileEncodingError(p, $.NSUTF8StringEncoding, null).js;
}

// ---- minimal SpreadsheetApp mock ------------------------------------------
function Sheet(name, grid) { this.name = name; this.g = grid || []; }
Sheet.prototype.getName = function () { return this.name; };
Sheet.prototype.getLastRow = function () { return this.g.length; };
Sheet.prototype.getLastColumn = function () {
  var w = 0; for (var i = 0; i < this.g.length; i++) w = Math.max(w, this.g[i].length); return w;
};
Sheet.prototype.deleteRow = function (r) { this.g.splice(r - 1, 1); };
Sheet.prototype.pad = function (r, c) {
  while (this.g.length < r) this.g.push([]);
  for (var i = 0; i < this.g.length; i++)
    while (this.g[i].length < c) this.g[i].push('');
};
Sheet.prototype.getRange = function (a, b, nr, nc) {
  var sh = this;
  if (typeof a === 'string') {                       // A1 notation (single cell)
    var m = /^([A-Z]+)(\d+)$/.exec(a);
    var col = 0; for (var i = 0; i < m[1].length; i++) col = col * 26 + (m[1].charCodeAt(i) - 64);
    return sh.getRange(parseInt(m[2], 10), col);
  }
  nr = nr || 1; nc = nc || 1;
  return {
    getValues: function () {
      sh.pad(a + nr - 1, b + nc - 1);
      var out = [];
      for (var r = a; r < a + nr; r++) out.push(sh.g[r - 1].slice(b - 1, b - 1 + nc));
      return out;
    },
    setValues: function (grid) {
      // Real Apps Script rejects undefined cells and requires the grid to match
      // the range exactly. The mock enforced neither, which hid a sparse-array
      // bug that would only have surfaced on the first live POST.
      if (grid.length !== nr) throw new Error('setValues row mismatch: ' + grid.length + ' vs ' + nr);
      for (var r = 0; r < grid.length; r++) {
        if (grid[r].length !== nc) throw new Error('setValues col mismatch: ' + grid[r].length + ' vs ' + nc);
        for (var c = 0; c < grid[r].length; c++) {
          if (grid[r][c] === undefined) throw new Error('setValues got undefined at r' + r + 'c' + c);
        }
      }
      sh.pad(a + grid.length - 1, b + (grid[0] ? grid[0].length : 1) - 1);
      for (var r2 = 0; r2 < grid.length; r2++)
        for (var c2 = 0; c2 < grid[r2].length; c2++)
          sh.g[a - 1 + r2][b - 1 + c2] = grid[r2][c2];
    },
    setValue: function (v) { sh.pad(a, b); sh.g[a - 1][b - 1] = v; },
    clearContent: function () { sh.pad(a, b); sh.g[a - 1][b - 1] = ''; },
    _row: a, _col: b, _nrows: nr, _ncols: nc
  };
};
Sheet.prototype.deleteRows = function (start, count) { this.g.splice(start - 1, count); };
Sheet.prototype.insertRowsAfter = function (idx, n) {
  this.pad(idx, 1);
  var w = this.getLastColumn();
  for (var i = 0; i < n; i++) {
    var blank = []; for (var c = 0; c < w; c++) blank.push('');
    this.g.splice(idx, 0, blank);
  }
};
Sheet.prototype.moveRows = function (rangeObj, destIndex) {
  // Real API: destinationIndex uses BEFORE-move coordinates; rows land before
  // the row currently at that index. We only ever move a single later row up.
  var r = rangeObj._row;
  if (r < destIndex) throw new Error('mock only supports moving a row upward');
  var row = this.g.splice(r - 1, 1)[0];
  this.g.splice(destIndex - 1, 0, row);
};
function SS(sheets) { this.sheets = sheets; }
SS.prototype.getSheetByName = function (n) {
  for (var i = 0; i < this.sheets.length; i++) if (this.sheets[i].name === n) return this.sheets[i];
  return null;
};
SS.prototype.insertSheet = function (n) { var s = new Sheet(n, []); this.sheets.push(s); return s; };
SS.prototype.deleteSheet = function (s) { this.sheets = this.sheets.filter(function (x) { return x !== s; }); };
SS.prototype.getSheets = function () { return this.sheets; };

var SHEET = new SS([new Sheet('Openings', [
  ['Date Added', 'Company', 'Role', 'Link', 'Status', 'OA Deadline', 'Last Email', 'Notes'],
  ['2026-08-01', 'Acme Corp', 'SWE New Grad', 'https://x/1', 'Applied', '', '', 'keep me'],
  ['2026-08-02', 'Globex Trading', 'Quant Dev', 'https://x/2', 'OA', '2026-08-07', '', '']
])]);
var SpreadsheetApp = { getActiveSpreadsheet: function () { return SHEET; } };
var ContentService = {
  MimeType: {JSON: 'json'},
  createTextOutput: function (s) { return {payload: s, setMimeType: function () { return this; }}; }
};

eval(readFile($.NSProcessInfo.processInfo.arguments.js[4].js));

// ---- helpers ---------------------------------------------------------------
function post(body) { return JSON.parse(doPost({postData: {contents: JSON.stringify(body)}}).payload); }
function rows(tab) { return SHEET.getSheetByName(tab).g; }
var S = 'CHANGE-ME-TO-A-LONG-RANDOM-STRING';
var log = [];
function check(name, cond, extra) { log.push((cond ? '  PASS  ' : '  FAIL  ') + name + (extra ? '   ' + extra : '')); }

// 1. auth
check('rejects a bad secret', post({secret: 'nope', ops: [{op: 'ping'}]}).error === 'bad secret');
check('accepts the real secret', post({secret: S, ops: [{op: 'ping'}]}).ok === true);

// 2. append (batched) + unknown column reported not created
var r = post({secret: S, ops: [
  {op: 'append', tab: 'Openings', values: {Company: 'Baseten', Role: 'SWE Inference', Status: 'New', Bogus: 'x'}},
  {op: 'append', tab: 'Openings', values: {Company: 'Modal', Role: 'SWE', Status: 'New'}}
]});
check('appended 2 rows', r.appended === 2 && rows('Openings').length === 5, 'rows=' + rows('Openings').length);
check('unknown column reported, not created', r.ignoredCols.Bogus === 1 && rows('Openings')[0].indexOf('Bogus') === -1);

// 3. update by match, and clearing via explicit ''
r = post({secret: S, ops: [{op: 'update', tab: 'Openings', match: {col: 'Company', value: 'Globex Trading'},
                            values: {Status: 'OA Submitted', 'OA Deadline': ''}}]});
var imc = rows('Openings')[2];
check('update changed status', imc[4] === 'OA Submitted', 'got=' + imc[4]);
check('explicit "" CLEARED the deadline', imc[5] === '', 'got=' + JSON.stringify(imc[5]));

// 4. omitted key must not touch other cells
check('untouched cell preserved', rows('Openings')[1][7] === 'keep me');

// 5. unmatched update becomes a flagged append
r = post({secret: S, ops: [{op: 'update', tab: 'Openings', match: {col: 'Company', value: 'GhostCo'},
                            values: {Status: 'Rejected'}}]});
check('unmatched update -> flagged append', r.unmatched === 1 && rows('Openings').length === 6);

// 6. clear specific cells
r = post({secret: S, ops: [{op: 'clear', tab: 'Openings', match: {col: 'Company', value: 'Acme Corp'}, cols: ['Notes']}]});
check('clear blanked Notes', r.cleared === 1 && rows('Openings')[1][7] === '');

// 7. delete_row
var before = rows('Openings').length;
r = post({secret: S, ops: [{op: 'delete_row', tab: 'Openings', match: {col: 'Company', value: 'Modal'}}]});
check('delete_row removed one row', r.deleted === 1 && rows('Openings').length === before - 1);

// 8. ensure_tab creates with headers; rerun is idempotent
r = post({secret: S, ops: [{op: 'ensure_tab', tab: 'Watchlist', headers: ['Company', 'Feed URL', 'Type']}]});
var w = r.results[0];
check('ensure_tab created Watchlist', w.created === true && w.addedHeaders.length === 3);
r = post({secret: S, ops: [{op: 'ensure_tab', tab: 'Watchlist', headers: ['Company', 'Feed URL', 'Type', 'Signal']}]});
check('ensure_tab is idempotent, adds only new headers', r.results[0].addedHeaders.length === 1);

// 9. set_range writes one cell of any tab. No pipeline caller today - the
//    Profile tab it used to fill is gone; the op stays because it is deployed.
post({secret: S, ops: [{op: 'ensure_tab', tab: 'Scratch', headers: []},
                       {op: 'set_range', tab: 'Scratch', a1: 'A1', value: 'one cell'}]});
check('set_range wrote Scratch!A1', rows('Scratch')[0][0] === 'one cell');

// 10. dryRun mutates nothing
var snapshot = JSON.stringify(rows('Openings'));
r = post({secret: S, dryRun: true, ops: [
  {op: 'append', tab: 'Openings', values: {Company: 'ShouldNotExist'}},
  {op: 'delete_row', tab: 'Openings', match: {col: 'Company', value: 'Acme Corp'}}
]});
check('dryRun reports counts', r.appended === 1 && r.deleted === 1 && r.dryRun === true);
check('dryRun changed NOTHING', JSON.stringify(rows('Openings')) === snapshot);

// 11. delete_tab needs confirm
r = post({secret: S, ops: [{op: 'delete_tab', tab: 'Watchlist'}]});
check('delete_tab refused without confirm', !!r.results[0].error && !!SHEET.getSheetByName('Watchlist'));
r = post({secret: S, ops: [{op: 'delete_tab', tab: 'Watchlist', confirm: true}]});
check('delete_tab works with confirm', SHEET.getSheetByName('Watchlist') === null);

// 12. read
r = post({secret: S, ops: [{op: 'read', tab: 'Openings', limit: 3}]});
check('read returns headers + rows', r.results[0].headers[1] === 'Company' && r.results[0].rows.length === 3);

// 19 grouped append: existing company gets an INSERT next to its block
post({secret:S,ops:[{op:'ensure_tab',tab:'G',headers:['Company','Role','Source','Link','Status']}]});
post({secret:S,ops:[{op:'append',tab:'G',values:{Company:'Acme',Role:'r1'}},
                    {op:'append',tab:'G',values:{Company:'Zed',Role:'z1'}}]});
r=post({secret:S,ops:[{op:'append',tab:'G',values:{Company:'Acme',Role:'r2'}}]});
var G=rows('G');
check('grouped append inserts after company block',
      r.groupedInserts===1 && G[1][0]==='Acme' && G[2][0]==='Acme' && G[2][1]==='r2' && G[3][0]==='Zed',
      JSON.stringify(G.slice(1).map(function(x){return x[0]+':'+x[1];})));
// 20 grouped append respects dryRun
var snapG=JSON.stringify(rows('G'));
r=post({secret:S,dryRun:true,ops:[{op:'append',tab:'G',values:{Company:'Acme',Role:'r3'}}]});
check('grouped append dryRun counts but mutates nothing', r.groupedInserts===1 && JSON.stringify(rows('G'))===snapG);
// 21 delete_empty_rows: fully-empty + status-ghost rows go; real rows stay
var H=SHEET.getSheetByName('G'); // reuse mock class via a new tab
post({secret:S,ops:[{op:'ensure_tab',tab:'H',headers:['Company','Status','Notes']}]});
var HS=SHEET.getSheetByName('H');
HS.g.push(['x','','keep']); HS.g.push(['','','']); HS.g.push(['','Unreleased','']); HS.g.push(['y','','also']);
r=post({secret:S,dryRun:true,ops:[{op:'delete_empty_rows',tab:'H',confirm:true}]});
check('delete_empty_rows dryRun reports without deleting', r.results[0].removed===2 && HS.g.length===5);
r=post({secret:S,ops:[{op:'delete_empty_rows',tab:'H',confirm:true}]});
check('delete_empty_rows removes empty + ghost rows',
      r.results[0].removed===2 && HS.g.length===3 && HS.g[1][0]==='x' && HS.g[2][0]==='y');
check('delete_empty_rows refused without confirm',
      /confirm/.test(post({secret:S,ops:[{op:'delete_empty_rows',tab:'H'}]}).results[0].error||''));
// 22 regroup: stray row moves up next to its company block
var GS=SHEET.getSheetByName('G');
GS.g.push(['Acme','r9','scan','','']);   // stray at the very end, after Zed
r=post({secret:S,dryRun:true,ops:[{op:'regroup',tab:'G',confirm:true}]});
check('regroup dryRun counts strays', r.results[0].moved===1 && GS.g[GS.g.length-1][0]==='Acme');
r=post({secret:S,ops:[{op:'regroup',tab:'G',confirm:true}]});
var order=GS.g.slice(1).map(function(x){return x[0];}).join(',');
check('regroup moves stray next to its block', r.results[0].moved===1 && order==='Acme,Acme,Acme,Zed', order);

log.join('\n');
