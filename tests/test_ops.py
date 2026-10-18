import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

import agent_team
from agent_team.keys import KeyVault
from agent_team.logs import RunLog
from agent_team.providers import Reply, ToolCall, _anthropic_reply, _openai_reply
from agent_team.runner import ConfigError, build_team, default_config
from agent_team.server import App, Server
from agent_team.store import Store
from agent_team.team import Agent, Team
from agent_team.tools import CheckedRedirects, ToolError

REPO = Path(__file__).resolve().parent.parent
SESSION_TOKEN = "tok-session-7f3a9c1e5b"
STUB_KEY = "sk-ops-9999"


class StubCompletion(BaseHTTPRequestHandler):
    """Finishes every run at once, and reports token usage split into prompt and completion."""

    def do_POST(self):
        self.rfile.read(int(self.headers["content-length"]))
        finish = {"index": 0, "id": "call_ops", "function": {"name": "finish", "arguments": json.dumps({"answer": "done"})}}
        chunks = [
            {"choices": [{"delta": {"tool_calls": [finish]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500}},
        ]
        self.send_response(200)
        self.send_header("content-type", "text/event-stream")
        self.end_headers()
        for chunk in chunks:
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")

    def log_message(self, *args):
        pass


class _FixedUsage:
    """A provider that answers with one finish call and a known usage."""

    name = "p"

    def complete(self, model, system, messages, tools, on_text=None):
        return Reply(tool_calls=[ToolCall("c", "finish", {"answer": "ok"})], tokens=1500, tokens_in=1000, tokens_out=500)


class UsageSplitTests(unittest.TestCase):
    def test_anthropic_replies_report_input_and_output_tokens(self):
        reply = _anthropic_reply({"content": [{"type": "text", "text": "hi"}], "usage": {"input_tokens": 12, "output_tokens": 5}})
        self.assertEqual((reply.tokens_in, reply.tokens_out, reply.tokens), (12, 5, 17))

    def test_openai_replies_report_prompt_and_completion_tokens(self):
        reply = _openai_reply({"choices": [{"message": {"content": "hi"}}], "usage": {"prompt_tokens": 30, "completion_tokens": 7, "total_tokens": 37}})
        self.assertEqual((reply.tokens_in, reply.tokens_out, reply.tokens), (30, 7, 37))


class CostTests(unittest.TestCase):
    def config(self, prices=None):
        config = {
            "providers": {"p": {"type": "openai_compatible", "base_url": "http://example.invalid/v1"}},
            "lead": {"name": "lead", "provider": "p", "model": "m-known", "role": "plan"},
            "teammates": [],
        }
        if prices is not None:
            config["prices"] = prices
        return config

    def test_a_turn_costs_its_input_and_output_tokens_at_the_configured_price(self):
        events = []
        team = Team(
            [Agent("lead", "plans", _FixedUsage(), "m-known")],
            lead="lead",
            emit=lambda kind, **data: events.append((kind, data)),
            prices={("p", "m-known"): (2.0, 10.0)},
        )
        team.run("goal")

        turn = next(data for kind, data in events if kind == "turn")
        self.assertEqual((turn["tokens_in"], turn["tokens_out"]), (1000, 500))
        self.assertAlmostEqual(turn["cost"], (1000 * 2.0 + 500 * 10.0) / 1_000_000)

    def test_a_model_without_a_price_has_no_cost(self):
        events = []
        team = Team([Agent("lead", "plans", _FixedUsage(), "m-unknown")], lead="lead", emit=lambda kind, **data: events.append((kind, data)))
        team.run("goal")

        turn = next(data for kind, data in events if kind == "turn")
        self.assertIsNone(turn["cost"])

    def test_prices_from_the_config_reach_the_team(self):
        team, _ = build_team(self.config({"p/m-known": {"input_per_million": 1.0, "output_per_million": 2.0}}), lambda name: "key", emit=None)
        self.assertEqual(team.prices, {("p", "m-known"): (1.0, 2.0)})

    def test_a_price_that_is_not_a_number_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "prices for 'p/m-known': input_per_million"):
            build_team(self.config({"p/m-known": {"input_per_million": "cheap", "output_per_million": 1}}), lambda name: "key", emit=None)

    def test_a_price_without_a_provider_and_model_is_refused(self):
        with self.assertRaisesRegex(ConfigError, "named provider/model"):
            build_team(self.config({"just-a-model": {"input_per_million": 1, "output_per_million": 1}}), lambda name: "key", emit=None)


class ConfigVersionTests(unittest.TestCase):
    def config(self, version):
        config = {
            "providers": {"p": {"type": "openai_compatible", "base_url": "http://example.invalid/v1"}},
            "lead": {"name": "lead", "provider": "p", "model": "m", "role": "plan"},
            "teammates": [],
        }
        if version is not None:
            config["version"] = version
        return config

    def test_version_one_and_no_version_are_accepted(self):
        build_team(self.config(1), lambda name: "key", emit=None)
        build_team(self.config(None), lambda name: "key", emit=None)

    def test_an_unknown_version_is_refused_with_the_supported_one(self):
        with self.assertRaisesRegex(ConfigError, "config version 2 is not supported; this app reads version 1"):
            build_team(self.config(2), lambda name: "key", emit=None)

    def test_the_example_config_is_version_one(self):
        self.assertEqual(default_config()["version"], 1)


class LogTests(unittest.TestCase):
    def test_log_lines_are_json_and_never_contain_a_secret(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "agent_team.log"
        log = RunLog(path, secrets=lambda: ['sk-logged"8888'])

        log.write("run_finished", run="r1", status="done", note='key was sk-logged"8888')

        line = json.loads(path.read_text().strip())
        self.assertEqual(line["event"], "run_finished")
        self.assertIn("ts", line)
        self.assertNotIn("sk-logged", path.read_text())
        self.assertEqual(line["note"], "key was [redacted]")

    def test_a_failed_write_does_not_raise(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        blocker = Path(directory.name) / "file"
        blocker.write_text("not a folder")
        RunLog(blocker / "agent_team.log").write("run_finished", run="r1")  # the parent is a file; must not raise


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"})
        env.start()
        self.addCleanup(env.stop)
        stub = HTTPServer(("127.0.0.1", 0), StubCompletion)
        threading.Thread(target=stub.serve_forever, daemon=True).start()
        self.addCleanup(stub.server_close)
        self.addCleanup(stub.shutdown)
        self.stub_base = f"http://127.0.0.1:{stub.server_port}"
        self.store = Store(self.root / "data" / "agent_team.db")
        self.addCleanup(self.store.db.close)
        vault = KeyVault(self.root / "data", master_key_path=self.root / "master.key")
        vault.set("stub", STUB_KEY)
        self.log_path = self.root / "data" / "agent_team.log"
        log = RunLog(self.log_path, secrets=vault.secret_values)
        app = App(self.store, vault, SESSION_TOKEN, workspaces=self.root / "workspaces", log=log)
        self.server = Server(0, app)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def call(self, method, path, body=None, token=SESSION_TOKEN):
        request = urllib.request.Request(
            self.base + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"X-Session-Token": token, "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.read()

    def stub_team(self, prices=None):
        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "plan"},
            "teammates": [],
        }
        if prices is not None:
            config["prices"] = prices
        return config

    def run_to_end(self, config, goal="say hi"):
        status, body = self.call("POST", "/api/runs", {"config": config, "goal": goal})
        self.assertEqual(status, 201, body)
        run_id = json.loads(body)["id"]
        self.server.app.runs[run_id].thread.join(timeout=10)
        return run_id


class HealthAndLogTests(ServerTestCase):
    def test_health_reports_the_version_database_and_active_runs(self):
        status, body = self.call("GET", "/api/health")
        self.assertEqual(status, 200)
        health = json.loads(body)
        self.assertEqual(health, {"version": agent_team.__version__, "database": "ok", "active_runs": 0})
        self.assertNotIn(str(self.root), body.decode())

    def test_health_needs_the_session_token(self):
        self.assertEqual(self.call("GET", "/api/health", token="wrong")[0], 401)

    def test_a_finished_run_is_logged_with_its_outcome_and_no_key(self):
        run_id = self.run_to_end(self.stub_team(prices={"stub/m": {"input_per_million": 2.0, "output_per_million": 10.0}}))

        log = self.log_path.read_text()
        lines = [json.loads(line) for line in log.splitlines()]
        finished = next(line for line in lines if line["event"] == "run_finished")
        self.assertEqual((finished["run"], finished["status"], finished["tokens"]), (run_id, "done", 1500))
        self.assertNotIn(STUB_KEY, log)


class DashboardTests(ServerTestCase):
    def test_the_run_list_carries_tokens_cost_and_duration(self):
        run_id = self.run_to_end(self.stub_team(prices={"stub/m": {"input_per_million": 2.0, "output_per_million": 10.0}}))

        runs = json.loads(self.call("GET", "/api/runs")[1])["runs"]
        run = next(item for item in runs if item["id"] == run_id)
        self.assertEqual(run["status"], "done")
        self.assertEqual(run["tokens"], 1500)
        self.assertAlmostEqual(run["cost"], (1000 * 2.0 + 500 * 10.0) / 1_000_000)
        self.assertIsNotNone(run["duration_seconds"])

    def test_a_run_without_a_price_has_no_cost(self):
        run_id = self.run_to_end(self.stub_team())

        run = next(item for item in json.loads(self.call("GET", "/api/runs")[1])["runs"] if item["id"] == run_id)
        self.assertEqual(run["tokens"], 1500)
        self.assertIsNone(run["cost"])

    def test_the_run_list_filters_by_status_and_by_goal(self):
        self.store.create_run("filter-a", "summarise the Q3 report", None)
        self.store.finish_run("filter-a", "done")
        self.store.create_run("filter-b", "write a poem", None)
        self.store.finish_run("filter-b", "failed")

        by_status = json.loads(self.call("GET", "/api/runs?status=failed")[1])["runs"]
        self.assertEqual([run["id"] for run in by_status], ["filter-b"])

        by_goal = json.loads(self.call("GET", "/api/runs?q=q3%20report")[1])["runs"]
        self.assertEqual([run["id"] for run in by_goal], ["filter-a"])

        self.assertEqual(json.loads(self.call("GET", "/api/runs?q=nothing-like-this")[1])["runs"], [])


class SecurityRoundTwoTests(ServerTestCase):
    """The review's checklist for this round. Each item names the test that covers it."""

    def test_an_export_does_not_carry_the_session_token_or_a_key(self):
        # Checklist: token handling in exports. The token travels in the link's query string, never in the file.
        run_id = self.run_to_end(self.stub_team())
        for query in ("format=md", "format=json"):
            status, body = self.call("GET", f"/api/runs/{run_id}/export?{query}")
            self.assertEqual(status, 200)
            self.assertNotIn(SESSION_TOKEN, body.decode())
            self.assertNotIn(STUB_KEY, body.decode())

    def test_a_redirect_to_a_private_address_is_refused(self):
        # Checklist: fetch redirects. Every hop is checked, not only the first address.
        handler = CheckedRedirects(allow_private=False)
        with self.assertRaisesRegex(ToolError, "private address"):
            handler.redirect_request(None, None, 302, "Found", {}, "http://127.0.0.1:9/secret")

    def test_a_redirect_to_a_file_is_refused(self):
        handler = CheckedRedirects(allow_private=False)
        with self.assertRaisesRegex(ToolError, "only http and https"):
            handler.redirect_request(None, None, 302, "Found", {}, "file:///etc/passwd")



class PackageTests(unittest.TestCase):
    def test_the_files_the_package_ships_are_on_disk(self):
        package = REPO / "agent_team"
        for name in ("static/index.html", "static/app.js", "static/style.css", "default_team.json"):
            self.assertTrue((package / name).is_file(), name)

    def test_the_version_in_the_code_matches_the_package_metadata(self):
        metadata = (REPO / "pyproject.toml").read_text()
        self.assertEqual(re.search(r'^version = "(.+)"$', metadata, re.M).group(1), agent_team.__version__)

    def test_the_default_team_is_valid_with_placeholder_keys(self):
        build_team(default_config(), lambda name: "placeholder-key", emit=None)

    def test_a_built_wheel_installs_into_a_clean_folder_and_its_default_team_is_valid(self):
        with tempfile.TemporaryDirectory() as wheels, tempfile.TemporaryDirectory() as clean:
            subprocess.run(
                [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", wheels, str(REPO)],
                check=True,
                timeout=300,
                capture_output=True,
            )
            wheel = next(Path(wheels).glob("agent_team-*.whl"))
            install = Path(clean) / "site"
            with zipfile.ZipFile(wheel) as archive:
                names = archive.namelist()
                archive.extractall(install)
            self.assertIn("agent_team/default_team.json", names)
            self.assertIn("agent_team/static/app.js", names)

            check = (
                "import sys, agent_team\n"
                "from agent_team.runner import build_team, default_config\n"
                "assert agent_team.__file__.startswith(sys.argv[1]), agent_team.__file__\n"
                "build_team(default_config(), lambda name: 'placeholder-key', emit=None)\n"
                "print('ok')\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", check, str(install)],
                cwd=clean,
                env={**os.environ, "PYTHONPATH": str(install)},
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
