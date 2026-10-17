# ggg

## Agent team

`agent_team` runs a small team of AI agents that message each other until the lead has a final answer. Each agent can use a different provider and model, and you supply your own API keys.

Supported providers:
- `anthropic`: the Anthropic Messages API
- `openai_compatible`: any server with a `POST {base_url}/chat/completions` endpoint, such as OpenAI, DeepSeek, Groq, or a local Ollama server

### Setup

No dependencies beyond Python 3.10+. Keys are read from environment variables named in the config, so they never go in the repo:

```bash
export ANTHROPIC_API_KEY=...
export OPENAI_API_KEY=...
```

### Run

```bash
cp team.example.json team.json   # then edit roles, models, and providers
python -m agent_team team.json "Write a one-page guide to our release process" -v
```

`-v` prints each message between agents to stderr. The final answer goes to stdout.

### How it works

- The lead receives the goal. Every agent takes turns when it has messages waiting.
- An agent sends a message by starting a line with `TO <name>:`. The message runs until the next `TO` line.
- The lead ends the run with a line starting with `FINAL:`.
- `max_turns` (default 30) stops runaway conversations.

### Tests

```bash
python -m unittest discover -s tests
```

The tests use scripted replies and a local stub server, so they don't call any real API.
