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
from urllib.parse import parse_qs, unquote, urlparse

from .keys import KeyVault, KeyVaultError
from .locks import acquire_data_lock
from .providers import ProviderError
from .runner import ConfigError, build_team, check_provider, default_config, redact, refuse_secret_fields, run_in_background
from .store import FINISHED, Store
from .templates import TEMPLATES
from .review import WorkspaceReview
from .trace import build_trace
from .tools import ToolError, build_tools, copy_workspace
from .transcript import to_json, to_markdown

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
    def __init__(self, store, vault, token, max_active_runs=MAX_ACTIVE_RUNS, workspaces=None, baselines=None):
        self.store = store
        self.vault = vault
        self.token = token
        if baselines is None and workspaces is not None:
            baselines = Path(workspaces).parent / "accepted"
        self.baselines = baselines  # one accepted-state folder per run, for reviewing changes
        self.max_active_runs = max_active_runs
        self.workspaces = workspaces  # folder holding one workspace per run, for the files tools
        self.runs = {}  # run id -> RunControl, for runs started by this process; finished ones are released
        self.lock = threading.Lock()

    def active_count(self):
        return sum(1 for control in self.runs.values() if control.thread.is_alive())

    def forget_finished(self):
        """Drop finished runs from memory. Continuing one later rebuilds it from the database."""
        for run_id in [run_id for run_id, control in self.runs.items() if not control.thread.is_alive()]:
            del self.runs[run_id]


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
        parts = [unquote(part) for part in url.path.split("/") if part]
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
        except (ConfigError, ProviderError, KeyVaultError, ToolError, ValueError, KeyError, TypeError) as error:
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
        if method == "GET" and parts == ["templates"]:
            return self._send_json(200, {"templates": TEMPLATES})
        if parts[:1] == ["presets"]:
            return self._presets(method, parts[1:])
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
        if len(parts) == 4 and parts[0] == "runs" and parts[2] == "approvals" and method == "POST":
            return self._approval(parts[1], parts[3])
        if len(parts) == 3 and parts[0] == "runs":
            run_id = parts[1]
            if app.store.get_run(run_id) is None:
                return self._send_json(404, {"error": "unknown run"})
            if parts[2] == "changes" and method == "GET":
                return self._send_json(200, {"changes": self._review_for(run_id).changes() if app.workspaces else []})
            if parts[2] == "changes" and method == "POST":
                return self._review_change(run_id)
            if parts[2] == "workspace.zip" and method == "GET":
                return self._send(
                    200,
                    self._review_for(run_id).accepted_archive(),
                    "application/zip",
                    {"Content-Disposition": f'attachment; filename="run-{run_id[:8]}-accepted.zip"'},
                )
            if method == "GET" and parts[2] == "events":
                return self._stream(run_id, query)
            if method == "GET" and parts[2] == "export":
                return self._export(run_id, query)
            if method == "POST" and parts[2] == "message":
                return self._human_message(run_id)
            if method == "POST" and parts[2] == "stop":
                return self._stop(run_id)
            if method == "GET" and parts[2] == "checkpoints":
                return self._send_json(200, {"checkpoints": app.store.list_checkpoints(run_id)})
            if method == "POST" and parts[2] == "rewind":
                return self._rewind(run_id)
            if method == "POST" and parts[2] == "branch":
                return self._branch(run_id)
            if method == "GET" and parts[2] == "trace":
                return self._send_json(200, self._trace(run_id))
            if method == "GET" and parts[2] == "trace.json":
                body = json.dumps(self._trace(run_id), indent=2).encode()
                return self._send(
                    200,
                    body,
                    "application/json; charset=utf-8",
                    {"Content-Disposition": f'attachment; filename="trace-{run_id[:8]}.json"'},
                )
            if method == "POST" and parts[2] == "advance":
                return self._advance(run_id)
            if method == "POST" and parts[2] == "step":
                return self._set_step(run_id)
        self._send_json(404, {"error": "not found"})

    def _trace(self, run_id):
        store = self.server.app.store
        return build_trace(store.get_run(run_id), store.get_run_events(run_id))

    def _advance(self, run_id):
        control = self.server.app.runs.get(run_id)
        if control is None or not control.team.step_next():
            return self._send_json(409, {"error": "this run is not paused, so there is no step to advance"})
        self._send_json(202, {"ok": True})

    def _set_step(self, run_id):
        app = self.server.app
        enabled = self._body().get("enabled")
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be true or false")
        control = app.runs.get(run_id)
        if control is None or not control.thread.is_alive():
            return self._send_json(409, {"error": "this run is not running"})
        control.team.set_step_mode(enabled)
        app.store.add_event(run_id, "step_mode", {"enabled": enabled})
        self._send_json(202, {"enabled": enabled})

    def _review_for(self, run_id):
        app = self.server.app
        if not app.workspaces or not app.baselines:
            raise ValueError("this server has no workspaces, so there is nothing to review")
        review = WorkspaceReview(app.workspaces / run_id, app.baselines / run_id)
        review.ensure_baseline()
        return review

    def _review_change(self, run_id):
        """Accept or reject one changed file. Refused while the run is working, so a write cannot race a review."""
        app = self.server.app
        body = self._body()
        action, path = body.get("action"), str(body.get("path", ""))
        if action not in ("accept", "reject") or not path:
            raise ValueError("say which file, and whether to accept or reject it")
        control = app.runs.get(run_id)
        if control is not None and control.thread.is_alive():
            return self._send_json(409, {"error": "wait for the run to finish or stop it before reviewing its files"})
        review = self._review_for(run_id)
        if action == "accept":
            review.accept(path)
        else:
            review.reject(path)
        self._send_json(200, {"changes": review.changes()})

    def _approval(self, run_id, call_id):
        """The page's answer to a tool call that is waiting: approve (optionally with changed arguments), or reject."""
        app = self.server.app
        body = self._body()
        action = body.get("action")
        if action not in ("approve", "reject"):
            raise ValueError("the action is approve or reject")
        decision = {"action": action, "reason": str(body.get("reason", ""))[:MAX_TEXT]}
        if isinstance(body.get("arguments"), dict):
            decision["arguments"] = body["arguments"]
        control = app.runs.get(run_id)
        if control is None or not control.thread.is_alive():
            return self._send_json(409, {"error": "this run is not running, so nothing is waiting for approval"})
        if not control.team.decide_approval(call_id, decision):
            return self._send_json(404, {"error": "no approval is waiting with that id"})
        self._send_json(202, {"ok": True})

    def _rewind(self, run_id):
        """Put a run back at an earlier turn, then continue it with a message. Later turns are dropped from the
        checkpoints; their events stay in the log, so the history shows what happened."""
        app = self.server.app
        body = self._body()
        turn, text = _turn_number(body), _message_text(body)
        with app.lock:
            app.forget_finished()
            control = app.runs.get(run_id)
            if control is not None and control.thread.is_alive():
                return self._send_json(409, {"error": "stop the run before rewinding it"})
            snapshot = app.store.get_checkpoint(run_id, turn)
            if snapshot is None:
                return self._send_json(404, {"error": f"there is no checkpoint for turn {turn}"})
            if app.active_count() >= app.max_active_runs:
                return self._send_json(429, {"error": "too many runs are running; wait for one to finish or stop one"})
            app.store.discard_checkpoints_after(run_id, turn)
            app.store.save_state(run_id, snapshot)
            if control is None:
                control = self._restore_control(run_id, snapshot)
            else:
                control.team.restore(snapshot)
            app.runs[run_id] = control
            app.store.add_event(run_id, "rewind", {"to_turn": turn})
            app.store.resume_run(run_id)
            app.store.add_event(run_id, "human", {"text": text})
            control.stop.clear()
            control.thread = run_in_background(
                app.store, run_id, control.team, lambda: control.team.continue_run(text), control.secrets
            )
        self._send_json(202, {"resumed": True})

    def _branch(self, run_id):
        """Start a new run from a turn of this one. The parent keeps its history; the new run gets a copy of its
        workspace."""
        app = self.server.app
        body = self._body()
        turn, text = _turn_number(body), _message_text(body)
        record = app.store.get_run_record(run_id)
        snapshot = app.store.get_checkpoint(run_id, turn)
        if snapshot is None:
            return self._send_json(404, {"error": f"there is no checkpoint for turn {turn}"})
        if not record["config"]:
            raise ValueError("this run cannot be branched: it was started before team configs were saved")
        with app.lock:
            app.forget_finished()
            if app.active_count() >= app.max_active_runs:
                return self._send_json(429, {"error": "too many runs are running; wait for one to finish or stop one"})
            new_id = uuid.uuid4().hex
            control = RunControl()
            team, secrets = _build_for_run(app, new_id, record["config"], control)
            if app.workspaces:
                # The branch starts from the parent's files and its accepted state. The parent's unreviewed
                # changes show up as pending in the branch, to review there.
                copy_workspace(app.workspaces / run_id, app.workspaces / new_id)
                parent_review = self._review_for(run_id)
                copy_workspace(parent_review.baseline, app.baselines / new_id)
            app.store.create_run(new_id, record["goal"], record["config"], parent_id=run_id, parent_turn=turn)
            app.store.save_state(new_id, snapshot)
            team.restore(snapshot)
            team.announce()
            app.store.add_event(new_id, "branched", {"from_run": run_id, "turn": turn})
            app.store.add_event(new_id, "human", {"text": text})
            control.team, control.secrets = team, secrets
            control.thread = run_in_background(app.store, new_id, team, lambda: team.continue_run(text), secrets)
            app.runs[new_id] = control
        self._send_json(201, {"id": new_id})

    def _start_run(self):
        app = self.server.app
        body = self._body()
        goal = str(body.get("goal", "")).strip()
        if not goal or len(goal) > MAX_TEXT:
            raise ValueError(f"the goal must be 1 to {MAX_TEXT} characters")
        with app.lock:  # the cap check and the registration must happen together
            app.forget_finished()
            active = app.active_count()
            if active >= app.max_active_runs:
                return self._send_json(
                    429, {"error": f"{active} runs are already running; wait for one to finish or stop one"}
                )
            run_id = uuid.uuid4().hex
            control = RunControl()
            team, secret_values = _build_for_run(app, run_id, body.get("config"), control)
            app.store.create_run(run_id, goal, body.get("config"))
            if app.workspaces:
                self._review_for(run_id)  # a new run starts with nothing accepted yet
            control.team, control.secrets = team, secret_values
            control.thread = run_in_background(app.store, run_id, team, lambda: team.run(goal), secret_values)
            app.runs[run_id] = control
        self._send_json(201, {"id": run_id})

    def _human_message(self, run_id):
        app = self.server.app
        text = str(self._body().get("text", "")).strip()[:MAX_TEXT]
        if not text:
            raise ValueError("the message is empty")
        # One lock decides whether this message queues on a live run or continues a finished one. Without it,
        # two messages sent together could both continue the same run.
        with app.lock:
            app.forget_finished()
            control = app.runs.get(run_id)
            if control is not None and control.thread.is_alive():
                control.human.put(text)
                app.store.add_event(run_id, "human", {"text": text})
                return self._send_json(202, {"resumed": False})
            # The run has ended: continue it with the same agents and their histories.
            if app.active_count() >= app.max_active_runs:
                return self._send_json(429, {"error": "too many runs are running; wait for one to finish or stop one"})
            if control is None:
                control = self._restore_control(run_id)
                app.runs[run_id] = control
            app.store.resume_run(run_id)
            app.store.add_event(run_id, "human", {"text": text})
            control.stop.clear()
            control.thread = run_in_background(
                app.store, run_id, control.team, lambda: control.team.continue_run(text), control.secrets
            )
        self._send_json(202, {"resumed": True})

    def _restore_control(self, run_id, snapshot=None):
        """Rebuild a run's team after a server restart, or at a given checkpoint, from the saved config."""
        app = self.server.app
        record = app.store.get_run_record(run_id)
        if record is None or not record["config"]:
            raise ValueError("this run cannot be continued: it was started before team configs were saved")
        control = RunControl()
        team, secrets = _build_for_run(app, run_id, record["config"], control)
        if snapshot is None:
            snapshot = record["state"]
        if snapshot:
            team.restore(snapshot)
        control.team, control.secrets = team, secrets
        return control

    def _presets(self, method, rest):
        store = self.server.app.store
        if method == "GET" and not rest:
            return self._send_json(200, {"presets": store.list_presets()})
        if method == "POST" and not rest:
            body = self._body()
            name = str(body.get("name", "")).strip()
            if not 1 <= len(name) <= 60:
                raise ValueError("a preset needs a name of 1 to 60 characters")
            config = body.get("config")
            if not isinstance(config, dict):
                raise ValueError("a preset needs a team config")
            refuse_secret_fields(config)
            store.save_preset(name, config)
            return self._send_json(201, {"presets": store.list_presets()})
        if len(rest) == 1 and method == "GET":
            config = store.get_preset(rest[0])
            if config is None:
                return self._send_json(404, {"error": "no preset with that name"})
            return self._send_json(200, {"config": config})
        if len(rest) == 1 and method == "DELETE":
            store.delete_preset(rest[0])
            return self._send_json(200, {"presets": store.list_presets()})
        self._send_json(404, {"error": "not found"})

    def _export(self, run_id, query):
        store = self.server.app.store
        run = store.get_run(run_id)
        events = store.get_run_events(run_id)
        fmt = (query.get("format") or ["md"])[0]
        if fmt == "md":
            body, content_type, extension = to_markdown(run, events).encode(), "text/markdown; charset=utf-8", "md"
        elif fmt == "json":
            body, content_type, extension = to_json(run, events).encode(), "application/json; charset=utf-8", "json"
        else:
            raise ValueError("format must be md or json")
        self._send(200, body, content_type, {"Content-Disposition": f'attachment; filename="run-{run_id[:8]}.{extension}"'})

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
        sent_live = {}  # agent -> live text last sent on this connection
        try:
            while True:
                version = store.current_version()
                run = store.get_run(run_id)
                rows = store.events_after(run_id, after)
                for seq, kind, data in rows:
                    self.wfile.write(f"id: {seq}\nevent: {kind}\ndata: {json.dumps(data)}\n\n".encode())
                    after = seq
                current = store.live_text(run_id)
                for agent in set(current) | set(sent_live):
                    text = current.get(agent, "")
                    if sent_live.get(agent) != text:
                        # Live text is not saved: a reconnect only sees what is streaming at that moment.
                        self.wfile.write(f"event: live\ndata: {json.dumps({'agent': agent, 'text': text})}\n\n".encode())
                        sent_live[agent] = text
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

    def _send(self, status, body, content_type, headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in {**SECURITY_HEADERS, **(headers or {})}.items():
            self.send_header(name, value)
        if body is not None:
            self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body is not None:
            self.wfile.write(body)


def _turn_number(body):
    try:
        return int(body.get("turn"))
    except (TypeError, ValueError):
        raise ValueError("choose a turn number") from None


def _message_text(body):
    text = str(body.get("message", "")).strip()[:MAX_TEXT]
    if not text:
        raise ValueError("write the message to continue with")
    return text


def _build_for_run(app, run_id, config, control):
    """Build a run's team wired to the event log, the run's controls, and a checkpoint after each turn."""
    tools = build_tools(app.workspaces / run_id) if app.workspaces else None
    return build_team(
        config,
        app.vault.get,
        emit=lambda kind, **data: app.store.add_event(run_id, kind, data),
        human_inbox=control.human,
        stop_event=control.stop,
        save_state=lambda snapshot: app.store.save_state(run_id, snapshot),
        tools=tools,
        on_live=lambda agent, text: app.store.set_live(run_id, agent, text),
    )


def _env_keys():
    """Which default providers have their key in the environment. Only a yes or no, never the key."""
    return {
        name: bool(os.environ.get(spec["api_key_env"]))
        for name, spec in default_config()["providers"].items()
        if spec.get("api_key_env")
    }


def serve(port=8765, data_dir="data"):
    data = Path(data_dir)
    lock = acquire_data_lock(data)  # held until the process exits
    store = Store(data / "agent_team.db")
    store.mark_interrupted()
    token = secrets.token_urlsafe(24)
    server = Server(port, App(store, KeyVault(data), token, workspaces=data / "workspaces", baselines=data / "accepted"))
    print(f"Agent team is running. Open http://127.0.0.1:{server.server_port}/?token={token}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
