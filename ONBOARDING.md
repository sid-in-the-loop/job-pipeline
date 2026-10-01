# Onboarding — set up your own copy

This repo is a Claude Code plugin that runs a new-grad job search for you: every morning it scans curated new-grad lists and ~120 company job boards, scores each posting against *your* written preferences, checks the posting really is open to someone with your experience, sweeps your Gmail for OA invites / interviews / rejections (with deadlines), and writes everything into a Google Sheet — then hands you a short digest. On Sundays it reviews the week. It runs in Anthropic's cloud, so your laptop can be closed.

It ships with **no one's personal data**: no profile, no resume, no sheet, no secrets, no history. You add yours below. Budget about an hour.

## What you need

- A **GitHub** account.
- A **Claude Pro, Max, Team or Enterprise** plan (the scheduled runs are Claude Code *routines*), and **Claude Code** — the desktop app or the CLI — signed in with that claude.ai account (not an API key).
- A **Google account to own the tracker sheet** — a personal @gmail.com is safest (school/work Google accounts can block the sheet-writer deployment).
- Your **resume as a Google Doc** (open your PDF with Google Docs and save it as one).
- `python3` 3.9+ and `git`. On a Mac: `xcode-select --install` gives you both.

## Step 1 — Make it your own private repo

You received this as a folder or a zip. Give it its own git history and a **private** GitHub repo — private matters: after onboarding, `skills/job-pipeline/assets/config.seed.json` holds a secret that can edit your tracker sheet.

```bash
cd job-pipeline
git init -b main
git add -A
git commit -m "initial import"
```

Then create an empty **private** repository on github.com (no README, no .gitignore) and push to it:

```bash
git remote add origin git@github.com:<you>/<repo>.git
git push -u origin main
```

(With the GitHub CLI, `gh repo create <repo> --private --source . --push` does both.)

## Step 2 — Install

```bash
./setup.sh
```

It repairs Python's TLS certificates if needed, installs the skills and agents into `~/.claude/`, and runs a live self-test against the real job sources. At this point it **warns that onboarding isn't done** — expected.

## Step 3 — Onboard with Claude

Open Claude Code **in this folder** (desktop app: open the folder as the project; CLI: run `claude` here) and type:

```
/job-onboarding
```

(or just say *"onboard me — follow skills/job-onboarding/SKILL.md"*). Claude interviews you and does the configuration: it writes `policy.md` (your eligibility, target roles, sponsorship needs, company tiers, locations) and `config.seed.json` (class year, timezone, sheet + resume ids, writer URL + secret), verifies each piece live, creates the tracker tabs, prepares the watchlist, re-runs `./setup.sh`, and commits.

A few steps are clicks only you can do. Claude will walk you through each, but here's what to expect:

1. **Share your resume Doc** — Share → General access → *Anyone with the link* → Viewer. The pipeline reads it (only when you ask for tailored bullets or outreach) through Google's public export link, so no Drive login is needed.
2. **Create the tracker** — a blank sheet at [sheets.new](https://sheets.new), in the Google account that will own it.
3. **Deploy the sheet writer** — in the sheet: Extensions → Apps Script → paste the provided script → set its secret → Deploy as a web app (*Execute as: Me*, *Who has access: Anyone*) → authorize ("unverified app" → Advanced → Allow; it's your own script). This is how unattended cloud runs write to your sheet without storing any Google password. Details: `skills/job-pipeline/references/setup.md` §3.
4. **Connect Google to Claude** — at [claude.ai/customize/connectors](https://claude.ai/customize/connectors), connect **Gmail**, **Google Calendar** and **Google Drive** using the account where recruiting email arrives.

Prefer to do it by hand? Fill every `{{…}}` in `skills/job-pipeline/policy.md`, copy `skills/job-pipeline/assets/config.example.json` to `config.seed.json` in the same folder and fill `user`, `sources.posted_after` / `newgrad_title` / `exclude_title` (class year), `storage` and `sheets_writer`, then run `./setup.sh` until it's clean.

## Step 4 — Do one supervised run

Still in Claude Code in this folder, say **"run the morning brief"**. Watch it scan, triage, sweep your mail, write the sheet and push `state/`. The first run is the biggest (nothing has been seen yet); after that each morning only handles what's new. Fix anything on its Errors line before scheduling.

## Step 5 — Schedule it

Follow **[ROUTINES.md](ROUTINES.md)**: two cloud routines (daily brief, weekly review), a cloud environment with Full network access, the Claude GitHub App on your repo, and the prompts to paste.

## Day to day

- **Read the morning digest** (the final message of each routine run on [claude.ai/code/routines](https://claude.ai/code/routines)): DUE SOON first, then ★ picks worth applying to today.
- **In Claude Code, in your clone** (`git pull` first — the routines push daily): "apply sprint" → Claude proposes 5–10 roles and writes tailored resume bullets, a why-this-team line and screening answers for each; "add Acme to my watchlist"; "draft a referral ask to Jane at Acme"; "what OAs are due this week?".
- **Tune** by editing and pushing: `skills/job-pipeline/policy.md` changes *judgment* (what counts as a fit), `skills/job-pipeline/assets/config.seed.json` changes *mechanics* (date cutoff, title filters, fit threshold). If the sheet gets noisy, raise `triage.fit_min` to 4.

## Where things are

| Path | What |
|---|---|
| `skills/job-pipeline/policy.md` | your preferences — the judgment layer every run defers to |
| `skills/job-pipeline/assets/config.seed.json` | your settings + the sheet writer's URL and secret (created by onboarding; private) |
| `state/watchlist.csv` | companies whose job boards are scanned daily (starter list included) |
| `state/seen.txt`, `state/runs/` | what's already been judged, and one record per run — written by the pipeline |
| `ROUTINES.md` | scheduling in the cloud |
| `skills/job-pipeline/references/setup.md` | the full reference, including the failure ladder |
