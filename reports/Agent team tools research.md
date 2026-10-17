# Multi-agent tools: how they are designed, built, and operated

Research date: 2026-10-17. Scope: open tools and products for running several AI agents together, with a UI: AutoGen Studio, OpenHands, CrewAI Crew Studio, Langflow / Dify / Flowise, LangGraph Studio, Magentic-One, coding agents (Cline, Cursor), and the OpenAI Agents SDK. The question was how each is designed, how it works inside, how the UI makes it usable, and how the backend is built.

Confidence labels: **[primary]** read from the project's own repository or docs; **[secondary]** blog, directory, or aggregator; **[unverified]** could not be checked.

## 1. The tools

### AutoGen Studio (Microsoft / AG2)
- A no-code web UI for building and debugging multi-agent workflows. It has a playground for running sessions, a builder for agent specs, and a gallery of components. It shows agent messages and metrics through a profiler. **[primary: EMNLP 2024 paper, summarized in [Microsoft Research](https://www.microsoft.com/en-us/research/publication/autogen-studio-a-no-code-developer-tool-for-building-and-debugging-multi-agent-systems/)]**
- Agents are described in a declarative JSON spec, exposed through a web API, a Python API, and a CLI. **[secondary: [Victor Dibia's guide](https://newsletter.victordibia.com/p/autogen-studio-a-no-code-user-interface)]**
- Layers: a Core runtime (message passing, event-driven agents, local and distributed runtimes), an AgentChat layer (two-agent and group chat), and an Extensions layer (model clients, code execution). **[primary: [AutoGen repository](https://github.com/microsoft/autogen)]**
- **Status: archived.** The repository says Microsoft no longer provides fixes or features and recommends Microsoft Agent Framework. **[primary]**

### OpenHands (formerly OpenDevin)
- An agent platform with three parts: an agent abstraction, an **event stream** that records every action and observation in order, and a **runtime** that executes actions in a Docker sandbox. Agents read the event history and produce the next action. **[primary: [arXiv 2407.16741](https://arxiv.org/pdf/2407.16741)]**
- The current stack is split. A frontend (the "agent canvas") talks to an **Agent Server**, a REST API that runs several agents on one machine and owns agents, tools, conversations, workspaces, and events. A TypeScript client sits between them. An automation server handles schedules, webhooks, and run history. **[primary: [OpenHands repository](https://github.com/All-Hands-AI/OpenHands)]**
- Isolation is optional per conversation: `OH_CONVERSATION_RUNTIME=docker` gives each conversation its own container. Workspace files and state are mounted so they survive container replacement. The UI binds to 127.0.0.1 by default. **[primary: same repository]**
- The live-update transport and event schema are in the separate Agent SDK repository, which I did not read. **[unverified]**

### CrewAI Crew Studio
- A visual builder layered on the CrewAI framework. Agents and tasks are nodes on a canvas, flanked by three panels: streaming AI reasoning, the canvas, and draggable components. A chat panel can build parts of the workflow, and the canvas and chat share one state. **[primary: [Crew Studio docs](https://docs.crewai.com/en/enterprise/features/crew-studio)]**
- An Execution view shows an event timeline and logs, and runs can be tested locally before publishing. Workflows can be exported as a ZIP, a React component, or an MCP server. **[primary for the features; the timeline detail is from the docs page]**
- Two execution models: Crews (autonomous agents inside a task) and Flows (event-driven control with persisted state). **[secondary: [yespress review](https://yespress.io/crewai)]**

### Langflow, Dify, Flowise
- **Langflow**: a visual editor (React Flow, per the repository tags) for Python components. It has a desktop app, a local install, Docker, and cloud options. It has an interactive playground with step-by-step control and exports flows as JSON or as an API or MCP server. The internal backend and streaming mechanism are not described on the repository page. **[primary for features; internals unverified]** ([repository](https://github.com/langflow-ai/langflow))
- **Dify**: a full application platform with a worker model (Celery and Redis) so long flows run asynchronously and status can be polled. **[secondary: [comparison](https://futureagi.com/blog/dify-vs-flowise-vs-langflow-2026/); not verified against the repository]**
- **Flowise**: a Node.js canvas over LangChain components, with enterprise features such as RBAC and rate limiting. **[secondary]**
- Licensing differs between sources. Check each repository before choosing. **[secondary]**

### LangGraph Studio and checkpoints
- LangGraph saves a **checkpoint of the graph state at every step**, grouped into threads. Any checkpoint can be inspected and resumed from, which gives time travel: rewind, change state, and continue down another path. **[primary: [LangChain docs](https://docs.langchain.com/oss/javascript/langchain/frontend/time-travel)]**
- Human-in-the-loop uses interrupts. Execution stops before or after a step, a person changes state or answers, and execution resumes from the saved checkpoint. The persistence layer is what makes this work. **[primary: [persistence docs](https://docs.langchain.com/oss/python/langgraph/persistence.md)]**
- Resuming from an old checkpoint replaces the current path. The guide recommends a confirmation step. **[primary]**
- Studio's debugging UI shows the timeline of checkpoints, and checkpoints that hold an interrupt get distinct visual treatment. **[secondary: [Analytics Vidhya](https://www.analyticsvidhya.com/blog/2025/06/langgraph-studio/)]**

### Magentic-One
- A lead **Orchestrator** plans, delegates, and tracks progress. Four specialists (web browsing, file reading, coding, and a terminal) report to it. The specialists do not talk to each other. **[primary for the team structure: [autogen_ext source](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-ext/src/autogen_ext/teams/magentic_one.py)]**
- Two **ledgers**. The **task ledger** holds given facts, facts to look up, facts to derive, educated guesses, and a plan. The **progress ledger** is checked each step: is the request satisfied, is the team looping, is progress being made, who speaks next, and what to ask them. When progress stalls, the orchestrator re-plans. **[secondary: [agentpatterns.ai](https://agentpatterns.ai/patterns/multi-agent/magentic-orchestration/)]**
- The ledger logic is not in the file I read; it lives in the base class. The stall thresholds and default limits are **[unverified]**. The paper (arXiv 2411.04468) could not be fetched from this environment.

### Coding agents: Cline, Cursor, and others
- **Cline** shows a proposed edit as a diff and waits for approval before it acts. It separates planning from execution. **[secondary: [Ryan Walker's review](https://rywalker.com/research/cline)]**
- Design principle from the same sources: approving a single command is different from accepting the final patch. Review should happen at the patch level, with **per-hunk accept or reject** and a summary of touched files. Apply-all with no per-hunk control is the main complaint on multi-file runs. **[secondary: [glossary](https://aiuxplayground.com/glossary/diff-patch-review)]**
- Cursor's default flow lets edits land before review, and users have asked for a plan-then-approve mode. **[secondary: forum request]**

### OpenAI Agents SDK
- Core primitives: agents, handoffs, guardrails, sessions, and tracing. Handoffs appear to the model as a tool named `transfer_to_<agent>`. Input guardrails run on the first agent, output guardrails on the last, and tool guardrails on each tool. **[secondary: [third-party guide](https://claw.aguidetocloud.com/openai/agents-sdk/concepts/); the SDK's own handoff doc is primary: [handoffs.md](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md)]**
- Tracing is on by default. Every run, model call, tool call, handoff, and guardrail check is a span. Sessions store conversation history in SQLite by default. **[secondary]**

## 2. How they are built

| Concern | Common approach | Our app today |
|---|---|---|
| Event backbone | Append-only log of actions and observations (OpenHands event stream, LangGraph checkpoints, AutoGen messages) | Append-only `events` table with sequence numbers. Good. |
| State recovery | A snapshot per step, grouped into threads, so any point can be reopened (LangGraph) | One latest snapshot per run. No history of snapshots. **Gap.** |
| Execution isolation | Docker or similar per conversation (OpenHands) | No code execution. File tools in a folder per run. |
| Live transport | SSE or socket.io from a REST backend; the page replays the log and then follows it | SSE with replay from the event table, plus transient live text. Matches the common pattern. |
| Separation of frontend and backend | Frontend, API server, and a client library (OpenHands); React Flow canvas (Langflow) | Single process serving a plain-JavaScript page. Simpler to run. Fine at this size. |
| Workers | Celery and Redis for long flows (Dify, secondary) | Threads inside one process. Fine for one user. |
| Configuration | Declarative JSON specs, exportable (AutoGen, CrewAI) | Team JSON, presets, and templates. No schema version. **Gap.** |
| Tracing | OpenTelemetry-style spans for agent runs, model calls, and tools (OpenAI SDK, OTel GenAI conventions) | Flat event feed. No durations or tree. **Gap.** |

On the OpenTelemetry GenAI conventions: the spec defines span types for agent invocation, model calls, and tool execution, with attributes such as token usage. The spec has moved to a new repository, and I could not confirm its stability status. **[unverified]**

## 3. Functions that matter

- **Plan and delegate:** a lead splits the goal and assigns work (Magentic-One, our lead).
- **Check progress:** a progress ledger each step asks whether the goal is met and whether the team is looping. Ours only counts identical turns.
- **Re-plan:** on a stall, the orchestrator rewrites the plan instead of stopping. Ours stops the run.
- **Verify:** a verifier or output guardrail checks the answer before it is delivered. We have this.
- **Approve:** actions that change things wait for a person (Cline, Cursor plan mode, LangGraph interrupts). We have none for writes or fetches.
- **Rewind and branch:** resume from an earlier checkpoint with a changed input (LangGraph). We cannot.
- **Trace:** show the run as a tree of spans with durations and tokens. We show a flat list.
- **Pause and step:** run one turn at a time (Langflow playground). We have stop only.

## 4. How the UI keeps it usable

Patterns that recur across these tools:
1. **Three-panel layout:** reasoning or messages, the structure (canvas or roster), and the tools or components (Crew Studio, Studio).
2. **Timeline with checkpoints:** the run is a list of steps; clicking one shows the state at that point, and interrupts stand out (LangGraph Studio).
3. **Review at the patch level:** per-file and per-hunk accept or reject, with a summary of what changed (Cline, Cursor requests).
4. **Live status per agent:** who is working, on what, and how many tokens it has used.
5. **Stop and step controls:** stop at once, and optionally step through one turn at a time.
6. **Plain setup:** one command, a local-only bind, and a default that needs no account (OpenHands, Langflow).

## 5. Backend shape

- A REST API for commands (start, stop, send, approve) and an event stream for updates. The client replays from a cursor, so a reconnect loses nothing. Our SSE design already matches this.
- An append-only log as the source of truth, with snapshots for fast resume. LangGraph stores a snapshot per step, not just the latest.
- Sandboxes per run when code runs (OpenHands). We have not built code execution.
- Long work on background threads or workers, with cancellation and timeouts. Ours matches, but limits are not enforced during a call.
- Config as versioned data, with secrets kept apart from it. Ours keeps secrets apart; versioning is missing.

## 6. What this means for us

The strongest lessons, in order:
1. **Snapshot per step, not just the latest.** This gives us rewind, branch, and a correct resume. It is the largest structural gap.
2. **Approvals at the action level and review at the patch level.** Writes should wait for a person, and the result should be a diff with per-file accept.
3. **A trace view.** The events already carry what is needed for a timeline, with turn durations and tokens.
4. **Re-plan on stall, instead of stopping.** The progress ledger idea gives the lead something to act on, and a bounded number of re-plans keeps cost under control.
5. **Step mode and a message graph** as cheap operational aids.

Things to avoid: a full visual canvas (costly, and we do not need it yet), a worker queue (one user, one process), and code execution without a sandbox.

## Sources

- [AutoGen Studio, Microsoft Research publication](https://www.microsoft.com/en-us/research/publication/autogen-studio-a-no-code-developer-tool-for-building-and-debugging-multi-agent-systems/)
- [AutoGen Studio, Victor Dibia guide](https://newsletter.victordibia.com/p/autogen-studio-a-no-code-user-interface)
- [AG2 AutoGen Studio docs](https://docs.ag2.ai/docs/autogen-studio/getting-started)
- [AutoGen repository (archived)](https://github.com/microsoft/autogen)
- [OpenHands paper, arXiv 2407.16741](https://arxiv.org/pdf/2407.16741)
- [OpenHands repository](https://github.com/All-Hands-AI/OpenHands)
- [CrewAI Crew Studio docs](https://docs.crewai.com/en/enterprise/features/crew-studio)
- [yespress CrewAI review](https://yespress.io/crewai)
- [Langflow repository](https://github.com/langflow-ai/langflow)
- [Dify vs Flowise vs Langflow comparison](https://futureagi.com/blog/dify-vs-flowise-vs-langflow-2026/)
- [LangGraph time travel docs](https://docs.langchain.com/oss/javascript/langchain/frontend/time-travel)
- [LangGraph persistence docs](https://docs.langchain.com/oss/python/langgraph/persistence.md)
- [Analytics Vidhya, LangGraph Studio](https://www.analyticsvidhya.com/blog/2025/06/langgraph-studio/)
- [Magentic-One team source, autogen_ext](https://raw.githubusercontent.com/microsoft/autogen/main/python/packages/autogen-ext/src/autogen_ext/teams/magentic_one.py)
- [agentpatterns.ai, Magentic orchestration](https://agentpatterns.ai/patterns/multi-agent/magentic-orchestration/)
- [Ryan Walker, Cline review](https://rywalker.com/research/cline)
- [Diff and patch review glossary](https://aiuxplayground.com/glossary/diff-patch-review)
- [OpenAI Agents SDK, handoffs doc](https://github.com/openai/openai-agents-python/blob/main/docs/handoffs.md)
- [OpenAI Agents SDK concepts, third-party guide](https://claw.aguidetocloud.com/openai/agents-sdk/concepts/)
- [OpenTelemetry GenAI spans, moved repository](https://raw.githubusercontent.com/open-telemetry/semantic-conventions/main/docs/gen-ai/gen-ai-spans.md)

## Not verified
- The primary OpenTelemetry, arXiv, and OpenAI documentation sites did not resolve from this environment. GitHub did.
- Langflow's and Dify's internals were not read from source.
- The Magentic-One stall thresholds and default limits.
- OpenHands' event schema and live transport (in the Agent SDK repository).
