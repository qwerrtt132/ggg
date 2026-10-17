# Agent team: implementation plan

Status: phase 1 and phase 2 done. Phase 3 in progress. Owner: the repo's maintainer.

## Phase 3: capability and durability

| # | Item | Plan |
|---|---|---|
| 1 | Resumable runs | After each turn, save every agent's history, the team's counters, and the pending answer to SQLite. Save the run's config too, without keys. After a restart, a message to an interrupted run rebuilds the team from the saved config and the saved state, then continues. Keys come from the vault, so a memory-only key must be entered again. |
| 2 | Agent tools: files and URLs | Each run gets a workspace folder under the data directory. Members can be given `files` (list, read, write, scoped to the workspace, no path escapes) and `fetch` (http and https only, private and loopback addresses refused). Tools are opt-in per member in the config. |
| 3 | Role templates and team presets | Built-in role templates (researcher, writer, critic, verifier, coder). The team builder can add a member from a template. Teams can be saved as named presets in SQLite and loaded again. |
| 4 | Transcript export | Export a run as Markdown or JSON from the run list. |
| 5 | Packaging | `pyproject.toml` with an `agent-team` command, and `cryptography` as an optional extra. |
| 6 | Streaming model output | Stream text as it arrives, for both adapters. Tool-call arguments are assembled from the stream. Deferred from phase 2. |

### Deliberately not built

- **Running agent-written code.** Executing model output on the user's machine needs real isolation, such as a container with no network and resource limits. Without that it is a serious risk. The plan leaves this out. Revisit it only with an isolation layer chosen on purpose.
- **Web search.** It needs a search provider and its own key. `fetch` covers pages whose URL the agent already knows.

### Tests for phase 3

- Restart: a run's state survives a new process, and a message continues it with the same histories.
- Files: reads and writes stay inside the workspace, and path escapes and symlinks are refused.
- Fetch: non-http schemes and private addresses are refused. A stub server returns a page.
- Presets and templates: save, list, load, delete, and invalid input.
- Export: Markdown and JSON contain the messages, tool results, and final answer.
- Streaming: a stub server that sends server-sent events gives the same reply as a non-streamed call, including tool calls split across chunks.
- Browser: a restarted run can be continued from the page.

Phase 2 scope as built:
- Team builder form with member cards, verifier select, limits, readiness checks (names, roles, models, missing keys) that block Start, and an "Advanced" raw JSON editor. The team draft is kept in the browser's local storage.
- Agent cards with live state (thinking or idle), tokens per agent, and a run-level token meter against the budget.
- Key panel: status per provider (saved, this session, environment, or missing), Test button that makes one tiny request with the team's model, Replace and Remove. Environment keys are reported as yes or no only.
- Continue a finished run by sending a message. Every agent keeps its history. This works only while the server process is alive, because histories are held in memory.
- Feed only auto-scrolls when you are at the bottom. Live-region announcements. Connection state shown in the run status. Ctrl+Enter starts a run.
- Concurrency cap returns HTTP 429 with a clear message.

Deferred to phase 3: streamed model output (needs streaming parsers for both providers), resumable run state across restarts.

Phase 1 notes from implementation:
- The stall check compares short turns exactly and only applies similarity to turns of 80 characters or more. Short messages like "try 1" and "try 2" are different turns.
- Stop returns at once, but the abandoned model call keeps running until the provider finishes or times out.
- Plain text is not delivered, so the README and the agents' prompts both say to use tools.

## Goal

Make agent runs reliable on real models first, then make the app pleasant to use and more capable. Phase 1 fixes what would fail or waste money on a real run. The later phases add features.

## Phase 1: reliability (this change)

| # | Problem found in review | Change |
|---|---|---|
| 1 | Markdown like `**TO writer:**` or `**FINAL:**` is silently ignored, so runs wander | Agents act only through tools: `send_message(to, message)`, `finish(answer)` for the lead, and `submit_verdict(passed, feedback)` for the verifier. Plain text in a reply is not delivered. |
| 2 | A reply with no routing line goes back to its own sender and costs turns | No-tool replies get a nudge. Two in a row end the run as `no_progress`. |
| 3 | Near-duplicate replies only hit `max_turns` | Stall check compares normalized text and tool calls with a similarity ratio, not exact equality. |
| 4 | An answer cut off by the output limit looks complete | Both adapters report truncation. The agent is told to keep messages shorter, and the UI marks the turn. |
| 5 | A rate limit or 5xx fails the whole run | Retries with backoff for 429, 5xx, and network errors. `Retry-After` is honored. |
| 6 | Every agent resends its full history, so cost grows fast | A history window keeps the first item and the recent tail, cut at a user message so tool results keep their calls. |
| 7 | Stop waits for the current model call, up to 120 seconds | Model calls run on a worker thread. Stop returns at once and abandons the call. |
| 8 | Unlimited concurrent runs | At most `max_active_runs` (default 3) running at once. Extra starts get HTTP 429. |

### Design decisions

- **Tool calls, not text parsing.** Models follow tool schemas more reliably than they follow a text format. Each provider adapter converts one neutral history format to its own wire format.
- **Neutral history.** Items are `user` (text), `assistant` (text plus tool calls), and `tool` (result for one call). The Anthropic adapter merges consecutive user items because its API requires strict alternation.
- **Errors go back to the model.** A failed tool call returns an error result, and the agent is woken to fix it. Invalid JSON arguments are reported the same way.
- **Abandoned calls.** A call stopped mid-flight still bills tokens on the provider's side. The UI should say so in phase 2.

### Out of scope for phase 1 (later phases)

- Phase 2: team builder form, agent status cards, continuing finished runs, key test button, live token and cost meter, streamed output, feed scroll fix, connection indicator.
- Phase 3: agent tools (web, workspace files, sandboxed code), skill templates, presets, transcript export, resumable run state, Playwright UI tests, packaging.
- Not planned yet: a shared task board and typed artifacts from the research report. These need a decision on how much structure the team really needs.

### Tests

- Team logic with scripted tool-call replies: routing, errors, nudges, stalls, verifier gate, budgets, stop, human messages, truncation nudge.
- Adapters: request shape for both providers, tool-call parsing, malformed arguments, history conversion, retries (429 then success, and non-retryable 400).
- Context window: short histories unchanged, long histories keep the goal and a tail that starts at a user message.
- Server: end-to-end run through a stub provider that answers with a `finish` tool call, and the concurrency cap.

### Risks

- Some OpenAI-compatible servers do not support tool calling. Those runs fail with the provider's error. The README will say so.
- The history window can drop context the lead still needs. Phase 2 can add summaries.
- The stop path leaves a worker thread running until the provider's own timeout ends the call.

## Acceptance for phase 1

- All tests pass.
- A scripted run covers: the verifier rejecting then accepting an answer, the no-tool nudge, a truncated reply, a 429 retry, a stop during a slow call, and the concurrency cap.
- README describes the tool protocol and the new run statuses.
