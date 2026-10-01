#!/usr/bin/env python3
"""fetch_sources.py — the deterministic layer of the source-scan worker.

Fetches curated GitHub lists + ATS JSON feeds, normalizes to candidates.jsonl,
prefilters mechanically, dedupes against known.txt, and writes a per-source
status report so the agent spends judgment ONLY on what failed or needs it
(careers pages, drifted formats). Stdlib only — runs anywhere with python3
and network. Partial failure is normal: exit 0, failures live in the report.

Usage:
  python3 fetch_sources.py --run-dir run                 # full scan
  python3 fetch_sources.py --probe "Five Rings"          # find a feed URL
  python3 fetch_sources.py --run-dir run --posted-after 2026-07-01
Inputs (cross-run state is committed in the repo — see --state-dir):
  state/watchlist.csv    typed feed list                (--watchlist overrides)
  state/seen.txt         every key triage has ever judged: the dedupe ledger
  run/known.txt          keys already on the sheet, built by the orchestrator
Outputs:
  run/candidates.jsonl   (schema per job-pipeline contracts.md)
  run/scan_report.json   {"ok":[...],"failed":[{name,url,error}],
                          "manual":[{company,url,type}],"counts":{...},
                          "config":{source,...},"inputs":{what was read}}
"""
import argparse, csv, json, os, re, sys, time, urllib.error, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

# --- config ---------------------------------------------------------------
# Every tunable — dates, thresholds, title regexes — lives in config, never in
# code or in a skill's prose. The durable copy is IN THE REPO:
# skills/job-pipeline/assets/config.seed.json (private repo; it also carries
# the sheets_writer credentials). The cloud routine clones the repo, so a run
# needs no machine-local file and nothing is dumped into the run dir first.
#
# Resolution order (first that exists wins):
#   1. --config PATH
#   2. $JOB_PIPELINE_CONFIG
#   3. <skills>/job-pipeline/assets/config.seed.json   <- the repo copy; the
#      same path works from a ~/.claude/skills install, since setup.sh copies
#      assets/ along with the skill
#   4. <skills>/job-pipeline/assets/config.example.json  (credential-free twin)
#   5. the DEFAULTS below
# Deliberately gone: <run-dir>/config.json and ~/.claude/job-pipeline/config.json.
# A stale shadow copy silently overriding the repo was the failure mode.
# Missing keys fall back individually, so a partial file is valid.
# This loader is duplicated in job-pipeline's pipeline_ops.py — keep both in sync.
HERE = os.path.dirname(os.path.abspath(__file__))
SKILLS_ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))        # .../skills
REPO_ROOT = os.path.dirname(SKILLS_ROOT)
CONFIG_SEED = os.path.join(SKILLS_ROOT, "job-pipeline", "assets", "config.seed.json")
CONFIG_EXAMPLE = os.path.join(SKILLS_ROOT, "job-pipeline", "assets", "config.example.json")
DEFAULTS = {
    "exclude_title": r"senior|staff|principal|\bsr\.?\b|manager|director|vp\b|head of"
                     r"|intern(ship)?\b|co[- ]?op\b|phd"
                     # The previous cohort's roles are stale when targeting 2027 starts, and
                     # hardware/silicon roles are out of scope. Config (set by onboarding)
                     # overrides this for other class years.
                     r"|\b2026\b|vlsi|\basic\b|\brtl\b|fpga|analog design|mixed[- ]signal"
                     r"|physical design|circuit design|silicon engineer",
    "newgrad_title": r"new grad|university|early career|entry[- ]level|college grad|campus"
                     r"|graduate|engineer i\b|associate software|2027",
    "seniority_markers": r"\b(ii|iii|iv|v)\b|\blead\b|architect|chief|distinguished|fellow"
                         r"|expert|\b(experienced|advanced)\b|\b[5-9]\+?\s*years",
    "eng_role": r"software engineer|swe\b|developer|programmer|engineer,|engineer -"
                r"|engineer –|quantitative|research engineer|systems engineer"
                r"|infrastructure engineer|platform engineer|ml engineer"
                r"|machine learning engineer|performance engineer",
    "posted_after": "2026-07-01",
    "ats_strict": False,
}
CONFIG = {}


def cfg(section, key, default):
    return (CONFIG.get(section) or {}).get(key, default)


def config_path(explicit=None):
    for p in (explicit, os.environ.get("JOB_PIPELINE_CONFIG"), CONFIG_SEED, CONFIG_EXAMPLE):
        if p and os.path.exists(p):
            return p
    return None


def configure(explicit=None):
    """Load config and rebind the compiled patterns. Called from main(), not at
    import, so --config and the environment are honored."""
    global CONFIG, EXCLUDE, TITLE, LEVELED, ENG_ROLE, POSTED_AFTER, FIXED
    path = config_path(explicit)
    if path:
        try:
            with open(path, encoding="utf-8") as f:
                CONFIG = json.load(f)
        except (OSError, json.JSONDecodeError):
            CONFIG = {}
    EXCLUDE = re.compile(cfg("sources", "exclude_title", DEFAULTS["exclude_title"]), re.I)
    TITLE = re.compile(cfg("sources", "newgrad_title", DEFAULTS["newgrad_title"]), re.I)
    LEVELED = re.compile(cfg("sources", "seniority_markers", DEFAULTS["seniority_markers"]), re.I)
    ENG_ROLE = re.compile(cfg("sources", "eng_role", DEFAULTS["eng_role"]), re.I)
    POSTED_AFTER = cfg("sources", "posted_after", DEFAULTS["posted_after"])
    # The curated lists are per class year (speedyapply publishes one repo per
    # cohort), so a user targeting another year swaps them in config.
    FIXED = cfg("sources", "fixed", None) or FIXED_DEFAULT
    return path


# --- repo state -----------------------------------------------------------
# What the pipeline remembers between runs is committed under state/ in the
# repo: watchlist.csv (the feeds) and seen.txt (every key triage has judged —
# what keeps a rejected posting rejected; sheet links alone resurrect it
# daily). Both are read straight from the checkout; the orchestrator no longer
# dumps copies into the run dir. Resolution: --state-dir, $JOB_PIPELINE_STATE,
# <repo>/state beside this skill tree, ./state under the cwd (a ~/.claude
# install run from inside a clone). None -> no feeds, no ledger, and the
# report says so. Duplicated in pipeline_ops.py — keep both in sync.
def state_dir(explicit=None):
    for p in (explicit, os.environ.get("JOB_PIPELINE_STATE")):
        if p:
            return os.path.abspath(p)
    for p in (os.path.join(REPO_ROOT, "state"), os.path.join(os.getcwd(), "state")):
        if os.path.isdir(p):
            return os.path.abspath(p)
    return None


def read_keys(path):
    """One dedupe key per line -> set; a missing file is an empty set."""
    if not (path and os.path.exists(path)):
        return set()
    with open(path, encoding="utf-8") as f:
        return {l.strip() for l in f if l.strip()}


FIXED_DEFAULT = [
    {"name": "speedyapply", "kind": "markdown",
     "url": "https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md"},
    {"name": "simplify", "kind": "markdown",
     "url": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md",
     "fallback": "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/main/README.md"},
]
FIXED = FIXED_DEFAULT   # config `sources.fixed` replaces it (configure())
EXCLUDE = re.compile(DEFAULTS["exclude_title"], re.I)
TITLE = re.compile(DEFAULTS["newgrad_title"], re.I)
IMG_HOSTS = ("camo.githubusercontent.com", "i.imgur.com", "img.shields.io")
TRACKER_HOSTS = ("simplify.jobs",)   # aggregator redirect: keep only if no direct link
UA = {"User-Agent": "Mozilla/5.0 (job-pipeline source-scan)"}
# Absolute cutoff, not a rolling window: drop anything posted before this date.
# Override per run with --posted-after YYYY-MM-DD.
POSTED_AFTER = DEFAULTS["posted_after"]


def too_old(age, cutoff):
    """Sources report age relatively ('3d', '2mo', an ATS timestamp), so convert
    to a posting date and compare. Unknown age is kept — never drop on a guess."""
    if age is None:
        return False
    return date.today() - timedelta(days=age) < cutoff


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


ENTITIES = {"&amp;": "&", "&#x27;": "'", "&#39;": "'", "&quot;": '"', "&nbsp;": " ",
            "&#x2F;": "/", "&lt;": "<", "&gt;": ">"}


def strip_md(cell):
    """Cell text with markdown, HTML tags, and entities removed."""
    cell = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", cell or "")
    cell = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", cell)
    cell = re.sub(r"<[^>]+>", " ", cell)          # both lists now ship HTML inside cells
    for e, c in ENTITIES.items():
        cell = cell.replace(e, c)
    cell = re.sub(r"[\u200b-\u200f\ufeff]", "", cell)   # zero-width junk breaks matching
    return re.sub(r"\s+", " ", re.sub(r"[*_`]", "", cell)).strip()


# Markdown [x](url) and HTML href="url", captured in document order. Never img
# src — the apply link is the anchor wrapping the badge, not the badge itself.
LINK_RE = re.compile(r"\]\((https?://[^)\s]+)\)|href=[\"'](https?://[^\"']+)[\"']")


def links_in(text):
    return [a or b for a, b in LINK_RE.findall(text or "")]


def pick_url(cells):
    """Apply link = last non-image URL outside the company cell, preferring a
    direct employer link over an aggregator redirect (Simplify's own /p/ page)."""
    home = set(links_in(cells[0])) if cells else set()
    urls = [u for u in links_in(" ".join(cells[1:]))
            if u not in home and not any(h in u for h in IMG_HOSTS)]
    direct = [u for u in urls if not any(h in u for h in TRACKER_HOSTS)]
    pool = direct or urls
    return pool[-1] if pool else ""


def age_days(cell):
    s = strip_md(cell or "").lower()
    m = re.fullmatch(r"(\d+)\s*d", s)
    if m: return int(m.group(1))
    m = re.fullmatch(r"(\d+)\s*mo", s)
    if m: return int(m.group(1)) * 30
    for f in ("%b %d", "%b %d %Y", "%Y-%m-%d", "%m/%d/%Y"):
        try:
            d = datetime.strptime(s.title() if "%b" in f else s, f)
            if d.year == 1900: d = d.replace(year=date.today().year)
            n = (date.today() - d.date()).days
            return n + 365 if n < -30 else max(n, 0)   # posted "Dec 30" seen in Jan
        except ValueError:
            pass
    return None


CONTINUATION = ("", "↳", "->", "|_", "⤷")


def clean_company(s):
    """Strip list decorations (speedyapply prefixes hot listings with 🔥) so one
    company is one identity — the company name keys dedupe, the sheet's update
    matching, and policy.md's company tiers. '🔥 TikTok' must equal 'TikTok'."""
    s = re.sub(r"^[^\w(]+", "", s or "")
    s = re.sub(r"[^\w).\]'’]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S | re.I)


def row_to_job(cells, source_name, last_company):
    """Shared row shape for both lists: Company, Role, Location, …, Age.
    Returns (job|None, company) — company is returned even when the row yields
    no job, so a following continuation row still attributes correctly."""
    company = clean_company(strip_md(cells[0]))
    if company in CONTINUATION:
        company = last_company
    if not company:
        return None, last_company
    url = pick_url(cells)
    if not url:                      # closed postings (🔒) carry no link — drop
        return None, company
    return {"company": company,
            "role": strip_md(cells[1]) if len(cells) > 1 else "",
            "location": strip_md(cells[2]) if len(cells) > 2 else "",
            "url": url, "source": source_name,
            "age_days": age_days(cells[-1]) if len(cells) > 3 else None,
            "notes": ""}, company


def parse_markdown_table(md, source_name):
    jobs, last_company = [], ""
    for line in md.splitlines():
        s = line.strip()
        if not s.startswith("|"): continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 3: continue
        if all(re.fullmatch(r":?-{2,}:?", c or "--") for c in cells): continue
        if re.fullmatch(r"company", strip_md(cells[0]), re.I): continue
        job, last_company = row_to_job(cells, source_name, last_company)
        if job: jobs.append(job)
    return jobs


def parse_html_table(html, source_name):
    jobs, last_company = [], ""
    for row in TR_RE.findall(html):
        cells = [c.strip() for c in TD_RE.findall(row)]
        if len(cells) < 3: continue
        if re.fullmatch(r"company", strip_md(cells[0]), re.I): continue
        job, last_company = row_to_job(cells, source_name, last_company)
        if job: jobs.append(job)
    return jobs


def parse_listing(text, source_name):
    """Upstream lists drift between markdown pipe tables and raw HTML tables
    (speedyapply: pipe rows with HTML anchors; Simplify: full <table>). Run both
    parsers and keep everything — the dedupe pass collapses any overlap."""
    return parse_markdown_table(text, source_name) + parse_html_table(text, source_name)


# A watchlist row is itself the curation — the user hand-picked that company, so
# a title prefilter tuned for open-web noise is the wrong gate there. ML-infra
# startups never write "new grad": Baseten's whole board (65 postings, 28 junior
# eng roles) scored zero under TITLE-only, which made the exception lane in the
# policy dead weight. So for watchlist feeds keep engineering roles that carry no
# seniority marker, tag the ones without an explicit new-grad signal, and let
# triage judge. --ats-strict restores TITLE-only.
LEVELED = re.compile(DEFAULTS["seniority_markers"], re.I)
ENG_ROLE = re.compile(DEFAULTS["eng_role"], re.I)


def stamp_age(value):
    """Posting age in days from an ATS timestamp: ISO-8601 string (greenhouse,
    ashby) or epoch milliseconds (lever). None when absent or unparseable."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            d = datetime.fromtimestamp(value / 1000).date()
        else:
            d = datetime.fromisoformat(str(value).replace("Z", "+00:00")).date()
    except (ValueError, OverflowError, OSError):
        return None
    return max((date.today() - d).days, 0)


def ashby_comp(job):
    """Ashby's structured pay fields. The board API returns them for the whole
    feed behind one query param, so comp costs the scan nothing here — unlike
    Greenhouse, which buries pay in `?content=true` HTML and would multiply the
    daily fetch by the size of every board. Greenhouse rows get their Comp from
    the verify step, which fetches those postings anyway. Duplicated in
    pipeline_ops.py (same fields, used for backfill) — change both."""
    c = job.get("compensation") or {}
    raw = (c.get("scrapeableCompensationSalarySummary")
           or c.get("compensationTierSummary") or "")
    raw = re.sub(r"[\u2013\u2014]", "-", raw.split("\u2022")[0])       # tidy_comp, kept in sync
    return re.sub(r"\s*-\s*", "-", re.sub(r"\s+", " ", raw)).strip()


def ats_jobs(company, kind, url, strict=False):
    if kind == "ashby" and "includeCompensation" not in url:
        url += ("&" if "?" in url else "?") + "includeCompensation=true"
    data = json.loads(fetch(url))
    out = []
    # Every board exposes a posting date; feeding it into age_days is what lets
    # the posted-after cutoff apply here at all. Without it these rows carried age
    # None and nothing aged out — most of Baseten's board predates the cutoff
    # (median 100 days old, oldest 864), and all of it landed in the sheet as "New".
    if kind == "greenhouse":
        rows = [(j.get("title", ""), (j.get("location") or {}).get("name", ""),
                 j.get("absolute_url", ""),
                 stamp_age(j.get("first_published") or j.get("updated_at")), "")
                for j in data.get("jobs", [])]
    elif kind == "lever":
        rows = [(j.get("text", ""), (j.get("categories") or {}).get("location", "") or "",
                 j.get("hostedUrl", ""), stamp_age(j.get("createdAt")), "")
                for j in (data if isinstance(data, list) else [])]
    elif kind == "ashby":
        rows = [(j.get("title", ""), j.get("location", "") or "",
                 j.get("jobUrl") or j.get("applyUrl") or "",
                 stamp_age(j.get("publishedAt") or j.get("updatedAt")), ashby_comp(j))
                for j in data.get("jobs", [])]
    else:
        raise ValueError(f"unknown feed type {kind!r}")
    return keep_titles(company, rows, strict)


def keep_titles(company, rows, strict=False):
    """Apply the title prefilter to (title, loc, link, age, comp) tuples.

    Shared by every typed feed so a new feed kind cannot quietly acquire its own
    slightly different idea of what counts as an engineering title.
    """
    out = []
    for title, loc, link, age, comp in rows:
        newgrad = bool(TITLE.search(title))
        if strict:
            if not newgrad: continue
        elif not (newgrad or (ENG_ROLE.search(title) and not LEVELED.search(title))):
            continue
        out.append({"company": company, "role": title, "location": loc,
                    "url": link, "source": f"ats:{company}", "age_days": age,
                    "comp": comp,
                    "notes": "" if newgrad else "no explicit new-grad signal in title"})
    return out


def jget(obj, path):
    """Dotted lookup: jget(d, "data.job_post_list"). Missing link -> None."""
    for part in (path or "").split("."):
        if not part:
            continue
        if isinstance(obj, dict):
            obj = obj.get(part)
        elif isinstance(obj, list) and part.isdigit() and int(part) < len(obj):
            obj = obj[int(part)]
        else:
            return None
    return obj


def api_fetch(url, method="GET", headers=None, body=None, timeout=30, tries=3):
    """One request for a json-api feed, with backoff on 429/5xx.

    These are ordinary public search endpoints, not ATS boards, and they rate
    limit: Microsoft's answered 429 after a handful of probes. A feed that gives
    up on the first 429 is a feed that fails on a busy morning.
    """
    data = json.dumps(body).encode() if body is not None else None
    hdrs = dict(headers or {})
    if data is not None:
        hdrs.setdefault("Content-Type", "application/json")
    last = None
    for attempt in range(tries):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8", errors="replace"))
        except urllib.error.HTTPError as e:
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
            time.sleep(2 ** attempt * 2)
        except Exception as e:
            last = e
            if attempt == tries - 1:
                raise
            time.sleep(2 ** attempt)
    raise last


def json_api_jobs(company, url, spec, strict=False):
    """A company's own search API, described by `sources.json_api` in config.

    Several of the biggest boards have no ATS and no server-rendered listing,
    only the XHR endpoint their own careers SPA calls. Before this they were
    `careers-page` rows, which means an agent re-derived the route by hand every
    single morning and usually failed: Microsoft, Tesla, IBM, TikTok, Apple and
    friends failed 3-6 of the 7 runs to 2026-09-20. The request shape lives in
    config rather than in this file so a moved endpoint is a config edit.
    """
    method = (spec.get("method") or "GET").upper()
    headers = dict(spec.get("headers") or {})
    # Some hosts reject our normal UA (Microsoft's pcsx 200s for curl's default
    # and returns a non-JSON body for a Mozilla string), so UA is opt-in here.
    if spec.get("user_agent"):
        headers["User-Agent"] = spec["user_agent"]
    page = spec.get("page") or {}
    size = int(page.get("size") or 0)
    max_pages = int(page.get("max_pages") or 1)
    rows, seen_links = [], set()
    for i in range(max(max_pages, 1)):
        offset = i * size
        u, body = url, None
        if spec.get("body") is not None:
            body = dict(spec["body"])
            if page.get("body_param"):
                body[page["body_param"]] = offset
        if page.get("param") and offset:
            u += ("&" if "?" in u else "?") + f'{page["param"]}={offset}'
        try:
            data = api_fetch(u, method, headers, body, int(spec.get("timeout") or 30))
        except Exception:
            if i == 0:
                raise          # page 1 failing is a broken feed; later pages are partial data
            break
        items = jget(data, spec.get("list") or "") or []
        if not isinstance(items, list) or not items:
            break
        for it in items:
            title = str(jget(it, spec.get("title") or "title") or "").strip()
            loc = jget(it, spec.get("location") or "")
            if isinstance(loc, list):
                loc = ", ".join(str(x) for x in loc[:3])
            link = ""
            if spec.get("url_template"):
                try:
                    link = spec["url_template"].format(**{k: it.get(k) for k in it})                         if isinstance(it, dict) else ""
                except Exception:
                    link = ""
            elif spec.get("url_field"):
                link = str(jget(it, spec["url_field"]) or "")
                if link.startswith("/"):
                    link = (spec.get("url_base") or "").rstrip("/") + link
            if not title or not link or link in seen_links:
                continue
            seen_links.add(link)
            age = None
            if spec.get("posted_field"):
                ts = jget(it, spec["posted_field"])
                if isinstance(ts, (int, float)) and ts > 0:
                    ts = ts / 1000 if ts > 1e11 else ts
                    age = max(0, int((time.time() - ts) / 86400))
                elif isinstance(ts, str) and ts:
                    age = stamp_age(ts)
            rows.append((title, str(loc or ""), link, age, ""))
        if size and len(items) < size:
            break
        time.sleep(float(spec.get("sleep") or 0.5))
    return keep_titles(company, rows, strict)


# Tracking junk to drop from dedupe keys. Everything else in the query string is
# treated as identifying: Indeed puts the job id in ?jk=, so blanket-stripping the
# query collapsed every Indeed posting onto one key and silently dropped the rest.
# Erring toward a duplicate row (triage flags dupe_suspect) beats losing a job.
TRACK_PARAMS = {"ref", "refid", "source", "src", "gh_src", "trk", "trackingid",
                "origin", "mode", "position", "pagenum", "seed", "lipi", "medium",
                "campaign", "utm"}


def norm_url(u):
    base, _, q = (u or "").strip().partition("?")
    keep = sorted(p for p in q.split("&")
                  if p and not (p.split("=")[0].lower().startswith("utm_")
                                or p.split("=")[0].lower() in TRACK_PARAMS))
    return base.rstrip("/").lower() + ("?" + "&".join(keep) if keep else "")


def key(j):
    if j.get("url"):
        return norm_url(j["url"])
    return re.sub(r"\s+", " ", f'{j["company"]}|{j["role"]}|{j["location"]}').lower()


def jobspy_jobs():
    """Optional rung: speedyapply's JobSpy library (pip install python-jobspy).
    Indeed is the reliable board (no rate limit, searches descriptions with
    exact-match syntax); LinkedIn rate-limits quickly without proxies. ToS-gray
    for LinkedIn - keep result counts modest. Guarded: missing lib => failed
    entry in the report, never a crash."""
    from jobspy import scrape_jobs   # ImportError handled by caller
    df = scrape_jobs(
        site_name=["indeed", "linkedin"],
        search_term='software engineer ("new grad" OR "university grad" OR "early career" OR 2027)',
        location="United States", results_wanted=50, hours_old=72,
        country_indeed="USA", linkedin_fetch_description=False)
    out = []
    for r in df.to_dict("records"):
        loc = ", ".join(x for x in [str(r.get("city") or ""), str(r.get("state") or "")] if x and x != "nan")               or str(r.get("location") or "")
        out.append({"company": str(r.get("company") or ""), "role": str(r.get("title") or ""),
                    "location": loc, "url": str(r.get("job_url") or ""),
                    "source": "jobspy:" + str(r.get("site") or ""), "age_days": None, "notes": ""})
    return [j for j in out if j["company"] and j["url"] and TITLE.search(j["role"])]


HN_ROLE = re.compile(r"new\s*grad|university|entry[- ]level|early\s*career|junior", re.I)

def hn_parse_comment(text, cid):
    """'Company | Role | Location | ...' first-line convention -> candidate or None."""
    text = re.sub(r"<[^>]+>", " ", text or "").replace("&#x2F;", "/").replace("&amp;", "&")
    first = re.sub(r"\s+", " ", text.split("\n")[0].split(". ")[0])[:200].strip()
    if "|" not in first or not HN_ROLE.search(text):
        return None
    parts = [p.strip() for p in first.split("|")]
    return {"company": parts[0][:60], "role": (parts[1] if len(parts) > 1 else "SWE (see thread)")[:90],
            "location": (parts[2] if len(parts) > 2 else "")[:60],
            "url": f"https://news.ycombinator.com/item?id={cid}",
            "source": "hn:whoishiring", "age_days": None,
            "notes": "from HN Who is hiring; open thread comment for details"}


def hn_jobs():
    """Optional rung: latest 'Ask HN: Who is hiring' via the free Algolia API.
    Low volume for new-grad, but strong for the ML-infra startup lane."""
    q = fetch("https://hn.algolia.com/api/v1/search_by_date?query=%22who%20is%20hiring%22&tags=story,author_whoishiring&hitsPerPage=1")
    hits = json.loads(q).get("hits", [])
    if not hits: return []
    item = json.loads(fetch(f'https://hn.algolia.com/api/v1/items/{hits[0]["objectID"]}'))
    out = []
    for c in (item.get("children") or []):
        j = hn_parse_comment(c.get("text"), c.get("id"))
        if j: out.append(j)
    return out


def read_watchlist(path):
    if not os.path.exists(path): return []
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    def col(r, *names):
        for n in names:
            for k in r:
                if re.sub(r"[^a-z0-9]", "", (k or "").lower()) == re.sub(r"[^a-z0-9]", "", n.lower()):
                    if str(r[k]).strip(): return str(r[k]).strip()
        return ""
    return [{"company": col(r, "Company"), "url": col(r, "Feed URL", "FeedURL", "URL"),
             "type": col(r, "Type").lower()} for r in rows if col(r, "Company")]


FILLER = re.compile(r"\b(ai|inc|llc|ltd|corp|corporation|technologies|technology|labs?|"
                    r"trading|securities|capital|group|partners|systems|software|the)\b", re.I)


def slug_variants(name, safe=False):
    """Company name -> plausible ATS slugs, best guess first. Real boards hide
    behind abbreviations: 'Fireworks AI' is `fireworks`, 'Five Rings' is
    `fiveringsllc`, 'Hudson River Trading' is `hrt`.

    safe=True drops initials and the bare first word. Those two are the only
    variants that can land on a DIFFERENT company's board ('Sun Trading' and
    'scalp trade' both initial to `st`), which would silently import the wrong
    company's jobs. Interactive --probe keeps them because a human reads the
    output; unattended bulk --resolve does not."""
    n = name.strip().lower()
    compact = re.sub(r"[^a-z0-9]", "", n)
    words = [w for w in re.split(r"[^a-z0-9]+", n) if w]
    short = [w for w in re.split(r"[^a-z0-9]+", FILLER.sub(" ", n)) if w]
    out = [compact, re.sub(r"[^a-z0-9]+", "-", n).strip("-"), "".join(short),
           "-".join(short), compact + "llc", compact + "inc"]
    if len(words) > 1:
        out.append("".join(short) + "llc")
        if not safe:
            out += ["".join(w[0] for w in words), words[0]]
    seen, uniq = set(), []
    for s in out:
        if s and s not in seen:
            seen.add(s); uniq.append(s)
    return uniq[:9]


def probe(name):
    def try_one(args):
        kind, u = args
        try:
            d = json.loads(fetch(u, timeout=12))
            return kind, u, job_count(d)
        except Exception:
            return None
    targets = [(k, u) for s in slug_variants(name) for k, u in
               [("greenhouse", f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs"),
                ("lever", f"https://api.lever.co/v0/postings/{s}?mode=json"),
                ("ashby", f"https://api.ashbyhq.com/posting-api/job-board/{s}")]]
    with ThreadPoolExecutor(max_workers=12) as ex:
        hits = [h for h in ex.map(try_one, targets) if h]
    live = sorted([h for h in hits if h[2] > 0], key=lambda h: -h[2])
    for kind, u, n in live:
        print(f"FOUND {kind:<10} {u}   ({n} live postings)")
    for kind, u, n in [h for h in hits if h[2] == 0]:
        print(f"empty {kind:<10} {u}   (board exists, 0 postings — usually not their real portal)")
    if not live:
        print(f"no public greenhouse/lever/ashby board with postings for {name!r} — likely "
              f"Workday or a custom portal; add its careers page URL with Type=careers-page")


ATS_URLS = [("greenhouse", "https://boards-api.greenhouse.io/v1/boards/{}/jobs"),
            ("lever", "https://api.lever.co/v0/postings/{}?mode=json"),
            ("ashby", "https://api.ashbyhq.com/posting-api/job-board/{}")]


def job_count(d):
    """Postings in an ATS payload. Lever returns a bare LIST, greenhouse/ashby
    return {"jobs": [...]}. Calling .get() on the list raised AttributeError that
    the callers' bare `except` swallowed, so Lever boards were never detected."""
    if isinstance(d, list): return len(d)
    if isinstance(d, dict): return len(d.get("jobs") or [])
    return 0


def resolve_one(name, max_variants=6):
    """Best feed for a company, or a careers-page placeholder. Stops at the first
    slug that returns a board WITH postings, so well-known companies cost 1-3
    requests. Resolution is a one-time cost per company — the answer is persisted
    in state/watchlist.csv's Feed URL column and daily scans just read it."""
    for slug in slug_variants(name, safe=True)[:max_variants]:
        hits = []
        for kind, tmpl in ATS_URLS:
            u = tmpl.format(slug)
            try:
                n = job_count(json.loads(fetch(u, timeout=10)))
            except Exception:
                continue
            if n:
                hits.append({"company": name, "url": u, "type": kind, "count": n})
        if hits:   # same slug can exist on two ATSs — the fuller board is the live one
            return max(hits, key=lambda h: h["count"])
    # A board that exists but is empty is not their real portal — recording it as
    # a typed feed just buys a dead fetch every run. Route to careers-page.
    return {"company": name, "url": "", "type": "careers-page", "count": 0}


def cmd_resolve(names_path, out_path, cache_path):
    names = [l.strip() for l in open(names_path, encoding="utf-8") if l.strip()]
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try: cache = json.load(open(cache_path, encoding="utf-8"))
        except Exception: cache = {}
    todo = [n for n in names if n not in cache]
    print(f"resolving {len(todo)} new company names ({len(names) - len(todo)} cached)", file=sys.stderr)
    if todo:
        with ThreadPoolExecutor(max_workers=16) as ex:
            for r in ex.map(resolve_one, todo):
                cache[r["company"]] = r
    if cache_path:
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=1, ensure_ascii=False)
    today = date.today().isoformat()
    rows = [cache[n] for n in names if n in cache]
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Feed URL", "Type", "Signal", "Added", "Last Hit"])
        for r in sorted(rows, key=lambda r: (-r["count"], r["company"].lower())):
            w.writerow([r["company"], r["url"], r["type"], "", today, ""])
    live = sum(1 for r in rows if r["count"])
    print(json.dumps({"total": len(rows), "with_live_feed": live,
                      "careers_page_or_empty": len(rows) - live, "out": out_path}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", default="run")
    ap.add_argument("--probe")
    ap.add_argument("--jobspy", action="store_true", help="also scrape Indeed+LinkedIn via python-jobspy")
    ap.add_argument("--hn", action="store_true", help="also parse the latest HN Who-is-hiring thread")
    ap.add_argument("--posted-after", default=None, metavar="YYYY-MM-DD",
                    help="drop postings older than this date (default: config posted_after)")
    ap.add_argument("--config", default=None, metavar="PATH",
                    help="config JSON; default $JOB_PIPELINE_CONFIG, then the repo's "
                         "skills/job-pipeline/assets/config.seed.json")
    ap.add_argument("--state-dir", default=None, metavar="DIR",
                    help="repo state dir holding watchlist.csv + seen.txt; default "
                         "$JOB_PIPELINE_STATE, then <repo>/state, then ./state")
    ap.add_argument("--watchlist", default=None, metavar="CSV",
                    help="feed list; default <state-dir>/watchlist.csv")
    ap.add_argument("--resolve", metavar="NAMES.txt",
                    help="one-time: resolve company names (one per line) to ATS feed URLs")
    ap.add_argument("--out", default="watchlist.csv",
                    help="--resolve output CSV (the live list is state/watchlist.csv)")
    ap.add_argument("--cache", default="", help="--resolve JSON cache; already-known names are never refetched")
    ap.add_argument("--ats-strict", action="store_true", default=None,
                    help="watchlist feeds: keep only explicit new-grad titles "
                         "(default keeps junior-eligible eng roles too — see ats_jobs)")
    a = ap.parse_args()
    if a.probe:
        probe(a.probe); return
    if a.resolve:
        cmd_resolve(a.resolve, a.out, a.cache); return

    rd = a.run_dir
    # Config is resolved here, not at import, so --config and the environment win.
    used = configure(a.config)
    strict = cfg("sources", "ats_strict", DEFAULTS["ats_strict"]) if a.ats_strict is None else a.ats_strict
    try:
        cutoff = date.fromisoformat(a.posted_after or POSTED_AFTER)
    except ValueError:
        sys.exit(f"--posted-after must be YYYY-MM-DD, got {a.posted_after!r}")
    print(f"config: {used or 'built-in defaults'} (posted_after={cutoff}, ats_strict={strict})",
          file=sys.stderr)
    # Dedupe set = keys already on the sheet (run/known.txt, orchestrator-built)
    # UNION the repo's seen ledger. Either alone is wrong: the sheet forgets what
    # triage dropped, and the ledger cannot know what the user added by hand.
    sd = state_dir(a.state_dir)
    kp = os.path.join(rd, "known.txt")
    lp = os.path.join(sd, "seen.txt") if sd else None
    sheet_keys, ledger_keys = read_keys(kp), read_keys(lp)
    known = sheet_keys | ledger_keys
    wp = a.watchlist or (os.path.join(sd, "watchlist.csv") if sd else None)
    if not (wp and os.path.exists(wp)):
        legacy = os.path.join(rd, "watchlist.csv")          # pre-state/ layout
        wp = legacy if os.path.exists(legacy) else wp
    watch = read_watchlist(wp) if wp else []
    inputs = {"state_dir": sd,
              "watchlist": wp if wp and os.path.exists(wp) else None, "watchlist_rows": len(watch),
              "known": kp if os.path.exists(kp) else None, "known_keys": len(sheet_keys),
              "ledger": lp if lp and os.path.exists(lp) else None, "ledger_keys": len(ledger_keys)}
    print(f"state: {sd or 'none'} (watchlist rows={len(watch)}, sheet keys={len(sheet_keys)}, "
          f"ledger keys={len(ledger_keys)})", file=sys.stderr)

    report = {"ok": [], "failed": [], "manual": [], "counts": {},
              "config": {"source": used or "built-in defaults",
                         "posted_after": str(cutoff), "ats_strict": strict},
              "inputs": inputs}
    pool = []

    for src in FIXED:
        try:
            try: md = fetch(src["url"])
            except Exception:
                if "fallback" not in src: raise
                md = fetch(src["fallback"])
            pool += parse_listing(md, src["name"])
            report["ok"].append(src["name"])
        except Exception as e:
            report["failed"].append({"name": src["name"], "url": src["url"], "error": str(e)})

    if a.jobspy:
        try:
            pool += jobspy_jobs(); report["ok"].append("jobspy")
        except ImportError:
            report["failed"].append({"name": "jobspy", "url": "", "error": "pip install python-jobspy"})
        except Exception as e:
            report["failed"].append({"name": "jobspy", "url": "", "error": str(e)})
    if a.hn:
        try:
            pool += hn_jobs(); report["ok"].append("hn:whoishiring")
        except Exception as e:
            report["failed"].append({"name": "hn:whoishiring", "url": "", "error": str(e)})

    api_specs = (CONFIG.get("sources") or {}).get("json_api") or {}
    for w in watch:
        if w["type"] in ("greenhouse", "lever", "ashby") and w["url"]:
            try:
                pool += ats_jobs(w["company"], w["type"], w["url"], strict)
                report["ok"].append(f'ats:{w["company"]}')
            except Exception as e:
                report["failed"].append({"name": f'ats:{w["company"]}', "url": w["url"], "error": str(e)})
        elif w["type"] == "json-api" and w["url"] and api_specs.get(w["company"]):
            try:
                pool += json_api_jobs(w["company"], w["url"], api_specs[w["company"]], strict)
                report["ok"].append(f'ats:{w["company"]}')
            except Exception as e:
                report["failed"].append({"name": f'ats:{w["company"]}', "url": w["url"], "error": str(e)})
        else:
            report["manual"].append(w)   # careers-page / missing feed: the agent's job

    # `hits` = relevant postings per source, counted AFTER the prefilters but
    # BEFORE dedupe, and it is what `persist` stamps into watchlist.csv's Last
    # Hit. Counting after dedupe would be the wrong signal to prune on: a live
    # board whose roles triage has already judged yields zero new candidates
    # forever, so a healthy ML-infra startup would age past 30 days and get
    # proposed for prune — exactly the feed policy.md says never to drop. What
    # prune wants to know is "does this feed still carry work the user would look at",
    # which goes to zero when a board dies, 404s, or empties out.
    kept, seen_now, dropped = [], set(), 0
    hits = {}
    for j in pool:
        if EXCLUDE.search(j["role"]) or too_old(j.get("age_days"), cutoff):
            dropped += 1; continue
        src = j.get("source") or ""
        hits[src] = hits.get(src, 0) + 1
        k = key(j)
        if k in known or k in seen_now:
            dropped += 1; continue
        seen_now.add(k); kept.append(j)

    os.makedirs(rd, exist_ok=True)
    with open(os.path.join(rd, "candidates.jsonl"), "w", encoding="utf-8") as f:
        for j in kept:
            f.write(json.dumps(j, ensure_ascii=False) + "\n")
    report["hits"] = hits
    report["counts"] = {"fetched": len(pool), "kept": len(kept), "dropped": dropped}
    with open(os.path.join(rd, "scan_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("REPORT_JSON: " + json.dumps(report))


if __name__ == "__main__":
    main()
