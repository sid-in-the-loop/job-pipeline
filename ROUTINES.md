# Cloud routines — running the pipeline on a schedule

The pipeline is meant to run unattended as two **Claude Code routines**: saved prompts that Anthropic's cloud runs on a schedule, each in a fresh clone of your repo. Your laptop can be closed. Every run reads `policy.md`, `config.seed.json` and `state/` from the checkout, does its work, writes your tracker sheet, and commits the updated `state/` back to `main` — that commit is the pipeline's memory for the next run.

| Routine | Suggested schedule | What it does |
|---|---|---|
| `morning-pipeline-brief` | daily, ~7:30 am your time | scans the curated lists + your watchlist feeds, triages against `policy.md`, verifies eligibility on the posting pages, sweeps Gmail for OAs / interviews / rejections, writes the sheet, prints the digest, pushes `state/` |
| `weekly-pipeline-review` | Sundays, evening | pulls hiring signals and grows the watchlist, reviews the funnel and scan health, flags stale applications and overdue follow-ups (drafts the nudges), runs an 8-day mail backstop, prints a candid "what to change" paragraph, pushes `state/` |

## Before you create them

Do these once. Onboarding (`ONBOARDING.md`) covers the first two.

1. **Your repo is onboarded and pushed.** `./setup.sh --check` passes, and `skills/job-pipeline/assets/config.seed.json` + the filled-in `policy.md` are committed to `main` on GitHub. The repo is **private**.
2. **Connectors** — at [claude.ai/customize/connectors](https://claude.ai/customize/connectors), connect **Gmail**, **Google Calendar** and **Google Drive** with the Google account where recruiting email arrives.
3. **Plan** — routines need a Claude **Pro, Max, Team or Enterprise** plan. Each run draws down your normal usage, and there is a daily cap on routine runs per account (see [claude.ai/settings/usage](https://claude.ai/settings/usage)).
4. **GitHub access with push** — install the **Claude GitHub App** on your repo ([github.com/apps/claude](https://github.com/apps/claude); the routine form also prompts for it) and give it access to that repository. Routines push as *you*. Leave `main` **unprotected** (no branch-protection rule): a routine's push to a protected branch is refused.
5. **A cloud environment with Full network access** — the default environment only allows a short list of package registries, and the pipeline has to reach `script.google.com` (your sheet writer), the Greenhouse / Lever / Ashby APIs, `raw.githubusercontent.com` and arbitrary company careers pages. Create or edit one in the routine form: click the cloud icon (it shows the environment's name, e.g. **Default**) below the Instructions box → hover an environment → ⚙ → **Network access: Full** → **Save changes**. Naming a new one `job-pipeline` keeps it separate from anything else you run. Optional setup script: `pip install python-jobspy` if you later enable the `--jobspy` rung.

## Create the morning routine (web)

1. Go to [claude.ai/code/routines](https://claude.ai/code/routines) → **New routine**. (The Claude desktop app works too: **Code** tab → **Routines** → **New routine** → **Cloud**. Choosing **Local** there makes a laptop-bound task instead, which isn't what you want.)
2. **Name:** `morning-pipeline-brief`.
3. **Prompt:** paste the *Morning prompt* below, unchanged. In the prompt box's **model selector**, pick the most capable model you have (the pipeline was tuned on Opus; Sonnet is cheaper and works, but judges a little worse).
4. **Repository:** your private repo. Runs start from its default branch (`main`).
5. **Environment:** the Full-network one from step 5 above.
6. **Trigger → Schedule → Daily**, at a time a few minutes past the hour — e.g. **7:37 am**. Times are entered in your local zone and converted automatically; runs scheduled exactly on the hour can start several minutes late.
7. **Connectors:** keep **Gmail**, **Google Calendar** and **Google Drive**; remove everything else. A routine can use every tool of every included connector, writes included, without asking.
8. **Create.** Then open the routine and click **Run now** for a first test (see *First run* below).

## Create the weekly routine

Same steps, with **Name** `weekly-pipeline-review`, the *Weekly prompt* below, and **Trigger → Schedule → Weekly → Sunday**, e.g. **6:07 pm**.

## Or create them from the CLI

In Claude Code signed in with your claude.ai account (not an API key), run `/schedule` and give it the name, schedule, repo and the prompt text below — it asks follow-up questions and saves the routine to your account (`/schedule list`, `/schedule update`, `/schedule run` manage it later). Afterwards open the routine on [claude.ai/code/routines](https://claude.ai/code/routines) and confirm the **environment is the Full-network one** and only the three Google connectors are included.

## First run — what to check

Click **Run now**, then open the run (it's a normal session you can watch). A green status only means the session didn't crash — read the transcript. A healthy morning run:

- ends with the **digest** as its final message: DUE SOON, ★ picks, New / OA-interview / Follow-ups lines, and an Errors line only if something degraded;
- reports it used `config.seed.json` (not the example) and read the ledger;
- added rows to your sheet's `SWE` / `MLE` / `HFT` tabs;
- left a commit `run: morning <date> — …` on `main` in GitHub, with `state/seen.txt` and a new `state/runs/run-<date>-morning.json`.

The first run triages the most postings it ever will (nothing is in the seen-ledger yet), so it is the slowest and the noisiest; later runs only see what's new.

| Symptom | Cause → fix |
|---|---|
| fetch errors, `403` with `host_not_allowed` | environment isn't **Full** network access |
| digest has a `claude/routine-…` branch on its Errors line | the push to `main` was refused (protected branch, or the GitHub App lacks write access) — fix that, then merge the branch on GitHub |
| sheet writes arrive as TSV in the digest | `sheets_writer` URL/secret wrong or the Apps Script isn't deployed as *Anyone* — re-run the ping in `skills/job-pipeline/references/setup.md` §3 |
| "pipeline not onboarded" | `config.seed.json` missing or `policy.md` still has `{{…}}` — finish onboarding and push |
| mail sweep finds nothing / Gmail tools missing | Gmail connector not connected, or removed from the routine |
| run hangs on a permission prompt | a `.mcp.json` was committed to the repo root — delete it |
| routine stopped firing | GitHub connection expired: routines skip runs for up to 72 h, then switch off — reconnect GitHub and switch the routine back on |

## Living with it

- **Every morning:** read the digest — DUE SOON first. Tuning happens in the repo: `policy.md` for judgment (tiers, eligibility, locations), `config.seed.json` for thresholds and filters. Commit + push; the next run uses it.
- **Your own sessions:** open Claude Code in your clone for the interactive workflows — "apply sprint" (tailored bullets per finalist), "add <company> to my watchlist", "draft a referral ask to <name> at <company>". Run `git pull` first: the routines push state every day.
- **Pause** a routine with the on/off switch on its page (vacation, offer signed). **Run now** accepts optional extra text for one run.
- If you change a routine's prompt, update the copy below too, so the next person to set this up gets the version that works.

## The prompts

Paste these as-is. They are self-contained on purpose: a routine run has no memory beyond the repo, so the prompt spells out where everything is and — above all — the persist + push step, because a run whose state didn't land repeats itself tomorrow.

### Morning prompt (`morning-pipeline-brief`)

```text
Use the job-pipeline skill — skills/job-pipeline/SKILL.md in this checkout — and run its morning-brief workflow end to end, unattended.

Where things are: the working directory is a fresh clone of this repo, and it already holds everything the pipeline remembers — skills/job-pipeline/policy.md (the judgment layer), skills/job-pipeline/assets/config.seed.json (every tunable plus the sheets_writer URL and secret), state/watchlist.csv, state/seen.txt (the seen ledger) and state/runs/. Read them in place. There is no Drive folder, no ~/.claude config and no run/config.json — do not look for them, and do not copy state into run/. Create run/ at the start; nothing in it survives the run.

Delegate: read skills/job-pipeline/references/contracts.md before your first spawn and use its verbatim worker prompts, with $WS = this checkout, for source-scan, mail-sweep, the triage batches and the newgrad-check batches; run skills/job-pipeline/scripts/pipeline_ops.py for split, verify-queue and merge. Keep raw job tables and email bodies out of your own context. Tracker: the Google Sheet whose id is storage.tracker_sheet_id in config.seed.json (https://docs.google.com/spreadsheets/d/<that id>/edit) — read its lane tabs once at the start (the sheets_writer read op, or the Google connector) to build run/open_oas.csv, run/sheet_index.csv and run/known.txt per references/sheet-io.md, and send the sheet ops through sheets_writer at the end exactly as sheet-io.md → Write describes (appends first and verified, then updates; curl -sL, never -X POST). If the sheet write fails, put appends in the digest as paste-ready TSV and updates as "change row: …" lines. No browser. Never ask a question — decide, and flag it on the digest.

Finish in this order:
1. Write the digest — DUE SOON first, then ★ picks with one-line reasons, then the New / OA-interview / Follow-ups / Errors lines — into run/run_summary.json under "digest".
2. python3 skills/job-pipeline/scripts/pipeline_ops.py persist --run-dir run — if it prints digest_empty: true, write the digest and run it again.
3. git add -A state && git commit -m "run: morning <date> — +<seen_added> seen, <kept> kept, <updated> updated" (if git has no identity, commit with git -c user.name="job-pipeline" -c user.email="job-pipeline@users.noreply.github.com").
4. git pull --rebase origin main && git push origin HEAD:main. Rejected as non-fast-forward: repeat this step, up to three times. Rebase conflict: git rebase --abort, then the branch fallback. Push refused (permissions or a protected branch): git push origin HEAD:refs/heads/claude/routine-morning-pipeline-brief-<date> and name that branch on the digest's Errors line so it can be merged.
5. If no commit or push is possible at all (no git, no route to GitHub), append the run summary and the full contents of run/seen_delta.txt to the digest under "state not persisted" so they can be committed by hand — a lost ledger delta means tomorrow re-triages today's drops.
Name every fallback you used on the digest's Errors line, and end by printing the digest as your final message. The run has not happened until state/ is pushed; if it did not land, say so on the digest.
```

### Weekly prompt (`weekly-pipeline-review`)

```text
Use the job-pipeline skill — skills/job-pipeline/SKILL.md in this checkout — and run its weekly-review workflow end to end, unattended.

Where things are: the working directory is a fresh clone of this repo, and it already holds everything the pipeline remembers — skills/job-pipeline/policy.md (the judgment layer), skills/job-pipeline/assets/config.seed.json (every tunable plus the sheets_writer URL and secret), state/watchlist.csv, state/seen.txt (the seen ledger) and state/runs/. Read them in place. There is no Drive folder, no ~/.claude config and no run/config.json — do not look for them, and do not copy state into run/. Create run/ at the start; nothing in it survives the run.

Delegate: read skills/job-pipeline/references/contracts.md before your first spawn and use its verbatim worker prompts, with $WS = this checkout. Spawn signal-sync first (JSON API first; the browser is allowed only for its one fallback page) → run/signals.jsonl; every company with ≥3 recent reports gets a row appended to state/watchlist.csv (resolve its feed URL per skills/job-source-scan — the three ATS API patterns, else type careers-page — put the reason in the Signal column, keep the header and column order). Review the full sheet plus the last ~7 files in state/runs/: funnel (applications → responses → OA → interview), scan health (kept/dropped, repeat source failures), Applied rows stale > 14 days, overdue follow-ups on the Referrals tab (draft the nudges per references/outreach.md — drafts only, the user sends), and watchlist rows with Last Hit > 30 days (propose the prune; do not delete). Then spawn mail-sweep in wide mode (8-day window) as the backstop and take its events through pipeline_ops.py merge exactly as the morning brief does. Same tracker as the morning brief (storage.tracker_sheet_id in config.seed.json) and the same read/write rules: read the lane tabs once at the start, write through sheets_writer at the end per sheet-io.md → Write (curl -sL, never -X POST), TSV in the digest if the write fails. No other browser use. Never ask a question — decide, and flag it on the digest.

Finish in this order:
1. Write the digest — DUE SOON first, then the funnel and scan-health numbers, the proposed prunes and drafted nudges, one candid paragraph on what to change next week, Errors last — into run/run_summary.json under "digest", and the signal-sync rows under "signals".
2. python3 skills/job-pipeline/scripts/pipeline_ops.py persist --run-dir run — if it prints digest_empty: true, write the digest and run it again.
3. git add -A state && git commit -m "run: weekly <date> — +<seen_added> seen, <n> watchlist added, <updated> updated" — the watchlist edits ride in this same commit (if git has no identity, commit with git -c user.name="job-pipeline" -c user.email="job-pipeline@users.noreply.github.com").
4. git pull --rebase origin main && git push origin HEAD:main. Rejected as non-fast-forward: repeat this step, up to three times. Rebase conflict (someone hand-edited the same watchlist line): git rebase --abort, then the branch fallback. Push refused (permissions or a protected branch): git push origin HEAD:refs/heads/claude/routine-weekly-pipeline-review-<date> and name that branch on the digest's Errors line so it can be merged.
5. If no commit or push is possible at all (no git, no route to GitHub), append the run summary, the new watchlist rows and the full contents of run/seen_delta.txt to the digest under "state not persisted" so they can be committed by hand.
Name every fallback you used on the digest's Errors line, and end by printing the digest as your final message. The run has not happened until state/ is pushed; if it did not land, say so on the digest.
```
