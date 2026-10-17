import json
import os
import stat
import tempfile
import threading
import unittest
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
