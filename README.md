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

### Team members and tools

Each member has a name, a role, a provider, a model, and optional `tools`:
- `files`: list, read, and write files in the run's workspace. Paths outside the workspace and symbolic links are refused.
- `fetch`: read an http or https page as text. Private, loopback, and link-local addresses are refused, including on redirects.

A run's workspace lives in `data/workspaces/<run id>/`.

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
- The model's output limit can cut a reply short. The agent is told to keep messages shorter, and the feed marks the turn.
- Rate limits and server errors are retried with backoff (up to 4 attempts, honoring `Retry-After`).
- Only the most recent 16 history items are sent to the model, so long runs cost less.
- At most 3 runs execute at once. Stop takes effect immediately, even during a model call.
- A config without `max_tokens` gets a budget of 200,000 tokens. Set `"max_tokens": null` to run with no budget. The time limit covers the whole run, including a call that is in progress.
- A stream that fails before any output is retried. One that fails after output has started is not, so no tokens are billed twice. A stream that ends early is reported as an error, not as a short reply.
- Limits stop runaway runs, and the run status says which one applied: `max_turns`, `max_seconds` (`timeout`), `max_tokens` (`token_budget`), a repeated turn (`stalled`), `no_progress`, `stopped`, `failed`, `idle` when no one has anything left to do, or `interrupted` after a restart.

### Tests

```bash
python -m unittest discover -s tests
```

The tests use scripted replies and local stub servers, so they don't call any real API. The browser tests need Playwright and a Chromium binary, and skip themselves when either is missing. Set `AGENT_TEAM_CHROME` to choose the Chromium binary.

### Known limits

- Only one server can use a data directory at a time. The lock is an OS file lock. It is not enforced on Windows, where you must run one server per data directory yourself.
- Replies stream. The page shows the end of each agent's reply while it arrives. That live text is not saved, so after a reconnect it shows only what is streaming at that moment. The saved feed shows the full message once the turn ends.
- The `openai_compatible` provider needs a server with tool calling. Chat-only servers fail. Streaming also asks for token usage with `stream_options`. A server that rejects that option will fail the request.
- The fetch tool checks an address before connecting. A name whose address changes between the check and the connection could still reach a private address. Treat fetch as reading public pages, not as a hardened proxy.
