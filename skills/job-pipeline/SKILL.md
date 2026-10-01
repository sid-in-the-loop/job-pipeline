---
name: job-pipeline
description: Orchestrate the user's new-grad SWE job search. Use whenever the conversation involves job openings, the job tracker sheet, recruiting, OAs, interviews, recruiter/referral outreach, the morning pipeline brief, an apply sprint, the weekly review, syncing community signals, or "add some company to my watchlist" — even if the skill isn't named. Scheduled tasks saying "morning brief", "pipeline", or "weekly review" mean this skill. This is the ORCHESTRATOR — it delegates fetching, parsing, triage, and mail sweeps to subagents and never does that work in its own context.
---

# Job Pipeline — Orchestrator

**Not set up yet?** If `assets/config.seed.json` is missing or `policy.md` still contains `{{…}}` slots, nothing below can run correctly — in an interactive session, stop and run the `job-onboarding` skill (`skills/job-onboarding/SKILL.md`) first; in a scheduled run, put "pipeline not onboarded" on the digest's Errors line and stop.

You are the conductor, not the band. Heavy work (fetching sources, parsing postings, judging relevance, reading email) happens in **subagents with their own context windows**; you receive small structured files and one-line summaries. Your context should contain decisions and digests, never raw HTML, raw markdown job tables, or full email bodies.

## The system in one view

- **Durable state = the Google Sheet (human-facing tracker) + this repo (machine state)** — split and schemas in `references/sheet-io.md`. Sheet: the user's lane tabs (HFT/SWE/MLE) + Referrals — nothing the user wouldn't look at daily. Repo: `policy.md` (hand-written judgment layer), `assets/config.seed.json` (tunables + writer creds), `state/watchlist.csv`, `state/seen.txt` (the ledger of every key triage has ever judged), and one run file per execution under `state/runs/`. The resume is read live from its Google Doc (`references/resume-read.md`) — never copied, so it cannot go stale. There is no other cross-run state: no Drive folder, no machine-local file.
- **In-run state = files in `run/` in the session workspace.** Subagents communicate through these files using the exact schemas in `references/contracts.md`. Fresh every run; nothing in `run/` survives the session.
- **Workers** (custom agents if installed, else generic subagents given the verbatim prompts from contracts.md): `source-scan`, `triage`, `mail-sweep`, `deep-check`, `signal-sync`.
- **Tunables = `assets/config.seed.json` in this repo** — posting cutoff, `fit_min`, triage batch size, the title regexes, tracker/resume ids, and the `sheets_writer` URL + secret. Nothing machine-local: a cloud run has no laptop to read, but it always has the checkout, and the scripts resolve this file beside themselves (`--config` / `$JOB_PIPELINE_CONFIG` override it). Read it yourself for the writer creds, and for thresholds when running without a shell. If the user asks to change a threshold, edit that file and commit — never hardcode a number into a skill. A `~/.claude/job-pipeline/config.json` from an older install is ignored.
- The user's preferences (grad year, sponsorship needs, location, company bar) live in `policy.md` in this repo — every judgment call defers to it. It is hand-written; nothing generates it.

## Orchestration rules

1. Read `references/contracts.md` before your first spawn in any run; pass workers file paths + the contract prompt, never inline data dumps.
2. **Deterministic transforms run as code, judgment runs as agents.** When a shell exists, use the bundled scripts — workers run their own (source-scan's `fetch_sources.py`); you run `scripts/pipeline_ops.py` for batch splitting (`split`) and the triage/mail merge into sheet ops (`merge`). Hand-copying rows between files is how URLs get mangled; don't. No shell → follow contracts.md manually. Never fetch job sources, parse job tables, or read email bodies yourself. If a worker fails, respawn once with its error noted; then degrade gracefully and report.
3. Fan out: triage in parallel batches of ≤40 candidates per subagent; sources can be split across 2 source-scan workers if the watchlist is large.
4. Workers return one line each. If a worker returns more than ~3 lines or pastes data, use only its files and ignore the prose.
5. All sheet writes are batched into `run/sheet_ops.jsonl` and executed once, at the end, through `sheets_writer` (procedure in `references/sheet-io.md`). If that write path is missing or failing, emit appends as paste-ready TSV and updates as "change row: …" lines in the digest and log the failure in the run file — never lose data silently. The repo half has its own fallback (rule 8).
6. Scheduled runs never ask questions; choose sensibly, flag in the digest.
7. No browser in daily runs. Browser is allowed only in `signal-sync` (one page, weekly) and apply sprints with the user present.
8. Every run ends with the digest (format in contracts.md), then `pipeline_ops.py persist` and a commit + push of `state/` (sequence and fallbacks in sheet-io.md → Write). A run whose state did not land will repeat itself tomorrow — say so on the digest.

## Workflows

### morning-brief (scheduled daily)
1. **Sheet dump** (you, cheaply): nothing to fetch for the repo half — the checkout already carries `policy.md`, `config.seed.json`, `state/watchlist.csv` and `state/seen.txt`. From the sheet, per sheet-io.md: open OA rows → `run/open_oas.csv`; every lane tab's Link column → `run/known.txt` (sheet keys only — the scan unions the ledger itself). Note on the digest which config and which ledger the scan reported using (`scan_report.json` → `config.source`, `inputs`).
2. Spawn **source-scan** → `run/candidates.jsonl` (pre-deduped, mechanically prefiltered).
3. Spawn **mail-sweep** (parallel with 2) → `run/mail_events.jsonl`. Route any `alert_role` events into the candidate pool for triage.
4. Split candidates into batches (`pipeline_ops.py split`), spawn **triage** workers in parallel → `run/triage.bK.jsonl`.
5. **Verify eligibility**: run `pipeline_ops.py verify-queue`; if it produced batches, spawn **newgrad-check** workers in parallel (one per `verify_queue.bK.jsonl`) → `run/verify.bK.jsonl`. This is what keeps not-actually-new-grad roles out of the sheet — triage never fetches, so watchlist/wide-net rows are unverified until here.
6. Merge with `pipeline_ops.py merge` → `run/sheet_ops.jsonl` + `run/run_summary.json` (fit ≥ 3 appended with Fit + Flags; OA/interview/rejection events become updates, unmatched ones become flagged appends; ★ = triage stars). Review the printed summary — re-spawn triage for any `untriaged` leftovers before executing ops.
7. Execute sheet ops; deadline events also get Calendar entries when a Calendar connector is attached — without one, DUE SOON on the digest is the reminder, and the missing connector goes on the Errors line.
8. Digest: DUE SOON first, then ★ picks with one-line reasons. Write it into `run/run_summary.json` (`"digest"`).
9. **Persist + push**: `python3 skills/job-pipeline/scripts/pipeline_ops.py persist --run-dir run` (appends the seen delta to `state/seen.txt`, files the run under `state/runs/`), then `git add -A state && git commit -m "run: morning <date> — …" && git pull --rebase origin main && git push origin HEAD:main` — retry the pull+push on a non-fast-forward rejection, fall back to `git push origin HEAD:refs/heads/claude/routine-morning-pipeline-brief-<date>` if the push is refused, and name any fallback on the digest's Errors line (exact sequence and identity note in sheet-io.md → Write).

### apply-sprint (manual, user present)
Pull Status=New sorted by Fit then age (surface `Comp` alongside — it is usually the deciding factor between two similar-fit rows, and a blank there means the posting stated nothing, not that the role pays badly); propose 5–10. For each finalist the user accepts, spawn **deep-check** (one worker per role, parallel) → tailoring block + verified deadline/sponsorship note in `run/deepcheck/<n>.md`; each worker reads the resume live per `references/resume-read.md`. Relay each block; if a finalist's row has no `Comp` and its deep-check found a stated range, write it back with the Applied update. User applies via Simplify autofill; browser agent only for portals it can't handle. Mark Applied, then run outreach (below) per company. Digest at the end.

### outreach
Read `references/outreach.md` before drafting. Drafts only — the user sends. Log every sent message to Outreach with Follow-up Due = +5 business days.

### capture ("add X to my watchlist / tracker")
For a company name/link/screenshot: extract company (+role if present). Role → lane-tab append op (Status=New, Source=capture). Company → watchlist entry: find its feed URL by trying the three ATS API patterns in `skills/job-source-scan` (this tiny probe you may do inline — ≤4 fetches) or record its careers-page URL as `type: careers-page`, then append the row to `state/watchlist.csv` (keep the header and column order: Company, Feed URL, Type, Signal, Added, Last Hit) and commit + push it with the run-end git sequence (message `watchlist: add <Company>`). Confirm in one line.

### weekly-review (scheduled Sunday)
1. Spawn **signal-sync** → `run/signals.jsonl`; companies with ≥3 recent reports → add to `state/watchlist.csv` (probe the feed URL via capture logic; put the reason in the Signal column); all signals go into this run's file (`"signals"` in `run/run_summary.json`).
2. Full-sheet review + the last ~7 run files in `state/runs/`: funnel metrics (apps → responses → OA → interview), scan health (kept/dropped, repeat source failures), stale Applied >14d, overdue Outreach follow-ups (draft the nudges), watchlist rows with Last Hit >30d → propose prune (blank Last Hit falls back to `Added`, so a new feed gets 30 days before it can be proposed). **A feed that keeps failing its fetch is a repair, not a prune** — it has no Last Hit because nothing can reach it, which says nothing about whether the company is worth watching; separate the two lists and name the failing rung for each repair.
3. Wide-net mail backstop: spawn mail-sweep in wide mode (8-day window).
4. One candid paragraph on what to change next week. Digest into `run_summary.json`, then persist + push exactly as the morning brief's step 9 (branch fallback `claude/routine-weekly-pipeline-review-<date>`) — the watchlist edits ride in the same commit.

## References
- `references/contracts.md` — run-dir layout, JSONL schemas, verbatim worker prompts. Read before spawning.
- `references/sheet-io.md` — tab schemas, the repo state files, read/write procedure including the persist + push step, fallbacks. Read before sheet ops.
- `references/outreach.md` — message templates + rules.
- `references/setup.md` — connectors, the cloud routines and their prompts, dashboard, the failure ladder. Read when something's missing or the user asks about setup.
- `references/resume-read.md` — how to read the resume Doc live, and the extraction rules. Read before any deep-check spawn.
- `policy.md` — the hand-written judgment layer (tiers, eligibility, sponsorship, locations).
