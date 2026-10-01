# Candidate policy

**Hand-written. After onboarding nothing generates this file — no sync, no routine, no agent writes it.**
It holds only what a resume cannot say: eligibility rules, company tiers, sponsorship
posture, target roles, locations. Resume material is read live from the resume named in
`assets/config.seed.json` (`storage.resumes.primary`, see `references/resume-read.md`), so
nothing here goes stale.

Edit it directly and commit. Every judgment call in triage and newgrad-check defers to it.

## Profile

- Program: **MS, Intelligent Information Systems (Language Technologies Institute), Carnegie Mellon University — graduating December 2026**. Prior: B.Tech in Artificial Intelligence, National Institute of Technology Karnataka (completed May 2025).
- Experience: RL post-training researcher focused on failure modes in on-policy / self-distillation, training models to abstain reliably under uncertainty, and agentic search — alongside systems-side work designing RL environments and grading infrastructure for inference/kernel-generation tasks (builds the training/eval environment, not the kernels themselves). Publications: ACL SRW 2024, ACL 2025, ICML 2026; one paper under review at COLM, another at ICLR 2027. Full-time professional experience: **0 years** (internships — IBM Research, Preference Model Inc. — and academic research at CMU/UCF don't count toward this number).
- Outreach identity (fills `references/outreach.md`): school **CMU**, program **MS CS** (resume/precise program name is "MIIS, LTI" — use that where accuracy matters, e.g. formal applications; "MS CS" is fine for casual cold outreach), graduating **December 2026**.

## Eligibility (triage + newgrad-check ground truth)

- **Eligible:** a stated minimum of **≤ 1 year** of experience, a range whose lower bound is ≤ 1, a degree-only minimum the candidate meets, or an explicit new-grad / university / campus / early-career program for the class of **2027**.
- **Gray (keep, flag):** a stated minimum of exactly **2 years**.
- **Out:** a minimum of **≥ 3 years**, senior/staff/lead-level requirements, or a required degree the candidate does not hold (**PhD required — candidate holds an MS**).
- **Target start: Jan 2027 – Aug 2027.** A posting that demands a start before this window keeps its verdict but gets `start_date_conflict`. Cohort labels for **2026** or earlier are stale → `year_mismatch`.
- Target roles: research engineer, ML engineer, member of technical staff (post-training), RL environments / RL data infrastructure, applied scientist, ML/training-infra SWE — at frontier ("neo") ML/AI labs. Comp preference: base **$220k+**; where a posting states comp, treat meeting this bar as a fit-score boost, not a hard filter (most postings don't state comp, and absence of a stated figure is never a penalty). Not interested: pure data science, DevOps/SRE, frontend, QA/SDET-only, IT/support, solutions engineering, hardware-only.
- Visa sponsorship: **REQUIRED**. When required, auto-flag defense/government/clearance/"US persons"/federal. Known no-sponsor list: none yet. Grow this list only from confirmed cases, never from assumption.

## Company policy (tiers — triage applies these)

**Tier M only.** Quant (Tier Q) and generic top-brand SWE (Tier A) are explicitly out of scope — do not star or favorably score a role solely for being at a quant firm or a FAANG-style brand; judge it only as Tier M (frontier ML/AI) or as a non-fit.

- **Tier M — frontier ML/AI.**
  **Company size is irrelevant here** — a ten-person startup, a mid-stage lab and a public company all qualify equally, and the only question is whether the *work* is frontier ML/AI systems. Never downrank something in this tier for being small or unfamiliar.
  - *Startups:* Cartesia, Baseten, Fireworks, Together AI, Modal, Anyscale, CoreWeave, Groq, Cerebras, SambaNova, Lambda, Etched, Decagon, Cognition, Perplexity, Anysphere/Cursor, Mercor, Abridge.
  - *Frontier labs:* OpenAI, Anthropic, Mistral, xAI, Cohere, ElevenLabs, Thinking Machines, SSI, Reflection.
  - *Enterprise doing the same work:* NVIDIA, Databricks (Mosaic/AI Runtime), Meta (Systems ML / AI Infra), Google DeepMind, Microsoft AI, Apple (AIML), Amazon Annapurna & AWS Neuron, Tesla, Waymo, Nuro, Snowflake, Modular.
  - **An unknown company still counts as Tier M when the posting is unmistakably frontier ML/AI work** — inference and model serving, GPU kernels and performance, training or RL infrastructure and environments, post-training, evals, agent systems, distributed training, quantization. Judge the work, not the brand. High-credential young startups — ex-frontier-lab founders, well funded, working on a cutting-edge problem — are exactly the target and must **not** be dismissed as unknown.

- **Auto-downrank (`low_signal_company`):** staffing agencies, consultancies and bodyshops, enterprise-IT and legacy shops, and unknown companies whose role is *generic* (CRUD backend, internal tools, IT) with no frontier signal. An unknown name alone is not grounds to downrank — only an unknown name **plus** an unremarkable role.

- Locations: prefer NYC, SF, and broader California; remote: **US-remote OK**, counts as a location fit alongside NYC/SF/CA onsite/hybrid. **Hard constraint: anywhere outside the US and Canada is out** — flag `location_conflict` and downrank anything that violates it. A req listing an in-bounds city alongside an out-of-bounds one is fine.
