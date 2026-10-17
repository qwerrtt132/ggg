import json
import os
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest import mock

from agent_team.providers import Reply, ToolCall
from agent_team.runner import DEFAULT_MAX_TOKENS, ConfigError, build_team
from agent_team.team import Agent, Team
from agent_team.tools import ToolError, Workspace, build_tools, fetch, check_url


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.workspace = Workspace(self.root / "ws")

    def test_write_then_read_and_list(self):
        self.workspace.write("notes/intro.md", "hello")

        self.assertEqual(self.workspace.read("notes/intro.md"), "hello")
        self.assertEqual(self.workspace.list_files(), ["notes/intro.md"])

    def test_paths_cannot_leave_the_workspace(self):
        for path in ["../escape.txt", "notes/../../escape.txt", "/etc/passwd"]:
            with self.subTest(path=path), self.assertRaises(ToolError):
                self.workspace.write(path, "x")
        self.assertFalse((self.root / "escape.txt").exists())

    def test_links_cannot_lead_out_of_the_workspace(self):
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "secret.txt").write_text("secret")
        os.symlink(outside, self.root / "ws" / "link")

        with self.assertRaisesRegex(ToolError, "links are not allowed"):
            self.workspace.read("link/secret.txt")
        self.assertNotIn("link", " ".join(self.workspace.list_files()))

    def test_missing_file_and_oversized_write_are_reported(self):
        with self.assertRaisesRegex(ToolError, "no file named"):
            self.workspace.read("missing.txt")
        with self.assertRaisesRegex(ToolError, "larger than"):
            self.workspace.write("big.txt", "x" * 200_001)


class _Page(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><script>var x = 1;</script><body><h1>Title</h1><p>Some &amp; text</p></body></html>"
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Page)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}/"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)

    def test_only_web_addresses_are_fetched(self):
        with self.assertRaisesRegex(ToolError, "only http and https"):
            check_url("file:///etc/passwd")

    def test_private_and_loopback_addresses_are_refused(self):
        with self.assertRaisesRegex(ToolError, "private address"):
            fetch(self.base)

    def test_a_public_page_is_returned_as_text(self):
        text = fetch(self.base, allow_private=True)

        self.assertIn("Title", text)
        self.assertIn("Some & text", text)
        self.assertNotIn("<h1>", text)
        self.assertNotIn("var x", text)

    def test_the_fetch_tool_reports_errors_to_the_model(self):
        tools = build_tools(tempfile.mkdtemp(), allow_private=False)
        text, is_error = tools["fetch_url"][1]({"url": "file:///etc/passwd"})

        self.assertTrue(is_error)
        self.assertIn("only http and https", text)


class TeamToolTests(unittest.TestCase):
    def test_a_member_with_file_tools_writes_and_reads_a_file(self):
        class Scripted:
            name = "scripted"

            def __init__(self, *replies):
                self.replies = list(replies)
                self.tool_names = []

            def complete(self, model, system, messages, tools, on_text=None):
                self.tool_names.append([tool["name"] for tool in tools])
                return self.replies.pop(0)

        def turn(*calls):
            return Reply(tool_calls=list(calls))

        lead = Scripted(turn(ToolCall("c1", "send_message", {"to": "writer", "message": "write the note"})), turn(ToolCall("c4", "finish", {"answer": "done"})))
        writer = Scripted(
            turn(ToolCall("c2", "write_file", {"path": "note.md", "content": "hello from the writer"})),
            turn(ToolCall("c3", "read_file", {"path": "note.md"})),
            turn(ToolCall("c5", "send_message", {"to": "lead", "message": "written"})),
        )
        workspace = tempfile.mkdtemp()
        team = Team(
            [
                Agent("lead", "plans", lead, "m"),
                Agent("writer", "writes", writer, "m", tools=["write_file", "read_file"]),
            ],
            lead="lead",
            extra_tools=build_tools(workspace),
        )

        self.assertEqual(team.run("write a note"), "done")
        self.assertIn("write_file", writer.tool_names[0])
        self.assertNotIn("write_file", lead.tool_names[0])
        self.assertEqual((Path(workspace) / "note.md").read_text(), "hello from the writer")
        results = [item for item in team.agents["writer"].history if item.get("role") == "tool"]
        self.assertIn("hello from the writer", results[1]["content"])


class ToolFailureTests(unittest.TestCase):
    def test_os_and_encoding_errors_come_back_as_tool_errors(self):
        tools = build_tools(tempfile.mkdtemp())
        tools["write_file"][1]({"path": "sub/x.txt", "content": "x"})

        for label, arguments in [
            ("a folder path", {"path": "sub", "content": "x"}),
            ("a lone surrogate", {"path": "bad.txt", "content": "bad \ud800 text"}),
        ]:
            with self.subTest(label):
                text, is_error = tools["write_file"][1](arguments)
                self.assertTrue(is_error)
                self.assertTrue(text)


class ConfigToolTests(unittest.TestCase):
    def config(self, tools):
        return {
            "providers": {"p": {"type": "openai_compatible", "base_url": "http://example.invalid/v1"}},
            "lead": {"name": "lead", "provider": "p", "model": "m", "role": "plan", "tools": tools},
            "teammates": [],
        }

    def test_unknown_tool_groups_are_refused(self):
        with self.assertRaisesRegex(ConfigError, "unknown tool group"):
            build_team(self.config(["shell"]), lambda name: "key", emit=None, tools={})

    def test_tools_need_a_workspace(self):
        with self.assertRaisesRegex(ConfigError, "no workspace"):
            build_team(self.config(["files"]), lambda name: "key", emit=None, tools=None)

    def test_a_config_without_a_token_budget_gets_the_default(self):
        team, _ = build_team(self.config([]), lambda name: "key", emit=None)
        self.assertEqual(team.max_tokens, DEFAULT_MAX_TOKENS)

        config = self.config([])
        config["max_tokens"] = None
        unlimited, _ = build_team(config, lambda name: "key", emit=None)
        self.assertIsNone(unlimited.max_tokens)

    def test_replan_settings_are_checked(self):
        config = self.config([])
        config["max_replans"] = 9
        with self.assertRaisesRegex(ConfigError, "max_replans"):
            build_team(config, lambda name: "key", emit=None)

        config = self.config([])
        config["stall_turns"] = 1
        with self.assertRaisesRegex(ConfigError, "stall_turns"):
            build_team(config, lambda name: "key", emit=None)

        config = self.config([])
        config["max_replans"] = 0
        team, _ = build_team(config, lambda name: "key", emit=None)
        self.assertEqual(team.max_replans, 0)

    def test_a_valid_tool_group_gives_the_member_its_tools(self):
        team, _ = build_team(self.config(["files"]), lambda name: "key", emit=None, tools=build_tools(tempfile.mkdtemp()))

        self.assertEqual(team.agents["lead"].tools, ["list_files", "read_file", "write_file"])


if __name__ == "__main__":
    unittest.main()
