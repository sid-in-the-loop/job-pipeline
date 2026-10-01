---
name: job-source-scan
description: Fetch and normalize new-grad SWE job postings from curated GitHub lists, company ATS feeds, and careers pages into candidates.jsonl for the job pipeline. Use when asked to scan job sources, run the discovery step of the morning brief, refresh the watchlist feeds, or probe a company's job feed. This skill is usually run inside a subagent spawned by the job-pipeline orchestrator.
---

# Source scan

## Script first, judgment second

When a shell is available, START by running the bundled deterministic layer:

    python3 <this skill>/scripts/fetch_sources.py --run-dir run

It fetches the fixed lists + every typed feed in `state/watchlist.csv` (the repo's; found via `--state-dir`, `$JOB_PIPELINE_STATE` or the checkout itself — `--watchlist` overrides), normalizes, prefilters, dedupes against `run/known.txt` ∪ `state/seen.txt`, writes `run/candidates.jsonl`, and prints `REPORT_JSON` with three lists: `ok` (done — don't refetch), `failed` (feeds that errored: your job, via the fetch ladder below, appending results to candidates.jsonl in the same schema), and `manual` (careers-page watchlist rows: also your job). Code handles the stable 90% exactly and cheaply; you spend tokens only on drift and judgment-shaped fetching. `--probe "Company"` finds a new company's feed URL. No shell → do everything below by hand.


Input: `state/watchlist.csv`, `run/known.txt` (sheet keys), `state/seen.txt` (the ledger). Output: `run/candidates.jsonl` (schema in job-pipeline's contracts.md). You fetch and normalize; you do **not** judge relevance — that's triage's job. You are an agent, not a parser script: when a source's format shifts, adapt; when a feed dies, walk the ladder below instead of failing.

## Fixed sources (always)

- speedyapply new-grad USA: https://raw.githubusercontent.com/speedyapply/2027-SWE-College-Jobs/main/NEW_GRAD_USA.md (one repo per class year — `sources.fixed` in config swaps both lists for another cohort)
- Simplify new-grad ("pittcsc"): https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/README.md (if 404, try the `main` branch)

Parsing notes: both lists are **Company, Role, Location, …, Age** rows, but the markup drifts — speedyapply ships markdown pipe rows whose cells contain HTML `<a href>` anchors, Simplify ships a full HTML `<table>` with no pipes at all. The script runs both a pipe parser and an HTML-table parser over every source and keeps whatever either finds, so a format flip degrades to zero-loss instead of zero-results. The **apply link is the last non-image URL outside the company cell** (skip camo.githubusercontent.com / imgur / shields.io badges, and prefer a direct employer link over a `simplify.jobs/p/` redirect); `↳` in the company cell = same company as the previous row; a row whose application cell is `🔒` is a closed posting and carries no link — dropping it is correct (Simplify's list is ~83% closed rows). Strip decorations from the company name — speedyapply prefixes hot listings with 🔥, and `🔥 TikTok` must dedupe and tier-match as `TikTok`. These notes describe the format *today*; trust what you actually fetch over these notes.

## Optional wide-net rungs (off by default; enable once validated)

- `--jobspy`: scrapes Indeed + LinkedIn through speedyapply's python-jobspy (`pip install python-jobspy`, same org as the curated list). Indeed is the dependable board; LinkedIn rate-limits fast and scraping it is ToS-gray — keep counts modest, drop the board if it errors repeatedly.
- `--hn`: parses the latest "Ask HN: Who is hiring" thread via the free Algolia API (`Company | Role | Location` first-line convention, new-grad-signal filtered). Low volume, but disproportionately good for the ML-infra startup lane.

Both fail soft into the report's `failed` list. Everything they return still passes the same prefilters and then triage — wide net in, judgment after.

## Watchlist feeds (per row of state/watchlist.csv)

Fetch ladder — go down one rung on failure, and note which rung worked:
1. **Feed URL as given.** JSON APIs by type: greenhouse `.jobs[] → title, absolute_url, location.name`; lever `[] → text, hostedUrl, categories.location`; ashby `.jobs[] → title, jobUrl|applyUrl, location`.
2. **Careers page** (type `careers-page`, or when the API 404s/changed): fetch the page; extract posting titles + links from whatever structure it has. Workday and custom portals land here.
3. **Targeted search**: web-search `"<Company>" new grad software engineer <class year> site careers` (class year from policy.md) and fetch the best careers/board result once.
4. Give up on that company for this run; record it in your failure list. Two consecutive failed runs is worth surfacing so the orchestrator can flag the row for repair.

**Type `json-api`** is rung 2 made permanent: the XHR endpoint a careers SPA calls, for the big boards that have no ATS and no server-rendered listing. The script fetches these itself — the per-company request recipe (method, headers, POST body, the dotted path to the list, field names, pagination) lives in config under `sources.json_api`, so a moved endpoint is a config edit and not a code change. Resolved this way on 2026-09-21: Microsoft (Eightfold `/api/pcsx/search`, GET) and TikTok (`api.lifeattiktok.com`, POST with a `website-path` header). Two gotchas worth knowing before adding one: some hosts reject our own User-Agent and answer a plain `curl` (Microsoft returns non-JSON for a Mozilla string), and these endpoints rate-limit where ATS boards do not, so `api_fetch` backs off on 429 and `page.max_pages` deliberately takes the newest N rather than the whole board.

**A careers page that answers 200 is not proof the feed works.** Cisco's `jobs.cisco.com` 302'd to a Phenom portal and Bloomberg's `careers.bloomberg.com` was retired entirely, 301ing to marketing content — both kept returning 200 to a redirect-following fetcher while carrying no postings at all. Judge by posting count, never by status code. And check `robots.txt` before adopting a route: Google's careers results page is perfectly fetchable and explicitly `Disallow`ed, which makes it off-limits rather than broken.

Title prefilter for feed results (curated lists skip this — they're pre-curated): keep titles matching the config's `sources.newgrad_title` (`new grad|university|early career|…|<class year>`), **plus** engineering titles that carry no seniority marker. The watchlist row is itself the curation — the user hand-picked that company — and ML-infra startups never write "new grad": under new-grad-titles-only, Baseten's 65-posting board yielded exactly zero despite 28 junior-eligible engineering roles, which quietly made the policy's ML-infra exception lane dead weight. Roles kept on the second path get `notes: "no explicit new-grad signal in title"` so triage weighs the grad-year window itself. `--ats-strict` restores titles-only.

## Probing a new company (capture support)

**Feed URLs are resolved once and then persisted — never re-probe on a scheduled run.** `state/watchlist.csv`'s `Feed URL` + `Type` columns are the durable cache (committed in the repo); the daily scan reads them verbatim and probes nothing. Probing happens only in two places: `--probe "Company"` when capture adds a new company, and the one-time bulk `--resolve`.

    python3 fetch_sources.py --resolve names.txt --out state/watchlist.csv --cache feed_cache.json

`--resolve` takes one company name per line and writes watchlist-shaped rows (commit the result). `--cache` makes it idempotent — already-resolved names are never refetched, so re-running over a grown list costs only the new names.

Slug guessing, best guess first: compact (`janestreet`), hyphenated, corporate-filler stripped (`Fireworks AI` → `fireworks`), `+llc` / `+inc` (`Five Rings` → `fiveringsllc`). Interactive `--probe` additionally tries initials and the bare first word; bulk `--resolve` deliberately does not, because those two can land on a *different* company's board (`Sun Trading` and `scalp trade` both initial to `st`) and silently import the wrong company's jobs.

URL shapes: `https://boards-api.greenhouse.io/v1/boards/<slug>/jobs` · `https://api.lever.co/v0/postings/<slug>?mode=json` · `https://api.ashbyhq.com/posting-api/job-board/<slug>`

Judge a hit by **posting count, not by valid JSON**: a slug can exist on two ATSs, so take the fuller board (Anyscale answers on Lever with 1 and Ashby with 18). A board that returns 200 with **zero** postings is not their real portal (Optiver, HRT) — record `careers-page` instead, or it buys a dead fetch every single run. Note Lever returns a bare JSON **list** while greenhouse/ashby return `{"jobs": […]}` — count accordingly.

## Mechanical prefilters (apply to everything)

- Exclude titles matching: `senior|staff|principal|\bsr\.?\b|manager|director|vp\b|head of|intern(ship)?\b|co[- ]?op\b|phd`
- Drop `age_days > 45`.
- Dedupe: normalize keys per contracts.md; drop anything in `run/known.txt`, in `state/seen.txt`, or already emitted this run.
- Normalize whitespace; keep fields terse. No commentary in `notes` unless something odd is worth triage seeing (e.g. "US citizenship required" seen in the feed → note it — that's signal, not judgment).

Write the JSONL, then return exactly one line: `done: <n> kept, <m> deduped, sources failed: <names or none>`.
