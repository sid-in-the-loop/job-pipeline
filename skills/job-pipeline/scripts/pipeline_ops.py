#!/usr/bin/env python3
"""pipeline_ops.py — deterministic transforms for the job-pipeline orchestrator.

The orchestrator should never hand-copy rows between files: joins, splits, and
op-building are exact transforms, so they live here. Judgment (what to do about
errors, digest wording) stays with the agent. Stdlib only.

Subcommands:
  split  --run-dir run --size 40
      candidates.jsonl -> candidates.b1.jsonl, candidates.b2.jsonl, ...
  merge  --run-dir run --kind morning [--today YYYY-MM-DD]
      candidates.jsonl + triage.b*.jsonl + mail_events.jsonl (+ open_oas.csv,
      scan_report.json if present) -> sheet_ops.jsonl + run_summary.json
      Prints a small JSON summary; writes files; never prints row data.
  backfill-comp [--links FILE] --out ops.jsonl [--limit N] [--overwrite]
      One-off: fetch rows already on the sheet and fill their Comp cell from
      the posting. With no --links it reads the open lane-tab rows itself and
      skips any that already have a Comp; FILE overrides that with one row per
      line, either a bare URL or {"tab":"SWE","company":"IMC","link":"https://…"}
      (without a tab, pass --tab).
      Rows the pipeline adds from now on carry Comp at append time — this is
      only for the backlog, which nothing else reaches (see cmd_backfill_comp).
  persist --run-dir run [--state-dir DIR]
      run/seen_delta.txt -> appended into <state>/seen.txt (union, order kept);
      run/run_summary.json -> <state>/runs/run-<date>-<kind>.json.
      The only subcommand that writes outside the run dir. The orchestrator
      commits + pushes state/ right after it.
"""
import argparse, csv, glob, html as htmllib, json, os, re, shutil, sys
from datetime import date, datetime, timezone

# Config resolution — the durable copy is IN THE REPO, assets/config.seed.json
# beside this skill (private repo; it also carries the sheets_writer creds),
# so a cloud run needs nothing but the checkout. Order: --config,
# $JOB_PIPELINE_CONFIG, config.seed.json, config.example.json (credential-free
# twin), then these defaults. Deliberately no <run-dir>/config.json or
# ~/.claude step: a stale shadow copy silently overriding the repo was the
# failure mode. Loader duplicated in job-source-scan's fetch_sources.py — keep
# both in sync.
# The tracker is split by lane (HFT / SWE / MLE) rather than having one Openings
# tab, and those lanes already mirror policy.md's tiers — so routing preserves
# the user's own structure instead of imposing a schema on 2000+ existing rows.
# Lane is decided by company first (authoritative), then role wording.
DEFAULTS = {
    "fit_min": 3,
    "batch_size": 40,
    "tabs": {"quant": "HFT", "mlinfra": "MLE", "swe": "SWE", "outreach": "Referrals"},
    "quant_companies": [
        "citadel", "citadel securities", "jane street", "hrt", "hudson river trading",
        "optiver", "five rings", "akuna", "akuna capital", "imc", "imc trading", "drw",
        "sig", "susquehanna", "jump", "jump trading", "tower research capital", "virtu",
        "radix trading", "point72", "cubist", "millennium", "xtx markets", "old mission",
        "chicago trading company", "quadrature", "hap capital", "flow traders",
        "geneva trading", "two sigma", "d.e. shaw", "de shaw", "aqr", "arrowstreet",
        "balyasny", "squarepoint", "exoduspoint", "voleon", "headlands", "vatic",
        "wolverine", "belvedere", "peak6", "schonfeld", "verition", "walleye", "databento"],
    "mlinfra_companies": [
        "baseten", "fireworks", "fireworks ai", "together ai", "coreweave", "modal",
        "anyscale", "groq", "cerebras", "sambanova", "lambda", "lambda labs", "etched",
        "mosaic", "octoml", "runpod", "replicate", "nebius", "vast.ai"],
    "verify_max_urls": 60,
    "verify_batch_size": 10,
    # The sheet's Status column carries data validation with the USER'S OWN
    # vocabulary; writing our internal names threw a validation error at the
    # first SWE row and aborted the whole batch in e2e. Internal names stay as
    # RANK keys; only the written cell value is translated. Config-overridable.
    "status_map": {"New": "Not Applied", "Applied": "Applied", "OA": "OA received",
                   "OA Submitted": "OA submitted", "Interview": "Interview scheduled",
                   "Rejected": "Reject", "Offer": "Offer"},
    # status_map is global, but a sheet's validation lists need not be identical
    # per tab: a tab whose Status dropdown lacks "Reject" has nowhere valid for
    # a rejection to land. One refused Status aborts the whole batch, so merge
    # has to know this BEFORE it builds the op. A tab absent here is
    # unrestricted (the default: the tracker onboarding creates has no
    # validation). The fix is to add the missing value to the sheet's dropdown;
    # this guard only stops the batch dying in the meantime. Never substitute a
    # near-miss value ("Interview done" for a rejection is worse than leaving
    # the cell stale).
    "status_vocab": {},
    # Comp capture. `comp_label` is what must appear near a dollar figure for it
    # to count as pay — without it a $1,000 referral bonus or a "$10M Series B"
    # becomes the salary. The bounds are the second guard: anything outside them
    # is not an annual US comp figure for these roles.
    "comp_label": r"salary|compensation|pay range|pay band|base pay|base salary"
                  r"|total cash|on[- ]target earnings|\bOTE\b|annual base",
    "comp_min": 40000,
    "comp_max": 2000000,
    "quant_role": r"quant|trading|trader|systematic|market mak|low[- ]latency|hft",
    "mlinfra_role": r"\bml\b|machine learning|inference|gpu|kernel|cuda|training infra"
                    r"|model performance|ai infra|accelerat|pytorch|triton|distributed training",
}
CONFIG = {}
FIT_MIN = DEFAULTS["fit_min"]
HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_ROOT = os.path.abspath(os.path.join(HERE, ".."))              # skills/job-pipeline
REPO_ROOT = os.path.abspath(os.path.join(SKILL_ROOT, "..", ".."))
CONFIG_SEED = os.path.join(SKILL_ROOT, "assets", "config.seed.json")
CONFIG_EXAMPLE = os.path.join(SKILL_ROOT, "assets", "config.example.json")


def cfg(section, key, default):
    return (CONFIG.get(section) or {}).get(key, default)


def configure(explicit=None):
    global CONFIG, FIT_MIN
    for p in (explicit, os.environ.get("JOB_PIPELINE_CONFIG"), CONFIG_SEED, CONFIG_EXAMPLE):
        if p and os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    CONFIG = json.load(f)
            except (OSError, json.JSONDecodeError):
                CONFIG = {}
            FIT_MIN = cfg("triage", "fit_min", DEFAULTS["fit_min"])
            return p
    return None


# Cross-run state is committed under state/ in the repo (watchlist.csv,
# seen.txt, runs/). Resolution: --state-dir, $JOB_PIPELINE_STATE, <repo>/state
# beside this skill tree, ./state under the cwd. Duplicated in
# fetch_sources.py — keep both in sync.
def state_dir(explicit=None):
    for p in (explicit, os.environ.get("JOB_PIPELINE_STATE")):
        if p:
            return os.path.abspath(p)
    for p in (os.path.join(REPO_ROOT, "state"), os.path.join(os.getcwd(), "state")):
        if os.path.isdir(p):
            return os.path.abspath(p)
    return None


def jload(path):
    out = []
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if line:
                try: out.append(json.loads(line))
                except json.JSONDecodeError: pass
    return out


# Must stay identical to fetch_sources.py's norm_url — this is the join key
# between candidates.jsonl and triage output. Tracking params are dropped;
# everything else identifies the posting (Indeed's job id lives in ?jk=).
TRACK_PARAMS = {"ref", "refid", "source", "src", "gh_src", "trk", "trackingid",
                "origin", "mode", "position", "pagenum", "seed", "lipi", "medium",
                "campaign", "utm"}


def norm_url(u):
    base, _, q = (u or "").strip().partition("?")
    keep = sorted(p for p in q.split("&")
                  if p and not (p.split("=")[0].lower().startswith("utm_")
                                or p.split("=")[0].lower() in TRACK_PARAMS))
    return base.rstrip("/").lower() + ("?" + "&".join(keep) if keep else "")


def key(url, company="", role="", loc=""):
    if url: return norm_url(url)
    return re.sub(r"\s+", " ", f"{company}|{role}|{loc}").lower()


# --- Comp -------------------------------------------------------------------
# Comp decides which row the user opens first, and the pipeline recorded none of
# it: a $138K L4 row and a $300K trading-research row looked identical on the
# sheet. Two rules keep this trustworthy. Only what the POSTING states is ever
# written — no levels.fyi, no "typical for the level", no inference from company
# or title. And a posting that states nothing leaves the cell EMPTY; a guessed
# range is worse than a blank one, because a blank invites a look and a wrong
# number ends the question. Ashby publishes the range as a structured field, so
# fetch_sources reads it during the scan for free; everywhere else it is prose,
# which is what this parser is for, run against pages the verify worker fetches
# anyway. Deliberately not in fetch_sources: Greenhouse only carries pay inside
# `?content=true`, and pulling full HTML for every board would balloon the daily
# scan for a field the verify step gets at no extra cost.
MONEY = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{2,4}(?:\.\d+)?\s?[kK]\b|\d{5,7})")
# <style> blocks are dropped whole; <script> blocks keep their TEXT, because a
# client-rendered careers page (Google's, for one) ships the pay line inside a
# JS data blob and nowhere else. That is also why matching is proximity-based
# below rather than per-sentence: minified JS has no sentences.
_TAGS = re.compile(r"<style[^>]*>.*?</style>|<[^>]+>", re.S | re.I)
def _plain(text):
    """Readable text from a posting body.

    Entities are unescaped BEFORE tags are stripped, and twice: Greenhouse's API
    returns its content double-escaped (`&amp;lt;div&amp;gt;`), so stripping first
    left the markup behind as literal text — which pushed the two ends of
    "$250,000 &lt;span&gt;-&lt;/span&gt; $300,000" far enough apart that the range
    read as a single figure."""
    t = htmllib.unescape(htmllib.unescape(text or ""))
    return re.sub(r"\s+", " ", _TAGS.sub(" ", t))


def _money(tok):
    tok = tok.replace(",", "").strip()
    if tok[-1:].lower() == "k":
        return int(float(tok[:-1].strip()) * 1000)
    return int(float(tok))


def _fmt(vals):
    """One style per cell: K-notation only when every endpoint is a round
    thousand, so a band never reads "$264,800-$331K"."""
    k = all(v % 1000 == 0 for v in vals)
    out = [f"${v // 1000}K" if k else f"${v:,}" for v in vals]
    return out[0] if len(out) == 1 else f"{out[0]}-{out[-1]}"


def parse_comp(text):
    """A posting's STATED pay, as a short sheet cell ("$138K-$201K"), or "".

    Scans only chunks that name pay (`comp_label`) and keeps dollar figures
    inside the configured bounds, so equity dollar amounts, signing bonuses and
    funding totals elsewhere on the page cannot be mistaken for salary. The
    first qualifying chunk wins: postings state the band once, up front, and
    later mentions are usually benefits prose."""
    label = re.compile(cfg("comp", "label", DEFAULTS["comp_label"]), re.I)
    lo = cfg("comp", "min", DEFAULTS["comp_min"])
    hi = cfg("comp", "max", DEFAULTS["comp_max"])
    plain = _plain(text)
    for mm in MONEY.finditer(plain):
        v = _money(mm.group(1))
        if not lo <= v <= hi:
            continue
        # Anchor on the figure and take its NEAREST label, not the first label on
        # the page. Anthropic's postings open with boilerplate explaining that
        # sales ranges are "On Target Earnings", then state "Annual Salary:
        # $280,000 - $850,000" — label-first matching read that plain base band
        # as OTE. Look behind first (the usual "Base salary: $X" order), then a
        # short way ahead for "$300,000 is the base salary".
        prior = list(label.finditer(plain[max(0, mm.start() - 120):mm.start()]))
        lm = prior[-1] if prior else label.search(plain[mm.end():mm.end() + 70])
        if not lm:
            continue
        # A range is written as adjacent figures ("$280,000 - $850,000 USD"), so
        # keep walking while the next one is close; a figure two sentences away
        # belongs to something else.
        vals, pos = [v], mm.end()
        while True:
            nxt = MONEY.search(plain, pos, pos + 40)
            if not nxt:
                break
            nv = _money(nxt.group(1))
            pos = nxt.end()
            if lo <= nv <= hi:
                vals.append(nv)
        vals = sorted(set(vals))
        kind = "OTE " if re.search(r"on[- ]target|total cash", lm.group(0), re.I) else ""
        return kind + _fmt(vals)
    return ""


UA = {"User-Agent": "Mozilla/5.0 (job-pipeline comp-backfill)"}
ASHBY_JOB = re.compile(r"jobs\.ashbyhq\.com/([^/]+)/([0-9a-f-]{36})", re.I)
GH_JID = re.compile(r"[?&]gh_jid=(\d+)")
GH_BOARD = re.compile(r"greenhouse\.io/(?:v1/boards/|embed/job_board\?for=)([A-Za-z0-9_-]+)")


def gh_token(company, state=None):
    """The Greenhouse board token for a company, read off its watchlist feed URL.

    Firms like Jump and Tower serve Greenhouse jobs from their own domain
    (`tower-research.com/open-positions/?gh_jid=…`), and that page is a shell —
    fetching it finds no pay at all. The board API has the posting, but only if
    you know the token, and the watchlist already resolved exactly that."""
    wl = os.path.join(state or state_dir(), "watchlist.csv")
    if not company or not os.path.exists(wl):
        return ""
    want = re.sub(r"[^a-z0-9]", "", company.lower())
    with open(wl, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if re.sub(r"[^a-z0-9]", "", (row.get("Company") or "").lower()) == want:
                m = GH_BOARD.search(row.get("Feed URL") or "")
                return m.group(1) if m else ""
    return ""


def comp_from_url(url, company="", timeout=25):
    """(comp, error) for one posting. Two hosted-board quirks are handled here
    because a plain GET silently returns a page with no pay on it: Ashby job
    pages are client-rendered shells (the trap documented for newgrad-check), and
    Greenhouse-embedded career sites carry the posting only behind `gh_jid`."""
    import urllib.request
    m = ASHBY_JOB.search(url or "")
    jid = GH_JID.search(url or "")
    try:
        if not m and jid and "greenhouse.io" not in (url or ""):
            tok = gh_token(company)
            if tok:
                api = f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs/{jid.group(1)}"
                req = urllib.request.Request(api, headers=UA)
                job = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
                return parse_comp(job.get("content") or ""), ""
        if m:
            org, uuid = m.group(1), m.group(2).lower()
            api = f"https://api.ashbyhq.com/posting-api/job-board/{org}?includeCompensation=true"
            req = urllib.request.Request(api, headers=UA)
            board = json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace"))
            for j in board.get("jobs", []):
                if str(j.get("id", "")).lower() == uuid:
                    return ashby_comp(j) or parse_comp(j.get("descriptionHtml") or j.get("descriptionPlain") or ""), ""
            return "", "not on live board"
        req = urllib.request.Request(url, headers=UA)
        return parse_comp(urllib.request.urlopen(req, timeout=timeout).read().decode("utf-8", "replace")), ""
    except Exception as e:                                   # network, TLS, 404, JSON
        return "", f"{type(e).__name__}: {str(e)[:60]}"


def tidy_comp(s):
    """One shape for the column whatever produced it: en/em dashes to hyphens,
    no stray spaces around the range, and Ashby's trailing "• Offers Equity"
    chatter dropped so "$200K - $400K • Offers Equity" and a parsed
    "$200K-$400K" are the same cell. Duplicated in fetch_sources.py."""
    s = re.sub(r"[\u2013\u2014]", "-", (s or "").split("\u2022")[0])
    return re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", s)).strip()


def ashby_comp(job):
    """Ashby's structured pay fields, preferred over its prose. Duplicated in
    fetch_sources.py (which reads the same feed during the scan) — change both."""
    c = job.get("compensation") or {}
    return tidy_comp(c.get("scrapeableCompensationSalarySummary")
                     or c.get("compensationTierSummary") or "")


def lane_tab(company, role, explicit=None):
    """Which tab a posting belongs in. Company decides it when known — the user's
    lane tabs are company-taste tabs — and role wording only breaks ties for
    companies not on either list. A `lane` field from triage wins outright."""
    tabs = cfg("routing", "tabs", DEFAULTS["tabs"])
    if explicit in tabs:
        return tabs[explicit]
    c = re.sub(r"[^a-z0-9 ]", "", (company or "").lower()).strip()
    for lane, field in (("quant", "quant_companies"), ("mlinfra", "mlinfra_companies")):
        for name in cfg("routing", field, DEFAULTS[field]):
            if c == name or c.startswith(name + " ") or name in c.split(" | "):
                return tabs[lane]
    r = role or ""
    if re.search(cfg("routing", "quant_role", DEFAULTS["quant_role"]), r, re.I):
        return tabs["quant"]
    if re.search(cfg("routing", "mlinfra_role", DEFAULTS["mlinfra_role"]), r, re.I):
        return tabs["mlinfra"]
    return tabs["swe"]


def instant(received):
    """`received` as a lexicographically sortable UTC instant.

    Ordering decides which mail event wins and which email a row cites, so the
    precision matters: a date-only stamp cannot separate an assessment invite
    from its submission receipt hours later, which is how a stray reminder once
    became a row's evidence. Full RFC 3339 UTC sorts correctly as a plain
    string. A bare date is padded to midnight so older data still orders, and
    any offset is converted so mixed forms stay comparable."""
    s = str(received or "").strip()
    if not s:
        return ""
    if len(s) == 10:                      # legacy date-only event
        return s + "T00:00:00Z"
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s                          # unparseable: compare as given
    if d.tzinfo is not None:
        d = d.astimezone(timezone.utc).replace(tzinfo=None)
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def cmd_split(rd, size):
    cands = jload(os.path.join(rd, "candidates.jsonl"))
    batches = [cands[i:i + size] for i in range(0, len(cands), size)] or [[]]
    for i, b in enumerate(batches, 1):
        with open(os.path.join(rd, f"candidates.b{i}.jsonl"), "w", encoding="utf-8") as f:
            for j in b: f.write(json.dumps(j, ensure_ascii=False) + "\n")
    print(json.dumps({"batches": len(batches), "sizes": [len(b) for b in batches],
                      "files": [f"candidates.b{i}.jsonl" for i in range(1, len(batches) + 1)]}))


def load_cands_triage(rd):
    cands = {key(c.get("url"), c.get("company", ""), c.get("role", ""), c.get("location", "")): c
             for c in jload(os.path.join(rd, "candidates.jsonl"))}
    triage = {}
    for p in sorted(glob.glob(os.path.join(rd, "triage.b*.jsonl"))) + [os.path.join(rd, "triage.jsonl")]:
        for t in jload(p):
            triage[key(t.get("url"))] = t
    return cands, triage


def needs_verify(c):
    """Which candidates deserve a posting fetch. Curated lists are pre-curated
    new-grad, so verifying them buys nothing; the risk concentrates in watchlist
    rows kept on the relaxed path (tagged in notes), the wide-net scrapes, and
    mail-alert rows whose TITLE lacks an explicit new-grad signal — a starred
    "GPU Kernel Engineer" from an alert email is exactly the unverified noise
    this step exists to stop. (Alert links are often Gmail permalinks the worker
    cannot fetch; that resolves to unclear -> flagged + unstarred, intended.)"""
    src = c.get("source", "")
    if ("no explicit new-grad signal" in (c.get("notes") or "")
            or src.startswith("jobspy:") or src.startswith("hn:")):
        return True
    if src == "mail-alert":
        ng = re.search(cfg("sources", "newgrad_title",
                           r"new grad|university|early career|entry[- ]level|college grad"
                           r"|campus|graduate|engineer i\b|associate software|202[67]"),
                       c.get("role", ""), re.I)
        return not ng
    return False


def cmd_verify_queue(rd):
    """candidates + triage -> verify_queue.bK.jsonl for the newgrad-check workers.
    Only rows that would otherwise be written (fit >= FIT_MIN) are worth a fetch;
    stars first, then fit, so a cap cuts the cheapest-to-lose rows. The cap is
    printed — a silently truncated queue would read as "all verified"."""
    cands, triage = load_cands_triage(rd)
    picked = []
    for k, c in cands.items():
        t = triage.get(k)
        if not t or int(t.get("fit", 0)) < FIT_MIN or not c.get("url"):
            continue
        if needs_verify(c):
            picked.append((bool(t.get("star")), int(t.get("fit", 0)), c, t))
    picked.sort(key=lambda x: (-x[0], -x[1]))
    cap = cfg("verify", "max_urls", DEFAULTS["verify_max_urls"])
    over_cap = max(0, len(picked) - cap)
    picked = picked[:cap]
    bs = cfg("verify", "batch_size", DEFAULTS["verify_batch_size"])
    batches = [picked[i:i + bs] for i in range(0, len(picked), bs)]
    for i, b in enumerate(batches, 1):
        with open(os.path.join(rd, f"verify_queue.b{i}.jsonl"), "w", encoding="utf-8") as f:
            for star, fit, c, t in b:
                f.write(json.dumps({"url": c["url"], "company": c["company"], "role": c["role"],
                                    "location": c.get("location", ""),
                                    "notes": c.get("notes", ""), "fit": fit, "star": star},
                                   ensure_ascii=False) + "\n")
    print(json.dumps({"queued": len(picked), "batches": len(batches),
                      "files": [f"verify_queue.b{i}.jsonl" for i in range(1, len(batches) + 1)],
                      "over_cap_unverified": over_cap}))


def header_ops(ops):
    """Lead a write batch with `ensure_tab` for every column it writes.

    The deployed Apps Script honors `addMissingCols` on append only, so an
    update naming a column the sheet lacks reports `updated: N` and writes
    nothing — silent loss, visible only in `ignoredCols`. sheets_writer.gs no
    longer has that hole, but a deployment is redeployed by hand and this repo
    cannot assume anyone has, so the batch carries its own guarantee: ensureTab
    adds only headers that are missing and is a no-op otherwise, at a cost of
    one op per tab. It runs before the missing-tab guard in doPost, so a tab
    named here that does not exist is CREATED — which is why the headers come
    from ops the pipeline itself built, never from free text."""
    cols, out = {}, []
    for o in ops:
        cols.setdefault(o["tab"], []).extend(
            k for k in (o.get("values") or {}) if k not in cols.get(o["tab"], []))
    for tab, names in cols.items():
        out.append({"op": "ensure_tab", "tab": tab, "headers": names})
    return out


# Rows the sheet considers a live process. A mail event about a company almost
# always concerns its open row, not a year-old rejected one.
OPEN_STATUSES = {"applied", "oa", "oa received", "oa submitted",
                 "interview", "interview scheduled"}


def load_sheet_index(rd):
    """Every lane-tab row as {tab, company, role, link, status}.

    `open_oas.csv` holds only OPEN rows, which is why a company already on the
    sheet at Status=Reject read as "not on the sheet" and merge emitted a
    duplicate append for it (a trading firm's rejected row, 2026-09-20). The
    orchestrator derives this fuller index from the same single sheet pull;
    when it is absent we fall back to open_oas.csv and behave as before.
    """
    rows = []
    for name in ("sheet_index.csv", "open_oas.csv"):
        path = os.path.join(rd, name)
        if not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                g = {re.sub(r"[^a-z0-9]", "", (k or "").lower()): (v or "") for k, v in r.items()}
                rows.append({"tab": g.get("tab", "").strip(),
                             "company": g.get("company", "").strip(),
                             "role": g.get("role", "").strip(),
                             "link": g.get("link", "").strip(),
                             "status": g.get("status", "").strip()})
        break
    return [r for r in rows if r["company"]]


def pick_target_row(rows, role, url, prefer_tab=None):
    """Which sheet row a mail event means, and whether the choice was a guess.

    The writer matches bottom-up and takes the newest hit, so a bare Company
    match on a company with many rows is a coin flip dressed as a write: on
    2026-09-20 one big-company name matched 22 SWE rows. Narrow by open
    status, then by role, then by link, and report what is left as ambiguous so
    the digest can say so instead of the orchestrator eyeballing every op.
    """
    if not rows:
        return None, False
    pool = [r for r in rows if r["status"].strip().lower() in OPEN_STATUSES] or list(rows)
    # A company can hold rows on more than one lane tab ("NimbusAI" on MLE and
    # "Nimbus AI" on SWE). Without this the winner is whichever tab the index
    # file happened to list last — file order deciding a write. Lane routing is
    # the same rule that placed the row, so prefer that tab; the bottom-most
    # tie-break then stays WITHIN one tab, matching how the writer itself picks.
    if prefer_tab:
        same_tab = [r for r in pool if r["tab"] == prefer_tab]
        if same_tab:
            pool = same_tab
    if url:
        exact = [r for r in pool if r["link"] and key(r["link"]) == key(url)]
        if exact:
            return exact[-1], False
    if role:
        want = re.sub(r"[^a-z0-9]+", " ", role.lower()).strip()
        hit = [r for r in pool
               if r["role"] and re.sub(r"[^a-z0-9]+", " ", r["role"].lower()).strip() == want]
        if len(hit) == 1:
            return hit[0], False
        if hit:
            pool = hit
    return pool[-1], len(pool) > 1


def match_for(row, fallback_company):
    """The narrowest match key that identifies `row` — Link beats Role beats Company."""
    if row and row.get("link"):
        return {"col": "Link", "value": row["link"]}
    if row and row.get("role"):
        return {"col": "Role", "value": row["role"]}
    return {"col": "Company", "value": (row or {}).get("company") or fallback_company}


def cmd_merge(rd, kind, today):
    cands, triage = load_cands_triage(rd)
    mail = jload(os.path.join(rd, "mail_events.jsonl"))

    # newgrad-check verdicts: "no" never reaches the sheet, "unclear" is written
    # but flagged and never starred (a star means "apply today" — don't spend it
    # on a maybe-senior role). Queued-but-unanswered rows count as unclear: a
    # worker dying must not silently upgrade its batch to verified.
    verify = {}
    for pth in sorted(glob.glob(os.path.join(rd, "verify.b*.jsonl"))) + [os.path.join(rd, "verify.jsonl")]:
        for v in jload(pth):
            verify[key(v.get("url"))] = v
    queued = set()
    for pth in sorted(glob.glob(os.path.join(rd, "verify_queue.b*.jsonl"))):
        for q in jload(pth):
            queued.add(key(q.get("url")))

    open_companies = set()
    oap = os.path.join(rd, "open_oas.csv")
    if os.path.exists(oap):
        with open(oap, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                for k in r:
                    if re.sub(r"[^a-z0-9]", "", (k or "").lower()) == "company" and str(r[k]).strip():
                        open_companies.add(str(r[k]).strip().lower())

    sheet_rows = load_sheet_index(rd)
    by_company = {}
    for r in sheet_rows:
        by_company.setdefault(_norm_company(r["company"]), []).append(r)

    ops, stars, untriaged, appended_companies = [], [], [], set()
    verified_out, location_out = [], []
    kept = dropped = dropped_verify = dropped_location = 0
    for k, c in cands.items():
        t = triage.get(k)
        if t is None:
            untriaged.append({"company": c["company"], "role": c["role"], "url": c.get("url", "")})
            continue
        if int(t.get("fit", 0)) < FIT_MIN:
            dropped += 1; continue
        flags = list(t.get("flags", []))
        star = bool(t.get("star"))
        v = verify.get(k)
        if v is not None:
            verdict = v.get("newgrad")
            if verdict == "no":
                dropped_verify += 1
                verified_out.append({"company": c["company"], "role": c["role"],
                                     "url": c.get("url", ""), "evidence": v.get("evidence", "")})
                continue
            if verdict != "yes":
                flags.append("unverified_newgrad"); star = False
            flags += [f for f in v.get("flags", []) if f not in flags and f != "fetch_failed"]
        elif k in queued:
            flags.append("unverified_newgrad"); star = False
        # A location the policy calls a hard no is a hard no, not a -1. Triage
        # scores it down and flags it, but a fit-5 role in Singapore still lands
        # at 4 -- above fit_min -- so scoring alone never kept one out. 71 such
        # rows reached the sheet before this existed. Enforced here rather than
        # in the rubric: a hard constraint is a transform, not a judgement call.
        if "location_conflict" in flags:
            dropped_location += 1
            location_out.append({"company": c["company"], "role": c["role"],
                                 "url": c.get("url", ""), "location": c.get("location", "")})
            continue
        kept += 1
        appended_companies.add(c["company"].lower())
        # Comp: the verify worker read the posting body, so its figure beats the
        # scan's (which only Ashby feeds carry). Neither found one -> empty cell.
        comp = (v or {}).get("comp") or c.get("comp") or ""
        ops.append({"op": "append", "tab": lane_tab(c["company"], c["role"], t.get("lane")),
                    "addMissingCols": True, "values": {
            "Date Added": today, "Company": c["company"], "Role": c["role"],
            "Location": c.get("location", ""), "Link": c.get("url", ""),
            "Source": c.get("source", ""), "Fit": int(t.get("fit", 0)),
            "Comp": comp,
            "Flags": ";".join(flags),
            "Status": cfg("routing", "status_map", DEFAULTS["status_map"]).get("New", "New")}})
        if star:
            stars.append({"company": c["company"], "role": c["role"],
                          "url": c.get("url", ""), "why": t.get("why", "")})

    # One real process generates several emails — company mail + platform mail +
    # calendar invite — so raw events must be consolidated before they become
    # rows. Untouched, a live mailbox produced 16 events for ~8 processes: one
    # firm with three different OA deadlines, three identical interview rows.
    # Consolidation is deterministic, so it belongs here rather than in the
    # worker's judgment.
    STATUS_OF = {"ack": "Applied", "oa": "OA", "oa_submitted": "OA Submitted",
                 "interview": "Interview", "rejection": "Rejected"}
    RANK = {"Applied": 1, "OA": 2, "OA Submitted": 3, "Interview": 4, "Rejected": 5}
    # A submitted OA is no longer owed, so it must not keep a due date alive on
    # the digest — only an outstanding assessment or a scheduled interview can.
    DEADLINE_STATUS = {"OA", "Interview"}

    best = {}
    for ev in mail:
        kind_ev, comp = ev.get("kind"), (ev.get("company") or "").strip()
        if kind_ev == "alert_role" or not comp:
            continue   # alert roles should have been routed into candidates pre-triage
        status = STATUS_OF.get(kind_ev)
        if not status:
            continue   # recruiter replies are digest material, not row changes
        cur = best.get(comp.lower())
        if cur is None:
            cur = best[comp.lower()] = {"company": comp, "status": status,
                                        "deadline": None, "assumed": True,
                                        "role": ev.get("role") or "", "url": ev.get("url") or "",
                                        "thread_url": "", "provenance": (0, "")}
        # Never let an earlier stage overwrite a later one: an acknowledgment or
        # a stray OA notice arriving after submission must not reopen the OA.
        rank, recv = RANK.get(status, 0), instant(ev.get("received"))
        if rank > RANK.get(cur["status"], 0):
            cur["status"] = status
        cur["role"] = cur["role"] or (ev.get("role") or "")
        cur["url"] = cur["url"] or (ev.get("url") or "")
        # Provenance must explain the status the row ends up in, so rank first
        # and recency only as the tie-break — a stray reminder landing the same
        # day as the submission receipt must not become the row's evidence.
        if ev.get("thread_url") and (rank, recv) >= cur["provenance"]:
            cur["thread_url"], cur["provenance"] = ev["thread_url"], (rank, recv)
        d, assumed = ev.get("deadline"), bool(ev.get("deadline_assumed"))
        if not d:
            continue
        # A stated deadline always beats a guessed one; between two stated ones
        # take the earlier — acting early is recoverable, acting late is not.
        if cur["deadline"] is None or (cur["assumed"] and not assumed) \
                or (cur["assumed"] == assumed and d < cur["deadline"]):
            cur["deadline"], cur["assumed"] = d, assumed

    updated = 0
    smap = cfg("routing", "status_map", DEFAULTS["status_map"])
    vocab = cfg("routing", "status_vocab", DEFAULTS["status_vocab"])
    ambiguous_matches, status_unwritable = [], []

    def place_status(values, tab, company, role):
        """Drop a Status the tab's validation would refuse, and say so.

        One refused value aborts the entire batch server-side, taking every
        still-buffered append with it, so this has to happen before the write —
        and the row's other values are still correct, so they go in regardless.
        """
        allowed = vocab.get(tab)
        want = values.get("Status")
        if not allowed or not want or want in allowed:
            return values
        status_unwritable.append({"tab": tab, "company": company, "role": role,
                                  "status": want, "allowed": list(allowed)})
        return {k: v for k, v in values.items() if k != "Status"}

    for comp_l, e in best.items():
        values = {"Status": smap.get(e["status"], e["status"])}
        # Guard both branches: a rejection or an acknowledgment has no deadline,
        # and rule-3's received+7 guess must never reach an OA Deadline cell.
        if e["deadline"] and e["status"] in DEADLINE_STATUS:
            values["OA Deadline"] = e["deadline"]
        if e["thread_url"]:
            values["Last Email"] = e["thread_url"]
        rows = by_company.get(_norm_company(e["company"]), [])
        target, ambiguous = pick_target_row(rows, e["role"], e["url"],
                                            lane_tab(e["company"], e["role"]))
        # The row's own tab wins over a fresh lane guess: Chicago Trading's row
        # lives on HFT, and writing the guess instead is how a duplicate is born.
        tab = (target or {}).get("tab") or lane_tab(e["company"], e["role"])
        if target or comp_l in open_companies or comp_l in appended_companies:
            m = match_for(target, e["company"])
            # A Company match on a company with several rows is only as good as
            # the writer's bottom-most-wins rule — right here today, a coin flip
            # tomorrow. Narrowing to one row does not make the *write* precise
            # if that row carries neither a Link nor a Role to aim at, so this
            # reports on the match key, not on how many candidates survived.
            if ambiguous or (m["col"] == "Company" and len(rows) > 1):
                ambiguous_matches.append({"tab": tab, "company": e["company"],
                                          "role": e["role"], "candidates": len(rows),
                                          "matched_on": m["col"],
                                          "chose": (target or {}).get("role") or "(blank role)"})
            ops.append({"op": "update", "tab": tab, "addMissingCols": True,
                        "match": m,
                        "values": place_status(values, tab, e["company"], e["role"])})
        else:
            ops.append({"op": "append", "tab": tab, "addMissingCols": True,
                        "values": place_status({
                            "Date Added": today, "Company": e["company"], "Role": e["role"],
                            "Link": e["url"], "Source": "mail", "Status": values["Status"],
                            "OA Deadline": values.get("OA Deadline", ""),
                            "Last Email": e["thread_url"],
                            "Flags": "unmatched-update"}, tab, e["company"], e["role"])})
        updated += 1

    with open(os.path.join(rd, "sheet_ops.jsonl"), "w", encoding="utf-8") as f:
        for o in header_ops(ops) + ops: f.write(json.dumps(o, ensure_ascii=False) + "\n")

    # Seen ledger: every key triage actually judged this run — kept AND dropped.
    # Sheet-based dedupe alone resurrects every rejected posting daily (~19% of
    # the pool, forever); `persist` appends this delta to the repo's
    # state/seen.txt, which fetch_sources.py unions into its dedupe set.
    with open(os.path.join(rd, "seen_delta.txt"), "w", encoding="utf-8") as f:
        for k in cands:
            if k in triage: f.write(k + "\n")

    scan = {}
    sp = os.path.join(rd, "scan_report.json")
    if os.path.exists(sp):
        try: scan = json.load(open(sp, encoding="utf-8"))
        except Exception: pass
    summary = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "date": today, "kind": kind,
               "scanned": (scan.get("counts") or {}).get("fetched"),
               "kept": kept, "dropped_by_triage": dropped,
               "dropped_by_verify": dropped_verify, "verified_out": verified_out,
               "dropped_by_location": dropped_location, "location_out": location_out,
               "updated": updated,
               "ambiguous_matches": ambiguous_matches,
               "status_unwritable": status_unwritable,
               "stars": stars, "untriaged": untriaged,
               "errors": [f'{x["name"]}: {x["error"]}' for x in scan.get("failed", [])],
               "manual_sources": scan.get("manual", []), "digest": ""}
    with open(os.path.join(rd, "run_summary.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1, ensure_ascii=False)
    print(json.dumps({"ops": len(ops), "triage_appends": kept, "mail_events_applied": updated,
                      "stars": len(stars), "untriaged": len(untriaged),
                      "dropped_by_verify": dropped_verify,
                      "ambiguous_matches": len(ambiguous_matches),
                      "status_unwritable": len(status_unwritable),
                      "source_errors": len(summary["errors"])}))


def sheet_backfill_targets(overwrite=False):
    """Open lane-tab rows that still need a Comp, straight from the sheet.

    Reuses retriage's `read_sheet_rows` rather than making the caller hand-build
    a links file. Rows that already carry a Comp are skipped unless `overwrite`,
    which is what makes a second pass cheap: the first run's ~700 fetches are not
    repeated to re-derive figures the sheet already has."""
    new_status = cfg("routing", "status_map", DEFAULTS["status_map"]).get("New", "New")
    out = []
    for r in read_sheet_rows():
        link = (r.get("Link") or "").strip()
        if not link.startswith("http"):
            continue
        if (r.get("Status") or "").strip() not in (new_status, ""):
            continue                                  # a closed process needs no pay figure
        if (r.get("Comp") or "").strip() and not overwrite:
            continue
        out.append({"tab": r["_tab"], "company": (r.get("Company") or "").strip(), "link": link})
    seen, uniq = set(), []
    for r in out:                                     # one fetch per posting, not per row
        if r["link"] in seen:
            continue
        seen.add(r["link"]); uniq.append(r)
    return uniq


def file_backfill_targets(links_file, tab_default):
    """Explicit rows to fill: one per line, a bare URL or {tab, company, link}."""
    rows = []
    with open(links_file, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("{"):
                o = json.loads(line)
                url = (o.get("link") or o.get("url") or "").strip()
                tab, co = o.get("tab") or tab_default, o.get("company", "")
            else:
                url, tab, co = line, tab_default, ""
            if url.startswith("http") and tab:
                rows.append((tab, url, co))
    return rows


def cmd_backfill_comp(links_file, out_path, tab_default, limit, sleep_s=0.4, overwrite=False):
    """Fill Comp on rows that predate the column.

    Nothing else can: `seen.txt` makes every one of these URLs permanently
    deduped, so no future scan re-surfaces them, and triage never fetches. An
    apply sprint reaches only the handful of finalists it deep-checks, and
    retriage re-scores rows without fetching them. So the backlog needs one
    deliberate pass — and only one, since new rows carry Comp from append time."""
    import time
    rows = (file_backfill_targets(links_file, tab_default) if links_file
            else [(r["tab"], r["link"], r["company"]) for r in sheet_backfill_targets(overwrite)])
    if limit:
        rows = rows[:limit]
    found = failed = 0
    pending = []
    with open(out_path, "w", encoding="utf-8") as out:
        for tab, url, co in rows:
            comp, err = comp_from_url(url, co)
            if err:
                failed += 1
            if comp:
                found += 1
                pending.append({"op": "update", "tab": tab,
                                "match": {"col": "Link", "value": url},
                                "addMissingCols": True, "values": {"Comp": comp}})
            time.sleep(sleep_s)                     # one board, many rows: don't hammer
        for o in header_ops(pending) + pending:
            out.write(json.dumps(o, ensure_ascii=False) + "\n")
    # `checked - found - failed` is the honest third bucket: the page fetched
    # fine and states no pay. Reporting those as failures would invite a re-run
    # that can only produce the same nothing.
    print(json.dumps({"checked": len(rows), "found": found, "fetch_failed": failed,
                      "no_comp_stated": len(rows) - found - failed, "ops": out_path}))


def _hit_companies(rd):
    """Watchlist companies whose feed produced relevant postings this run.

    Two inputs, because the scan has two halves. `scan_report.json`'s `hits`
    covers every typed feed the script fetched itself (`ats:<Company>`), counted
    after the prefilters and before dedupe. `candidates.jsonl` covers the
    careers-page rows the source-scan agent walks by hand, which reach the pool
    as `careers:<Company>` and never pass through the script's counter.

    Only those two prefixes count. A posting from speedyapply, jobspy, HN or a
    mail alert says nothing about whether a watchlist feed is still alive, and
    stamping Last Hit from one would keep a dead feed looking healthy forever.
    """
    out = set()
    def take(src):
        s = str(src or "")
        for pre in ("ats:", "careers:"):
            if s.startswith(pre):
                name = s[len(pre):].strip()
                if name:
                    out.add(name)
    sp = os.path.join(rd, "scan_report.json")
    if os.path.exists(sp):
        try:
            hits = (json.load(open(sp, encoding="utf-8")) or {}).get("hits") or {}
            for src, n in hits.items():
                if n:
                    take(src)
        except Exception:
            pass
    for c in jload(os.path.join(rd, "candidates.jsonl")):
        take(c.get("source"))
    return out


def _norm_company(s):
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def stamp_last_hit(rd, sd, day):
    """Stamp `Last Hit` = <day> on every watchlist row whose feed hit this run.

    Last Hit is the only input to the weekly review's prune rule, and for most
    of this pipeline's life nothing wrote it: the column existed, the rule read
    it, and it was blank on all 108 rows, so the rule silently proposed nothing
    and read as a working safeguard. This is that fix.

    Never regresses and never clears: a row that did not hit this run keeps
    whatever date it had, and a run whose scan half failed entirely leaves the
    whole column alone rather than aging every feed at once. Rewrites the CSV
    in place, preserving the header spelling, the column order, and any extra
    columns the user has added by hand.
    """
    wp = os.path.join(sd, "watchlist.csv")
    if not os.path.exists(wp):
        return 0
    hit = {_norm_company(c) for c in _hit_companies(rd)}
    if not hit:
        return 0
    with open(wp, newline="", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        cols, rows = rdr.fieldnames or [], list(rdr)
    col = next((c for c in cols if _norm_company(c) == "lasthit"), None)
    ccol = next((c for c in cols if _norm_company(c) == "company"), None)
    if not col or not ccol:
        return 0
    n = 0
    for r in rows:
        if _norm_company(r.get(ccol)) in hit and str(r.get(col) or "").strip() < day:
            r[col] = day
            n += 1
    if n:
        with open(wp, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
    return n


def cmd_persist(rd, sd):
    """run/ -> repo state. The only step that writes outside the run dir.
    1. seen_delta.txt (every key triage judged this run) is unioned into
       <state>/seen.txt — append-only, order kept, duplicates skipped — so a
       reject stays rejected on every later run.
    2. watchlist.csv's Last Hit is stamped for every feed that produced relevant
       postings this run (stamp_last_hit) — the weekly prune rule's only input.
    3. run_summary.json is copied to <state>/runs/run-<date>-<kind>.json. A
       same-day rerun of the same kind never overwrites history: if that name
       holds a different run (different ts) the new file carries its UTC time
       instead, run-<date>-<kind>-<HHMM>Z.json; the same ts (persist re-run
       after fixing the digest) overwrites itself.
    Idempotent. The orchestrator commits + pushes state/ right after this.
    Sharp edge: run from inside the checkout this finds the REAL state/ by
    itself — any test or dry run must pass --state-dir (setup.sh does)."""
    if not sd:
        sys.exit("persist: no state dir found — pass --state-dir or set $JOB_PIPELINE_STATE")
    os.makedirs(os.path.join(sd, "runs"), exist_ok=True)
    ledger = os.path.join(sd, "seen.txt")
    text = open(ledger, encoding="utf-8").read() if os.path.exists(ledger) else ""
    have = {l.strip() for l in text.splitlines() if l.strip()}
    added = []
    dp = os.path.join(rd, "seen_delta.txt")
    if os.path.exists(dp):
        for line in open(dp, encoding="utf-8"):
            k = line.strip()
            if k and k not in have:
                have.add(k); added.append(k)
    if added:
        with open(ledger, "a", encoding="utf-8") as f:
            if text and not text.endswith("\n"):
                f.write("\n")
            f.write("".join(k + "\n" for k in added))

    run_file = digest_empty = None
    day = date.today().isoformat()
    sp = os.path.join(rd, "run_summary.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            summary = json.load(f)
        ts = str(summary.get("ts") or "")
        day = summary.get("date") or ts[:10] or day
        kind = re.sub(r"[^a-z0-9]+", "-", str(summary.get("kind") or "run").lower()).strip("-") or "run"
        dst = os.path.join(sd, "runs", f"run-{day}-{kind}.json")
        if os.path.exists(dst):
            try:
                prev_ts = json.load(open(dst, encoding="utf-8")).get("ts")
            except Exception:
                prev_ts = None
            if prev_ts != ts:
                m = re.search(r"T(\d\d):(\d\d)", ts)
                stamp = (m.group(1) + m.group(2)) if m else "rerun"
                dst = os.path.join(sd, "runs", f"run-{day}-{kind}-{stamp}Z.json")
        shutil.copyfile(sp, dst)
        run_file, digest_empty = os.path.abspath(dst), not summary.get("digest")
    stamped = stamp_last_hit(rd, sd, day)
    print(json.dumps({"seen_added": len(added), "seen_total": len(have),
                      "last_hit_stamped": stamped,
                      "run_file": run_file, "digest_empty": digest_empty}))


# Statuses that mean "still a live prospect" -- the rows a re-score should touch.
# Anything past Applied is history: re-scoring it would churn the sheet and could
# overwrite a status the mail sweep set.
OPEN_STATUS = {"Not Applied", "Unreleased", ""}


def read_sheet_rows(tabs=None):
    """Read the lane tabs through the sheets_writer `read` op.

    pipeline_ops has never talked to the sheet -- the orchestrator does that and
    hands it files. retriage is the exception: its whole input IS the sheet, and
    routing that through the orchestrator would mean pasting 700 rows through a
    context window for no reason.
    """
    import urllib.request, time
    url = cfg("sheets_writer", "url", None)
    secret = cfg("sheets_writer", "secret", None)
    if not url or not secret:
        raise SystemExit("retriage needs sheets_writer.url + .secret in config")
    tabs = tabs or cfg("routing", "tabs", ["HFT", "SWE", "MLE"])
    out = []
    for tab in tabs:
        body = json.dumps({"secret": secret, "ops": [{"op": "read", "tab": tab, "limit": 2000}]}).encode()
        for attempt in range(4):
            try:
                req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"})
                d = json.loads(urllib.request.urlopen(req).read().decode())
                if "results" not in d:
                    raise RuntimeError(d.get("error", "no results"))
                r0 = d["results"][0]
                hdrs = r0.get("headers", [])
                idx = {h: i for i, h in enumerate(hdrs) if h}
                for row in r0.get("rows", []):
                    rec = {h: (str(row[i]) if i < len(row) else "") for h, i in idx.items()}
                    rec["_tab"] = tab
                    out.append(rec)
                break
            except Exception:
                if attempt == 3:
                    raise
                time.sleep(3)
    return out


def cmd_retriage(run_dir, size, tabs):
    """Sheet's open rows -> candidate batches, so a policy change reaches the
    BACKLOG and not only new candidates.

    Fit is written once, at ingest, by whichever triage worker ran that day, and
    never recomputed. That is fine until policy.md changes -- at which point every
    existing row carries a score from a rubric that no longer exists. (Concretely:
    a tier rewrite left 712 open rows scored under the old tiers, 243 of them tied
    at fit 5, which is not a ranking.) This re-emits those rows as candidates so
    triage can score them again; `merge` writes the result back as updates matched
    on Link.

    --size defaults to config triage.batch_size, which is tuned for the daily pool
    where parallelism buys wall-clock. A backlog pass is one-off and not
    latency-bound, so pass something larger. The binding limit is a worker's
    OUTPUT, not its input: a row costs ~280 bytes to state and ~60 tokens to
    answer, so 700 rows is ~49k tokens in but ~43k out -- which is where a single
    worker starts truncating. ~150 per batch is comfortable.
    """
    rows = read_sheet_rows(tabs)
    open_rows = [r for r in rows
                 if (r.get("Status") or "").strip() in OPEN_STATUS
                 and r.get("Company") and r.get("Role")]
    skipped_nolink = sum(1 for r in open_rows if not (r.get("Link") or "").strip())
    cands = [{"company": r["Company"], "role": r["Role"],
              "location": r.get("Location", ""), "url": (r.get("Link") or "").strip(),
              "source": r.get("Source", ""), "age_days": None,
              "notes": "retriage: existing sheet row, re-scored under current policy"}
             for r in open_rows if (r.get("Link") or "").strip()]
    os.makedirs(run_dir, exist_ok=True)
    with open(os.path.join(run_dir, "candidates.jsonl"), "w", encoding="utf-8") as f:
        for c in cands:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    cmd_split(run_dir, size)
    print(json.dumps({"sheet_rows": len(rows), "open_rows": len(open_rows),
                      "queued": len(cands), "skipped_no_link": skipped_nolink,
                      "batch_size": size}), file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split"); s.add_argument("--run-dir", default="run"); s.add_argument("--size", type=int, default=None); s.add_argument("--config", default=None)
    v = sub.add_parser("verify-queue"); v.add_argument("--run-dir", default="run"); v.add_argument("--config", default=None)
    m = sub.add_parser("merge"); m.add_argument("--run-dir", default="run"); m.add_argument("--kind", default="morning"); m.add_argument("--today", default=date.today().isoformat()); m.add_argument("--config", default=None)
    b = sub.add_parser("backfill-comp"); b.add_argument("--links", default=None, help="rows to fill; omit to read the open lane-tab rows straight from the sheet"); b.add_argument("--overwrite", action="store_true", help="refetch rows that already have a Comp"); b.add_argument("--out", default="run/comp_ops.jsonl"); b.add_argument("--tab", default=None); b.add_argument("--limit", type=int, default=None); b.add_argument("--config", default=None)
    rt = sub.add_parser("retriage"); rt.add_argument("--run-dir", default="run"); rt.add_argument("--size", type=int, default=None); rt.add_argument("--tabs", default=None); rt.add_argument("--config", default=None)
    p = sub.add_parser("persist"); p.add_argument("--run-dir", default="run"); p.add_argument("--state-dir", default=None, metavar="DIR", help="default $JOB_PIPELINE_STATE, then <repo>/state, then ./state"); p.add_argument("--config", default=None)
    a = ap.parse_args()
    used = configure(a.config)
    print(f"config: {used or 'built-in defaults'} (fit_min={FIT_MIN})", file=sys.stderr)
    if a.cmd == "retriage":
        cmd_retriage(a.run_dir, a.size or cfg("triage", "batch_size", DEFAULTS["batch_size"]),
                     [t.strip() for t in a.tabs.split(",")] if a.tabs else None)
    elif a.cmd == "split":
        cmd_split(a.run_dir, a.size or cfg("triage", "batch_size", DEFAULTS["batch_size"]))
    elif a.cmd == "verify-queue":
        cmd_verify_queue(a.run_dir)
    elif a.cmd == "backfill-comp":
        cmd_backfill_comp(a.links, a.out, a.tab, a.limit, overwrite=a.overwrite)
    elif a.cmd == "persist":
        cmd_persist(a.run_dir, state_dir(a.state_dir))
    else:
        cmd_merge(a.run_dir, a.kind, a.today)


if __name__ == "__main__":
    main()
