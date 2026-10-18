"""Turn a team config into a running team, and run it on a background thread."""

import json
import os
import re
import threading
from pathlib import Path

from .providers import ProviderError, build_provider
from .team import DEFAULT_MAX_REPLANS, DEFAULT_STALL_TURNS, Agent, Team
from .tools import GROUPS

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "default_team.json"
DEFAULT_MAX_TOKENS = 200_000  # used when a config has no token budget; null in a config means no budget
CONFIG_VERSION = 1  # the version this app reads; a config without a "version" field is read as version 1
NAME_RULE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


class ConfigError(ValueError):
    pass


def default_config():
    return json.loads(DEFAULT_CONFIG_PATH.read_text())


def check_version(config):
    version = config.get("version", CONFIG_VERSION)
    if version != CONFIG_VERSION:
        raise ConfigError(f"config version {version} is not supported; this app reads version {CONFIG_VERSION}")


def _prices(config):
    """Per-model prices, keyed "provider/model" in the config and (provider, model) in the team."""
    specs = config.get("prices", {})
    if not isinstance(specs, dict):
        raise ConfigError("prices must be an object keyed by provider/model")
    prices = {}
    for key, spec in specs.items():
        provider, slash, model = key.partition("/")
        if not slash or not provider or not model:
            raise ConfigError(f"the price {key!r} must be named provider/model")
        if not isinstance(spec, dict):
            raise ConfigError(f"prices for {key!r} need input_per_million and output_per_million")
        values = []
        for field in ("input_per_million", "output_per_million"):
            value = spec.get(field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
                raise ConfigError(f"prices for {key!r}: {field} must be a number, zero or more")
            values.append(float(value))
        prices[(provider, model)] = tuple(values)
    return prices


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


def _small_int(config, key, default, low, high):
    value = config.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
        raise ConfigError(f"{key} must be a whole number from {low} to {high}")
    return value


def _flag(config, key):
    value = config.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(f"{key} must be true or false")
    return value


def _member_approvals(spec, tool_names):
    approve = spec.get("approve", [])
    if not isinstance(approve, list) or any(tool not in tool_names for tool in approve):
        raise ConfigError(f"{spec['name']!r} can only require approval for its own tools: {sorted(tool_names)}")
    return approve


def build_team(config, lookup_key, emit, human_inbox=None, stop_event=None, save_state=None, tools=None, on_live=None):
    """Returns (team, secrets). lookup_key(provider) gives a key from the vault, or None to fall back to the env var."""
    if not isinstance(config, dict):
        raise ConfigError("the team config must be a JSON object")
    refuse_secret_fields(config)
    check_version(config)
    prices = _prices(config)
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
            approve=_member_approvals(spec, _member_tools(spec, tools)),
        )
        for spec in specs
    ]
    team = Team(
        agents,
        lead=lead_spec["name"],
        verifier=config.get("verifier"),
        max_turns=config.get("max_turns", 30),
        max_seconds=config.get("max_seconds", 600),
        max_tokens=config.get("max_tokens", DEFAULT_MAX_TOKENS),
        step_mode=_flag(config, "step_mode"),
        max_replans=_small_int(config, "max_replans", DEFAULT_MAX_REPLANS, 0, 5),
        stall_turns=_small_int(config, "stall_turns", DEFAULT_STALL_TURNS, 2, 20),
        prices=prices,
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


def run_in_background(store, run_id, team, action, secrets, log=None):
    """Run action() (which returns the answer, or None) on a thread. The run's final status goes to the store, and
    to the log when there is one."""

    def work():
        try:
            answer = action()
            if answer is not None:
                store.add_event(run_id, "final", {"answer": answer})
            status = team.status
        except Exception as error:  # a failed provider call ends this run, not the server
            message = redact(str(error), secrets)
            store.add_event(run_id, "error", {"message": message})
            status = "failed"
            if log:
                log.write("run_failed", run=run_id, message=message)
        store.clear_live(run_id)
        store.finish_run(run_id, status)
        if log:
            log.write("run_finished", run=run_id, status=status, tokens=team.tokens_used)

    thread = threading.Thread(target=work, daemon=True, name=f"run-{run_id[:8]}")
    thread.start()
    return thread
