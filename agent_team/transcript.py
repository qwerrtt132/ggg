"""Turn a run's event log into a transcript people can read (Markdown) or keep as data (JSON)."""

import json
from datetime import datetime, timezone


def _time(seconds):
    return datetime.fromtimestamp(seconds, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def _quote(text):
    return "\n".join(f"> {line}" if line else ">" for line in text.splitlines()) or ">"


def to_markdown(run, events):
    lines = [f"# {run['goal']}", "", f"- Status: {run['status']}", f"- Started: {_time(run['created_at'])}"]
    if run.get("finished_at"):
        lines.append(f"- Last finished: {_time(run['finished_at'])}")
    lines.append("")
    for event in events:
        kind, data = event["kind"], event["data"]
        if kind == "roster":
            members = ", ".join(f"{m['name']} ({m['model']}, {m['provider']})" for m in data["members"])
            lines += ["## Team", "", members, ""]
        elif kind == "message":
            lines += [f"**{data['sender']} → {data['recipient']}**", "", _quote(data["text"]), ""]
        elif kind == "human":
            lines += ["**You**", "", _quote(data["text"]), ""]
        elif kind == "final":
            lines += ["## Final answer", "", data["answer"], ""]
        elif kind == "error":
            lines += [f"**Error:** {data['message']}", ""]
        elif kind == "rewind":
            lines += [f"*Rewound to turn {data['to_turn']}. The turns after it were dropped.*", ""]
        elif kind == "branched":
            lines += [f"*Branched from run {data['from_run'][:8]} at turn {data['turn']}.*", ""]
        elif kind == "status" and data["status"] != "running":
            lines += [f"*Run ended: {data['status']}*", ""]
    return "\n".join(lines).rstrip() + "\n"


def to_json(run, events):
    return json.dumps(
        {
            "run": {key: run[key] for key in ("id", "goal", "status", "created_at", "finished_at")},
            "events": events,
        },
        indent=2,
    )
