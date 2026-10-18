"""Command line. `serve` starts the web app. `run` runs one team from a config file, without the UI."""

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from .auth import hash_password
from .providers import ProviderError
from .runner import ConfigError, build_team, redact
from .server import serve


def _environment_secrets(config_path):
    """The keys the command line will use, so errors can hide them."""
    try:
        providers = json.loads(Path(config_path).read_text()).get("providers", {})
    except (OSError, ValueError, AttributeError):
        return []
    values = (os.environ.get(spec.get("api_key_env", "")) for spec in providers.values() if isinstance(spec, dict))
    return [value for value in values if value]


def run_once(config_path, goal, verbose):
    config = json.loads(Path(config_path).read_text())

    def emit(kind, **data):
        if verbose and kind == "message":
            print(f"[{data['sender']} -> {data['recipient']}]\n{data['text']}\n", file=sys.stderr)

    team, _ = build_team(config, lookup_key=lambda name: None, emit=emit)
    return team.status, team.run(goal)


def _set_password(read=getpass.getpass):
    """Prints a password hash for AGENT_TEAM_OWNER_PASSWORD_HASH. The password itself is never printed or stored."""
    first = read("Owner password: ")
    if len(first) < 12:
        print("error: use at least 12 characters", file=sys.stderr)
        return 2
    if read("Repeat the password: ") != first:
        print("error: the two passwords differ", file=sys.stderr)
        return 2
    print(hash_password(first))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent_team", description="Teams of model-backed agents that message each other.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve_command = commands.add_parser("serve", help="start the web app on 127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765)
    serve_command.add_argument("--data-dir", default="data", help="where runs and saved keys are stored")
    serve_command.add_argument("--lan", action="store_true", help="let other devices on this home network open the site")

    commands.add_parser("set-password", help="print a password hash for AGENT_TEAM_OWNER_PASSWORD_HASH on the public site")

    run_command = commands.add_parser("run", help="run one team from a config file, without the web app")
    run_command.add_argument("config", help="path to the team JSON config")
    run_command.add_argument("goal", help="what the team should accomplish")
    run_command.add_argument("-v", "--verbose", action="store_true", help="print each message between agents")

    args = parser.parse_args(argv)
    if args.command == "set-password":
        return _set_password()
    if args.command == "serve":
        try:
            serve(args.port, args.data_dir, lan=args.lan)
        except RuntimeError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2
        return 0
    secrets = _environment_secrets(args.config)
    try:
        status, answer = run_once(args.config, args.goal, args.verbose)
    except (ConfigError, ProviderError, ValueError, KeyError, OSError) as error:
        print(f"error: {redact(str(error), secrets)}", file=sys.stderr)
        return 2
    if answer is None:
        print(f"The team stopped without a final answer ({status}).", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
