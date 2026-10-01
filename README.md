# job-pipeline plugin

Multi-agent new-grad job-search pipeline for Claude Code: an orchestrator skill, worker skills, and custom agent definitions, scheduled as cloud routines.

**New here? Start with [ONBOARDING.md](ONBOARDING.md)** — make it your own private repo, run `./setup.sh`, then `/job-onboarding` in Claude Code. Scheduling: [ROUTINES.md](ROUTINES.md).

Layout: `skills/` (job-pipeline orchestrator + job-source-scan / job-triage / job-mail-sweep / job-newgrad-check workers + job-onboarding), `agents/` (source-scan, triage, mail-sweep, deep-check, newgrad-check, signal-sync definitions with pinned models, restricted tools, and preloaded skills), `state/` (the pipeline's cross-run memory — `watchlist.csv`, `seen.txt`, `runs/` — committed back by every scheduled run).

Install: **`./setup.sh`** (adds `--wide` for python-jobspy, `--check` to self-test only). It repairs the TLS trust store, copies agents/ and skills/ into `~/.claude/`, checks onboarding is complete, and runs a live end-to-end self-test. Full setup — connectors, sheet, schedules — in `skills/job-pipeline/references/setup.md`. claude.ai / Cowork: save the skills; the orchestrator's contracts.md carries verbatim worker prompts so generic subagents work where custom agent definitions don't load.

Adding companies in bulk (one name per line; feed URLs are resolved once and cached in the watchlist, never re-probed on scheduled runs). `--out` overwrites its target, so resolve into a scratch file and append the rows you want to `state/watchlist.csv`:

    python3 skills/job-source-scan/scripts/fetch_sources.py --resolve names.txt --out /tmp/resolved.csv --cache feed_cache.json

Two stores: the Google Sheet is the human-facing tracker (schemas in `skills/job-pipeline/references/sheet-io.md`); this repo holds everything the pipeline remembers — config, policy, the watchlist, the seen ledger, run history. Scheduled runs clone it, run, and push `state/` back; nothing lives in Drive or on any one machine.
