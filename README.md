# ggg

## Agent team

`agent_team` runs a team of AI agents that message each other toward a goal. Each agent can use a different provider and model. You bring your own API keys. You can run it from a web page, or from the command line.

Supported providers:
- `anthropic`: the Anthropic Messages API
- `openai_compatible`: any server with a `POST {base_url}/chat/completions` endpoint that supports tool calling, such as OpenAI, DeepSeek, Groq, or a local Ollama server

### Install

Python 3.10 or newer.

```bash
pip install .                 # provides the `agent-team` command
pip install ".[ui-tests]"     # also installs Playwright for the browser tests
```

Without installing, run it from the repository with `python -m agent_team`.

### Web app

```bash
agent-team serve              # http://127.0.0.1:8765, data in ./data
```

The command prints a link with a session token. Open it in your browser. The page lets you:
- Build the team with a form: member names, roles, providers, models, tools, a verifier, and limits. Start stays disabled until the team is ready, and the page says what is missing. The raw JSON is under "Advanced".
- Add a member from a role template (researcher, writer, critic, verifier, coder).
- Save a team as a preset, and load it again later.
- Test a key with one tiny request, using the model of a team member that uses that provider.
- Watch messages, agent states, and token use stream in live, with a meter against the budget.
- Send the lead a message during a run, or stop the run.
- Continue a finished run by sending a message. This works after a server restart too, because the team's histories are saved after every turn.
- Export a run as Markdown or JSON.
- Look over past runs on a dashboard: status, tokens, estimated cost, and duration. Filter by status, or search the goal. Cost needs prices in the config (see Config).
- Go back in time. The Turns row lists a checkpoint for every turn. Select one and write a message:
  - **Rewind here** puts the run back at that turn and continues with your message. It needs a finished run, and it asks you to confirm. Later turns are dropped from the checkpoints, but their events stay in the history.
  - **Branch from here** starts a new run from that turn. The original run is unchanged, and the new run gets a copy of its workspace.
  - Rewinding does not undo files the agents already wrote. Reviewing and reverting file changes is planned with approvals.

Keys: enter them in the API keys panel. They stay on the server and are never shown in full. They live in memory unless you tick "save encrypted". Saved keys are encrypted with a master key at `~/.config/agent_team/master.key` (override with `AGENT_TEAM_MASTER_KEY_FILE`). Back up that file. Without it, saved keys cannot be read and must be entered again. A key can also come from an environment variable named in the provider's config (`api_key_env`), and the page only shows whether that variable is set.

Configs never hold keys. A config with a key field is refused.

The server listens only on 127.0.0.1 and rejects requests from other hosts or origins. `data/` holds run history, presets, workspaces, and any saved keys. It is git-ignored.

### Command line

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
agent-team run agent_team/default_team.json "Write a one-page guide to our release process" -v
```

`-v` prints each message between agents to stderr. The final answer goes to stdout. Copy `agent_team/default_team.json` to edit a team.

### Config

A team config is JSON with `"version": 1`, the only version this app reads. A config with another version is refused with a message. A config without a version is read as version 1.

Prices are optional. They are per million tokens, keyed by provider and model. The numbers below are examples, so use your provider's current price list:

```json
"prices": {
  "anthropic/claude-haiku-5-5": { "input_per_million": 1.00, "output_per_million": 5.00 }
}
```

Cost is input tokens times the input price, plus output tokens times the output price, summed over the run's turns. Turns from a rewound part of a run still count, since they were paid for. A run whose models have no price shows "no price set" in the dashboard. The figure is an estimate from these numbers, not billing data.

### Team members and tools

Each member has a name, a role, a provider, a model, and optional `tools`:
- `files`: list, read, and write files in the run's workspace. Paths outside the workspace and symbolic links are refused.
- `fetch`: read an http or https page as text. Private, loopback, and link-local addresses are refused, including on redirects. Each connection goes to the address that was checked, so a second DNS lookup cannot send it elsewhere.

A run's workspace lives in `data/workspaces/<run id>/`.

Approvals: a member's `approve` list names the tools whose calls wait for you. The page shows the call with its arguments. You can approve it, edit the arguments and approve, or reject it with a reason, which the agent receives. Stopping the run or reaching the time limit ends a wait without running the call.

Trace: each turn is a span holding its model call and its tool calls, with durations, tokens, and errors. Download it as JSON. The span names follow the OpenTelemetry GenAI operation names (`invoke_agent`, `chat`, `execute_tool`) as labels only; the export format is not a verified standard.

Step mode: set "step_mode": true in the config, or tick the box in the team editor, or toggle it during a run. The run pauses after every turn until you press Next step. A pause counts toward the time limit.

Who talks to whom: a small graph shows how many messages each agent sent to each other agent in the run.

Review: changes to a workspace are listed per file, with a diff for text. Accept a file to keep it, or reject it to restore the accepted version. Nothing is final until you accept it. "Download accepted files" gives a zip of accepted content only. Review is available once the run has finished or been stopped.

Running code is not available. Model-written code would run on your machine, so it needs a container or similar isolation first.

### How a team works

- The lead receives the goal. Agents take turns in the order their messages arrive.
- Agents act only through tools. Plain text in a reply is not delivered to anyone.
  - `send_message(to, message)`: every agent. The reply arrives later as a new message.
  - `finish(answer)`: the lead only. Ends the run with the answer for you.
  - `submit_verdict(passed, feedback)`: the verifier only.
- If the config names a `verifier`, the lead's `finish` goes to that agent first. The run ends only when it passes the answer. Otherwise the lead gets the feedback and revises.
- An agent that used a file or fetch tool gets another turn to act on the result.
- An agent whose reply has no tool call is told to use a tool. Two such replies in a row end the run.
- The lead can write the team's plan with `update_plan`. The plan is saved with each checkpoint.
- A run is stalled when the same turns keep repeating, or when several turns in a row produce nothing new (a call that succeeds with arguments the run has not used before counts as new). A stalled run is not ended straight away. The lead gets a progress ledger (goal, reason, the current plan, recent messages) and is asked for a new plan. Each run gets `max_replans` of these (default 2, from 0 to 5). After that the run ends as `stalled`. `stall_turns` sets how many idle turns count as a stall (default 4, from 2 to 20).
- The model's output limit can cut a reply short. The agent is told to keep messages shorter, and the feed marks the turn.
- Rate limits and server errors are retried with backoff (up to 4 attempts, honoring `Retry-After`).
- Only the most recent 16 history items are sent to the model, so long runs cost less.
- At most 3 runs execute at once. Stop takes effect immediately, even during a model call.
- A config without `max_tokens` gets a budget of 200,000 tokens. Set `"max_tokens": null` to run with no budget. The time limit covers the whole run, including a call that is in progress.
- A stream that fails before any output is retried. One that fails after output has started is not, so no tokens are billed twice. A stream that ends early is reported as an error, not as a short reply.
- Limits stop runaway runs, and the run status says which one applied: `max_turns`, `max_seconds` (`timeout`), `max_tokens` (`token_budget`), a repeated turn (`stalled`), `no_progress`, `stopped`, `failed`, `idle` when no one has anything left to do, or `interrupted` after a restart.

### Operations

- `GET /api/health` (with the session token) reports the version, whether the database answers, and how many runs are active. It returns 503 when the database does not answer.
- `data/agent_team.log` is a log with one JSON object per line. A line is written when a run finishes, with its status and tokens, and when a run fails. Keys are replaced with `[redacted]` before a line is written. When the log passes 5 MB it moves to `agent_team.log.1`, which replaces any earlier copy, so at most two files are kept.

### Tests

```bash
python -m unittest discover -s tests
```

The tests use scripted replies and local stub servers, so they don't call any real API. The browser tests need Playwright and a Chromium binary, and skip themselves when either is missing. Set `AGENT_TEAM_CHROME` to choose the Chromium binary. The package test builds a wheel with pip, installs it into an empty folder, and checks the default team from there.

GitHub Actions runs the suite on Python 3.10 and 3.13 (`.github/workflows/tests.yml`). It installs Chromium, and it fails if any test is skipped, so the browser tests cannot quietly stop running.

### Known limits

- Only one server can use a data directory at a time. The lock is an OS file lock. It is not enforced on Windows, where you must run one server per data directory yourself.
- Replies stream. The page shows the end of each agent's reply while it arrives. That live text is not saved, so after a reconnect it shows only what is streaming at that moment. The saved feed shows the full message once the turn ends.
- Cost figures are estimates from the prices in the config. A config with a version other than 1 is refused when it is saved as a preset and when a run starts.
- The `openai_compatible` provider needs a server with tool calling. Chat-only servers fail. Streaming also asks for token usage with `stream_options`. A server that rejects that option will fail the request.
- The fetch tool checks every address a name resolves to, then connects to one of the checked addresses. It does not look the name up again, so a name that changes its address is not followed. Fetch still reads any public page an agent asks for, so do not treat it as a content filter.
