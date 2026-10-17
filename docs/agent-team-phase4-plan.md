# Agent team: phase 4 plan

Status: planned, not started. Based on [the research report](../reports/Agent%20team%20tools%20research.md) and the [review of phases 1 to 3](../reports/Agent%20team%20tools%20research.md#6-what-this-means-for-us). Owner: the repo's maintainer.

Phase 4 does two things. It fixes the issues from the review, and it adds the four capabilities the research showed matter most for running agents in a way people can operate: per-step snapshots with rewind, approvals with patch-level review, a trace view, and re-planning on stall.

## Order of work

Each milestone ends with passing tests and a commit. Bugs come first, and every bug gets a test that fails before the fix.

### M1: Fix the review findings (bugs first)

| # | Finding | Fix | Test that must fail first |
|---|---|---|---|
| 1 | After a stop, the abandoned call still reports live text, which is stored for a finished run | Each model call gets a turn number. Callbacks from earlier turns are dropped. | A stop followed by a late update adds no live text after the run ends. |
| 2 | Writing to a folder path, or content with a lone surrogate, fails the whole run | The tool wrapper catches `OSError` and `UnicodeError` and returns an error result. | Both inputs return an error result and the run continues. |
| 3 | Two messages sent at once to a finished run can start two threads on one team | A per-run lock around resume. The second request sees the run as running and queues its message. | Two simultaneous messages result in one continuation. |
| 4 | `max_seconds` is checked only between turns; one call can exceed it by minutes | Pass the remaining time into the call. Stop waiting when it runs out and end the run as `timeout`. | A slow call ends at the deadline, not after it. |
| 5 | The run cap is check-then-act | Same lock as item 3. | Concurrent starts never exceed the cap. |
| 6 | Finished runs stay in memory for the life of the process | Drop finished runs from memory; rebuild from the database when continued (the restore path already does this). | Memory for finished runs is released; continuing still works. |
| 7 | A stream that fails after output has started is retried, so the first attempt's tokens are billed again | Retry only if no output has been received. After that, fail with a clear message. | A stream that breaks mid-reply is not retried. |
| 8 | A second server on the same data directory marks the first server's runs as interrupted | A lock file in the data directory. A second server refuses to start with a clear message. | Starting a second server on the same directory fails. |
| 9 | The CLI prints provider errors without redaction | Redact with the keys in use, as the server does. | A key in a CLI error is replaced. |
| 10 | Token budgets are optional, so a custom config can run without a cost cap | Default `max_tokens` when the config has none, documented in the README. | A config with no budget gets the default. |

Exit criteria: all ten items closed, 80 or more tests passing, and no regression in the browser tests.

### M2: Snapshots per turn, rewind, and branch

Why: LangGraph saves a snapshot every step, which gives rewind, branch, and a correct resume. We keep only the latest snapshot, so resume is correct but we cannot rewind.

- **Storage:** a `checkpoints` table: run id, turn number, time, a snapshot of every agent's history and the token count, and the pending message queue. A snapshot is about the size of the history, so keep the last N turns per run (default 50) and drop the oldest.
- **Rewind:** `POST /api/runs/<id>/rewind {turn}` restores the snapshot for that turn, marks later events as superseded in the feed, and continues from there. The events are not deleted, so history stays honest.
- **Branch:** the same call with `branch: true` creates a new run that starts from the checkpoint, with a link to its parent. Config and workspace are copied.
- **UI:** a timeline under the feed with one dot per turn. Clicking a dot shows the state at that turn. Rewind and Branch buttons appear after selecting a turn. A confirmation step is required before rewinding, as the LangGraph guide recommends.

Exit criteria: a test that rewinds and continues and checks the history; a test that branches and checks the parent is unchanged; a browser test that rewinds from the timeline.

### M3: Approvals and patch-level review

Why: Cline and Cursor show that people want to approve actions and review results as diffs with per-hunk control.

- **Approval mode per member:** `approve: ["write_file", "fetch_url"]` in the member config. When a member calls an approved tool, the call waits. The page shows the pending call with its arguments and Approve, Edit, or Reject buttons. A reject returns an error result to the model, with the reason.
- **Workspace diff:** before a run ends, the workspace is compared with its state at the start. Changes are listed per file as added, changed, or removed, with a diff for each text file.
- **Accept per file:** the user accepts or rejects each file. Accepted files stay in the workspace; rejected ones are reverted. Export includes only accepted files.
- **Safe defaults:** approvals are off for read-only tools and on for writes if the team is created from a template. Presets can change this.

Exit criteria: a test where a write waits for approval and is rejected, and the model sees the reason; a test that a rejected file is reverted; a browser test that approves one call and rejects one file.

### M4: Trace view, message graph, and step mode

Why: the research shows that people operate multi-agent runs by looking at a timeline of spans, not a flat chat log.

- **Trace tree:** a run is a tree: run, then turn (one per agent), then model call and tool calls. Each node shows its duration and tokens. Built from events we already record, plus start and end times on each turn. The names follow the OpenTelemetry GenAI operation names (`invoke_agent`, `chat`, `execute_tool`) so exports can be mapped later. The spec's stability is unverified, so the names are labels only.
- **Trace export:** the tree as JSON with those names, for other tools to read.
- **Message graph:** a small SVG on the run page, with one node per agent and an arrow per message, weighted by count. Read-only.
- **Step mode:** a toggle that pauses the run after each turn. The page shows Continue and Stop. Implemented as a pause event the engine waits on between turns.

Exit criteria: a test that the trace tree matches a scripted run; a test that step mode pauses after one turn; a browser test that continues one step at a time.

### M5: Re-plan on stall, not stop

Why: Magentic-One checks a progress ledger each step and re-plans when progress stalls. We stop the run. A stop ends useful work; a bounded re-plan keeps it going.

- **Progress ledger:** after each turn, the engine records a short ledger: turn count, whether the last turns made new progress (new facts, new files, new messages), and whether the team is repeating itself. This is computed from events, not a model call, so it costs nothing.
- **Re-plan:** when the ledger shows a stall, the lead gets one message: the goal, the ledger, and an instruction to write a new plan. The team continues. Re-plans are capped (default 2 per run), and after the cap the run ends as `stalled`, as now.
- **Task ledger:** the lead's first turn must return a plan, stored in the run state and shown in the UI. Re-plans update it.

Exit criteria: a scripted stall that re-plans once and then completes; a scripted stall that exhausts the cap and ends as `stalled`; the task ledger appears in the snapshot.

### M6: Operations

- **Health and logs:** `GET /api/health` (version, database writable, active runs). Structured log lines to a file in the data directory, with keys redacted.
- **Config schema version:** a `"version"` field in team configs and presets, with a migration that accepts version 1 and rejects unknown versions with a clear message.
- **Dashboard:** runs listed with status, duration, tokens, and an estimated cost from per-model prices set in the config. Filter by status and search by goal.
- **Packaging check in CI:** build the wheel, install it into a clean folder, and run the default team's config check. The package check that I ran by hand becomes a test.
- **Security review round 2:** a checklist from the review (fetch redirect handling, lock file, token handling in exports), with a test for each item.

Exit criteria: health endpoint test; config version tests; a dashboard browser test; the package check in the test suite.

## Not in phase 4

- Running agent-written code. It still needs a sandbox. OpenHands runs each conversation in a container, which is the model to follow when we take this on.
- A visual canvas for building teams. The research shows canvas tools are useful for new users, but the form and JSON editor cover our needs for now.
- A worker queue. One user, one process: threads are enough.
- Changes to the verifier and the core engine's message protocol. Both work and have tests.

## Risks

- **Snapshot size.** Long runs with many turns will make checkpoints large. The cap on stored checkpoints (M2) limits this, but it should be measured on a real run before the default is set.
- **Approval waits.** A run that waits for approval holds a thread. The timeout (M1 item 4) must cover waiting time too, or a forgotten approval blocks a run for ever.
- **Re-plan loops.** Re-planning can repeat the same mistake. The cap and the progress ledger limit this, but it is the riskiest part of M5 and should get its own review.
- **Unverified sources.** The OpenTelemetry names, the Magentic-One thresholds, and the Langflow and Dify internals are unverified (see the research report). M4 and M5 should be checked against primary sources before they ship.

## Acceptance for phase 4

- All M1 items closed with a test that failed first.
- Rewind, branch, approvals, the diff review, the trace view, step mode, and re-plan each have unit tests and at least one browser test.
- The review's issues list is closed, and the next review lists only new findings.
