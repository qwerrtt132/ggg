"""Turn a team config into a running team, and run it on a background thread."""

import json
import os
import threading
from pathlib import Path

from .providers import build_provider
from .team import Agent, Team

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "team.example.json"


class ConfigError(ValueError):
    pass


def default_config():
    return json.loads(DEFAULT_CONFIG_PATH.read_text())


def build_team(config, lookup_key, emit, human_inbox=None, stop_event=None):
    """Returns (team, secrets). lookup_key(provider) gives a key from the vault, or None to fall back to the env var."""
    if not isinstance(config, dict):
        raise ConfigError("the team config must be a JSON object")
    try:
        specs = [config["lead"], *config["teammates"]]
        provider_specs = config["providers"]
    except KeyError as error:
        raise ConfigError(f"the team config is missing {error}") from None
    providers, secrets = {}, []
    for name in sorted({spec["provider"] for spec in specs}):
        if name not in provider_specs:
            raise ConfigError(f"unknown provider {name!r}")
        spec = provider_specs[name]
        api_key = lookup_key(name) or os.environ.get(spec.get("api_key_env", ""), "")
        if not api_key:
            hint = f"set {spec['api_key_env']}" if spec.get("api_key_env") else "add one in the API keys panel"
            raise ConfigError(f"no API key for {name!r}; {hint}")
        secrets.append(api_key)
        providers[name] = build_provider(name, spec, api_key)
    agents = [
        Agent(name=spec["name"], role=spec["role"], provider=providers[spec["provider"]], model=spec["model"])
        for spec in specs
    ]
    team = Team(
        agents,
        lead=config["lead"]["name"],
        verifier=config.get("verifier"),
        max_turns=config.get("max_turns", 30),
        max_seconds=config.get("max_seconds", 600),
        max_tokens=config.get("max_tokens"),
        emit=emit,
        human_inbox=human_inbox,
        stop_event=stop_event,
    )
    return team, secrets


def redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    return text


def run_in_background(store, run_id, team, goal, secrets):
    def work():
        try:
            answer = team.run(goal)
            if answer is not None:
                store.add_event(run_id, "final", {"answer": answer})
            status = team.status
        except Exception as error:  # a failed provider call ends this run, not the server
            store.add_event(run_id, "error", {"message": redact(str(error), secrets)})
            status = "failed"
        store.finish_run(run_id, status)

    thread = threading.Thread(target=work, daemon=True, name=f"run-{run_id[:8]}")
    thread.start()
    return thread
