"""Local web server. Serves the UI, keeps provider keys on the server, and streams run events over SSE."""

import hmac
import json
import os
import queue
import re
import secrets
import threading
import uuid
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .keys import KeyVault, KeyVaultError
from .providers import ProviderError
from .runner import ConfigError, build_team, check_provider, default_config, redact, run_in_background
from .store import FINISHED, Store

STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "text/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
}
PROVIDER_NAME = re.compile(r"^[a-z0-9_-]{1,40}$")
MAX_BODY = 1_000_000
MAX_TEXT = 10_000
MAX_ACTIVE_RUNS = 3
KEEPALIVE_SECONDS = 15
SECURITY_HEADERS = {
    "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


@dataclass
class RunControl:
    human: queue.Queue = field(default_factory=queue.Queue)
    stop: threading.Event = field(default_factory=threading.Event)
    thread: threading.Thread = None
    team: object = None
    secrets: list = field(default_factory=list)


class App:
    def __init__(self, store, vault, token, max_active_runs=MAX_ACTIVE_RUNS):
        self.store = store
        self.vault = vault
        self.token = token
        self.max_active_runs = max_active_runs
        self.runs = {}  # run id -> RunControl, for runs started by this process


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, app):
        super().__init__(("127.0.0.1", port), Handler)
        self.app = app
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # the UI shows what matters; keep the terminal quiet

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def _dispatch(self, method):
        if not self._local_request():
            return self._send_json(403, {"error": "this server only answers requests from its own page"})
        url = urlparse(self.path)
        query = parse_qs(url.query)
        parts = [part for part in url.path.split("/") if part]
        try:
            if method == "GET" and not parts:
                return self._send_static("index.html")
            if method == "GET" and len(parts) == 2 and parts[0] == "static":
                return self._send_static(parts[1])
            if not parts or parts[0] != "api":
                return self._send_json(404, {"error": "not found"})
            if not self._authorized(query):
                return self._send_json(401, {"error": "missing or wrong session token"})
            self._api(method, parts[1:], query)
        except (ConfigError, ProviderError, KeyVaultError, ValueError, KeyError, TypeError) as error:
            self._send_json(400, {"error": str(error)})

    def _api(self, method, parts, query):
        app = self.server.app
        if method == "GET" and parts == ["providers"]:
            return self._send_json(
                200,
                {
                    "keys": app.vault.status(),
                    "env_keys": _env_keys(),
                    "default_config": default_config(),
                },
            )
        if method == "POST" and len(parts) == 3 and parts[0] == "providers" and parts[2] == "test":
            return self._test_provider(parts[1])
        if method == "POST" and parts == ["keys"]:
            body = self._body()
            provider = str(body.get("provider", ""))
            if not PROVIDER_NAME.match(provider):
                raise ValueError("provider names use lowercase letters, digits, - and _")
            app.vault.set(provider, str(body.get("key", "")), persist=bool(body.get("persist")))
            return self._send_json(200, {"keys": app.vault.status()})
        if method == "DELETE" and len(parts) == 2 and parts[0] == "keys":
            app.vault.delete(parts[1])
            return self._send_json(200, {"keys": app.vault.status()})
        if method == "GET" and parts == ["runs"]:
            return self._send_json(200, {"runs": app.store.list_runs()})
        if method == "POST" and parts == ["runs"]:
            return self._start_run()
        if len(parts) == 3 and parts[0] == "runs":
            run_id = parts[1]
            if app.store.get_run(run_id) is None:
                return self._send_json(404, {"error": "unknown run"})
            if method == "GET" and parts[2] == "events":
                return self._stream(run_id, query)
            if method == "POST" and parts[2] == "message":
                return self._human_message(run_id)
            if method == "POST" and parts[2] == "stop":
                return self._stop(run_id)
        self._send_json(404, {"error": "not found"})

    def _start_run(self):
        app = self.server.app
        body = self._body()
        goal = str(body.get("goal", "")).strip()
        if not goal or len(goal) > MAX_TEXT:
            raise ValueError(f"the goal must be 1 to {MAX_TEXT} characters")
        active = sum(1 for control in app.runs.values() if control.thread.is_alive())
        if active >= app.max_active_runs:
            return self._send_json(
                429, {"error": f"{active} runs are already running; wait for one to finish or stop one"}
            )
        run_id = uuid.uuid4().hex
        control = RunControl()
        team, secret_values = build_team(
            body.get("config"),
            app.vault.get,
            emit=lambda kind, **data: app.store.add_event(run_id, kind, data),
            human_inbox=control.human,
            stop_event=control.stop,
        )
        app.store.create_run(run_id, goal)
        control.team, control.secrets = team, secret_values
        control.thread = run_in_background(app.store, run_id, team, lambda: team.run(goal), secret_values)
        app.runs[run_id] = control
        self._send_json(201, {"id": run_id})

    def _human_message(self, run_id):
        app = self.server.app
        control = app.runs.get(run_id)
        if control is None:
            return self._send_json(409, {"error": "this run cannot be continued; the server was restarted since it ran"})
        text = str(self._body().get("text", "")).strip()[:MAX_TEXT]
        if not text:
            raise ValueError("the message is empty")
        if control.thread.is_alive():
            control.human.put(text)
            app.store.add_event(run_id, "human", {"text": text})
            return self._send_json(202, {"resumed": False})
        # The run has ended: continue it with the same agents and their histories.
        if self._active_runs() >= app.max_active_runs:
            return self._send_json(429, {"error": "too many runs are running; wait for one to finish or stop one"})
        app.store.resume_run(run_id)
        app.store.add_event(run_id, "human", {"text": text})
        control.stop.clear()
        control.thread = run_in_background(
            app.store, run_id, control.team, lambda: control.team.continue_run(text), control.secrets
        )
        self._send_json(202, {"resumed": True})

    def _active_runs(self):
        return sum(1 for control in self.server.app.runs.values() if control.thread.is_alive())

    def _test_provider(self, name):
        app = self.server.app
        body = self._body()
        spec = body.get("spec")
        if spec is None:
            defaults = default_config()["providers"]
            if name not in defaults:
                raise ValueError(f"unknown provider {name!r}")
            spec = defaults[name]
        model = str(body.get("model", "")).strip()
        if not model:
            raise ValueError("choose a model to test with")
        api_key = app.vault.get(name) or os.environ.get(spec.get("api_key_env", ""), "")
        if not api_key:
            raise ValueError(f"no key for {name!r} yet")
        try:
            sample = check_provider(name, spec, api_key, model)
        except ProviderError as error:
            return self._send_json(400, {"error": redact(str(error), [api_key])})
        self._send_json(200, {"ok": True, "reply": sample})

    def _stop(self, run_id):
        control = self.server.app.runs.get(run_id)
        if control is not None:
            control.stop.set()
        self._send_json(202, {"ok": True})

    def _stream(self, run_id, query):
        store = self.server.app.store
        last_seen = self.headers.get("Last-Event-ID") or (query.get("after") or ["0"])[0]
        try:
            after = int(last_seen)
        except ValueError:
            after = 0
        self._send(200, None, "text/event-stream; charset=utf-8")
        try:
            while True:
                version = store.current_version()
                run = store.get_run(run_id)
                rows = store.events_after(run_id, after)
                for seq, kind, data in rows:
                    self.wfile.write(f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                    after = seq
                self.wfile.flush()
                if not rows:
                    if run["status"] in FINISHED:
                        # Tells the page the stream is truly over. A status event alone is not enough: a
                        # continued run has an older "done" status earlier in its history.
                        self.wfile.write(b"event: end\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    if not store.wait_for_change(version, KEEPALIVE_SECONDS):
                        self.wfile.write(b": keepalive\n\n")
                        self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _local_request(self):
        if self.headers.get("Host") not in self.server.allowed_hosts:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{host}" for host in self.server.allowed_hosts}:
            return False
        return True

    def _authorized(self, query):
        supplied = self.headers.get("X-Session-Token") or (query.get("token") or [""])[0]
        return hmac.compare_digest(supplied.encode(), self.server.app.token.encode())

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise ValueError("the request body is too large")
        body = json.loads(self.rfile.read(length) if length else b"{}")
        if not isinstance(body, dict):
            raise ValueError("the request body must be a JSON object")
        return body

    def _send_static(self, name):
        if name not in STATIC_FILES:
            return self._send_json(404, {"error": "not found"})
        self._send(200, (STATIC_DIR / name).read_bytes(), STATIC_FILES[name])

    def _send_json(self, status, payload):
        self._send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

    def _send(self, status, body, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in SECURITY_HEADERS.items():
            self.send_header(name, value)
        if body is not None:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)


def _env_keys():
    """Which default providers have their key in the environment. Only a yes or no, never the key."""
    return {
        name: bool(os.environ.get(spec["api_key_env"]))
        for name, spec in default_config()["providers"].items()
        if spec.get("api_key_env")
    }


def serve(port=8765, data_dir="data"):
    data = Path(data_dir)
    store = Store(data / "agent_team.db")
    store.mark_interrupted()
    token = secrets.token_urlsafe(24)
    server = Server(port, App(store, KeyVault(data), token))
    print(f"Agent team is running. Open http://127.0.0.1:{server.server_port}/?token={token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
