"""A structured log: one JSON object per line, with every saved or in-memory key replaced before it is written."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


class RunLog:
    def __init__(self, path, secrets=lambda: []):
        self.path = Path(path)
        self.secrets = secrets  # called on every write, so keys added later are redacted too
        self.lock = threading.Lock()

    def write(self, event, **fields):
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
        text = json.dumps(_redacted(line, [secret for secret in self.secrets() if secret]))
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(text + "\n")
            except OSError:
                pass  # a full disk must not stop a run; the run's own events still record what happened


def _redacted(value, secrets):
    if isinstance(value, dict):
        return {key: _redacted(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [_redacted(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, "[redacted]")
    return value
