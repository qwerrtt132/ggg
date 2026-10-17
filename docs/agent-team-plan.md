# Agent team: implementation plan

Status: phase 1 in progress. Owner: the repo's maintainer. Last updated with the phase 1 start.

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
