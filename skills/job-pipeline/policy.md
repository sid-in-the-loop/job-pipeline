# Candidate policy

**Hand-written. After onboarding nothing generates this file — no sync, no routine, no agent writes it.**
It holds only what a resume cannot say: eligibility rules, company tiers, sponsorship
posture, target roles, locations. Resume material is read live from the resume named in
`assets/config.seed.json` (`storage.resumes.primary`, see `references/resume-read.md`), so
nothing here goes stale.

Edit it directly and commit. Every judgment call in triage and newgrad-check defers to it.
The `job-onboarding` skill fills every double-brace SLOT_NAME below by interviewing you;
`./setup.sh` keeps warning until none are left. The tier lists below are a starting point — cut, add, reorder.

## Profile

- Program: **{{DEGREE}}, {{PROGRAM}}, {{SCHOOL}} — graduating {{GRAD_MONTH_YEAR}}**. Prior: {{PRIOR_EDUCATION}}.
- Experience: {{EXPERIENCE_SUMMARY}}. Full-time professional experience: **{{YOE}} years** (internships and research don't count toward this number).
- Outreach identity (fills `references/outreach.md`): school {{SCHOOL_SHORT}}, program {{PROGRAM_SHORT}}, graduating {{GRAD_MONTH_YEAR}}.

## Eligibility (triage + newgrad-check ground truth)

- **Eligible:** a stated minimum of **≤ {{MAX_YEARS}} years** of experience, a range whose lower bound is ≤ {{MAX_YEARS}}, a degree-only minimum the candidate meets, or an explicit new-grad / university / campus / early-career program for the class of **{{CLASS_YEAR}}**.
- **Gray (keep, flag):** a stated minimum of exactly **{{GRAY_YEARS}} years**.
- **Out:** a minimum of **≥ {{OUT_YEARS}} years**, senior/staff/lead-level requirements, or a required degree the candidate does not hold ({{DEGREE_GATE}}).
- **Target start: {{START_WINDOW}}.** A posting that demands a start before this window keeps its verdict but gets `start_date_conflict`. Cohort labels for **{{STALE_YEAR}}** or earlier are stale → `year_mismatch`.
- Target roles: {{TARGET_ROLES}}. Not interested: {{NOT_INTERESTED}}.
- Visa sponsorship: **{{SPONSORSHIP}}**. When required, auto-flag defense/government/clearance/"US persons"/federal. Known no-sponsor list: {{NO_SPONSOR_LIST}}. Grow this list only from confirmed cases, never from assumption.

## Company policy (tiers — triage applies these)

Priority when tiers compete: **{{TIER_PRIORITY}}**. A company can sit in more than one tier; take the best match for the role. Delete a tier you don't care about and its companies score like any other.

- **Tier Q — quant firms.** Citadel, Citadel Securities, Jane Street, HRT, Optiver, Five Rings, Akuna, IMC, DRW, SIG, Jump, Tower, Virtu, Radix, XTX, Point72/Cubist, Millennium tech, Squarepoint, Geneva, Flow Traders, Voleon, Belvedere, and peers. Star-eligible regardless of size.
  **Engineering roles only.** *Quantitative Researcher*, *Quantitative Trader*, *Quantitative Research Analyst* and the rest of the research/trading track are a **different career** and are not wanted — not even at a Tier Q firm. What is wanted: quant developer, core/platform/infra engineering, trading systems, low-latency C++. "Quantitative Research **Engineer**" is engineering and does count.

- **Tier A — SWE at top companies only.** FAANG++, elite unicorns, top-brand eng orgs. Mid/unknown generic SWE shops: downrank.

- **Tier M — frontier ML/AI.**
  **Company size is irrelevant here** — a ten-person startup, a mid-stage lab and a public company all qualify equally, and the only question is whether the *work* is frontier ML/AI systems. Never downrank something in this tier for being small or unfamiliar.
  - *Startups:* Cartesia, Baseten, Fireworks, Together AI, Modal, Anyscale, CoreWeave, Groq, Cerebras, SambaNova, Lambda, Etched, Decagon, Cognition, Perplexity, Anysphere/Cursor, Mercor, Abridge.
  - *Frontier labs:* OpenAI, Anthropic, Mistral, xAI, Cohere, ElevenLabs, Thinking Machines, SSI, Reflection.
  - *Enterprise doing the same work:* NVIDIA, Databricks (Mosaic/AI Runtime), Meta (Systems ML / AI Infra), Google DeepMind, Microsoft AI, Apple (AIML), Amazon Annapurna & AWS Neuron, Tesla, Waymo, Nuro, Snowflake, Modular.
  - **An unknown company still counts as Tier M when the posting is unmistakably frontier ML/AI work** — inference and model serving, GPU kernels and performance, training or RL infrastructure and environments, post-training, evals, agent systems, distributed training, quantization. Judge the work, not the brand. High-credential young startups — ex-frontier-lab founders, well funded, working on a cutting-edge problem — are exactly the target and must **not** be dismissed as unknown.

- **Auto-downrank (`low_signal_company`):** staffing agencies, consultancies and bodyshops, enterprise-IT and legacy shops, and unknown companies whose role is *generic* (CRUD backend, internal tools, IT) with no frontier signal. An unknown name alone is not grounds to downrank — only an unknown name **plus** an unremarkable role.

- Locations: prefer {{PREFERRED_LOCATIONS}}; remote: {{REMOTE_OK}}. **Hard constraint: {{LOCATION_HARD_RULE}}** — flag `location_conflict` and downrank anything that violates it. A req listing an in-bounds city alongside an out-of-bounds one is fine.
