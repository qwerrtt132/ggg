# Multi-Agent LLM Failure Modes and Mitigations

Scope: failure modes seen in multi-agent LLM systems, the mitigations that have evidence, and how strong that evidence is. Evidence tags used below: **[E]** controlled/empirical study, **[F]** documented framework feature (exists in the docs; says little about whether it works), **[P]** practitioner or vendor report, **[S]** secondary summary I could not check against the primary source. Sources fetched directly: Anthropic's multi-agent research post. Several primary arXiv and Cognition pages failed to fetch (network errors), so those claims come from search excerpts and are tagged accordingly.

## What failure modes are documented for multi-agent LLM systems?

### Takeaway
The most systematic public taxonomy is MAST (Cemri et al., 2025): 14 failure modes in three categories (system design, inter-agent misalignment, task verification), built from annotated traces across seven frameworks. Failures are mostly about coordination, specification and verification rather than raw model capability. Verification failures (declaring success without checking) are the most consistently reported.

### Cited Findings
- MAST defines 14 failure modes in three categories, annotated from 150 traces (inter-annotator kappa 0.88); a later version reports 1,642 traces across seven frameworks. Category names differ between versions (v2 says "specification issues"; v3 says "system design issues") — [arXiv 2503.13657v2](https://arxiv.org/html/2503.13657v2); [arXiv 2503.13657v3](https://arxiv.org/html/2503.13657v3) **[E]**
- Per-mode prevalence reported in a secondary overview (not checked against the paper's full text): premature termination 6.2%, no/incomplete verification 8.2%, incorrect verification 9.1%, task-verification category 23.5%; reasoning-action mismatch 13.2%; task derailment 7.4%; inter-agent misalignment category 32.3%; system design category 44.2% — [MAST paper](https://arxiv.org/pdf/2503.13657) **[S]**
- An IBM / UC Berkeley analysis of 310 ITBench SRE traces reports incorrect verification (FM-3.3) as the strongest predictor of failure, with agents often declaring success without checking ground truth — [Hugging Face blog](https://huggingface.co/blog/ibm-research/itbenchandmast) **[S]**
- Anthropic reports these early failures in its own multi-agent research system: spawning 50 subagents for simple queries; vague delegation causing duplicate work (one subagent covered the 2021 chip shortage while two searched the same 2025 supply-chain topic); overly long, specific search queries that returned almost nothing; a preference for SEO content farms over authoritative sources; wrong-tool selection — [Anthropic engineering](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Cognition's example: subagents given split subtasks (e.g. a game's visuals and logic) produce incompatible outputs because they cannot see each other's decisions. Source excerpt quotes the Flappy Bird clone example — [Cognition, "Don't Build Multi-Agents"](https://cognition.ai/blog/dont-build-multi-agents) (fetch failed; from search excerpt) **[P]**
- Debate-specific failures: agents favor agreement over challenging flawed peer reasoning, correct answers flip to incorrect under peer pressure, and accuracy can deteriorate as rounds progress — [Talk Isn't Always Cheap, arXiv 2509.05396v2](https://arxiv.org/html/2509.05396v2) (search excerpt) **[E]**
- Sycophancy propagates through multi-agent discussion and causes error cascades; sycophancy priors reduce the influence of sycophantic peers and improved final accuracy by an absolute 10.5% across six open-source models — [Too Polite to Disagree, arXiv 2604.02668](https://www.alphaxiv.org/abs/2604.02668) (search excerpt; verify numbers) **[E]**
- Same-model ("homogeneous") agents tend toward consensus collapse. A 2026 study of ten same-model agents found isolated self-correction beat unguided homogeneous debate — [CAIS 2026 paper](https://caisconf.org/program/2026/papers/decomposing-sycophancy-fragility-consensus-collapse-and-cost-in-homogeneous-mult/) (title and abstract excerpt only) **[E]**
- Parallel writers overwrite each other: two agents read the same file, edit independently, and the second write wins; git lock contention appears when agents share one repository — [Augment Code guide](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution) **[P]**
- Stalls and loops: Magentic-One's orchestrator tracks a progress ledger each round (is the request satisfied, is the team looping, is progress being made) and re-plans after a stall counter trips — [Microsoft Agent Framework summaries](https://startdebugging.net/2026/10/what-is-magentic-orchestration-in-microsoft-agent-framework/) (secondhand; original paper arXiv 2411.04468 not fetched) **[S]**

### Inferences
- Failures group into four families a builder can guard against: (1) delegation and specification (vague or overlapping subtasks), (2) context and state loss between agents, (3) termination and verification (stopping early or stopping on an unverified "done"), (4) runaway loops and cost.
- Multi-agent is not a default upgrade. Anthropic's breadth-first research workload benefited, while a Google/MIT study finds added agents hurt sequential and tool-heavy tasks (see the debate section below). Treat the topology choice as a hypothesis to test.

### Gaps
- Could not verify MAST per-mode percentages against the paper's full text.
- Primary arXiv and Cognition pages did not fetch; those findings rely on excerpts.
- No independent replication found for the "up to 70%" degradation figure from the Google/MIT study (see below).

## How does context grow as agents exchange messages, and what mitigates it?

### Takeaway
Context grows with transcript length multiplied by agent count, and long inputs degrade reliability even on simple tasks. The mitigations with the most support are: external state (plans, facts, artifacts stored outside the transcript) with lightweight references passed between agents; summarization at phase boundaries; and fresh subagents with handoffs when a window fills. No controlled head-to-head comparison of these options in multi-agent settings turned up.

### Cited Findings
- Anthropic's lead agent saves its plan to external memory because its context can be truncated past about 200,000 tokens. Agents summarize completed phases and store essentials externally. When limits approach, they spawn fresh subagents with handoffs and retrieve the plan from memory. Subagents write outputs to an external store and return lightweight references — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Cost: agents use roughly 4x chat tokens and multi-agent systems roughly 15x. On BrowseComp, token usage alone explained about 80% of performance variance — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Cognition's first principle is to share full agent traces, not isolated messages. Its second is a dedicated model that compresses action and conversation history into key decisions; the post says this is hard and still hits limits eventually — [Cognition](https://cognition.ai/blog/dont-build-multi-agents) (search excerpt) **[P]**
- Chroma tested 18 LLMs (including GPT-4.1, Claude 4, Gemini 2.5, Qwen3). Reliability became increasingly unreliable as input length grew, even on simple tasks; the position of relevant information and topically close distractors mattered. Secondary blogs quote larger accuracy drops that I could not confirm in the report — [Chroma context rot report](https://trychroma.com/research/context-rot) **[E]**
- Magentic-One keeps a task ledger (facts, plan) and a progress ledger outside the conversation. On a prolonged stall, one port wipes the conversation, rewrites facts and plan, and restarts — [Microsoft Agent Framework summaries](https://startdebugging.net/2026/10/what-is-magentic-orchestration-in-microsoft-agent-framework/) **[S]**

### Inferences
- Pass a structured state object (goal, facts, plan, decisions, artifact IDs with short summaries) rather than replaying full transcripts between agents. This follows from Anthropic's external-store pattern and Chroma's length degradation result.
- Give each agent a narrow view: the inputs its task needs, not the whole run. This is the practical reading of Anthropic's "clear task boundaries" and Cognition's warning that sharing too little causes conflicts. The tension between these two is unresolved in the sources; Cognition's answer is to share full traces.
- Trigger summarization or handoff on a token threshold, not on turn count. Inference from Anthropic's handoff description.

### Gaps
- No controlled comparison of summarization vs. windowing vs. shared state in multi-agent systems was found.
- "Lost in the middle" (Liu et al., 2023) was not retrieved. Only Chroma's position finding is cited here.
- No threshold recommendations for summarization triggers from any framework.

## How do systems prevent conflicting edits when multiple agents write shared files?

### Takeaway
Evidence here is almost entirely practitioner-level. The consistent pattern is: one writer per resource, parallel agents that read and propose, and isolation (separate worktrees or branches) with conflicts surfaced at merge time. I did not find a peer-reviewed study of locks, leases, or compare-and-swap for LLM agent edits.

### Cited Findings
- Anthropic's research system has subagents explore in parallel while a single lead agent synthesizes results and decides what to do next. Subagents return references rather than writing to shared outputs — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Cognition's April 2026 follow-up says parallel-writer swarms still lack meaningful adoption. The narrower pattern that works: multiple agents contribute intelligence while writes stay single-threaded — [Cognition, "Multi-Agents: What's Actually Working"](https://cognition.com/blog/multi-agents-working) (search excerpt) **[P]**
- Git worktrees give each agent its own working directory and branch while sharing one object store. Conflicts move to merge time, where standard tooling detects them — [Augment Code guide](https://augmentcode.com/guides/git-worktrees-parallel-ai-agent-execution); [MindStudio](https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents) **[P]**
- Without isolation, agents that read, edit and write back the same file can silently overwrite each other — [MindStudio](https://www.mindstudio.ai/blog/git-worktrees-parallel-ai-coding-agents) **[P]**

### Inferences
- Single-owner rule: each file or section has one writing agent at a time. Supported by Cognition's single-threaded-writes result. Evidence: practitioner.
- Claim before write: an agent claims a task or path before editing; claims expire on a timeout. No source found for this specific protocol; this is my inference and is untested here.
- Compare-and-swap on writes: reject a write whose base hash no longer matches the file. Inference from the silent-overwrite report; no source found that evaluates it.
- Merge gate: a separate reviewer or verifier agent (or a human) approves merges. Ties to MAST's verification failures, which argue against trusting an agent's own "done" signal.

### Gaps
- No empirical comparison of locks, ownership, or task claiming for LLM agents.
- Did not check framework docs (AutoGen, LangGraph, OpenAI Agents SDK) for built-in file-ownership features; the search was not aimed at those docs.

## What turn limits, budgets, termination conditions, and stuck-detection methods are recommended?

### Takeaway
All major frameworks ship a hard turn or step cap plus pluggable termination conditions. Those caps are coarse: they fire late and do not detect loops. The recommended stack layers a hard cap, a token/time budget, a semantic "goal met" check tied to a verifier, and repeat or no-progress detection with escalation (hint, then block, then stop). Concrete numbers come mostly from Anthropic's prompt guidance and one arXiv excerpt, not from controlled tuning studies.

### Cited Findings
- AutoGen (0.4.x docs): termination conditions are checked after each agent response and see only the messages added since the last check. Built-ins include MaxMessageTermination, TextMentionTermination, TokenUsageTermination, TimeoutTermination, HandoffTermination and ExternalTermination — [AutoGen termination docs, v0.4.9](https://microsoft.github.io/autogen/0.4.9/user-guide/agentchat-user-guide/tutorial/termination.html) **[F]** (search returned version-pinned pages; check the current version)
- LangGraph: default recursion limit is 25 steps; exceeding it raises GraphRecursionError. Third-party sources say to raise it via the `recursion_limit` config key and warn that raising it without fixing the cause just delays an infinite loop — [GitHub docs issue quoting the error](https://github.com/langchain-ai/docs/issues/1121) **[F]** (official docs not fetched; config placement claim unverified)
- OpenAI Agents SDK: the runner raises MaxTurnsExceeded once `max_turns` is passed. Forum reports describe agents that consume the whole budget in a tool-call loop, and catching the exception did not recover the final output — [OpenAI community thread](https://community.openai.com/t/maxturnsexceeded-error-during-agent-tool-execution-unable-to-retrieve-final-output/1370708) **[P]** (official reference not fetched)
- Anthropic embeds effort budgets in subagent prompts: simple fact-finding uses one agent with 3–10 tool calls; direct comparisons use 2–4 subagents with 10–15 calls each; complex research may use more than 10 subagents. The lead agent decides when enough information has been gathered, and guardrails were added against spiraling. The post states no global turn cap — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Anthropic recommends end-state evaluation over turn-by-turn process checks for stateful agents, with checkpoints for complex workflows, and resumable state so a failure does not restart the run — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]**
- Repeat detection: fingerprinting (tool, arguments) pairs catches stuck loops within three repetitions. The paper argues generic iteration caps are too coarse because they fire late and on any repeated tool type — [arXiv 2603.05344](https://arxiv.org/pdf/2603.05344) (search excerpt; terminal coding agents) **[P]**
- Loop shapes to detect: identical repeats, A-B-A-B ping-pong, and no-op results. Escalation can warn the model, abort the run, or progress from hint to block to terminate — [pydantic-deepagents stuck-loop docs](https://cdn.jsdelivr.net/gh/vstorm-co/pydantic-deepagents@main/docs/advanced/stuck-loop-detection.md) **[F]**
- Stalled loops: each call is valid but none advances the task; detect this by tracking progress against the completion condition, not by exact-repeat checks alone — [OneUptime, 2026](https://oneuptime.com/blog/post/2026-09-12-detect-tool-loops-dead-ends-production-agents/markdown) **[P]**
- Magentic-One's stall counter triggers a re-plan. One port defaults round and reset limits to unlimited, which must be set explicitly; I could not verify the default or the threshold against the original paper — [Microsoft Agent Framework summaries](https://startdebugging.net/2026/10/what-is-magentic-orchestration-in-microsoft-agent-framework/) **[S]**
- Vercel AI SDK's loop control stops on any of several step-count or custom conditions — [AI SDK loop control](https://ai-sdk.dev/docs/agents/loop-control) **[F]**

### Inferences (suggested guardrail stack)
1. Hard step cap per run, set by the framework (LangGraph recursion limit, OpenAI max_turns, AutoGen MaxMessageTermination). Treat hitting it as a failure to surface, not a retry trigger.
2. Budget guard on tokens and wall-clock time (AutoGen TokenUsageTermination and TimeoutTermination). Anthropic's token-cost data suggests tokens are the variable to budget.
3. Per-subagent tool-call budget, using Anthropic's 3–10 and 10–15 ranges as starting points, not validated values.
4. Semantic termination: stop on a verifier's pass, not on an agent's claim of completion (MAST verification evidence).
5. Repeat detector: fingerprint (tool, args) and trip at three identical repeats per the arXiv excerpt; add A-B-A-B and no-op checks; escalate hint, then block, then stop.
6. Progress check: every N rounds, ask whether the task-completion condition moved. A stall triggers a re-plan with a bounded number of resets.

### Gaps
- No framework publishes recommended cap values for multi-agent systems.
- Magentic-One's stall threshold and default limits were not confirmed.
- The loop-fingerprinting result comes from a search excerpt; I did not read the paper's method or evaluation.
- No controlled study compares stuck detectors.

## What does the research say about multi-agent debate and collaboration (when it helps, when it hurts)?

### Takeaway
Controlled studies mostly find that multi-agent debate does not reliably beat simple self-consistency or majority voting at equal compute, and that conformity and sycophancy can make it worse. Collaboration helps most where subtasks are parallel and breadth matters (Anthropic's research workload) and hurts on sequential, tool-heavy or tightly coupled work (Google/MIT scaling study). The evidence is strongest for math and logic QA benchmarks; coding and agentic tasks are thinly covered.

### Cited Findings
- Huang et al. (2023, older): on GSM8K with gpt-3.5-turbo, 3 agents over 2 debate rounds beat self-consistency only by a slight margin; with equal numbers of responses, debate significantly underperformed majority-vote self-consistency. Single model and single benchmark — [arXiv 2310.01798](https://arxiv.org/pdf/2310.01798) **[E]** (older)
- Smit et al. (ICML 2024): current multi-agent debate protocols do not reliably outperform self-consistency or ensembling; they are more sensitive to hyperparameters and harder to tune. Some variants, such as Multi-Persona, improved results in some settings — [PMLR/ICML 2024](https://proceedings.mlr.press/v235/smit24a.html) **[E]** (2024)
- "If Multi-Agent Debate is the Answer, What is the Question?" (later retitled "Stop Overvaluing Multi-Agent Debate"): five debate methods across nine benchmarks and four models do not reliably beat Chain-of-Thought or Self-Consistency, even with extra inference compute. Tuning hyperparameters did not reverse this. Model heterogeneity improved existing frameworks — [arXiv 2502.08788v3](https://arxiv.org/html/2502.08788v3) **[E]** (2025)
- "Talk Isn't Always Cheap" (2025): debate can underperform majority voting; agents favor agreement over challenging flawed reasoning; a weaker agent can drag down the group — [arXiv 2509.05396v2](https://arxiv.org/html/2509.05396v2) (search excerpt) **[E]**
- Google Research, DeepMind and MIT (arXiv 2512.08296): a controlled comparison of a single agent against four multi-agent topologies (independent, centralized, decentralized, hybrid) across three model families, with tools, prompts and token budgets held fixed. Coordination overhead trades off against parallel gains. Extra agents hurt most on tool-heavy and sequential tasks; one summary reports drops of up to 70%. A predictive model is claimed to pick the best architecture for 87% of unseen tasks. Versions disagree on configuration count (180 vs. 260) and fit (R² 0.513 vs. 0.373) — [arXiv 2512.08296](https://arxiv.org/abs/2512.08296); [MIT project page](https://www.media.mit.edu/projects/towards-a-science-of-scaling-agent-systems-when-and-why-agent-systems-work/overview/) **[E]** (numbers need checking against the cited version)
- Anthropic's multi-agent research system (Opus 4 lead, Sonnet 4 subagents) beat single-agent Opus 4 by 90.2% on an internal research eval, at roughly 15x chat tokens. Anthropic says it fits breadth-first, parallelizable work and is less effective for tightly interdependent tasks such as coding — [Anthropic](https://www.anthropic.com/engineering/multi-agent-research-system) **[P]** (internal eval, not independently replicated)
- Homogeneous same-model debate underperforms isolated self-correction (2026) — [CAIS 2026](https://caisconf.org/program/2026/papers/decomposing-sycophancy-fragility-consensus-collapse-and-cost-in-homogeneous-mult/) **[E]** (abstract only)
- Sycophancy priors reduced the influence of sycophantic peers and raised accuracy by 10.5% absolute across six open-source models (2026) — [Too Polite to Disagree](https://www.alphaxiv.org/abs/2604.02668) **[E]** (excerpt)

### Inferences
- Default to a single agent with self-consistency or majority voting for verifiable reasoning tasks. Add agents only when subtasks are independent and parallel. This follows from the 2023–2025 debate results plus the Google/MIT finding.
- If you use debate, use diverse models or prompts (heterogeneity), collect independent first-round answers before any exchange, and cap rounds. The "independent first answers" step is my inference from the conformity findings and is untested in the sources here.
- Expect sycophancy in any agent-to-agent critique loop. A reviewer agent should be instructed to find errors and given a rubric, not asked to agree or disagree. The 2026 sycophancy-prior result is the only evidence for this, and it is one paper.

### Gaps
- Most debate evidence uses math and logic QA. Little controlled evidence covers coding, tool use or long-horizon agent work.
- I did not read the 2025 "Can LLM Agents Really Debate?" study (arXiv 2511.07784); only its setup appeared in search results.
- The Google/MIT numbers differ across versions; the 70% figure is from a summary.
- No replication of the Anthropic 90.2% figure outside Anthropic.

## Source quality notes for the report writer
- Strongest evidence: the debate and scaling papers (controlled, multiple models). Also MAST (annotated traces, reported agreement).
- Framework docs establish that caps and termination features exist. They do not show that specific values are right.
- Several claims rest on vendor blogs or search excerpts. Check these before citing numbers: MAST per-mode percentages, the Google/MIT figures, the Magentic-One thresholds, and the LangGraph config placement.
- Older sources: Huang et al. (2023). Everything else is 2024–2026.
