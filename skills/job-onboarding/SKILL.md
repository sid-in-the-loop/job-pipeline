---
name: job-onboarding
description: First-time setup of the job-pipeline plugin for a new user. Interviews them about their profile and job-search preferences, writes skills/job-pipeline/policy.md and skills/job-pipeline/assets/config.seed.json, walks them through the resume Doc, the Google Sheet tracker and the sheets_writer Apps Script deployment, verifies each one live, creates the tracker tabs, prepares the watchlist, runs setup.sh, commits, and hands off to the cloud-routine setup. Use when the user says "onboard me", "set up the job pipeline", "configure this for me", "/job-onboarding", or whenever config.seed.json is missing or policy.md still contains {{…}} placeholders.
---

# Job pipeline onboarding

The user is present; this is the one part of the pipeline that is *supposed* to ask questions. Batch them — a short numbered list or a multiple-choice prompt, at most ~5 at a time — and fill in sensible defaults they can accept with "yes". Work from the repo root (the directory that contains `skills/job-pipeline/`). Never invent a profile fact: anything not stated by the user is asked, or left out.

Progress in this order and tell the user which step you are on (1/10, 2/10, …). If they stop halfway, everything written so far is safe to keep; re-running this skill resumes at the first incomplete step (check what already exists before asking again).

## 1. Preflight

- `git rev-parse --show-toplevel` and `git remote -v`. The repo must be **the user's own private repo** — `config.seed.json` will hold a secret that can edit their tracker. If `gh` is available: `gh repo view --json visibility -q .visibility` must print `PRIVATE`. Public → stop and have them switch it (GitHub → Settings → General → Danger Zone → Change visibility) before any secret is written. No remote yet → fine; step 9 creates one.
- Already onboarded? (`skills/job-pipeline/assets/config.seed.json` exists **and** `grep -cE '\{\{[A-Z_]+\}\}' skills/job-pipeline/policy.md` is 0) → ask which part to redo instead of starting over.
- `python3 --version` works (3.9+). If not: macOS → `xcode-select --install`.

## 2. Interview → `policy.md`

Ask, in two or three batches:

1. Degree, program and school; graduation month + year; prior education (or "none").
2. One-paragraph experience summary (their words) and **full-time professional experience in years** (internships and research don't count).
3. Target class year — suggest: graduating Aug–Dec of year Y → **Y+1** (e.g. December 2026 → "2027 New Grad" programs); graduating Jan–Jul of Y → **Y**. Target start window (e.g. "Jan 2027 – Aug 2027").
4. Experience bar — suggest `MAX_YEARS = max(1, YOE)`, `GRAY_YEARS = MAX_YEARS + 1`, `OUT_YEARS = MAX_YEARS + 2` (0–1 YOE → 1 / 2 / 3; 2 YOE → 2 / 3 / 4). Degree gate — e.g. "PhD required — candidate holds an MS", or "PhD or MS required — candidate holds a BS".
5. Target roles (SWE generalist, backend, infra, ML systems, quant dev, …) and not-interested roles (QA/SDET-only, IT/support, solutions engineering, hardware-only, …).
6. Visa sponsorship: **REQUIRED** or **NOT REQUIRED**; any companies already confirmed not to sponsor (else "none yet").
7. Company tiers: show the three tiers in `policy.md` (Q = quant engineering, A = top SWE brands, M = frontier ML/AI). Ask the priority order (e.g. "M > A > Q") and whether to drop a tier or add companies to one. Dropping a tier = delete its bullet. Known quant firms' rows still route to the `HFT` tab, which is harmless.
8. Locations: preferred cities/regions; remote OK?; the hard constraint (default: "anywhere outside the US and Canada is out"). IANA timezone for dates (derive from where they live, confirm).
9. Outreach identity: short school name and program as they'd say it in a message ("CMU", "MS CS").

Then edit `skills/job-pipeline/policy.md`, replacing every `{{SLOT}}` with their answer in plain prose (`{{STALE_YEAR}}` = class year − 1; `{{REMOTE_OK}}` e.g. "US-remote fine"). Keep the file's structure; delete a tier bullet rather than leaving it half-filled. Show them the Profile + Eligibility sections for a quick yes. Finish with `grep -nE '\{\{[A-Z_]+\}\}' skills/job-pipeline/policy.md` → must print nothing.

## 3. Config → `config.seed.json`

Create it from the template and fill what is known so far (Python, so the JSON stays valid):

```bash
python3 - <<'EOF'
import json, re, datetime
p_ex = "skills/job-pipeline/assets/config.example.json"
p = "skills/job-pipeline/assets/config.seed.json"
c = json.load(open(p_ex))
CLASS_YEAR = 2027                      # <- from step 2
TZ = "America/New_York"                # <- from step 2
c["user"]["timezone"] = TZ
s = c["sources"]
s["posted_after"] = (datetime.date.today() - datetime.timedelta(days=60)).isoformat()
s["newgrad_title"] = re.sub(r"\|20\d\d$", f"|{CLASS_YEAR}", s["newgrad_title"])
s["exclude_title"] = s["exclude_title"].replace(r"\b2026\b", rf"\b{CLASS_YEAR - 1}\b")
json.dump(c, open(p, "w"), indent=1, ensure_ascii=False)
print(s["posted_after"], s["newgrad_title"][-20:])
EOF
```

If `CLASS_YEAR` is not 2027, also set `sources.fixed` (format in the template's `_fixed` note) to `https://raw.githubusercontent.com/speedyapply/<CLASS_YEAR>-SWE-College-Jobs/main/NEW_GRAD_USA.md` plus the Simplify list — first check it exists: `curl -sI <url> | head -1` must show `200`. If it 404s, keep only the Simplify entry and tell the user.

If they said they don't want quant roles at all, leave `exclude_title` alone (it already drops quant *research/trading* titles) and let triage handle quant-dev postings via the policy.

## 4. Resume → `storage.resumes.primary`

- The resume must be readable without login. Recommended: a **Google Doc** (a PDF can be opened with Google Docs and saved as one). Share → General access → **Anyone with the link** → Viewer.
- Ask for the link; the id is the part between `/d/` and the next `/`.
- Verify: `curl -sL "https://docs.google.com/document/d/<id>/export?format=txt" | head -c 300` must print resume text, not HTML. HTML / 401 → sharing isn't "anyone with the link" yet.
- Alternative: any `https://` URL that returns plain text (e.g. a raw GitHub `resume.md`) goes in the same field as a full URL.
- Optional `storage.resumes.secondary` (alternate version, talking points only) and `storage.resume_version_url` — skip unless they already have one.

Write the value into `config.seed.json`.

## 5. Tracker sheet → `storage.tracker_sheet_id`

- Have them open **sheets.new** while signed into the Google account that will **own** the tracker — a personal @gmail.com is safest, because school/work Workspace admins can block the "Anyone" web-app deployment in step 6. Name it (e.g. "Job Tracker").
- They paste the URL; the id is between `/d/` and `/edit`. Write it into `config.seed.json`.

## 6. Deploy `sheets_writer` → `sheets_writer.url` + `.secret`

1. Generate the secret and store it first:
   `python3 -c "import secrets; print(secrets.token_urlsafe(36))"` → write into `config.seed.json` → `sheets_writer.secret`. Don't paste it into chat if you can avoid it: on macOS put it on the clipboard (`python3 -c "import json;print(json.load(open('skills/job-pipeline/assets/config.seed.json'))['sheets_writer']['secret'],end='')" | pbcopy`) when they need it.
2. In the sheet: **Extensions → Apps Script**. Copy the script to their clipboard (`pbcopy < skills/job-pipeline/assets/sheets_writer.gs`), they replace everything in the editor with it.
3. Clipboard ← secret; they replace `CHANGE-ME-TO-A-LONG-RANDOM-STRING` on the `var SECRET = …` line, keeping the quotes. Save (⌘S).
4. **Deploy → New deployment → ⚙ → Web app**; Execute as **Me**; Who has access **Anyone** → Deploy → Authorize → "Google hasn't verified this app" → **Advanced → Go to … (unsafe) → Allow** (it's their own script).
5. They paste the **Web app URL** ending in `/exec` → write into `sheets_writer.url`.
6. Verify — **`-sL`, never `-X POST`**:
   ```bash
   python3 - <<'EOF'
   import json, subprocess
   c = json.load(open("skills/job-pipeline/assets/config.seed.json"))["sheets_writer"]
   body = json.dumps({"secret": c["secret"], "ops": [{"op": "ping"}]})
   print(subprocess.run(["curl", "-sL", c["url"], "-H", "Content-Type: application/json", "-d", body],
                        capture_output=True, text=True).stdout[:400])
   EOF
   ```
   Expect JSON with `"ok":true` and the sheet's tab names. `{"ok":false,"error":"bad secret"}` → the SECRET line doesn't match. An HTML page → URL is the `/dev` one, access isn't "Anyone", or the deployment wasn't created (details: `references/setup.md` §3).

## 7. Create the tracker tabs

One POST, same pattern as the ping, with these ops (all idempotent):

```json
[{"op":"ensure_tab","tab":"SWE","headers":["Company","Role","Location","Link","Status","Date Added","Source","Fit","Comp","Flags","OA Deadline","Last Email","Notes"]},
 {"op":"ensure_tab","tab":"MLE","headers":["Company","Role","Location","Link","Status","Date Added","Source","Fit","Comp","Flags","OA Deadline","Last Email","Notes"]},
 {"op":"ensure_tab","tab":"HFT","headers":["Company","Role","Location","Link","Status","Date Added","Source","Fit","Comp","Flags","OA Deadline","Last Email","Notes"]},
 {"op":"ensure_tab","tab":"Referrals","headers":["Date Sent","Company","Contact","Contact Role","Channel","Template","Role","Follow-up Due","Status","Notes"]}]
```

Confirm with a `{"op":"read","tab":"SWE","limit":1}` op. Tell them: the lanes are SWE (general), MLE (ML/AI infra), HFT (quant/trading) — an unused lane just stays empty; they can delete the empty default "Sheet1" tab by hand. Dropdowns on Status are optional; if they add them later, the list must contain every value in `routing.status_map` (Not Applied, Applied, OA received, OA submitted, Interview scheduled, Reject, Offer) or one refused value aborts a whole write batch.

## 8. Connectors + watchlist

- **Connectors** (their action, in the browser): claude.ai → Settings → Connectors (claude.ai/customize/connectors) → connect **Gmail**, **Google Calendar** and **Google Drive** with the Google account where recruiting email arrives. If that's a school account whose admin blocks Claude, see `references/setup.md` §2. If this session already exposes Gmail tools, run one cheap search (`newer_than:2d`) to prove access; otherwise don't block on it.
- **Watchlist** — `state/watchlist.csv` ships with ~120 companies whose job-board feeds are already resolved (count them by `Type` and show the user). Ask: keep all / trim some / start empty, and which companies to add. Then:
  - Removals: delete those rows (keep the header).
  - Additions: write the names one per line to a temp file, `python3 skills/job-source-scan/scripts/fetch_sources.py --resolve /tmp/names.txt --out /tmp/resolved.csv`, and append its rows to `state/watchlist.csv` — **`--out` overwrites its target, so never point it at the real watchlist**. Skip names already present (case-insensitive).
  - Stamp `Added` = today on every row whose `Added` is blank (the weekly review prunes on it).

## 9. Install, verify, commit

- `./setup.sh` — installs the skills and agents into `~/.claude`, checks onboarding is complete, and self-tests against the live sources. It must end with "Local pipeline is healthy". Fix anything it fails before continuing.
- Re-check the repo is private (step 1). Then:
  `git add -A && git commit -m "onboarding: policy, config, watchlist"`.
- Push: `git push -u origin main`. No remote yet → offer `gh repo create <name> --private --source . --push` (ask before running it — it creates a GitHub repo). Never push `config.seed.json` to a public repo.

## 10. First run and hand-off

- Offer a supervised first run now: "run the morning brief" in this session. It fills the sheet, sweeps Gmail and pushes `state/` — ask before starting, since it writes to their tracker. It is the best proof that everything above works before anything runs unattended.
- Then the cloud routines: walk them through **`ROUTINES.md`** (repo root). If this session has the `/schedule` command (Claude Code signed in with a claude.ai account), offer to create the two routines with it using the prompts in `ROUTINES.md`, then point out the two things to check on claude.ai/code/routines afterwards: the environment has **Full** network access and only Gmail / Google Calendar / Google Drive connectors are included.
- Close with a short summary: what was configured, what they did by hand, what's left (usually: routines), and where to tune later (`policy.md` for judgment, `config.seed.json` for thresholds). Don't include the secret.
