import json
import socket
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


class _Hops(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/to-internal":
            return self._redirect(f"http://internal.example:{self.server.server_port}/")
        if self.path == "/to-file":
            return self._redirect("file:///etc/passwd")
        if self.path == "/loop":
            return self._redirect("/loop")
        if self.path == "/missing":
            self.send_response(404)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        body = b"<html><body><h1>Title</h1></body></html>"
        self.send_response(200)
        self.send_header("content-type", "text/html; charset=utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("location", location)
        self.send_header("content-length", "0")
        self.end_headers()

    def log_message(self, *args):
        pass


class PinnedFetchTests(unittest.TestCase):
    """Fake DNS answers, so a name can be made to change its address between two lookups, as an attacker would."""

    PUBLIC = "93.184.216.34"

    def setUp(self):
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)
        self.server = HTTPServer(("127.0.0.1", 0), _Hops)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.answers = {}  # host -> list of answers, one per lookup; the last answer repeats
        self.connected = []  # the address each connection was made to
        self.lookups = {}
        real_connect = socket.create_connection

        def fake_getaddrinfo(host, port, *args, **kwargs):
            if host == "127.0.0.1":  # the local test server, reached by its address, needs no lookup
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, port or 0))]
            if host not in self.answers:
                raise socket.gaierror(f"unknown host {host}")
            index = self.lookups.get(host, 0)
            self.lookups[host] = index + 1
            ips = self.answers[host][min(index, len(self.answers[host]) - 1)]
            return [
                (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port or 0))
                for ip in ips
            ]

        def fake_connect(address, timeout=None, *args, **kwargs):
            host, port = address
            self.connected.append(host)
            if host == self.PUBLIC:  # the public address is served by the local test server
                return real_connect(("127.0.0.1", self.port), timeout)
            return real_connect(address, timeout)

        for patcher in (
            mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo),
            mock.patch("socket.create_connection", side_effect=fake_connect),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_the_connection_goes_to_the_checked_address_not_a_second_lookup(self):
        # A second lookup would now answer 127.0.0.1. The connection must still go to the address that was checked.
        self.answers["public.example"] = [[self.PUBLIC], ["127.0.0.1"]]

        text = fetch(f"http://public.example:{self.port}/")

        self.assertIn("Title", text)
        self.assertEqual(self.connected, [self.PUBLIC])

    def test_each_redirect_target_is_checked_before_it_is_connected(self):
        self.answers["public.example"] = [[self.PUBLIC]]
        self.answers["internal.example"] = [["10.0.0.5"]]

        with self.assertRaisesRegex(ToolError, "internal.example is a private address"):
            fetch(f"http://public.example:{self.port}/to-internal")
        self.assertNotIn("10.0.0.5", self.connected)

    def test_a_redirect_to_a_file_is_refused(self):
        self.answers["public.example"] = [[self.PUBLIC]]

        with self.assertRaisesRegex(ToolError, "only http and https"):
            fetch(f"http://public.example:{self.port}/to-file")

    def test_an_ipv4_mapped_loopback_address_is_refused(self):
        self.answers["mapped.example"] = [["::ffff:127.0.0.1"]]

        with self.assertRaisesRegex(ToolError, "private address"):
            fetch(f"http://mapped.example:{self.port}/")
        self.assertEqual(self.connected, [])

    def test_an_endless_redirect_is_stopped(self):
        self.answers["public.example"] = [[self.PUBLIC]]

        with self.assertRaisesRegex(ToolError, "redirected more than 5 times"):
            fetch(f"http://public.example:{self.port}/loop")

    def test_an_error_status_is_reported(self):
        self.answers["public.example"] = [[self.PUBLIC]]

        with self.assertRaisesRegex(ToolError, "HTTP 404"):
            fetch(f"http://public.example:{self.port}/missing")


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
