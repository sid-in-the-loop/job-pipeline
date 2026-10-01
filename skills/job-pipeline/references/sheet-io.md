# Storage & IO

Two stores, one rule: **the Sheet holds what the user looks at; the repo holds what the pipeline remembers.** Cloud runs are stateless in between — each one clones the repo, reads both stores, writes the Sheet through `sheets_writer`, and commits the repo half back. There is no third store: no Drive folder, no machine-local file.

## The Sheet (human-facing tracker — keep it human-scale)

Tracker: `https://docs.google.com/spreadsheets/d/<storage.tracker_sheet_id>/edit` — the id lives in `assets/config.seed.json`; the `sheets_writer` deployment is bound to the same sheet.

| Tab | Columns |
|---|---|
| `HFT` / `SWE` / `MLE` (lane tabs — pipeline routes by lane; onboarding creates them, and a lane you don't use just stays empty) | Company, Role, Location, Link, Status, … + pipeline columns: Date Added, Source, Fit, Comp, Flags, OA Deadline, Last Email |
| `Referrals` | the user's outreach log: Date Sent, Company, Contact, Contact Role, Channel, Template, Role, Follow-up Due, Status, Notes |

**Nothing the user wouldn't look at daily goes in the sheet.** The watchlist, the seen ledger, run history and the policy are machine state — they live in the repo (below). No Signals tab, no Runs tab, no Watchlist tab, no Profile tab. Map to the user's existing columns rather than renaming theirs. Dates ISO `YYYY-MM-DD` everywhere.

`Status`: internal stage names are New → Applied → OA → OA Submitted → Interview → Rejected (also Offer), but **the cells carry the sheet's own validated vocabulary** via config `routing.status_map` (New→"Not Applied", OA→"OA received", OA Submitted→"OA submitted", Interview→"Interview scheduled", Rejected→"Reject") — the tabs have data validation, and writing an unmapped value aborts the whole batch (found live: cell H997 rejected "New"). **A stage never moves backwards**: mail events take the highest-ranked status seen, so a stray acknowledgment or a re-sent assessment notice can't reopen a submitted OA. Only OA and Interview carry an `OA Deadline` — a submitted assessment is no longer owed and must drop off DUE SOON.

`Comp` is the pay the **posting itself states** — "$138K-$201K", "$300K", `OTE $230K-$320K` for on-target bands — and is **empty whenever the posting states nothing**. It is never an estimate: not levels.fyi, not "typical for the level", not inferred from the company or the title. A blank cell invites a look; a plausible wrong number ends the question, which is the worse failure. Ashby feeds publish it as a structured field so the scan captures it free; every other board states it in prose, read at the verify step from a page that worker fetches anyway. Rows added before the column exists stay blank until `pipeline_ops.py backfill-comp` fills them — no scan will resurface them, because `seen.txt` has their URLs.

`Last Email` is the Gmail permalink for the most recent message in that process. Every state in the sheet is then one click from the evidence that produced it, which is what makes a wrong state diagnosable instead of mysterious.

## The repo (machine state)

Everything the pipeline needs between runs is a committed file in this checkout — diffable, `cat`-able in the sandbox, readable with no connector and no credentials:

| Path | What | Written by |
|---|---|---|
| `skills/job-pipeline/policy.md` | the judgment layer: eligibility, tiers, sponsorship, locations | the user, by hand — never generated |
| `skills/job-pipeline/assets/config.seed.json` | every tunable + the `sheets_writer` URL and secret (why the repo is private) | the user, by hand |
| `state/watchlist.csv` | `Company, Feed URL, Type, Signal, Added, Last Hit` — the typed feed list; `Feed URL` + `Type` are the resolved-once cache the daily scan reads verbatim | capture and the weekly review append rows; `pipeline_ops.py persist` stamps `Last Hit` |
| `state/seen.txt` | one normalized dedupe key per line (contracts.md): **every key triage has ever judged, kept or dropped** | `pipeline_ops.py persist` — append-only |
| `state/runs/run-<date>-<kind>.json` | one file per run; the audit trail the weekly review reads (last ~7) | `pipeline_ops.py persist` |

The ledger is the part that is easy to underrate. Sheet links alone resurrect every rejected posting daily (~19% of the pool, forever); `seen.txt` is what makes a reject permanent. It only grows — never prune it, never rewrite it.

`Last Hit` is the date that feed last carried a posting worth looking at — **counted after the prefilters but before dedupe**, which is the distinction that makes it safe to prune on. Counting new candidates instead would zero out every healthy board whose roles triage has already judged, and the first feeds to age past 30 days would be the small ML-infra startups policy.md calls the most important tier. What the column answers is "does this feed still carry relevant work", which goes blank exactly when a board dies, 404s, or empties out. `persist` stamps it from the scan's per-source `hits` plus any `careers:<Company>` rows the scan agent walked by hand; it never clears a value and never moves one backwards, so a run whose scan half failed leaves the column alone instead of ageing every feed at once. Blank means no hit has ever been recorded — for prune purposes that falls back to `Added`, which gives a newly added feed a 30-day grace period.

Run file shape: `{"ts","date","kind","scanned","kept","dropped_by_triage","dropped_by_verify","verified_out":[…],"updated","stars":[…],"untriaged":[…],"errors":[…],"manual_sources":[…],"signals":[…],"digest":"…"}` — `merge` drafts it, the orchestrator fills `digest` (and `signals` on the weekly), `persist` files it. Naming: `run-<date>-<kind>.json`; a second run of the same kind on the same date never overwrites the first — `persist` writes it as `run-<date>-<kind>-<HHMM>Z.json` (UTC time from its `ts`).

Config: the scripts resolve `config.seed.json` themselves (`--config` and `$JOB_PIPELINE_CONFIG` override it). Read it yourself for the `sheets_writer` URL + secret, and for thresholds when you have no shell. A `~/.claude/job-pipeline/config.json` left over from an older install is ignored.

The resume is stored nowhere in the pipeline: `references/resume-read.md` reads the Google Doc live, and the user sends the primary resume (`storage.resumes.primary`) for everything.

## Read (run start)

Nothing to fetch for the repo half — it is the checkout you are running in. From the sheet, derive two small files into `run/`:

- `run/open_oas.csv` — lane-tab rows with Status ∈ {Applied, OA, OA Submitted, Interview} (mail-sweep cross-checks these).
- `run/sheet_index.csv` — `tab,Company,Role,Link,Status` for **every** lane-tab row (not just the open ones). This is what `merge` aims its updates at; without it every mail event falls back to a bare Company match, which on a busy sheet can mean 20+ candidate rows for one big company. Costs nothing — it comes out of the same single pull.
- `run/known.txt` — every lane tab's Link column (+ `company|role` fallback for rows without one), normalized per contracts.md. **Sheet keys only** — `fetch_sources.py` unions `state/seen.txt` into its dedupe set itself; do not copy the ledger in by hand.

Read the lane tabs once, either through the `sheets_writer` `read` op (`{"op":"read","tab":"SWE","limit":2000}` → `{headers, rows, totalRows}`, same envelope and same `curl -sL` rules as writes) or through the Sheets/Drive connector when the session has one, and derive both files from that one pull.

Every scan records what it actually read in `scan_report.json` — `config.source` and `inputs` (state dir, watchlist rows, sheet keys, ledger keys). A run that fell back to defaults or read no ledger goes on the digest's Errors line; left silent it re-triages the whole pool.

## Write (run end)

1. Execute `run/sheet_ops.jsonl`. Preferred path is the Apps Script web app (`assets/sheets_writer.gs`), whose URL + secret live in config under `sheets_writer` — POST `{"secret": "…", "ops": [<every line of sheet_ops.jsonl>]}` in **one** request and **follow redirects** (`curl -L`; the `/exec` URL 302s to googleusercontent.com, and not following it fails silently). It returns `{ok, appended, updated, unmatched, ignoredCols}` — report `ignoredCols` on the digest, since a key there means the pipeline tried to write a column the sheet does not have. If a Sheets MCP connector is configured instead, use it: batch appends per tab, updates located by the `match` column. **An HTML page back instead of JSON is a caller bug, not a dead writer**: Drive's "Sorry, unable to open the file" page after the `/exec` 302 means the request carried `-X POST`, which pins the method through the redirect so the follow-up hits the echo URL as a POST, which serves GET only. Drop `-X` and let `-d`/`--data-binary` imply POST on the first hop — `curl -sL -H 'Content-Type: application/json' -d @body "$URL"` — exactly as setup.md §3 warns. A `GET` answering `{"ok":true,"hint":…}` confirms the deployment is healthy while the POST fails, which is the tell. Fall back to the digest only after ruling this out. **`ignoredCols` is a silent data loss, not a warning**: the op reports `updated: N` while the value goes nowhere, so a batch whose only new column is missing writes literally nothing. Handled without a redeploy — `merge` and `backfill-comp` prepend an `ensure_tab` op per tab naming every column the batch writes, so the header exists before any update lands. (`assets/sheets_writer.gs` also honors `addMissingCols` on updates now, but that only takes effect if the Apps Script is ever redeployed by hand, so nothing depends on it.) Building a batch by hand? Lead with the same `ensure_tab` ops. Still report `ignoredCols` on the digest: non-empty now means a column the pipeline never declared. Send large batches in chunks of ~100 ops: updates are located by scanning the tab, so one enormous request risks the Apps Script execution limit. Either way ops are deduped first, and unmatched updates become appends flagged `unmatched-update`.

   **Send the appends and the updates as two separate POSTs — appends first, and verify them before sending the updates.** The writer buffers appends per tab and flushes a tab only when a non-append op for that tab arrives (or at the very end), while incrementing `appended` at *buffer* time. So one throwing update aborts the script before the still-buffered tabs are ever written, and the response reports every one of those rows as appended. This is silent data loss with a success-looking count: in one live run a rejected `Status` value on one tab killed the request after that tab had flushed, and 61 of 66 rows — every append for the other tabs — vanished while the response said `appended: 66`. Their keys still went into `seen.txt`, so no later scan can resurface them. Splitting the request removes the coupling entirely: an update that violates data validation can then only cost its own op. **Never trust `appended` as proof.** After the append POST, read the tabs back and confirm each append's `Link` is present; report the count you *verified*, not the count the writer claimed, and put any shortfall on the digest's Errors line with the missing rows as paste-ready TSV.

   `Status` writes are the likeliest thing to throw once a tab carries a data-validation dropdown, because the lists need not be identical per tab — e.g. a tab whose dropdown has no `Reject`. (The tracker onboarding creates has no validation, so nothing throws until you add dropdowns yourself.) A refused value does not fail its own op — the Apps Script throws and returns an **HTML error page**, so the batch dies and every still-buffered append dies with it. (The tell: a `GET` to the same URL still answers `{"ok":true,"hint":…}`. That is the same symptom as the `-X POST` caller bug, so check the op before blaming the deployment.) `routing.status_map` is global and cannot express a per-tab list, so `routing.status_vocab` now does: `merge` checks the target tab's vocabulary **before building the op**, drops only the `Status` key, keeps the row's other values, and records the row under `run_summary.status_unwritable` so the digest carries a "change row: …" line automatically. Never substitute a near-miss value — writing `Interview done` for a rejection would be worse than leaving it stale. The guard is not the fix: add the missing value to the tab's dropdown (Data ▸ Data validation), then delete that tab's entry from `status_vocab`.
2. Put the digest into `run/run_summary.json` (`"digest"`; the weekly also fills `"signals"`), then run `python3 skills/job-pipeline/scripts/pipeline_ops.py persist --run-dir run`. It appends `run/seen_delta.txt` (written by `merge`) to `state/seen.txt` — a union, so re-running is safe — and files the run as `state/runs/run-<date>-<kind>.json`, printing `{"seen_added","seen_total","run_file","digest_empty"}`. `digest_empty: true` means you persisted before writing the digest: fix it and re-run, the same `ts` overwrites itself.
3. Commit and push `state/` — the run has not happened until this lands:

   ```
   git add -A state
   git commit -m "run: <kind> <date> — +<seen_added> seen, <kept> kept, <updated> updated"
   git pull --rebase origin main && git push origin HEAD:main
   ```

   If `git config user.email` is empty (a fresh sandbox), commit with `git -c user.name="job-pipeline" -c user.email="job-pipeline@users.noreply.github.com" commit …`. Push **rejected** as non-fast-forward (another run landed first): repeat `git pull --rebase origin main && git push origin HEAD:main`, up to three times — `seen.txt` is append-only and run files are unique per run, so the rebase never conflicts on state. Push **refused** (permissions, protected branch): `git push origin HEAD:refs/heads/claude/routine-<name>-<date>` (a `claude/`-prefixed branch is the one kind a cloud routine is always allowed to push) and name that branch on the digest's Errors line so the user can merge it. A rebase conflict anyway (someone hand-edited the same line of `watchlist.csv`): `git rebase --abort`, then the branch fallback.

**Fallbacks — never lose data silently:** Sheets write down → appends as a paste-ready TSV block in the digest, updates as "change row: …" lines. Commit or push impossible (no git, no route to GitHub) → embed the run summary **and the seen delta** (`run/seen_delta.txt`, verbatim) at the bottom of the digest under "state not persisted", so the user can commit them by hand — a lost ledger delta means tomorrow re-triages today's drops. Every fallback is named on the digest's Errors line.
