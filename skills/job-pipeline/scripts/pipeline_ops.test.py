#!/usr/bin/env python3
"""Offline tests for pipeline_ops' comp capture.  Run: python3 <this file>

Comp is the one column written from free text rather than a feed field, so it
is the one that can silently invent a number. Every case below is either a real
posting shape that broke the parser during development or a decoy that must
stay empty — a blank cell is always allowed, a wrong one never is."""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pipeline_ops as po                                          # noqa: E402

po.configure()

ESCAPED = ('&lt;div&gt;Salary Range&lt;/div&gt;&lt;span&gt;$250,000&lt;/span&gt;'
           '&lt;span class="divider"&gt;-&lt;/span&gt;&lt;span&gt;$300,000 USD&lt;/span&gt;')

CASES = [
    # (name, posting text, expected cell)
    ("plain range",         "<p>Salary Range: $180K - $225K</p>",                  "$180K-$225K"),
    ("single figure",       "<p>Base Salary: $300,000</p>",                        "$300K"),
    ("figure before label", "<p>$300,000 is the base salary for this role.</p>",   "$300K"),
    ("usd suffix",          "<p>Annual Salary: $280,000 - $850,000 USD</p>",       "$280K-$850K"),
    ("non-round endpoints", "<p>Salary range: $264,800 - $331,000</p>",            "$264,800-$331,000"),
    # Greenhouse's API double-escapes its HTML; stripping tags before unescaping
    # left the markup as literal text and split the range into one figure.
    ("escaped markup",      ESCAPED,                                               "$250K-$300K"),
    # Anthropic states OTE only in boilerplate about *sales* roles, then gives a
    # plain "Annual Salary" band — the nearest label to the figure decides.
    ("ote boilerplate then salary",
     "<p>For sales roles the range is On Target Earnings (\"OTE\").</p>"
     "<p>Annual Salary: $280,000 - $850,000 USD</p>",                              "$280K-$850K"),
    ("genuine ote",         "<p>On-target earnings: $230,000 - $320,000</p>",      "OTE $230K-$320K"),
    # Decoys: a dollar figure with no pay label nearby, or outside the bounds.
    ("referral bonus",      "<p>Referral bonus of $2,000. Salary is competitive.</p>",       ""),
    ("funding round",       "<p>We raised $150,000,000 in Series C. Compensation is great.</p>", ""),
    ("equity only",         "<p>Equity grant valued at $400,000 over four years.</p>",       ""),
    ("nothing stated",      "<p>Free lunch, dog-friendly office.</p>",                       ""),
]

ASHBY = [
    ("scrapeable preferred",
     {"compensation": {"scrapeableCompensationSalarySummary": "$200K - $400K",
                       "compensationTierSummary": "$200K – $400K • Offers Equity"}}, "$200K-$400K"),
    ("tier summary fallback",
     {"compensation": {"compensationTierSummary": "$226K – $283K • Offers Equity"}}, "$226K-$283K"),
    ("no comp published", {"title": "SWE"}, ""),
]

fails = 0
for name, text, want in CASES:
    got = po.parse_comp(text)
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  parse_comp: {name:28s} -> {got!r}"
          + ("" if ok else f"   want {want!r}"))
for name, job, want in ASHBY:
    got = po.ashby_comp(job)
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  ashby_comp: {name:28s} -> {got!r}"
          + ("" if ok else f"   want {want!r}"))


# ---------------------------------------------------------------- Last Hit
# The prune rule's only input. It was blank on every row for the life of the
# pipeline because nothing wrote it, so these cases pin the two properties that
# make it safe to prune on: a feed that hit gets today's date, and NOTHING else
# in the file moves -- no other row, no other column, no regression to an older
# date, and no wholesale ageing of the list on a run whose scan half died.
import csv as _csv, shutil as _shutil, tempfile as _tempfile                # noqa: E402

WL_HEADER = "Company,Feed URL,Type,Signal,Added,Last Hit\n"
WL_ROWS = [
    "Baseten,https://api.ashbyhq.com/posting-api/job-board/baseten,ashby,,2026-08-09,\n",
    "Hippocratic AI,https://www.hippocraticai.com/careers,careers-page,,2026-09-04,\n",
    "Optiver,https://optiver.com/careers/,careers-page,,2026-09-06,2026-09-01\n",
    "Quiet Co,https://boards-api.greenhouse.io/v1/boards/quiet/jobs,greenhouse,,2026-08-09,2026-08-20\n",
]


def _wl_case(name, scan_report, candidates, day, want):
    """Build a throwaway run/ + state/, persist-stamp it, return the rows."""
    tmp = _tempfile.mkdtemp()
    try:
        rd, sd = os.path.join(tmp, "run"), os.path.join(tmp, "state")
        os.makedirs(rd); os.makedirs(sd)
        with open(os.path.join(sd, "watchlist.csv"), "w", encoding="utf-8") as f:
            f.write(WL_HEADER); f.writelines(WL_ROWS)
        if scan_report is not None:
            with open(os.path.join(rd, "scan_report.json"), "w", encoding="utf-8") as f:
                json.dump(scan_report, f)
        if candidates is not None:
            with open(os.path.join(rd, "candidates.jsonl"), "w", encoding="utf-8") as f:
                for c in candidates:
                    f.write(json.dumps(c) + "\n")
        po.stamp_last_hit(rd, sd, day)
        with open(os.path.join(sd, "watchlist.csv"), newline="", encoding="utf-8") as f:
            got = {r["Company"]: r["Last Hit"] for r in _csv.DictReader(f)}
        return got
    finally:
        _shutil.rmtree(tmp, ignore_errors=True)


BASE = {"Baseten": "", "Hippocratic AI": "", "Optiver": "2026-09-01", "Quiet Co": "2026-08-20"}
LASTHIT = [
    # (name, scan_report, candidates, day, expected Last Hit by company)
    ("ats hit stamps that row only",
     {"hits": {"ats:Baseten": 28}}, None, "2026-09-14",
     {**BASE, "Baseten": "2026-09-14"}),
    # The careers-page half never reaches the script's counter -- the agent walks
    # those by hand and they arrive as careers:<Company> candidates.
    ("careers-page hit counts",
     {"hits": {}}, [{"source": "careers:Hippocratic AI"}], "2026-09-14",
     {**BASE, "Hippocratic AI": "2026-09-14"}),
    # A source with zero relevant postings is not a hit: a board that emptied out
    # is exactly what prune is looking for.
    ("zero-count source is not a hit",
     {"hits": {"ats:Baseten": 0}}, None, "2026-09-14", BASE),
    # Curated lists and mail alerts say nothing about a watchlist feed's health.
    ("list and alert sources ignored",
     {"hits": {"speedyapply": 412, "hn:whoishiring": 9}},
     [{"source": "mail-alert"}, {"source": "jobspy:indeed"}], "2026-09-14", BASE),
    ("never regresses to an older date",
     {"hits": {"ats:Optiver": 3}}, None, "2026-08-15", BASE),
    ("advances an existing older date",
     {"hits": {"ats:Optiver": 3}}, None, "2026-09-14",
     {**BASE, "Optiver": "2026-09-14"}),
    # A dead scan must not age the whole list at once -- that would hand the next
    # weekly review a prune proposal for every feed the user has.
    ("scan produced nothing: file untouched",
     {"hits": {}}, [], "2026-09-14", BASE),
    ("no scan report at all: file untouched", None, None, "2026-09-14", BASE),
    ("company match ignores case and punctuation",
     {"hits": {"ats:hippocratic-ai": 4}}, None, "2026-09-14",
     {**BASE, "Hippocratic AI": "2026-09-14"}),
]

for name, rep, cands, day, want in LASTHIT:
    got = _wl_case(name, rep, cands, day, want)
    ok = got == want
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  last_hit:   {name:28s}"
          + ("" if ok else f"\n      got  {got}\n      want {want}"))

# --------------------------------------------------------------------------
# Write targeting. A mail event names a company, never a row, so every update
# is aimed by inference. All three cases below are live failures from the
# 2026-09-20 weekly run: a rejected row re-appended as a duplicate, a bare
# Company match that covered 22 rows, and a Status the tab's validation
# refuses (which aborts the whole batch server-side, not just its own op).
import csv as _csv, tempfile as _tmp, shutil as _sh

_SHEET = [
    ("MLE", "acme ai", "Inference Engineer, Kernel Performance", "", "Not Applied"),
    ("MLE", "acme ai", "Compiler Engineer, Software", "", "Not Applied"),
    ("MLE", "Acme AI", "", "", "Interview scheduled"),
    ("MLE", "Initech", "", "", "Applied"),
    # Same company, rows on two lane tabs. Which one wins must not depend on
    # the order the index file happens to list tabs in.
    ("MLE", "NimbusAI", "", "", "Applied"),
    ("SWE", "Nimbus AI", "", "", "Applied"),
    ("HFT", "Globex Trading", "", "", "Unreleased"),
    ("HFT", "Globex Trading", "Associate Engineer - New Grad", "", "Reject"),
    ("SWE", "Hooli", "", "", "Unreleased"),
    ("SWE", "Hooli", "Software Engineer, Early Career", "", "Not Applied"),
    ("SWE", "Hooli", "Software Engineer, Payments Infra", "", "Applied"),
]
_MAIL = [
    {"kind": "rejection", "company": "Initech", "role": None,
     "received": "2026-09-18T10:00:00Z", "deadline": None,
     "thread_url": "https://mail.google.com/x/initech"},
    {"kind": "rejection", "company": "Globex Trading",
     "role": "Associate Engineer - New Grad", "received": "2026-09-14T10:00:00Z",
     "deadline": None, "thread_url": "https://mail.google.com/x/globex"},
    {"kind": "ack", "company": "Hooli", "role": None,
     "received": "2026-09-17T10:00:00Z", "deadline": None,
     "thread_url": "https://mail.google.com/x/hooli"},
    {"kind": "interview", "company": "Acme AI", "role": None,
     "received": "2026-09-14T10:00:00Z", "deadline": None,
     "thread_url": "https://mail.google.com/x/acme"},
    {"kind": "interview", "company": "NimbusAI",
     "role": "Kernel Engineer (GPU)",
     "received": "2026-09-17T10:00:00Z", "deadline": "2026-09-24",
     "thread_url": "https://mail.google.com/x/nimbus"},
]


def _merge_case(with_index=True):
    rd = _tmp.mkdtemp()
    open(os.path.join(rd, "candidates.jsonl"), "w").close()
    open(os.path.join(rd, "triage.jsonl"), "w").close()
    with open(os.path.join(rd, "mail_events.jsonl"), "w") as f:
        for e in _MAIL:
            f.write(json.dumps(e) + "\n")
    if with_index:
        with open(os.path.join(rd, "sheet_index.csv"), "w", newline="") as f:
            w = _csv.writer(f)
            w.writerow(["tab", "Company", "Role", "Link", "Status"])
            for r in _SHEET:
                w.writerow(r)
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        po.cmd_merge(rd, "weekly", "2026-09-20")
    ops = [json.loads(l) for l in open(os.path.join(rd, "sheet_ops.jsonl")) if l.strip()]
    summ = json.load(open(os.path.join(rd, "run_summary.json")))
    _sh.rmtree(rd)
    return [o for o in ops if o["op"] != "ensure_tab"], summ


# A tab whose Status dropdown lacks "Reject" (routing.status_vocab). The
# shipped default is unrestricted, so the restricted case is set up here.
po.CONFIG.setdefault("routing", {})["status_vocab"] = {
    "MLE": ["Unreleased", "Not Applied", "Applied", "OA received",
            "OA submitted", "Interview scheduled", "Interview done", "Offer"]}
_ops, _summ = _merge_case()
_nv, _ctc, _goog, _etch, _den = (_ops + [{}] * 5)[:5]   # mail-event order
MERGE = [
    ("already-on-sheet company updates, never re-appends",
     _ctc.get("op") == "update" and not [o for o in _ops if o["op"] == "append"]),
    ("update lands on the row's own tab, not a fresh lane guess",
     _ctc.get("tab") == "HFT"),
    ("a unique Role beats a Company match",
     _ctc.get("match", {}).get("col") == "Role"),
    ("22 same-company rows narrow to the one open row",
     _goog.get("match", {}).get("value") == "Software Engineer, Payments Infra"),
    ("a Status the tab's validation lacks is dropped, not sent",
     "Status" not in _nv.get("values", {})),
    ("...while the row's other values still get written",
     _nv.get("values", {}).get("Last Email") == "https://mail.google.com/x/initech"),
    ("...and the skipped Status is reported for the digest",
     any(x["company"] == "Initech" and x["status"] == "Reject"
         for x in _summ.get("status_unwritable", []))),
    ("a Status the tab DOES have is still written",
     _ctc.get("values", {}).get("Status") == "Reject"),
    ("a blank-Role target reachable only by Company is flagged ambiguous",
     any(x["company"] == "Acme AI" and x["matched_on"] == "Company"
         for x in _summ.get("ambiguous_matches", []))),
    ("a company on two lane tabs routes by lane, not by index file order",
     _den.get("tab") == "MLE" and _den.get("match", {}).get("value") == "NimbusAI"),
    ("...and its OA Deadline still rides along",
     _den.get("values", {}).get("OA Deadline") == "2026-09-24"),
    ("no sheet index: degrades to the old behavior without crashing",
     len(_merge_case(with_index=False)[0]) > 0),
]
for name, ok in MERGE:
    fails += not ok
    print(f"{'PASS' if ok else 'FAIL'}  merge:      {name}")

TOTAL = len(CASES) + len(ASHBY) + len(LASTHIT) + len(MERGE)
print(f"\n{TOTAL - fails}/{TOTAL} passed")
sys.exit(1 if fails else 0)
