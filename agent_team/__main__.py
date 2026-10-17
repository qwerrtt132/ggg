"""Run a team from a JSON config: python -m agent_team team.json "goal"."""

import argparse
import json
import sys

from .providers import ProviderError, build_provider
from .team import Agent, Team


def load_team(path, verbose):
    with open(path) as file:
        config = json.load(file)
    specs = [config["lead"], *config["teammates"]]
    needed = {spec["provider"] for spec in specs}
    providers = {name: build_provider(name, config["providers"][name]) for name in needed}
    agents = [
        Agent(
            name=spec["name"],
            role=spec["role"],
            provider=providers[spec["provider"]],
            model=spec["model"],
        )
        for spec in specs
    ]

    def show(sender, recipient, text):
        if verbose:
            print(f"[{sender} -> {recipient}]\n{text}\n", file=sys.stderr)

    return Team(
        agents,
        lead=config["lead"]["name"],
        max_turns=config.get("max_turns", 30),
        on_message=show,
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="Run a team of model-backed agents on a goal.")
    parser.add_argument("config", help="path to the team JSON config")
    parser.add_argument("goal", help="what the team should accomplish")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each message between agents")
    args = parser.parse_args(argv)
    try:
        team = load_team(args.config, args.verbose)
        answer = team.run(args.goal)
    except (ProviderError, ValueError, KeyError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if answer is None:
        print("The team stopped without a FINAL answer.", file=sys.stderr)
        return 1
    print(answer)
    return 0


if __name__ == "__main__":
    sys.exit(main())
