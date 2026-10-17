"""Command line. `serve` starts the web app. `run` runs one team from a config file, without the UI."""

import argparse
import json
import sys
from pathlib import Path

from .providers import ProviderError
from .runner import ConfigError, build_team
from .server import serve


def run_once(config_path, goal, verbose):
    config = json.loads(Path(config_path).read_text())

    def emit(kind, **data):
        if verbose and kind == "message":
            print(f"[{data['sender']} -> {data['recipient']}]\n{data['text']}\n", file=sys.stderr)

    team, _ = build_team(config, lookup_key=lambda name: None, emit=emit)
    return team.status, team.run(goal)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="agent_team", description="Teams of model-backed agents that message each other.")
    commands = parser.add_subparsers(dest="command", required=True)

    serve_command = commands.add_parser("serve", help="start the web app on 127.0.0.1")
    serve_command.add_argument("--port", type=int, default=8765)
    serve_command.add_argument("--data-dir", default="data", help="where runs and saved keys are stored")

    run_command = commands.add_parser("run", help="run one team from a config file, without the web app")
    run_command.add_argument("config", help="path to the team JSON config")
    run_command.add_argument("goal", help="what the team should accomplish")
    run_command.add_argument("-v", "--verbose", action="store_true", help="print each message between agents")

    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(args.port, args.data_dir)
        return 0
    try:
        status, answer = run_once(args.config, args.goal, args.verbose)
    except (ConfigError, ProviderError, ValueError, KeyError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if answer is None:
        print(f"The team stopped without a final answer ({status}).", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
