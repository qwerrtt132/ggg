"""Turn a team config into a running team, and run it on a background thread."""

import json
import os
import re
import threading
from pathlib import Path

from .providers import ProviderError, build_provider
from .team import Agent, Team
from .tools import GROUPS

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_team.json"
NAME_RULE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    pass


def default_config():
    return json.loads(DEFAULT_CONFIG_PATH.read_text())


def _check_member(spec, label):
    if not isinstance(spec, dict):
        raise ConfigError(f"{label} must be an object")
    name = spec.get("name")
    if not isinstance(name, str) or not NAME_RULE.match(name):
        raise ConfigError(f"{label} needs a name: lowercase letters, digits, - or _, starting with a letter")
    for field_name in ("role", "model", "provider"):
        if not isinstance(spec.get(field_name), str) or not spec[field_name].strip():
            raise ConfigError(f"{name!r} needs a non-empty {field_name}")


def _check_limit(config, key, default, minimum):
    value = config.get(key, default)
    if value is None and key == "max_tokens":
        return
    if not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{key} must be a whole number of at least {minimum}")


SECRET_FIELDS = {"api_key", "key", "secret", "token", "password"}


def refuse_secret_fields(config):
    """Configs are saved with runs and presets, so keys must never sit inside them."""
    providers = config.get("providers", {})
    if not isinstance(providers, dict):
        raise ConfigError("providers must be an object")
    for name, spec in providers.items():
        if isinstance(spec, dict) and SECRET_FIELDS & set(spec):
            raise ConfigError(f"provider {name!r} has a key field in the config; enter keys in the API keys panel")


def _member_tools(spec, registry):
    groups = spec.get("tools", [])
    if not isinstance(groups, list) or any(group not in GROUPS for group in groups):
        raise ConfigError(f"{spec['name']!r} has an unknown tool group; choose from {sorted(GROUPS)}")
    if groups and registry is None:
        raise ConfigError(f"{spec['name']!r} asks for tools, but this run has no workspace for them")
    return [tool for group in groups for tool in GROUPS[group]]


def build_team(config, lookup_key, emit, human_inbox=None, stop_event=None, save_state=None, tools=None, on_live=None):
    """Returns (team, secrets). lookup_key(provider) gives a key from the vault, or None to fall back to the env var."""
    if not isinstance(config, dict):
        raise ConfigError("the team config must be a JSON object")
    refuse_secret_fields(config)
    try:
        lead_spec = config["lead"]
        teammate_specs = config["teammates"]
        provider_specs = config["providers"]
    except KeyError as error:
        raise ConfigError(f"the team config is missing {error}") from None
    if not isinstance(teammate_specs, list):
        raise ConfigError("teammates must be a list")
    specs = [lead_spec, *teammate_specs]
    for index, spec in enumerate(specs):
        _check_member(spec, "the lead" if index == 0 else f"teammate {index}")
    _check_limit(config, "max_turns", 30, 1)
    _check_limit(config, "max_seconds", 600, 10)
    _check_limit(config, "max_tokens", None, 1000)

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
        Agent(
            name=spec["name"],
            role=spec["role"],
            provider=providers[spec["provider"]],
            model=spec["model"],
            tools=_member_tools(spec, tools),
        )
        for spec in specs
    ]
    team = Team(
        agents,
        lead=lead_spec["name"],
        verifier=config.get("verifier"),
        max_turns=config.get("max_turns", 30),
        max_seconds=config.get("max_seconds", 600),
        max_tokens=config.get("max_tokens"),
        emit=emit,
        human_inbox=human_inbox,
        stop_event=stop_event,
        save_state=save_state,
        extra_tools=tools,
        on_live=on_live,
    )
    return team, secrets


def check_provider(name, spec, api_key, model):
    """Send one tiny request to check that a key and model work. Returns a short piece of the reply."""
    provider = build_provider(name, spec, api_key)
    reply = provider.complete(model, "You are a connectivity check.", [{"role": "user", "text": "Reply with OK."}], [])
    return reply.text.strip()[:100] or "(empty reply)"


def redact(text, secrets):
    for secret in secrets:
        text = text.replace(secret, "[redacted]")
    return text


def run_in_background(store, run_id, team, action, secrets):
    """Run action() (which returns the answer, or None) on a thread. The run's final status goes to the store."""

    def work():
        try:
            answer = action()
            if answer is not None:
                store.add_event(run_id, "final", {"answer": answer})
            status = team.status
        except Exception as error:  # a failed provider call ends this run, not the server
            store.add_event(run_id, "error", {"message": redact(str(error), secrets)})
            status = "failed"
        store.clear_live(run_id)
        store.finish_run(run_id, status)

    thread = threading.Thread(target=work, daemon=True, name=f"run-{run_id[:8]}")
    thread.start()
    return thread
