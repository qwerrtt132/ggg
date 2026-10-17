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
- An agent sends a message by starting a line with `TO <name>:`. The message runs until the next `TO` line.
- The lead ends the run with a line starting with `FINAL:`.
- If the config names a `verifier`, the lead's final answer goes to that agent first. Its reply must start with `VERDICT: PASS` before the run ends. Otherwise the lead gets the problems and revises.
- Limits stop runaway runs, and the run status says which one applied: `max_turns`, `max_seconds` (`timeout`), `max_tokens` (`token_budget`), a repeated reply (`stalled`), `stopped`, or `idle` when no one has anything left to do.

### Tests

```bash
python -m unittest discover -s tests
```

The tests use scripted replies and local stub servers, so they don't call any real API.
