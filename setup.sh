#!/usr/bin/env bash
# setup.sh — install the job-pipeline plugin and prove it works.
#
#   ./setup.sh              install + self-test
#   ./setup.sh --wide       also install python-jobspy (Indeed/LinkedIn rung)
#   ./setup.sh --check      self-test only, install nothing
#
# Idempotent: safe to re-run. Exits non-zero only when the pipeline is actually
# broken — a missing optional dep is reported, not fatal.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCAN="$HERE/skills/job-source-scan/scripts/fetch_sources.py"
OPS="$HERE/skills/job-pipeline/scripts/pipeline_ops.py"
WIDE=0; CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --wide) WIDE=1 ;;
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown flag: $a" >&2; exit 2 ;;
  esac
done

pass() { printf '  \033[32mok\033[0m   %s\n' "$1"; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$1"; FAILED=1; }
FAILED=0

PY="$(command -v python3 || true)"
[ -z "$PY" ] && { echo "python3 not found. Run: xcode-select --install" >&2; exit 1; }

echo "==> python"
pass "$($PY -V 2>&1) at $PY"

# 1. TLS trust store -------------------------------------------------------
# python.org framework builds ship without root certificates, so every HTTPS
# fetch dies with CERTIFICATE_VERIFY_FAILED and every source lands in `failed`.
echo "==> TLS certificates"
if $PY - <<'EOF' 2>/dev/null
import urllib.request
urllib.request.urlopen("https://raw.githubusercontent.com", timeout=15)
EOF
then pass "HTTPS verification works"
elif [ "$CHECK_ONLY" = 1 ]; then fail "TLS verification broken (re-run without --check to fix)"
else
  warn "no trusted roots — installing certifi and linking Python's cert path"
  $PY -m pip install --user --quiet certifi >/dev/null 2>&1
  $PY - <<'EOF'
import os, ssl, certifi
cafile = ssl.get_default_verify_paths().openssl_cafile
os.makedirs(os.path.dirname(cafile), exist_ok=True)
if os.path.islink(cafile) or os.path.exists(cafile):
    os.remove(cafile)
os.symlink(certifi.where(), cafile)
print("  linked", cafile, "->", certifi.where())
EOF
  $PY -c "import urllib.request;urllib.request.urlopen('https://raw.githubusercontent.com',timeout=15)" 2>/dev/null \
    && pass "HTTPS verification works" || fail "still failing — check for a TLS-intercepting proxy"
fi

# 2. Optional wide-net dependency -----------------------------------------
echo "==> optional deps"
if $PY -c "import jobspy" 2>/dev/null; then
  pass "python-jobspy present (--jobspy rung available)"
elif [ "$WIDE" = 1 ] && [ "$CHECK_ONLY" = 0 ]; then
  warn "installing python-jobspy (pulls pandas, ~1 min)"
  $PY -m pip install --user --quiet python-jobspy >/dev/null 2>&1
  $PY -c "import jobspy" 2>/dev/null && pass "python-jobspy installed" || fail "python-jobspy install failed"
else
  warn "python-jobspy absent — --jobspy fails soft into the report. Add it with: ./setup.sh --wide"
fi

# 3. Install agents + skills ----------------------------------------------
if [ "$CHECK_ONLY" = 0 ]; then
  echo "==> installing into ~/.claude"
  mkdir -p "$HOME/.claude/agents" "$HOME/.claude/skills"
  cp "$HERE"/agents/*.md "$HOME/.claude/agents/"
  cp -R "$HERE"/skills/* "$HOME/.claude/skills/"
  pass "$(ls -1 "$HERE"/agents/*.md | wc -l | tr -d ' ') agents -> ~/.claude/agents"
  pass "$(ls -1d "$HERE"/skills/*/ | wc -l | tr -d ' ') skills -> ~/.claude/skills"
  # No config is seeded anywhere: the scripts resolve assets/config.seed.json
  # beside themselves, and the cp -R above carries assets/ along, so the
  # ~/.claude install reads the very same file the repo does.
fi

# 3b. Config + repo state — both live in this checkout ---------------------
echo "==> config + state (in the repo)"
SEED="$HERE/skills/job-pipeline/assets/config.seed.json"
if [ -f "$SEED" ]; then
  $PY -c "import json,sys; json.load(open(sys.argv[1]))" "$SEED" 2>/dev/null \
    && pass "config.seed.json parses (authoritative; scripts resolve it directly)" \
    || fail "config.seed.json is not valid JSON"
else
  warn "no config.seed.json yet — scripts fall back to config.example.json (no sheet, no writer credentials)"
fi

# Onboarding writes config.seed.json and fills policy.md. Missing pieces are
# warnings, not failures: the local pipeline still self-tests without them,
# but a scheduled run would have nowhere to write and nothing to judge by.
echo "==> onboarding"
ONBOARDED=1
POLICY="$HERE/skills/job-pipeline/policy.md"
NSLOT=$(grep -cE '\{\{[A-Z_]+\}\}' "$POLICY" 2>/dev/null)
if [ "${NSLOT:-0}" -eq 0 ]; then pass "policy.md filled in"
else warn "policy.md still has $NSLOT line(s) with {{…}} slots"; ONBOARDED=0; fi
if [ -f "$SEED" ]; then
  MISSING="$($PY - "$SEED" <<'EOF'
import json, sys
try:
    c = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception:
    print("config.seed.json"); sys.exit()
st, sw = c.get("storage") or {}, c.get("sheets_writer") or {}
need = {"storage.tracker_sheet_id": st.get("tracker_sheet_id"),
        "storage.resumes.primary": (st.get("resumes") or {}).get("primary"),
        "sheets_writer.url": sw.get("url"),
        "sheets_writer.secret": sw.get("secret")}
print(" ".join(k for k, v in need.items() if not v))
EOF
)"
  if [ -z "$MISSING" ]; then pass "config.seed.json has sheet, resume and writer settings"
  else warn "config.seed.json is missing: $MISSING"; ONBOARDED=0; fi
else
  ONBOARDED=0
fi
[ "$ONBOARDED" = 1 ] || warn "not onboarded yet — run /job-onboarding in Claude Code (see ONBOARDING.md)"
[ -f "$HOME/.claude/job-pipeline/config.json" ] \
  && warn "legacy ~/.claude/job-pipeline/config.json found — ignored since the repo became authoritative; delete it"
# Retired by the Drive -> repo migration. cp -R never removes anything, so an
# older install leaves these in ~/.claude, where a local agent would still
# find them. Prune on install; on --check just say so.
for f in job-pipeline/assets/drive_writer.gs job-pipeline/assets/drive_writer.test.js \
         job-pipeline/assets/profile.example.md; do
  if [ -e "$HOME/.claude/skills/$f" ]; then
    if [ "$CHECK_ONLY" = 0 ]; then
      rm -f "$HOME/.claude/skills/$f" && pass "pruned retired ~/.claude/skills/$f"
    else
      warn "retired file still installed: ~/.claude/skills/$f — re-run without --check to prune it"
    fi
  fi
done
echo "==> repo state"
# The state files are what a run dedupes against and what the weekly review
# reads; a bad merge that corrupts one must fail here, not silently in a run.
read -r WROWS SKEYS NRUNS < <($PY - "$HERE/state" <<'EOF'
import csv, glob, json, os, sys
sd = sys.argv[1]
try:
    rdr = csv.DictReader(open(os.path.join(sd, "watchlist.csv"), newline="", encoding="utf-8"))
    assert all(k in (rdr.fieldnames or []) for k in ("Company", "Feed URL", "Type"))
    w = len(list(rdr))
except Exception:
    w = -1
try:
    keys = [l.strip() for l in open(os.path.join(sd, "seen.txt"), encoding="utf-8") if l.strip()]
    s = len(keys) if len(keys) == len(set(keys)) else -1
except Exception:
    s = -1
n = 0
for p in glob.glob(os.path.join(sd, "runs", "run-*.json")):
    try: json.load(open(p, encoding="utf-8")); n += 1
    except Exception: n = -1; break
print(w, s, n)
EOF
)
if [ "${WROWS:--1}" -gt 0 ]; then pass "state/watchlist.csv: $WROWS feeds"
elif [ "${WROWS:--1}" -eq 0 ]; then warn "state/watchlist.csv is empty — only the curated lists will be scanned"
else fail "state/watchlist.csv missing or malformed (needs Company, Feed URL, Type columns)"; fi
[ "${SKEYS:--1}" -ge 0 ] && pass "state/seen.txt: $SKEYS keys, no duplicates" \
                        || fail "state/seen.txt missing or carries duplicate keys"
[ "${NRUNS:--1}" -ge 0 ] && pass "state/runs: $NRUNS run files, all valid JSON" \
                        || fail "state/runs has a file that is not valid JSON"

# 4. Self-test: the pipeline must actually produce rows --------------------
echo "==> self-test (live network)"
# Hermetic: an empty --state-dir means fixed sources only, no ledger, and
# persist below writes into the temp dir — the repo's state/ is never touched.
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
if $PY "$SCAN" --run-dir "$TMP" --state-dir "$TMP/state" >"$TMP/out.txt" 2>&1; then
  read -r FETCHED KEPT NFAIL < <($PY - "$TMP" <<'EOF'
import json, os, sys
r = json.load(open(os.path.join(sys.argv[1], "scan_report.json")))
c = r["counts"]; print(c["fetched"], c["kept"], len(r["failed"]))
EOF
)
  [ "${FETCHED:-0}" -gt 100 ] && pass "curated lists parsed: $FETCHED fetched, $KEPT kept" \
                              || fail "only $FETCHED postings parsed — upstream format likely drifted again"
  [ "${NFAIL:-1}" -eq 0 ] && pass "no source failures" || warn "$NFAIL source(s) in failed[] — see scan_report.json"
else
  fail "scan crashed: $(tail -3 "$TMP/out.txt")"
fi

# split/merge round-trip on the scan output, with a synthetic triage verdict
$PY "$OPS" split --run-dir "$TMP" --size 40 >/dev/null 2>&1 \
  && pass "pipeline_ops split" || fail "pipeline_ops split"
$PY - "$TMP" <<'EOF' >/dev/null 2>&1
import json, os, sys
rd = sys.argv[1]
cands = [json.loads(l) for l in open(os.path.join(rd, "candidates.b1.jsonl"))]
with open(os.path.join(rd, "triage.b1.jsonl"), "w") as f:
    for c in cands:
        f.write(json.dumps({"url": c["url"], "fit": 4, "star": False, "flags": [], "why": "self-test"}) + "\n")
EOF
# stdout only — the scripts log config provenance to stderr, and folding it in
# would corrupt the JSON this parses.
if $PY "$OPS" merge --run-dir "$TMP" --kind morning >"$TMP/merge.json" 2>"$TMP/merge.err"; then
  OPS_N=$($PY -c "import json,sys;print(json.load(open(sys.argv[1]))['ops'])" "$TMP/merge.json" 2>/dev/null || echo 0)
  [ "${OPS_N:-0}" -gt 0 ] && pass "pipeline_ops merge produced $OPS_N sheet ops" \
                          || fail "merge produced no ops — candidate/triage keys are not joining"
else
  fail "pipeline_ops merge crashed"
fi
# persist: the run-end step that carries seen_delta + the run file into state/
if $PY "$OPS" persist --run-dir "$TMP" --state-dir "$TMP/state" >"$TMP/persist.json" 2>"$TMP/persist.err"; then
  read -r ADDED RUNF < <($PY -c "import json,sys; d=json.load(open(sys.argv[1])); print(d['seen_added'], d['run_file'] or '-')" "$TMP/persist.json" 2>/dev/null || echo "0 -")
  if [ "${ADDED:-0}" -gt 0 ] && [ -f "$TMP/state/seen.txt" ] && [ -f "$RUNF" ]; then
    pass "pipeline_ops persist: $ADDED keys -> seen.txt, run file $(basename "$RUNF")"
  else
    fail "persist wrote nothing (seen_added=$ADDED, run_file=$RUNF)"
  fi
else
  fail "pipeline_ops persist crashed: $(tail -2 "$TMP/persist.err")"
fi

# sheets_writer.gs logic, against a mock spreadsheet (macOS JavaScriptCore).
# Worth running before deploying: the script can delete rows and tabs.
if command -v osascript >/dev/null 2>&1; then
  GSOUT="$(osascript -l JavaScript "$HERE/skills/job-pipeline/assets/sheets_writer.test.js" \
           "$HERE/skills/job-pipeline/assets/sheets_writer.gs" 2>&1)"
  if printf '%s' "$GSOUT" | grep -q FAIL; then
    fail "sheets_writer.gs: $(printf '%s' "$GSOUT" | grep FAIL | head -3)"
  else
    pass "sheets_writer.gs: $(printf '%s' "$GSOUT" | grep -c PASS) checks"
  fi
fi

echo
if [ "$FAILED" = 0 ]; then
  if [ "$ONBOARDED" = 1 ]; then
    echo "Local pipeline is healthy and onboarded. Next: schedule it — ROUTINES.md"
  else
    echo "Local pipeline is healthy. Next: onboarding — open Claude Code here and run /job-onboarding (ONBOARDING.md)"
  fi
else
  echo "Something above FAILED — fix it before scheduling anything." >&2
fi
exit $FAILED
