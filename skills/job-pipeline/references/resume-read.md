# Reading the resume

**The resume is the single source of truth for everything the candidate has done.**
There is no derived copy, no `profile.md`, no sync job. Read it at the moment you
need it. `policy.md` holds the judgment layer (tiers, eligibility, sponsorship,
locations) — the two are disjoint on purpose: policy is what a resume cannot say.

## Fetch it — no credentials, no connector

Which resume: `storage.resumes.primary` in `skills/job-pipeline/assets/config.seed.json`.
It is either a Google Doc id or a full `https://` URL.

- **Google Doc id** (recommended): the Doc is shared "Anyone with the link can view", so
  Google's own export endpoint serves it to an unauthenticated GET — which is why this
  pipeline needs no Drive connector:

  ```
  curl -sL "https://docs.google.com/document/d/<storage.resumes.primary>/export?format=txt"
  ```

- **A URL**: fetch it as-is. It must return readable text (a raw GitHub `resume.md`, a
  plain-text export). A PDF link is a poor fit — convert the resume to a Google Doc instead.

The primary resume is the one the user attaches to every application unless `policy.md`
says otherwise; never propose swapping resume files. `storage.resumes.secondary`, when
set, is an alternate version that may be read for *talking points* (cover letters,
screening answers, outreach) but is never proposed as the file to send.

If the fetch returns 401/403, or an HTML sign-in page instead of text, the sharing setting
changed — say so plainly rather than falling back to stale material.

Skip template leftovers: a placeholder header (`Name Surname`, `yourname@gmail.com`,
`012 345 6789`) sitting above the real contact line is a stray in the Doc, not data.

## Version marker (optional)

If `storage.resume_version_url` is set, it returns `{"last_updated": "...", "sha256": "..."}`
for the resume (for example a JSON file a personal-site repo rewrites whenever the resume
changes). Use it for **traceability, not correctness**: stamp a deep-check block with the
`sha256` it was written against, and mention in the morning digest when `last_updated`
moved. Nothing should *depend* on it — the live resume is always authoritative, so there
is no cache to invalidate. Unset → skip this section entirely.

## Extraction rules — the part that matters

Anything you lift from the resume will be read as experience the candidate must defend in
an interview, out loud, to someone who does this for a living. So:

1. **Write only what the candidate did themselves.** Never import an advisor's research
   area, a lab or group affiliation, a course title, or an employer's product scope as if
   it were their own skill. These are the reliable failure mode: a line like "Advisor: X
   (field Y)" gets re-read downstream as "candidate works on Y", which produces wrong
   recommendations and unbackable claims in outreach.
2. **Quote results, not adjectives.** Concrete numbers (throughput, latency, speedup,
   memory, users, revenue) are the whole value — carry them across verbatim rather than
   paraphrasing into "improved performance".
3. **Coursework is not experience.** Projects the candidate built are; the course they
   were built for is not.
4. **Prefer recent.** When older and newer material overlap, the newer entry is the one
   the candidate can talk about.
5. If a section reads as a placeholder or template leftover, skip it and say so.

## Who reads this

**`deep-check` only.** Triage and newgrad-check judge from `policy.md` — role match,
company tier, sponsorship, grad-year window, location — none of which is on a resume.
Keeping resume reads confined to deep-check is what keeps the daily runs cheap: a sprint
does a handful of fetches, the morning brief does none.
