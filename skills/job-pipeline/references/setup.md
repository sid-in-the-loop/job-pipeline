# Setup

> **First time?** Follow `ONBOARDING.md` at the repo root — it walks through everything below in order, and the `job-onboarding` skill does most of it for you. This file is the reference behind it; `ROUTINES.md` covers the scheduled cloud runs.

## 0. Configuration — the repo is the source of truth

**`skills/job-pipeline/assets/config.seed.json`** holds every tunable: `posted_after` cutoff date, `ats_strict`, the exclude/new-grad/seniority/engineering title regexes, `fit_min`, triage `batch_size`, follow-up interval, your timezone, the tracker sheet id, the resume id — and the `sheets_writer` URL + secret, which is why **the repo must be private**. Onboarding creates it from `config.example.json` (the credential-free template, committed); after that, edit it, commit, push, and the next run clones it. Nothing writes it automatically. Keep the two in step when you add a key.

It lives in the repo — not on a machine, not in Drive — because the pipeline runs in the cloud: a scheduled run has no laptop to read, but it always has the checkout. The scripts resolve config in this order: `--config`, `$JOB_PIPELINE_CONFIG`, `config.seed.json`, `config.example.json`, built-in defaults. The seed sits beside the scripts, so the same lookup works from `~/.claude/skills/` after `setup.sh` (it copies `assets/` along — re-run `./setup.sh` after editing the seed if you run the pipeline from the `~/.claude` install). There is deliberately no `<run-dir>/config.json` and no `~/.claude/job-pipeline/config.json`: a stale shadow copy silently overriding the real one was the failure mode. Missing keys fall back individually, so a partial file is valid.

Every scan records the config it actually used in `scan_report.json` (`config.source`) and the state it read (`inputs`: watchlist rows, sheet keys, ledger keys), so a run that silently fell back to defaults — or ran without the ledger — is visible rather than mysterious.

Skills themselves take no parameters — there is no templating or input schema for a `SKILL.md`. A config file the skill and its scripts read is the mechanism.

## 1. Sheet = tracker, repo = memory

The sheet keeps only the lane tabs (`HFT`, `SWE`, `MLE`) and `Referrals` per `sheet-io.md`, with the pipeline columns (Date Added, Source, Fit, Comp, Flags, OA Deadline, Last Email — `sheets_writer` adds a missing header on first write, and onboarding creates the tabs up front). No Watchlist, Profile, Signals or Runs tabs, and no Drive folder: the watchlist is `state/watchlist.csv`, the judgment layer is `skills/job-pipeline/policy.md` (hand-written — edit it and commit), and run history is `state/runs/`, all in this repo. The resume stays where it is — the Google Doc (or URL) named in `config.seed.json`, read live per `references/resume-read.md`; there is no profile to sync.

## 2. Email and Google connectors — no forwarding filters

The account signed into Claude and the Google account you connect are independent; a connector grants access to whichever Google account you authenticate, one Google account per connector. So:

- **Connect Gmail, Google Calendar and Google Drive** at claude.ai → Settings → Connectors (claude.ai/customize/connectors) **with the Google account where recruiting email arrives**. Gmail is how mail-sweep finds OAs, interviews and rejections; Calendar gets deadline entries; Drive is how an interactive session can read the tracker. Detection happens at read time via mail-sweep's layered searches — no filter to miss anything, and the weekly wide sweep re-covers 8 days as a backstop.
- If the tracker sheet is owned by a *different* Google account than the connector, share the sheet with the connector's account as **Editor** (the connector mirrors Google permissions).
- If that inbox is a school/work Google Workspace account and its admin blocks the Claude app ("admin needs to review" error): connect your personal Google account natively instead and reach the school inbox through a Composio/Zapier **Gmail** tool (a separate OAuth app, often allowed where another is blocked). Absolute last resort: a forwarding filter from the school inbox to the connected one.
- Point LinkedIn saved-search alerts (daily email) and any company job-board alerts at the connected inbox so mail-sweep parses them.

## 3. Sheets write path (required — this is the tracker's write path)

Until this exists runs still work, but every write arrives as a paste-ready block in the digest instead of landing in the sheet.

**Use the bundled Apps Script web app (`assets/sheets_writer.gs`) — free, and it sidesteps the actual hard problem.** For an unattended cloud run the difficulty was never executing code; it is where the Google credentials live when there is no laptop and no browser. A script bound to the tracker already runs *as the sheet's owner*, so the caller needs no Google identity: a plain HTTPS POST with a shared secret. No OAuth, no refresh token to expire, no vendor quota, no monthly bill.

### Deploy

1. Open the tracker **signed in as the Google account that owns it**. The script inherits the owner's access; a non-owner deployment writes as the wrong identity or not at all. A personal @gmail.com account is the safe choice — school/work Workspace admins can disable anonymous web-app deployment, in which case the "Anyone" option below simply does not appear.
2. **Extensions → Apps Script.** This creates a project *bound* to the sheet, which is what makes `SpreadsheetApp.getActiveSpreadsheet()` resolve to the tracker with no id or auth.
3. Replace the stub `myFunction` with all of `assets/sheets_writer.gs`, set `SECRET` to the value in `config.seed.json` → `sheets_writer.secret` (onboarding generates a long random one; `python3 -c "import secrets; print(secrets.token_urlsafe(36))"` makes another), save.
4. **Deploy → New deployment → ⚙ → Web app.** Execute as **Me**; Who has access **Anyone**. Deploy.
5. Authorize when prompted. Google shows "hasn't verified this app" for your own script — **Advanced → Go to <project> (unsafe) → Allow**. Expected, not a warning about the code.
6. Copy the **`/exec`** URL (not `/dev`, which is owner-only and will 401 an unattended run). Put it into `config.seed.json` under `sheets_writer.url` and commit — the repo is private for exactly this reason.

**Redeploying after an edit:** saving does *not* change what the URL serves. Use **Manage deployments → ✏️ → Version: New version → Deploy** to keep the same URL, or New deployment to mint a fresh one (which also rotates the URL if the secret leaked).

### Verify before trusting it

    curl -sL "$URL" -H 'Content-Type: application/json' \
      -d '{"secret":"…","ops":[{"op":"ping"}]}'

**Both details matter, and each fails in a way that looks like a broken deployment.** `-L` is required: `/exec` 302-redirects to `googleusercontent.com`, and a client that doesn't follow it posts into the void. And **do not pass `-X POST`** — `-X` pins the method for *every* request including the redirect, so the follow-up hits the echo URL as a POST, which only serves GET, and Google answers with a Drive "Sorry, unable to open the file" page. `-d` already implies POST for the initial request and lets curl switch to GET on the 302, which is exactly the required behavior. Verified live: with `-X POST` the ping returns an HTML 404; without it, JSON. A successful ping echoes the tab names, which also confirms it bound to the right spreadsheet. Then run the same batch with `"dryRun": true` to see the counts it *would* apply before letting it write.

The bundled `assets/sheets_writer.test.js` exercises the script's logic against a mock `SpreadsheetApp` (`setup.sh` runs it on macOS). Useful, but it cannot prove the real API behaves like the mock — the ping and dry run above are what validate that.

The trade you are accepting: that URL is public and unauthenticated, and the shared secret is the only gate — Apps Script offers no rate limiting or key rotation. Because this build can delete rows and tabs, a leaked URL puts the whole sheet in reach: if it leaks (agent logs, a committed config in a public repo, prompt injection), redeploy for a fresh URL and change the secret, and remember File → Version history restores anything. Remaining guards are deliberately few — a per-request delete cap, `delete_tab` refusing without `confirm: true`, and unknown columns being reported rather than invented so the tracker's own layout always wins.

Alternatives, priced 2026-08-09 — none is better here:

| Option | Cost | Why not |
|---|---|---|
| **Apps Script web app** | free | *recommended* |
| Service account + Sheets API | free, no daily cap | Cleanest *credentialed* path and more secure, but needs an RSA-signed JWT (non-stdlib deps) and a key file present in the cloud run — the exact problem this avoids. Best fallback if a Workspace admin blocks web-app deployment. |
| Zapier MCP | ~$89/mo for this volume | Bills **2 tasks per tool call**; the free 100 tasks/month is ~50 writes *total*. |
| Composio | free 20K calls/mo, Pro $29/mo | Plausible, but check its current pricing for custom tools/MCP on the free tier before depending on it. |
| Pipedream / Make / n8n Cloud | free tiers too small / no free tier | 25 credits/day, MCP is Pro-only, and n8n Cloud has no permanent free tier. |

## 4. Install the plugin / skills

- **Claude Code (desktop or CLI):** run **`./setup.sh`** from the plugin root. It fixes the TLS trust store (python.org builds ship without root certs, which makes every source fail with `CERTIFICATE_VERIFY_FAILED`), copies `agents/*` → `~/.claude/agents/` and `skills/*` → `~/.claude/skills/`, checks onboarding is complete, then runs a live self-test that fails loudly if the curated lists stop parsing or the split/merge keys stop joining. `--wide` also installs `python-jobspy`; `--check` re-tests without installing. Re-run it whenever a source starts returning zero — it's the fastest way to tell "upstream changed format" from "my machine is broken".
- **claude.ai / Cowork:** save the six skills (job-pipeline, job-source-scan, job-triage, job-mail-sweep, job-newgrad-check, job-onboarding). Custom agent definitions may not load here — that's fine: the orchestrator's contracts.md contains verbatim worker prompts and each worker prompt inlines its fallback instructions, so generic subagents behave the same. Those surfaces have no checkout to commit to unless they are pointed at a clone, so a run there ends with the "state not persisted" fallback (`sheet-io.md` → Write) rather than a push.

## 5. Scheduling — cloud routines (nothing machine-local)

Both schedules run as **Claude Code cloud routines** against your private copy of this repo: `morning-pipeline-brief` daily and `weekly-pipeline-review` on Sundays. Each run clones the repo into a fresh sandbox (cwd = the checkout), reads config, policy and `state/` straight from it, runs the workflow with generic subagents on the contracts.md verbatim prompts, and ends with `pipeline_ops.py persist` plus a commit and push of `state/` to `main` (`sheet-io.md` → Write).

**`ROUTINES.md` at the repo root has the step-by-step setup and the exact prompts to paste.** The requirements, learned the hard way: the Claude GitHub app needs access to the repo **with push permission**; the environment needs **Full network access** (the default Trusted allowlist blocks script.google.com and every ATS host); `main` must not be a protected branch; no `.mcp.json` may sit in the repo root (it auto-loads and its permission prompt stalls the run); and nothing may touch `~/.claude` in the sandbox (same stall). Gmail and Calendar connectors — and the Google Drive connector the sheet can be read through — attach from the account and work headlessly. The digest is the run's final message, visible in the routine's run log.

A refused push must not sink the run: the fallback is a `claude/routine-<name>-<date>` branch (routines may always push `claude/`-prefixed branches), named on the digest so you can merge it. Two runs cannot collide on state — `seen.txt` is append-only and run files are unique per run — so the `pull --rebase` in the sequence is a formality, not a merge.

If the pipeline ever runs on a laptop instead, run it from inside the clone (`git pull` first), give it the routine prompt, and keep the persist + push step — a local run that does not push is invisible to the next routine.

## 6. Optional wide-net sources

After the first clean week, widen discovery: `pip install python-jobspy` in the run environment (a cloud environment's setup script can do it) and have the morning brief's source-scan run with `--jobspy --hn` (Indeed + LinkedIn via JobSpy, plus the monthly HN Who-is-hiring thread). Expect more noise — that's what triage is for; watch the digest's dropped-by-triage count and disable a rung if it's all chaff. For sprints on Workday portals (which Simplify handles poorly), the community `neonwatty/job-apply-plugin` adds browser autofill for Workday/Greenhouse/Ashby — installable alongside this plugin, use sparingly (browser = tokens).

## 7. Apply-sprint tooling

Free Simplify browser extension for autofill. Claude's browser agent is the exception path, with you present.

## 8. Moving to a new machine

Nothing the pipeline depends on is laptop-local, by design. Three things live in Google and stay there: the tracker sheet, the `sheets_writer` deployment bound to it, and the resume Doc. **Everything else is this git repo**: the skills and agent definitions, `policy.md`, `config.seed.json` (with the writer URL + secret — the reason the repo is private), and the machine state under `state/` — watchlist, seen ledger, run history — which the cloud routines commit back after every run. The routines run from GitHub, not from your machine, so changing laptops does not interrupt them.

New machine:
1. Install Claude Code and sign in. Connectors are account-level and come along; reconnect Google in settings if prompted.
2. Clone your private repo: `git clone git@github.com:<you>/<your-repo>.git ~/job-pipeline`.
3. `cd ~/job-pipeline && ./setup.sh` (`--wide` for the jobspy rung). That is everything.

Before any local run, `git pull` — the routines push state daily, and a stale clone would re-triage yesterday's drops and then fail to push.

## Failure ladder (memorize)

Every rung is named on the digest's Errors line — silence is the only unacceptable failure mode.

**Reading.** `config.seed.json` missing or unparseable → the scripts fall back to `config.example.json` (no writer creds, so sheet writes become TSV) and `scan_report.json` names the file actually used in `config.source`. Sheet unreadable → build `run/known.txt` empty, let the scan dedupe against `state/seen.txt` alone, expect a few already-tracked rows to resurface, and say so. Ledger not read (`scan_report.inputs` shows no state dir) → the run would re-triage the whole pool: stop and fix the checkout before writing anything. Resume returns 401/403 → the sharing setting changed; say so plainly, never fall back to stale material.

**Sources.** A watchlist feed 404s → source-scan walks careers page → search; two failed runs in a row flag the row for repair. ProcTracker unreachable → signal-sync writes an empty file and that week's review adds nothing signal-driven to the watchlist; capture still works. Google connector blocked by a school admin → see §2.

**Writing.** Sheets write down → appends as paste-ready TSV in the digest, updates as "change row: …" lines. No Calendar write → DUE SOON on the digest is the reminder.

**Persisting — the run has not happened until this lands.** `persist` prints `digest_empty: true` → write the digest and re-run it (the same `ts` overwrites itself). No git identity → `git -c user.name="job-pipeline" -c user.email="job-pipeline@users.noreply.github.com" commit …`. Push rejected as non-fast-forward → `git pull --rebase origin main && git push origin HEAD:main`, up to three times. Rebase conflict → `git rebase --abort`, then the branch fallback. Push refused (permissions, protected branch) → `git push origin HEAD:refs/heads/claude/routine-<name>-<date>` and name the branch on the digest so the user can merge it. Commit or push impossible (no git, no route to GitHub) → embed the run summary and `run/seen_delta.txt` verbatim in the digest under "state not persisted" so the user can commit them by hand — a lost ledger delta means tomorrow re-triages today's drops.
