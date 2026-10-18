"""A structured log: one JSON object per line, with every saved or in-memory key replaced before it is written."""

import json
import threading
from datetime import datetime, timezone
from pathlib import Path


MAX_LOG_BYTES = 5_000_000  # past this, the log moves to agent_team.log.1 (replacing any earlier one)


class RunLog:
    def __init__(self, path, secrets=lambda: [], max_bytes=MAX_LOG_BYTES):
        self.path = Path(path)
        self.secrets = secrets  # called on every write, so keys added later are redacted too
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def write(self, event, **fields):
        line = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"), "event": event, **fields}
        text = json.dumps(_redacted(line, [secret for secret in self.secrets() if secret])) + "\n"
        with self.lock:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                if self.path.exists() and self.path.stat().st_size + len(text.encode()) > self.max_bytes:
                    self.path.replace(self.path.with_name(self.path.name + ".1"))
                with self.path.open("a", encoding="utf-8") as handle:
                    handle.write(text)
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
