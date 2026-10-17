"""Turn a run's event log into a trace: each turn is a span, with its model call and tool calls inside it.

Span names follow the OpenTelemetry GenAI operation names (invoke_agent, chat, execute_tool). Those names are
used as labels only; the specification's stability was not confirmed when this was written.
"""

CONVENTIONS = (
    "Span names are labels that follow the OpenTelemetry GenAI operation names "
    "(invoke_agent, chat, execute_tool). They are not a verified export format."
)


def _ms(start, end):
    return round((end - start) * 1000)


def build_trace(run, events):
    """run: the run record. events: the run's events in order, each with kind, data, and created_at."""
    started = {}  # agent -> when its current model call began
    turns = {}  # turn number -> turn span
    order = []
    for row in events:
        kind, data, at = row["kind"], row["data"], row["created_at"]
        if kind == "agent" and data.get("state") == "thinking":
            started[data["name"]] = at
        elif kind == "turn":
            begin = started.pop(data["agent"], at)
            chat = {
                "name": "chat",
                "agent": data["agent"],
                "start": begin,
                "end": at,
                "duration_ms": _ms(begin, at),
                "tokens": data["tokens"],
                "truncated": data.get("truncated", False),
            }
            turn = {
                "name": "invoke_agent",
                "agent": data["agent"],
                "turn": data["turn"],
                "start": begin,
                "end": at,
                "duration_ms": _ms(begin, at),
                "tokens": data["tokens"],
                "truncated": data.get("truncated", False),
                "children": [chat],
            }
            turns[data["turn"]] = turn
            order.append(turn)
        elif kind == "tool" and data.get("turn") in turns:
            ended = at
            ran_for = data.get("duration_ms", 0) / 1000
            turns[data["turn"]]["children"].append(
                {
                    "name": "execute_tool",
                    "agent": data["agent"],
                    "tool": data["tool"],
                    "is_error": data.get("is_error", False),
                    "start": ended - ran_for,
                    "end": ended,
                    "duration_ms": data.get("duration_ms", 0),
                }
            )
    return {
        "schema": "agent-team-trace/1",
        "conventions": CONVENTIONS,
        "run": {key: run[key] for key in ("id", "goal", "status") if key in run},
        "turns": order,
    }
