---
name: job-triage
description: Score job-posting candidates 0–5 for fit against the hand-written candidate policy (policy.md) and flag concerns (visa sponsorship risk, wrong grad year, low-signal company, location conflict). Use when asked to triage, score, rank, or filter job postings, or to judge whether a role is relevant for the candidate. Usually run inside subagents spawned by the job-pipeline orchestrator, one batch of candidates per subagent.
---

# Triage

Input: `skills/job-pipeline/policy.md` (in the repo checkout — hand-written, never generated) + one `run/candidates.bK.jsonl` batch (≤40 lines). Output: `run/triage.bK.jsonl`, one line per input line, schema `{url, fit, star, flags[], why}`. **Never fetch URLs** — judge only from the given fields and the policy; deep verification happens later for finalists only. This keeps triage cheap enough to run on everything, which is what makes the noisy sources usable.

## Rubric

Start at 3, then adjust; clamp to 0–5.

- **Role match** (+1 / −1 / not_swe flag): +1 when the title matches the policy's target roles; −1 for adjacent-but-off (QA, IT, solutions, embedded-only unless the policy says otherwise); flag `not_swe` and score ≤1 for clearly wrong roles that leaked through (retail, recruiting, hardware-only).
- **Company tier** (+1 / −1; policy.md's Company policy section defines the tiers and is authoritative): **Tier Q (quant)** +1, star-eligible regardless of size — but only for *engineering* roles; a Quantitative Researcher or Quantitative Trader req at a Tier Q firm is the wrong career track, score ≤1 and flag `not_swe`. **Tier A (top SWE brands)** +1. **Tier M (frontier ML/AI)** +1 — the most important tier, and **size-blind**: a ten-person startup counts the same as NVIDIA. Named seeds count; so does an *unknown* company whose posting is unmistakably frontier ML/AI work (inference, serving, GPU kernels, training or RL infrastructure, post-training, evals, agent systems, quantization). Judge the work, not the brand — do not flag such a company `low_signal_company`. Watchlist membership also counts +1. `low_signal_company` −1 is for staffing agencies and bodyshops (always), enterprise-IT/legacy shops, and unknown names whose role is *also* generic. An unfamiliar name on its own is never enough.
- **Sponsorship** (−2 + `sponsorship_risk`): apply when the posting fields hint at citizenship/clearance requirements (defense, government, "US persons", TS/SCI, federal), the company is on the policy's known-no-sponsor list, or the sector notoriously doesn't sponsor. If the policy says sponsorship isn't needed, skip this entirely.
- **Grad-year window** (−1 + `year_mismatch`): role's stated start/class year falls outside the policy's window (e.g. "New College Grad 2025" when targeting 2027 starts). Undated roles are fine.
- **Location** (−1 + `location_conflict`): only when the policy states hard constraints and the role clearly violates them.
- **Staleness** (`stale` flag, no score change — source-scan already dropped >45d): flag 21–45d so the digest can deprioritize.

`star: true` for fit ≥ 4 with no sponsorship_risk / year_mismatch / not_swe — the ones worth applying to today. Cap ~5 stars per batch; if more qualify, star the best and say so in your summary line.

## Judgment principles

- Uncertainty → keep and flag, never silently drop. A false negative costs an opportunity; a false positive costs one sheet row.
- `why` ≤ 15 words, concrete ("known co, backend match, SF" not "good fit").
- `policy.md` is authoritative. If it conflicts with this rubric's defaults, the policy wins. It carries no resume detail by design — triage never needs it.
- Duplicate-looking entries across sources (same company+role, different URLs) → flag `dupe_suspect` on the later one; still score it.

Write the JSONL, then return exactly one line: `done: <n> scored, <s> starred, <f> flagged`.
