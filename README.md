# ggg

## Agent team

`agent_team` runs a team of AI agents that message each other toward a goal. Each agent can use a different provider and model. You can run it from a web page, where you enter your own API keys, or from the command line.

Supported providers:
- `anthropic`: the Anthropic Messages API
- `openai_compatible`: any server with a `POST {base_url}/chat/completions` endpoint, such as OpenAI, DeepSeek, Groq, or a local Ollama server

No dependencies beyond Python 3.10+ and `cryptography` (only needed to save keys to disk).

```bash
pip install cryptography
```

### Web app

```bash
python -m agent_team serve            # http://127.0.0.1:8765
```

The command prints a link with a session token. Open it in your browser. The page lets you:
- Build the team with a form: member names, roles, providers, and models, plus a verifier and limits. The raw JSON is under "Advanced". Start is disabled until the team is ready, and the page says what is missing.
- Test a key with one tiny request, using the model of a team member that uses that provider.
- Continue a finished run by sending a message. The agents keep their history, as long as the server has not restarted.
- Enter API keys. They stay on the server, in memory, unless you tick "save encrypted". Saved keys are encrypted with a master key in `~/.config/agent_team/master.key` (override with `AGENT_TEAM_MASTER_KEY_FILE`). Back up that file. Without it, saved keys cannot be read and must be entered again.
- Edit the team as JSON, enter a goal, and start a run.
- Watch messages, token use, and the final answer stream in live.
- Send the lead a message during a run, or stop the run.
- Reopen past runs. Their history is kept in `data/agent_team.db`.

The server listens only on 127.0.0.1 and rejects requests from other hosts or origins. `data/` holds run history and any saved keys, and it is git-ignored.

### Command line

Keys come from the environment variables named in the config:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
cp team.example.json team.json   # then edit roles, models, and providers
python -m agent_team run team.json "Write a one-page guide to our release process" -v
```

`-v` prints each message between agents to stderr. The final answer goes to stdout.

### How a team works

- The lead receives the goal. Agents take turns in the order their messages arrive.
- Agents act only through tools. Plain text in a reply is not delivered to anyone.
  - `send_message(to, message)`: every agent. The reply arrives later as a new message.
  - `finish(answer)`: the lead only. Ends the run with the answer for you.
  - `submit_verdict(passed, feedback)`: the verifier only.
- If the config names a `verifier`, the lead's `finish` goes to that agent first. The run ends only when it passes the answer. Otherwise the lead gets the feedback and revises.
- An agent whose reply has no tool call is told to use a tool. Two such replies in a row end the run.
- The model's output limit can cut a reply short. The agent is told to keep messages shorter, and the feed marks the turn.
- Rate limits and server errors are retried with backoff (up to 4 attempts, honoring `Retry-After`).
- Only the most recent 16 history items are sent to the model, so long runs cost less.
- At most 3 runs execute at once. Stop takes effect immediately, even during a model call.
- Limits stop runaway runs, and the run status says which one applied: `max_turns`, `max_seconds` (`timeout`), `max_tokens` (`token_budget`), a repeated turn (`stalled`), `no_progress`, `stopped`, `failed`, `idle` when no one has anything left to do, or `interrupted` after a restart.
- The `openai_compatible` provider needs a server with tool calling. Plain chat-only servers will fail.

### Tests

```bash
python -m unittest discover -s tests
```

The tests use scripted replies and local stub servers, so they don't call any real API.
