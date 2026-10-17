import json
import os
import stat
import tempfile
import threading
import time
import unittest
import zipfile
from io import BytesIO
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from cryptography.fernet import Fernet

from agent_team.keys import KeyVault, KeyVaultError
from agent_team.server import App, Server
from agent_team.store import Store


class StubProvider(BaseHTTPRequestHandler):
    """Answers every chat completion by calling the finish tool, like a tool-calling OpenAI-compatible server.

    Streamed requests get the same answer as server-sent events, split across chunks, with some text first.
    """

    def stream_finish(self):
        chunks = [
            {"choices": [{"delta": {"content": "thinking..."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "finish", "arguments": '{"answer": "hello '}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'from stub"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"total_tokens": 3}},
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        if body.get("stream"):
            return self.stream_finish()
        finish = {"id": "call_1", "type": "function", "function": {"name": "finish", "arguments": json.dumps({"answer": "hello from stub"})}}
        payload = json.dumps({"choices": [{"message": {"content": None, "tool_calls": [finish]}, "finish_reason": "tool_calls"}]}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


def start_http_server(address, handler):
    server = HTTPServer(address, handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class StoreTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = Store(Path(directory.name) / "runs.db")
        self.addCleanup(self.store.db.close)

    def test_events_replay_in_order_after_a_sequence_number(self):
        self.store.create_run("r1", "goal")
        self.store.add_event("r1", "message", {"text": "a"})
        self.store.add_event("r1", "message", {"text": "b"})

        rows = self.store.events_after("r1", 0)
        self.assertEqual([data["text"] for _, _, data in rows], ["a", "b"])
        self.assertEqual([data["text"] for _, _, data in self.store.events_after("r1", rows[0][0])], ["b"])

    def test_finish_writes_a_status_event_and_updates_the_run(self):
        self.store.create_run("r1", "goal")
        self.store.finish_run("r1", "done")

        self.assertEqual(self.store.get_run("r1")["status"], "done")
        self.assertEqual(self.store.events_after("r1", 0)[-1][1:], ("status", {"status": "done"}))

    def test_runs_left_running_are_marked_interrupted_on_startup(self):
        self.store.create_run("r1", "goal")
        self.store.mark_interrupted()

        self.assertEqual(self.store.get_run("r1")["status"], "interrupted")
        self.assertEqual(self.store.events_after("r1", 0)[-1][2], {"status": "interrupted"})


class KeyVaultTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.master = self.root / "master.key"

    def test_memory_only_keys_never_touch_disk(self):
        vault = KeyVault(None, master_key_path=self.master)
        vault.set("anthropic", "sk-ant-1234")

        self.assertEqual(vault.get("anthropic"), "sk-ant-1234")
        self.assertEqual(vault.status()["anthropic"], {"persisted": False, "hint": "••••1234"})
        self.assertFalse(self.master.exists())

    def test_saved_keys_are_encrypted_and_readable_with_the_master_key(self):
        data = self.root / "data"
        KeyVault(data, master_key_path=self.master).set("openai", "sk-saved-9999", persist=True)

        self.assertNotIn("sk-saved", (data / "keys.json").read_text())
        self.assertEqual(stat.S_IMODE(os.stat(self.master).st_mode), 0o600)
        self.assertEqual(KeyVault(data, master_key_path=self.master).get("openai"), "sk-saved-9999")

    def test_saved_keys_cannot_be_read_with_a_different_master_key(self):
        data = self.root / "data"
        KeyVault(data, master_key_path=self.master).set("openai", "sk-saved-9999", persist=True)
        other = self.root / "other.key"
        other.write_bytes(Fernet.generate_key())

        with self.assertRaisesRegex(KeyVaultError, "enter it again"):
            KeyVault(data, master_key_path=other).get("openai")


def decode(raw):
    """JSON for API replies, plain text for event streams."""
    text = raw.decode()
    try:
        return json.loads(text or "{}")
    except json.JSONDecodeError:
        return text


class CheckpointStoreTests(unittest.TestCase):
    def test_checkpoints_are_listed_pruned_and_discarded(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = Store(Path(directory.name) / "runs.db", keep_checkpoints=3)
        self.addCleanup(store.db.close)
        store.create_run("r", "goal")
        for turn in range(1, 6):
            store.save_state("r", {"turn": turn, "agent": "lead", "tokens_used": turn, "agents": {}})

        self.assertEqual([row["turn"] for row in store.list_checkpoints("r")], [3, 4, 5])
        self.assertEqual(store.get_checkpoint("r", 4)["turn"], 4)
        store.discard_checkpoints_after("r", 4)
        self.assertEqual([row["turn"] for row in store.list_checkpoints("r")], [3, 4])


class TwoAgentStub(BaseHTTPRequestHandler):
    """A lead and a writer. The lead asks the writer for a draft, then finishes once the reply arrives."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        system, messages = body["messages"][0]["content"], body["messages"][1:]
        if "You are 'lead'" in system:
            name, arguments = (
                ("send_message", {"to": "writer", "message": "draft please"})
                if len(messages) <= 1
                else ("finish", {"answer": "final answer"})
            )
        else:
            name, arguments = "send_message", {"to": "lead", "message": "here is the draft"}
        call = {"index": 0, "id": "call_stub", "function": {"name": name, "arguments": json.dumps(arguments)}}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"tool_calls": [call]}}]}, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


class FileStub(BaseHTTPRequestHandler):
    """The lead asks the writer for a note. The writer writes note.md, then reports. The lead finishes after that."""

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        system, messages = body["messages"][0]["content"], body["messages"][1:]
        if "You are 'lead'" in system:
            name, arguments = ("send_message", {"to": "writer", "message": "write the note"}) if len(messages) <= 1 else ("finish", {"answer": "done"})
        elif any(message["role"] == "tool" for message in messages):
            name, arguments = "send_message", {"to": "lead", "message": "written"}
        else:
            name, arguments = "write_file", {"path": "note.md", "content": "draft text"}
        call = {"index": 0, "id": "call_file", "function": {"name": name, "arguments": json.dumps(arguments)}}
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in ({"choices": [{"delta": {"tool_calls": [call]}}]}, {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]}):
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


class TraceAndStepTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)
        stub = start_http_server(("127.0.0.1", 0), TwoAgentStub)
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"
        self.store = Store(self.root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        vault = KeyVault(self.root / "data", master_key_path=self.root / "master.key")
        vault.set("stub", "sk-trace-6666")
        self.server = Server(0, App(self.store, vault, "tok", workspaces=self.root / "workspaces"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Session-Token": "tok", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def config(self, **extra):
        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "plan"},
            "teammates": [{"name": "writer", "provider": "stub", "model": "m", "role": "write"}],
        }
        config.update(extra)
        return config

    def wait_for_kind(self, run_id, kind, count, timeout=10):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seen = [row for row in self.store.events_after(run_id, 0) if row[1] == kind]
            if len(seen) >= count:
                return
            time.sleep(0.03)
        raise AssertionError(f"fewer than {count} {kind} events")

    def test_the_trace_lists_every_turn_with_its_tool_calls(self):
        _, _, body = self.call("POST", "/api/runs", {"config": self.config(), "goal": "write a note"})
        run_id = json.loads(body)["id"]
        self.call("GET", f"/api/runs/{run_id}/events")

        _, _, raw = self.call("GET", f"/api/runs/{run_id}/trace")
        turns = json.loads(raw)["turns"]
        self.assertEqual([turn["agent"] for turn in turns], ["lead", "writer", "lead"])
        self.assertIn("send_message", [child.get("tool") for child in turns[0]["children"]])

        _, headers, exported = self.call("GET", f"/api/runs/{run_id}/trace.json")
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(json.loads(exported)["run"]["id"], run_id)

    def test_step_mode_waits_for_each_advance(self):
        _, _, body = self.call("POST", "/api/runs", {"config": self.config(step_mode=True), "goal": "write a note"})
        run_id = json.loads(body)["id"]

        self.wait_for_kind(run_id, "paused", 1)
        for pauses in (1, 2):
            status, _, _ = self.call("POST", f"/api/runs/{run_id}/advance", {})
            self.assertEqual(status, 202)
            if pauses == 1:
                self.wait_for_kind(run_id, "paused", 2)
        _, _, events = self.call("GET", f"/api/runs/{run_id}/events")

        self.assertEqual(events.decode().count("event: paused"), 2)
        self.assertEqual(events.decode().count("event: final"), 1)

    def test_advancing_a_run_that_is_not_paused_is_refused(self):
        _, _, body = self.call("POST", "/api/runs", {"config": self.config(), "goal": "write a note"})
        run_id = json.loads(body)["id"]
        self.call("GET", f"/api/runs/{run_id}/events")

        status, _, _ = self.call("POST", f"/api/runs/{run_id}/advance", {})
        self.assertEqual(status, 409)


class ReviewTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)
        stub = start_http_server(("127.0.0.1", 0), FileStub)
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"
        self.store = Store(self.root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        vault = KeyVault(self.root / "data", master_key_path=self.root / "master.key")
        vault.set("stub", "sk-review-4444")
        self.workspaces = self.root / "workspaces"
        self.accepted = self.root / "accepted"
        self.server = Server(0, App(self.store, vault, "tok", workspaces=self.workspaces, baselines=self.accepted))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Session-Token": "tok", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def start_run(self):
        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "plan"},
            "teammates": [{"name": "writer", "provider": "stub", "model": "m", "role": "write", "tools": ["files"], "approve": ["write_file"]}],
        }
        status, body = self.call("POST", "/api/runs", {"config": config, "goal": "write a note"})
        return json.loads(body)["id"]

    def wait_for_approval(self, run_id):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            for _, kind, data in self.store.events_after(run_id, 0):
                if kind == "approval":
                    return data
            time.sleep(0.05)
        raise AssertionError("no approval was requested")

    def test_an_approved_write_is_reviewed_accepted_and_exported(self):
        run_id = self.start_run()
        approval = self.wait_for_approval(run_id)
        self.assertEqual(approval["tool"], "write_file")
        status, _ = self.call(
            "POST",
            f"/api/runs/{run_id}/approvals/{approval['id']}",
            {"action": "approve", "arguments": {"path": "note.md", "content": "edited text"}},
        )
        self.assertEqual(status, 202)
        _, events = self.call("GET", f"/api/runs/{run_id}/events")
        self.assertIn("event: approval_result", events.decode())

        _, changes = self.call("GET", f"/api/runs/{run_id}/changes")
        pending = json.loads(changes)["changes"]
        self.assertEqual([(change["path"], change["status"]) for change in pending], [("note.md", "added")])
        self.assertIn("+edited text", pending[0]["diff"])

        status, _ = self.call("POST", f"/api/runs/{run_id}/changes", {"path": "note.md", "action": "accept"})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(self.call("GET", f"/api/runs/{run_id}/changes")[1])["changes"], [])

        _, archive = self.call("GET", f"/api/runs/{run_id}/workspace.zip")
        with zipfile.ZipFile(BytesIO(archive)) as zipped:
            self.assertEqual(zipped.read("note.md"), b"edited text")

    def test_a_rejected_write_never_reaches_the_workspace(self):
        run_id = self.start_run()
        approval = self.wait_for_approval(run_id)
        self.call("POST", f"/api/runs/{run_id}/approvals/{approval['id']}", {"action": "reject", "reason": "not now"})
        _, events = self.call("GET", f"/api/runs/{run_id}/events")

        self.assertFalse((self.workspaces / run_id / "note.md").exists())
        self.assertIn("event: final", events.decode())
        self.assertEqual(json.loads(self.call("GET", f"/api/runs/{run_id}/changes")[1])["changes"], [])

    def test_a_decision_for_an_unknown_call_is_refused(self):
        run_id = self.start_run()
        approval = self.wait_for_approval(run_id)
        status, _ = self.call("POST", f"/api/runs/{run_id}/approvals/no-such-call", {"action": "approve"})
        self.assertEqual(status, 404)
        self.call("POST", f"/api/runs/{run_id}/approvals/{approval['id']}", {"action": "reject"})
        self.call("GET", f"/api/runs/{run_id}/events")  # the run can finish once its approval is answered

    def test_reviewing_is_refused_while_the_run_is_working(self):
        run_id = self.start_run()
        approval = self.wait_for_approval(run_id)
        status, _ = self.call("POST", f"/api/runs/{run_id}/changes", {"path": "note.md", "action": "accept"})
        self.assertEqual(status, 409)
        self.call("POST", f"/api/runs/{run_id}/approvals/{approval['id']}", {"action": "reject"})
        self.call("GET", f"/api/runs/{run_id}/events")


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)
        stub = start_http_server(("127.0.0.1", 0), TwoAgentStub)
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"
        self.store = Store(self.root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        vault = KeyVault(self.root / "data", master_key_path=self.root / "master.key")
        vault.set("stub", "sk-checkpoint-2222")
        self.workspaces = self.root / "workspaces"
        self.server = Server(0, App(self.store, vault, "tok", workspaces=self.workspaces))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Session-Token": "tok", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, decode(error.read())

    def finished_run(self):
        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "plan"},
            "teammates": [{"name": "writer", "provider": "stub", "model": "m", "role": "write"}],
        }
        _, body = self.call("POST", "/api/runs", {"config": config, "goal": "write a note"})
        self.call("GET", f"/api/runs/{body['id']}/events")  # returns once the run has finished
        return body["id"]

    def test_each_turn_is_a_checkpoint(self):
        run_id = self.finished_run()
        _, checkpoints = self.call("GET", f"/api/runs/{run_id}/checkpoints")

        self.assertEqual([row["turn"] for row in checkpoints["checkpoints"]], [1, 2, 3])
        self.assertEqual(checkpoints["checkpoints"][0]["agent"], "lead")

    def test_rewind_restores_a_turn_and_continues(self):
        run_id = self.finished_run()
        status, body = self.call("POST", f"/api/runs/{run_id}/rewind", {"turn": 1, "message": "try again"})

        self.assertEqual((status, body["resumed"]), (202, True))
        _, events = self.call("GET", f"/api/runs/{run_id}/events")
        self.assertIn("event: rewind", events)
        self.assertEqual(events.count("event: final"), 2)

    def test_rewind_to_an_unknown_turn_is_refused(self):
        run_id = self.finished_run()
        status, _ = self.call("POST", f"/api/runs/{run_id}/rewind", {"turn": 99, "message": "x"})
        self.assertEqual(status, 404)

    def test_branch_leaves_the_parent_unchanged_and_copies_its_workspace(self):
        run_id = self.finished_run()
        (self.workspaces / run_id).mkdir(parents=True, exist_ok=True)
        (self.workspaces / run_id / "note.txt").write_text("draft")
        _, parent_before = self.call("GET", f"/api/runs/{run_id}/events")

        status, body = self.call("POST", f"/api/runs/{run_id}/branch", {"turn": 1, "message": "another way"})
        self.assertEqual(status, 201)
        branch_id = body["id"]
        _, branch_events = self.call("GET", f"/api/runs/{branch_id}/events")

        self.assertIn("event: roster", branch_events)
        self.assertIn("event: branched", branch_events)
        self.assertEqual(branch_events.count("event: final"), 1)
        self.assertEqual((self.workspaces / branch_id / "note.txt").read_text(), "draft")
        self.assertEqual(self.store.get_run_record(branch_id)["parent_id"], run_id)
        _, parent_after = self.call("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(parent_after, parent_before)


class ConcurrencyTests(unittest.TestCase):
    """Each test holds a run open on a gate, so races between requests happen every time."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)
        stub = start_http_server(("127.0.0.1", 0), StubProvider)
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"
        self.store = Store(self.root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        vault = KeyVault(self.root / "data", master_key_path=self.root / "master.key")
        vault.set("stub", "sk-concurrency-1111")
        self.server = Server(0, App(self.store, vault, "tok"))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.gate = threading.Event()
        self.addCleanup(self.gate.set)

    def config(self):
        return {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "answer"},
            "teammates": [],
        }

    def call(self, method, path, body=None):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Session-Token": "tok", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, decode(response.read())
        except urllib.error.HTTPError as error:
            return error.code, decode(error.read())

    def gated_runs(self):
        """Runs stay alive until self.gate is set. Starting one takes 0.3 seconds, inside the server."""
        from agent_team import server as server_module

        real = server_module.run_in_background
        gate = self.gate

        def gated(store, run_id, team, action, secrets):
            def held():
                gate.wait(5)
                return action()

            thread = real(store, run_id, team, held, secrets)
            time.sleep(0.3)
            return thread

        return mock.patch.object(server_module, "run_in_background", side_effect=gated)

    def test_simultaneous_messages_to_a_finished_run_continue_once(self):
        status, body = self.call("POST", "/api/runs", {"config": self.config(), "goal": "say hi"})
        run_id = body["id"]
        self.gate.set()
        self.call("GET", f"/api/runs/{run_id}/events")
        self.gate.clear()

        results = []
        with self.gated_runs():
            threads = [
                threading.Thread(target=lambda text=text: results.append(self.call("POST", f"/api/runs/{run_id}/message", {"text": text})[1]))
                for text in ("first", "second")
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)
        self.gate.set()
        self.call("GET", f"/api/runs/{run_id}/events")

        self.assertEqual(sorted(result["resumed"] for result in results), [False, True])
        kinds = [kind for _, kind, _ in self.store.events_after(run_id, 0)]
        self.assertEqual(kinds.count("final"), 2)

    def test_simultaneous_starts_respect_the_run_cap(self):
        self.server.app.max_active_runs = 1
        results = []
        with self.gated_runs():
            threads = [
                threading.Thread(target=lambda: results.append(self.call("POST", "/api/runs", {"config": self.config(), "goal": "g"})[0]))
                for _ in range(3)
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(10)

        self.assertEqual(sorted(results), [201, 429, 429])

    def test_finished_runs_are_released_from_memory(self):
        self.gate.set()
        _, first = self.call("POST", "/api/runs", {"config": self.config(), "goal": "first"})
        self.call("GET", f"/api/runs/{first['id']}/events")
        _, second = self.call("POST", "/api/runs", {"config": self.config(), "goal": "second"})

        self.assertNotIn(first["id"], self.server.app.runs)
        status, body = self.call("POST", f"/api/runs/{first['id']}/message", {"text": "again"})
        self.assertEqual((status, body["resumed"]), (202, True))


class LockTests(unittest.TestCase):
    def test_a_second_server_on_the_same_data_directory_is_refused(self):
        from agent_team.locks import acquire_data_lock

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        first = acquire_data_lock(Path(directory.name))
        self.addCleanup(first.close)

        with self.assertRaisesRegex(RuntimeError, "already in use"):
            acquire_data_lock(Path(directory.name))


class CliTests(unittest.TestCase):
    def test_provider_errors_on_the_command_line_hide_the_key(self):
        import contextlib
        import io
        from agent_team.__main__ import main

        class Echo(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers["content-length"]))
                payload = json.dumps({"error": {"message": f"rejected {self.headers['authorization']}"}}).encode()
                self.send_response(400)
                self.send_header("content-length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args):
                pass

        server = start_http_server(("127.0.0.1", 0), Echo)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        config = Path(directory.name) / "team.json"
        config.write_text(json.dumps({
            "providers": {"p": {"type": "openai_compatible", "base_url": f"http://127.0.0.1:{server.server_port}", "api_key_env": "TEAM_CLI_KEY"}},
            "lead": {"name": "lead", "provider": "p", "model": "m", "role": "answer"},
            "teammates": [],
        }))
        errors = io.StringIO()
        with mock.patch.dict(os.environ, {"TEAM_CLI_KEY": "sk-cli-secret-9999", "NO_PROXY": "127.0.0.1"}), contextlib.redirect_stderr(errors):
            code = main(["run", str(config), "goal"])

        self.assertEqual(code, 2)
        self.assertIn("[redacted]", errors.getvalue())
        self.assertNotIn("sk-cli-secret-9999", errors.getvalue())


class TranscriptTests(unittest.TestCase):
    def test_markdown_shows_messages_answer_and_end_status(self):
        from agent_team.transcript import to_markdown

        run = {"goal": "Write a note", "status": "done", "created_at": 0, "finished_at": 5}
        events = [
            {"kind": "message", "data": {"sender": "lead", "recipient": "writer", "text": "draft it"}},
            {"kind": "final", "data": {"answer": "hello"}},
            {"kind": "status", "data": {"status": "done"}},
        ]

        text = to_markdown(run, events)
        self.assertIn("# Write a note", text)
        self.assertIn("**lead → writer**", text)
        self.assertIn("> draft it", text)
        self.assertIn("## Final answer\n\nhello", text)
        self.assertIn("*Run ended: done*", text)

    def test_json_keeps_every_event(self):
        import json as json_module
        from agent_team.transcript import to_json

        run = {"id": "r1", "goal": "g", "status": "done", "created_at": 0, "finished_at": 1}
        events = [{"kind": "human", "data": {"text": "hi"}, "created_at": 0}]

        document = json_module.loads(to_json(run, events))
        self.assertEqual(document["run"]["id"], "r1")
        self.assertEqual(document["events"], events)


class ServerTests(unittest.TestCase):
    TOKEN = "test-token"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)

        stub = start_http_server(("127.0.0.1", 0), StubProvider)
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"

        data = self.root / "data"
        store = Store(data / "agent_team.db")
        self.addCleanup(store.db.close)
        self.store = store
        vault = KeyVault(data, master_key_path=self.root / "master.key")
        self.server = Server(0, App(store, vault, self.TOKEN))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def request(self, method, path, body=None, headers=None):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"X-Session-Token": self.TOKEN, "Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read().decode()
        except urllib.error.HTTPError as error:
            return error.code, error.read().decode()

    def test_run_completes_with_a_saved_key_and_never_echoes_it(self):
        status, body = self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-test-1234"})
        self.assertEqual(status, 200)
        self.assertIn("1234", body)
        self.assertNotIn("sk-test-1234", body)

        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "answer"},
            "teammates": [],
        }
        status, body = self.request("POST", "/api/runs", {"config": config, "goal": "say hi"})
        self.assertEqual(status, 201)
        run_id = json.loads(body)["id"]

        # The event stream closes once the run has finished, so reading it returns the whole run.
        status, events = self.request("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(status, 200)
        self.assertIn("event: final", events)
        self.assertIn("hello from stub", events)
        self.assertIn('{"status": "done"}', events)
        self.assertNotIn("sk-test-1234", events)

    def test_concurrent_runs_are_capped(self):
        self.server.app.max_active_runs = 0
        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "answer"},
            "teammates": [],
        }
        status, body = self.request("POST", "/api/runs", {"config": config, "goal": "say hi"})
        self.assertEqual(status, 429)
        self.assertIn("already running", json.loads(body)["error"])

    def stub_config(self):
        return {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "answer"},
            "teammates": [],
        }

    def test_a_finished_run_continues_with_a_message(self):
        self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-resume-0000"})
        status, body = self.request("POST", "/api/runs", {"config": self.stub_config(), "goal": "say hi"})
        run_id = json.loads(body)["id"]
        self.request("GET", f"/api/runs/{run_id}/events")  # returns once the first run has finished

        status, body = self.request("POST", f"/api/runs/{run_id}/message", {"text": "and again"})
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["resumed"])

        status, events = self.request("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(events.count("event: final"), 2)
        self.assertIn("and again", events)

    def test_a_run_survives_a_restart_and_continues(self):
        self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-restart-7777", "persist": True})
        status, body = self.request("POST", "/api/runs", {"config": self.stub_config(), "goal": "say hi"})
        run_id = json.loads(body)["id"]
        self.request("GET", f"/api/runs/{run_id}/events")

        # A second server process with the same data directory: nothing is held in memory.
        data = self.root / "data"
        store = Store(data / "agent_team.db")
        self.addCleanup(store.db.close)
        store.mark_interrupted()
        restarted = Server(0, App(store, KeyVault(data, master_key_path=self.root / "master.key"), self.TOKEN))
        threading.Thread(target=restarted.serve_forever, daemon=True).start()
        self.addCleanup(restarted.server_close)
        self.addCleanup(restarted.shutdown)
        self.base = f"http://127.0.0.1:{restarted.server_port}"

        status, body = self.request("POST", f"/api/runs/{run_id}/message", {"text": "and after the restart"})
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(body)["resumed"])

        status, events = self.request("GET", f"/api/runs/{run_id}/events")
        self.assertEqual(events.count("event: final"), 2)
        self.assertIn("and after the restart", events)
        saved = store.get_run_record(run_id)["state"]
        self.assertGreater(len(saved["agents"]["lead"]), 2)

    def test_keys_inside_the_config_are_refused(self):
        config = self.stub_config()
        config["providers"]["stub"]["api_key"] = "sk-pasted-in-config"
        status, body = self.request("POST", "/api/runs", {"config": config, "goal": "say hi"})

        self.assertEqual(status, 400)
        self.assertIn("API keys panel", json.loads(body)["error"])
        self.assertNotIn("sk-pasted-in-config", body)

    def test_live_text_reaches_the_stream_but_is_not_stored(self):
        self.store.create_run("live-run", "goal")
        self.store.set_live("live-run", "lead", "thinking out loud")
        request = urllib.request.Request(self.base + "/api/runs/live-run/events", headers={"X-Session-Token": self.TOKEN})
        response = urllib.request.urlopen(request, timeout=10)
        seen = []
        for raw in response:
            line = raw.decode().strip()
            seen.append(line)
            if "thinking out loud" in line:
                break
        self.store.finish_run("live-run", "done")
        response.close()

        self.assertIn("event: live", seen)
        self.assertEqual([kind for _, kind, _ in self.store.events_after("live-run", 0)], ["status"])

    def test_key_test_checks_a_key_and_model_without_echoing_it(self):
        self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-check-5555"})
        status, body = self.request("POST", "/api/providers/stub/test", {"model": "m", "spec": {"type": "openai_compatible", "base_url": self.stub_base}})

        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)["ok"])
        self.assertNotIn("sk-check-5555", body)

    def test_providers_report_environment_keys_as_yes_or_no(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": "sk-from-env"}):
            status, body = self.request("GET", "/api/providers")

        self.assertEqual(status, 200)
        env_keys = json.loads(body)["env_keys"]
        self.assertIs(env_keys["openai"], True)
        self.assertNotIn("sk-from-env", body)

    def test_invalid_member_names_are_refused(self):
        config = self.stub_config()
        config["lead"]["name"] = "Lead Agent"
        status, body = self.request("POST", "/api/runs", {"config": config, "goal": "say hi"})

        self.assertEqual(status, 400)
        self.assertIn("needs a name", json.loads(body)["error"])

    def test_presets_save_list_load_and_delete(self):
        config = self.stub_config()
        status, _ = self.request("POST", "/api/presets", {"name": "stub team", "config": config})
        self.assertEqual(status, 201)

        status, body = self.request("GET", "/api/presets")
        self.assertEqual([preset["name"] for preset in json.loads(body)["presets"]], ["stub team"])

        status, body = self.request("GET", "/api/presets/stub%20team")
        self.assertEqual(json.loads(body)["config"]["lead"]["name"], "lead")

        status, body = self.request("DELETE", "/api/presets/stub%20team")
        self.assertEqual(json.loads(body)["presets"], [])
        status, _ = self.request("GET", "/api/presets/stub%20team")
        self.assertEqual(status, 404)

    def test_presets_refuse_keys_and_empty_names(self):
        config = self.stub_config()
        config["providers"]["stub"]["api_key"] = "sk-in-preset"
        status, body = self.request("POST", "/api/presets", {"name": "bad", "config": config})
        self.assertEqual(status, 400)
        self.assertNotIn("sk-in-preset", body)

        status, _ = self.request("POST", "/api/presets", {"name": "  ", "config": self.stub_config()})
        self.assertEqual(status, 400)

    def test_templates_are_listed(self):
        status, body = self.request("GET", "/api/templates")

        self.assertEqual(status, 200)
        self.assertIn("researcher", [template["id"] for template in json.loads(body)["templates"]])

    def test_a_finished_run_exports_as_markdown_and_json(self):
        self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-export-2222"})
        status, body = self.request("POST", "/api/runs", {"config": self.stub_config(), "goal": "say hi"})
        run_id = json.loads(body)["id"]
        self.request("GET", f"/api/runs/{run_id}/events")

        status, markdown = self.request("GET", f"/api/runs/{run_id}/export?format=md")
        self.assertEqual(status, 200)
        self.assertIn("## Final answer", markdown)
        self.assertIn("hello from stub", markdown)

        status, raw = self.request("GET", f"/api/runs/{run_id}/export?format=json")
        self.assertEqual(json.loads(raw)["run"]["goal"], "say hi")
        self.assertNotIn("sk-export-2222", markdown + raw)

    def test_saved_key_is_encrypted_on_disk(self):
        self.request("POST", "/api/keys", {"provider": "stub", "key": "sk-saved-9999", "persist": True})

        self.assertNotIn("sk-saved", (self.root / "data" / "keys.json").read_text())

    def test_rejects_other_hosts_and_missing_tokens(self):
        status, _ = self.request("GET", "/api/providers", headers={"Host": "evil.example"})
        self.assertEqual(status, 403)
        status, _ = self.request("GET", "/api/providers", headers={"X-Session-Token": "wrong"})
        self.assertEqual(status, 401)

    def test_run_without_a_key_is_refused_with_a_clear_message(self):
        config = {
            "providers": {"nokey": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "nokey", "model": "m", "role": "answer"},
            "teammates": [],
        }
        status, body = self.request("POST", "/api/runs", {"config": config, "goal": "say hi"})
        self.assertEqual(status, 400)
        self.assertIn("no API key for 'nokey'", json.loads(body)["error"])


if __name__ == "__main__":
    unittest.main()
