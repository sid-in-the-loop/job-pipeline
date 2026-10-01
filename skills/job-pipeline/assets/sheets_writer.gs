/**
 * sheets_writer.gs — full read/write control of the tracker, as a free Apps
 * Script web app. Deploy bound to the tracker; the agent POSTs a batch of ops.
 *
 * Why this instead of an MCP connector: an unattended cloud run has no laptop
 * and no browser, so the hard part is not executing code — it's where the
 * Google credentials live. This inverts it. The script runs *as the sheet's
 * owner*, so the caller needs no Google identity: a plain HTTPS POST with a
 * shared secret. No OAuth, no refresh tokens, no vendor quota.
 *
 * DEPLOY (from the account that OWNS the tracker):
 *   Extensions > Apps Script, paste this, set SECRET, then
 *   Deploy > New deployment
 *     Type: Web app | Execute as: Me | Who has access: Anyone
 *   Copy the /exec URL + secret into skills/job-pipeline/assets/config.seed.json
 *   under "sheets_writer" and commit (the repo is private for this reason).
 *   Callers MUST follow redirects (/exec 302s to googleusercontent.com).
 *
 * SECURITY: the URL is public and unauthenticated — the secret is the only
 * gate, and Apps Script offers no rate limiting or key rotation. If it leaks,
 * redeploy for a new URL and change the secret. Because this build can delete,
 * the blast radius of a leak is the whole sheet: the sheet's own version history
 * (File > Version history restores anything) is the undo; treat the URL as a
 * password.
 *
 * REQUEST   {"secret": "...", "ops": [ {op: ...}, ... ], "dryRun": false}
 * RESPONSE  {ok, appended, updated, deleted, cleared, unmatched, ignoredCols,
 *            results: [...], dryRun}
 *
 * OPS
 *   {op:"ping"}
 *   {op:"read",       tab, limit?, cols?}                 inspect rows
 *   {op:"append",     tab, values:{Col:val,...}, addMissingCols?}
 *       (a row whose Company already exists is INSERTED after that company's
 *        block, not appended at the end — the sheet stays grouped by company)
 *   {op:"update",     tab, match:{col,value}, values, all?, addMissingCols?}
 *   {op:"delete_row", tab, match:{col,value}, all?}
 *   {op:"clear",      tab, match:{col,value}, cols:[...], all?}
 *   {op:"set_range",  tab, a1:"A1", value}                write one cell
 *   {op:"set_validation", tab, col, values:[...], confirm:true}
 *       replace a column's dropdown list (data validation). The lane tabs'
 *       Status lists need not be identical (one may lack "Reject"), and a refused
 *       value throws, which kills the whole batch, not just its own op. This
 *       is the only way to fix that from the pipeline instead of by hand.
 *   {op:"ensure_tab", tab, headers:[...]}                 create tab / add headers
 *   {op:"delete_empty_rows", tab, confirm:true}           drop empty + status-ghost rows
 *   {op:"regroup",    tab, confirm:true}                  move strays next to their company's block
 *   {op:"delete_tab", tab, confirm:true}                  requires confirm
 *
 * In `values`, an omitted key leaves the cell alone; an explicit null or ""
 * CLEARS it. That distinction is the whole point — the agent needs to be able
 * to unset a stale deadline, not just overwrite it.
 */

var SECRET = 'CHANGE-ME-TO-A-LONG-RANDOM-STRING';
// dryRun bookkeeping: a dry run writes nothing, so columns and tabs it "would"
// create are invisible to later ops in the same batch — which made a preview
// report real data as undeliverable. These simulate them for the response only.
var VHDR = {};   // tab -> {normalizedHeader: colIndex}
var VTAB = {};   // tab -> true when ensure_tab would have created it
var MAX_OPS = 1000;          // a flood is a bug or an attack, not a morning run
var MAX_DELETES = 200;       // per request; raise if you ever bulk-prune

function doPost(e) {
  var out = {ok: true, appended: 0, updated: 0, deleted: 0, cleared: 0,
             unmatched: 0, ignoredCols: {}, results: [], dryRun: false};
  try {
    var body = JSON.parse((e && e.postData && e.postData.contents) || '{}');
    if (body.secret !== SECRET) return reply({ok: false, error: 'bad secret'});

    var ops = body.ops || (body.op ? [body] : []);
    out.dryRun = !!body.dryRun;
    VHDR = {}; VTAB = {};
    if (!ops.length) return reply({ok: true, note: 'no ops'});
    if (ops.length > MAX_OPS) return reply({ok: false, error: 'too many ops: ' + ops.length});

    var ss = SpreadsheetApp.getActiveSpreadsheet();
    var pending = {};   // tab -> rows buffered for a batched append

    for (var i = 0; i < ops.length; i++) {
      var op = ops[i] || {}, kind = op.op || 'append', tab = op.tab || 'Openings';

      if (kind === 'ping') { out.results.push({op: 'ping', tabs: tabNames(ss)}); continue; }

      // Anything that reads or mutates existing rows must see this run's own
      // appends first — a mail update can target a row appended moments ago.
      if (kind !== 'append') flush(ss, pending, tab, out);

      if (kind === 'ensure_tab')  { out.results.push(ensureTab(ss, tab, op.headers, out)); continue; }
      if (kind === 'delete_tab')  { out.results.push(deleteTab(ss, tab, op.confirm, out)); continue; }

      var sh = ss.getSheetByName(tab);
      if (!sh) {
        // A dry run that also contains ensure_tab would otherwise report every
        // following row as undeliverable, making first-time setup look broken.
        if (out.dryRun && VTAB[tab]) {
          if (kind === 'append' || kind === 'update') out.appended++;
          continue;
        }
        out.results.push({op: kind, tab: tab, error: 'missing tab'});
        continue;
      }

      if (kind === 'read')      { out.results.push(readRows(sh, op, out)); continue; }
      if (kind === 'set_range') { setRange(sh, op, out); continue; }
      if (kind === 'set_validation') { setValidation(sh, op, out); continue; }

      if (kind === 'append') {
        var hdrs = headerMap(sh);
        if (op.addMissingCols) hdrs = addCols(sh, hdrs, Object.keys(op.values || {}), out);
        (pending[tab] = pending[tab] || []).push(op.values || {});
        out.appended++;
        continue;
      }

      var rows = findRows(sh, headerMap(sh), (op.match || {}).col, (op.match || {}).value, op.all);
      if (kind === 'update') {
        // addMissingCols was honored on append only, though the header above has
        // always advertised it for update too. An update naming a column the
        // sheet lacks therefore reported updated:N with the value silently
        // counted into ignoredCols -- a 429-row Comp backfill "succeeded" and
        // wrote nothing. writeRow re-reads headerMap per row, so adding here is
        // enough for the writes below to see the new column.
        if (op.addMissingCols) addCols(sh, headerMap(sh), Object.keys(op.values || {}), out);
        if (!rows.length) {
          // Contract: an update matching nothing becomes a flagged append, so a
          // state change is never silently dropped.
          (pending[tab] = pending[tab] || []).push(
              merge(op.values, {Company: (op.match || {}).value, Flags: 'unmatched-update'}));
          out.unmatched++; out.appended++;
        } else {
          for (var r = 0; r < rows.length; r++) writeRow(sh, headerMap(sh), rows[r], op.values, out);
          out.updated += rows.length;
        }
        continue;
      }
      if (kind === 'delete_row') {
        if (out.deleted + rows.length > MAX_DELETES) return reply(
            {ok: false, error: 'delete cap hit (' + MAX_DELETES + ') — nothing further applied', partial: out});
        rows.sort(function (a, b) { return b - a; });         // bottom-up: indices stay valid
        for (var d = 0; d < rows.length; d++) if (!out.dryRun) sh.deleteRow(rows[d]);
        out.deleted += rows.length;
        out.results.push({op: 'delete_row', tab: tab, rows: rows});
        continue;
      }
      if (kind === 'clear') {
        var hm = headerMap(sh), cols = op.cols || [];
        for (var q = 0; q < rows.length; q++) {
          for (var c = 0; c < cols.length; c++) {
            var idx = hm[norm(cols[c])];
            if (!idx) { out.ignoredCols[cols[c]] = (out.ignoredCols[cols[c]] || 0) + 1; continue; }
            if (!out.dryRun) sh.getRange(rows[q], idx).clearContent();
            out.cleared++;
          }
        }
        continue;
      }
      if (kind === 'delete_empty_rows') {
        // One-time hygiene: rows with no content at all, plus "ghost" rows whose
        // only content is dropdown residue in Status-named columns.
        if (!op.confirm) { out.results.push({op: kind, tab: tab, error: 'refused: pass confirm:true'}); continue; }
        var hmE = headerMap(sh), lastE = sh.getLastRow(), wE = Math.max(sh.getLastColumn(), 1);
        var statusCols = {};
        for (var hkE in hmE) if (hmE.hasOwnProperty(hkE) && hkE.indexOf('status') !== -1) statusCols[hmE[hkE]] = true;
        var allE = lastE > 1 ? sh.getRange(2, 1, lastE - 1, wE).getValues() : [];
        var doomed = [];
        for (var qE = 0; qE < allE.length; qE++) {
          var hasContent = false, ghostOnly = true;
          for (var cE = 0; cE < allE[qE].length; cE++) {
            var cell = String(allE[qE][cE] == null ? '' : allE[qE][cE]).trim();
            if (!cell) continue;
            hasContent = true;
            if (!statusCols[cE + 1]) { ghostOnly = false; break; }
          }
          if (!hasContent || ghostOnly) doomed.push(qE + 2);
        }
        if (doomed.length > 2000) { out.results.push({op: kind, tab: tab, error: 'refusing: ' + doomed.length + ' rows (cap 2000)'}); continue; }
        // Delete contiguous RUNS via deleteRows, bottom-up. Row-at-a-time hit
        // Apps Script's ~6-minute execution cap live: 1,825 empties took three
        // executions to converge. Runs turn that into a few dozen calls.
        if (!out.dryRun) {
          var runs = [], s0 = null, prevE = null;
          for (var iE = 0; iE < doomed.length; iE++) {
            if (s0 === null) { s0 = doomed[iE]; prevE = doomed[iE]; continue; }
            if (doomed[iE] === prevE + 1) { prevE = doomed[iE]; continue; }
            runs.push([s0, prevE - s0 + 1]); s0 = doomed[iE]; prevE = doomed[iE];
          }
          if (s0 !== null) runs.push([s0, prevE - s0 + 1]);
          for (var rE = runs.length - 1; rE >= 0; rE--) sh.deleteRows(runs[rE][0], runs[rE][1]);
        }
        out.results.push({op: kind, tab: tab, removed: doomed.length, sample: doomed.slice(0, 20)});
        continue;
      }
      if (kind === 'regroup') {
        // One-time repair: move every stray row up to sit after the FIRST block
        // of its company, so one company reads as one contiguous group.
        if (!op.confirm) { out.results.push({op: kind, tab: tab, error: 'refused: pass confirm:true'}); continue; }
        var hmR = headerMap(sh), ciR = hmR[norm('Company')];
        if (!ciR) { out.results.push({op: kind, tab: tab, error: 'no Company column'}); continue; }
        var movedR = 0;
        if (out.dryRun) {
          movedR = regroupPass(sh, ciR, true);
        } else {
          for (var gR = 0; gR < 1000; gR++) {
            var nR = regroupPass(sh, ciR, false);
            if (!nR) break;
            movedR += nR;
          }
        }
        out.results.push({op: kind, tab: tab, moved: movedR, dryRun: out.dryRun});
        continue;
      }
      out.results.push({op: kind, error: 'unknown op'});
    }

    for (var t in pending) if (pending.hasOwnProperty(t)) flush(ss, pending, t, out);
    return reply(out);
  } catch (err) {
    out.ok = false; out.error = String(err);
    return reply(out);
  }
}

function doGet() {
  return reply({ok: true, hint: 'POST {secret, ops:[…]}; see sheets_writer.gs header'});
}

/** Write buffered appends for one tab. Rows whose Company already exists in
 *  the tab are INSERTED at the end of that company's first block — the user
 *  reads the sheet grouped by company, and the same company scattered across
 *  five places is noise. New companies append at the end in one batched
 *  setValues, sorted so same-company newcomers land contiguously. */
function flush(ss, pending, tab, out) {
  var rows = pending[tab];
  if (!rows || !rows.length) return;
  delete pending[tab];
  var sh = ss.getSheetByName(tab);
  if (!sh) { out.results.push({op: 'append', tab: tab, error: 'missing tab'}); return; }
  var hdrs = headerMap(sh);
  var atEnd = [];
  for (var i = 0; i < rows.length; i++) {
    var comp = rowCompany(rows[i]);
    var at = comp ? blockEnd(sh, hdrs, comp) : -1;   // fresh scan each time: inserts shift rows
    if (at < 0) { atEnd.push(rows[i]); continue; }
    out.groupedInserts = (out.groupedInserts || 0) + 1;
    if (out.dryRun) continue;
    sh.insertRowsAfter(at, 1);
    writeRow(sh, hdrs, at + 1, rows[i], out);
  }
  if (!atEnd.length) return;
  atEnd.sort(function (a, b) {
    var ca = norm(rowCompany(a)), cb = norm(rowCompany(b));
    return ca < cb ? -1 : ca > cb ? 1 : 0;
  });
  var width = Math.max(lastCol(sh), 1), grid = [];
  for (var j = 0; j < atEnd.length; j++) {
    // Fill explicitly: setValues rejects a sparse array's undefined holes.
    var line = [];
    for (var w = 0; w < width; w++) line.push('');
    for (var k in atEnd[j]) {
      if (!atEnd[j].hasOwnProperty(k)) continue;
      var idx = hdrs[norm(k)];
      if (!idx) { out.ignoredCols[k] = (out.ignoredCols[k] || 0) + 1; continue; }
      var v = atEnd[j][k];
      line[idx - 1] = (v === null || v === undefined) ? '' : v;
    }
    grid.push(line);
  }
  if (!out.dryRun) sh.getRange(sh.getLastRow() + 1, 1, grid.length, width).setValues(grid);
}

function rowCompany(values) {
  for (var k in values) {
    if (values.hasOwnProperty(k) && norm(k) === 'company') return String(values[k] == null ? '' : values[k]);
  }
  return '';
}

/** Row index of the last row in the FIRST contiguous block for company; -1 if absent. */
function blockEnd(sh, hdrs, company) {
  var ci = hdrs[norm('Company')];
  if (!ci || sh.getLastRow() < 2) return -1;
  var vals = sh.getRange(2, ci, sh.getLastRow() - 1, 1).getValues(), want = norm(company);
  for (var i = 0; i < vals.length; i++) {
    if (norm(vals[i][0]) === want) {
      var e = i;
      while (e + 1 < vals.length && norm(vals[e + 1][0]) === want) e++;
      return e + 2;
    }
  }
  return -1;
}

/** Column name -> 1-based index, from row 1. Case/space/punctuation
 *  insensitive, so the sheet's own header wording wins over the pipeline's. */
function headerMap(sh) {
  var last = sh.getLastColumn(), m = {};
  if (last >= 1) {
    var row = sh.getRange(1, 1, 1, last).getValues()[0];
    for (var c = 0; c < row.length; c++) {
      var k = norm(row[c]);
      if (k && !(k in m)) m[k] = c + 1;
    }
  }
  var v = VHDR[sh.getName()] || {};      // columns a dry run would have added
  for (var k2 in v) if (v.hasOwnProperty(k2) && !(k2 in m)) m[k2] = v[k2];
  return m;
}

function lastCol(sh) {
  var v = VHDR[sh.getName()] || {}, n = sh.getLastColumn();
  for (var k in v) if (v.hasOwnProperty(k)) n = Math.max(n, v[k]);
  return n;
}

function norm(s) { return String(s == null ? '' : s).toLowerCase().replace(/[^a-z0-9]/g, ''); }

function addCols(sh, hdrs, names, out) {
  for (var i = 0; i < names.length; i++) {
    if (hdrs[norm(names[i])]) continue;
    var col = lastCol(sh) + 1;
    if (out.dryRun) {
      VHDR[sh.getName()] = VHDR[sh.getName()] || {};
      VHDR[sh.getName()][norm(names[i])] = col;
    } else {
      sh.getRange(1, col).setValue(names[i]);
    }
    hdrs[norm(names[i])] = col;
  }
  return hdrs;
}

/** Matching rows, newest first. `all` false → newest match only. */
function findRows(sh, hdrs, col, value, all) {
  var idx = hdrs[norm(col)], hits = [];
  if (!idx || sh.getLastRow() < 2) return hits;
  var vals = sh.getRange(2, idx, sh.getLastRow() - 1, 1).getValues(), want = norm(value);
  for (var i = vals.length - 1; i >= 0; i--) {
    if (norm(vals[i][0]) === want) { hits.push(i + 2); if (!all) break; }
  }
  return hits;
}

/** Omitted key = leave alone. Explicit null/'' = clear the cell. */
function writeRow(sh, hdrs, row, values, out) {
  values = values || {};
  for (var k in values) {
    if (!values.hasOwnProperty(k)) continue;
    var idx = hdrs[norm(k)];
    if (!idx) { out.ignoredCols[k] = (out.ignoredCols[k] || 0) + 1; continue; }
    var v = values[k];
    if (out.dryRun) continue;
    if (v === null || v === undefined || v === '') sh.getRange(row, idx).clearContent();
    else sh.getRange(row, idx).setValue(v);
  }
}

function setRange(sh, op, out) {
  if (!out.dryRun) sh.getRange(op.a1 || 'A1').setValue(op.value == null ? '' : op.value);
  out.updated++;
}

/** Replace a column's dropdown list. Requires confirm — it rewrites every
 *  data cell's validation in that column, and a wrong list silently blocks
 *  every future write to it. Existing cell VALUES are never touched. */
function setValidation(sh, op, out) {
  if (!op.confirm) {
    out.results.push({op: 'set_validation', tab: sh.getName(), skipped: 'confirm required'});
    return;
  }
  var hdrs = headerMap(sh), idx = hdrs[norm(op.col)];
  if (!idx) {
    out.ignoredCols[op.col] = (out.ignoredCols[op.col] || 0) + 1;
    out.results.push({op: 'set_validation', tab: sh.getName(), error: 'no such column: ' + op.col});
    return;
  }
  var vals = (op.values || []).map(String).filter(function (v) { return v !== ''; });
  var last = Math.max(sh.getLastRow(), 2);
  var rng = sh.getRange(2, idx, last - 1, 1);
  if (!out.dryRun) {
    if (!vals.length) {
      rng.clearDataValidations();
    } else {
      rng.setDataValidation(SpreadsheetApp.newDataValidation()
          .requireValueInList(vals, true).setAllowInvalid(false).build());
    }
  }
  out.results.push({op: 'set_validation', tab: sh.getName(), col: op.col,
                    values: vals, rows: last - 1});
}

function readRows(sh, op, out) {
  var last = sh.getLastRow(), width = sh.getLastColumn();
  if (last < 1 || width < 1) return {op: 'read', tab: sh.getName(), rows: []};
  var limit = Math.min(op.limit || 50, Math.max(last - 1, 0));
  var hdr = sh.getRange(1, 1, 1, width).getValues()[0];
  var body = limit ? sh.getRange(2, 1, limit, width).getValues() : [];
  return {op: 'read', tab: sh.getName(), headers: hdr, rows: body, totalRows: last - 1};
}

/** Create the tab if absent and make sure every requested header exists. */
function ensureTab(ss, tab, headers, out) {
  var sh = ss.getSheetByName(tab), created = false;
  if (!sh) {
    if (out.dryRun) { VTAB[tab] = true; return {op: 'ensure_tab', tab: tab, wouldCreate: true}; }
    sh = ss.insertSheet(tab); created = true;
  }
  var hdrs = headerMap(sh), added = [];
  for (var i = 0; i < (headers || []).length; i++) {
    if (hdrs[norm(headers[i])]) continue;
    var col = lastCol(sh) + 1;          // 0 on a fresh sheet -> column 1
    if (out.dryRun) {
      VHDR[tab] = VHDR[tab] || {}; VHDR[tab][norm(headers[i])] = col;
    } else {
      sh.getRange(1, col).setValue(headers[i]);
    }
    hdrs[norm(headers[i])] = col; added.push(headers[i]);
  }
  return {op: 'ensure_tab', tab: tab, created: created, addedHeaders: added};
}

function deleteTab(ss, tab, confirm, out) {
  if (!confirm) return {op: 'delete_tab', tab: tab, error: 'refused: pass confirm:true'};
  var sh = ss.getSheetByName(tab);
  if (!sh) return {op: 'delete_tab', tab: tab, error: 'missing tab'};
  if (!out.dryRun) ss.deleteSheet(sh);
  return {op: 'delete_tab', tab: tab, deleted: true};
}

function merge(a, b) {
  var o = {}, k;
  for (k in (a || {})) if (a.hasOwnProperty(k)) o[k] = a[k];
  for (k in (b || {})) if (b.hasOwnProperty(k)) o[k] = b[k];
  return o;
}

function tabNames(ss) {
  return (ss || SpreadsheetApp.getActiveSpreadsheet()).getSheets()
      .map(function (s) { return s.getName(); });
}

/** One regroup step. dry=true: count every stray row. dry=false: move the
 *  FIRST stray found and return 1 (indices shift, so the caller loops). */
function regroupPass(sh, ci, dry) {
  var lastR = sh.getLastRow();
  if (lastR < 3) return 0;
  var comps = sh.getRange(2, ci, lastR - 1, 1).getValues().map(function (x) { return norm(x[0]); });
  var firstAt = {}, count = 0;
  for (var r = 0; r < comps.length; r++) {
    var cc = comps[r];
    if (!cc) continue;
    if (!(cc in firstAt)) { firstAt[cc] = r; continue; }
    var e = firstAt[cc];
    while (e + 1 < comps.length && comps[e + 1] === cc) e++;
    if (r <= e) continue;                       // inside the first block already
    count++;
    if (!dry) {
      sh.moveRows(sh.getRange(r + 2, 1, 1, Math.max(sh.getLastColumn(), 1)), e + 3);
      return 1;
    }
  }
  return dry ? count : 0;
}

function reply(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
      .setMimeType(ContentService.MimeType.JSON);
}
