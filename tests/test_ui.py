"""Browser tests for the web UI. They run only when Playwright and a Chromium binary are available.

Set AGENT_TEAM_CHROME to the Chromium executable to choose one explicitly.
"""

import glob
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from pathlib import Path
from unittest import mock

from test_app import StubProvider, start_http_server

from agent_team.keys import KeyVault
from agent_team.server import App, Server
from agent_team.store import Store

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # the rest of the suite does not need Playwright
    sync_playwright = None


def find_chrome():
    configured = os.environ.get("AGENT_TEAM_CHROME")
    if configured:
        return configured
    matches = sorted(glob.glob("/opt/pw-browsers/chromium-*/chrome-linux/chrome"))
    return matches[-1] if matches else None


@unittest.skipUnless(sync_playwright and find_chrome(), "needs Playwright and a Chromium binary")
class UiTests(unittest.TestCase):
    TOKEN = "ui-token"

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory()
        root = Path(cls.directory.name)
        cls.stub = start_http_server(("127.0.0.1", 0), StubProvider)
        cls.stub_base = f"http://127.0.0.1:{cls.stub.server_port}"
        cls.store = Store(root / "data" / "agent_team.db")
        app = App(cls.store, KeyVault(root / "data", master_key_path=root / "master.key"), cls.TOKEN, workspaces=root / "workspaces")
        cls.server = Server(0, app)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        cls.env.start()
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(executable_path=find_chrome(), args=["--no-sandbox"])

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.env.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.stub.shutdown()
        cls.stub.server_close()
        cls.store.db.close()
        cls.directory.cleanup()

    def open_page(self, provider="stub", team=None):
        team = team or {
            "providers": {provider: {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": provider, "model": "m", "role": "answer the user"},
            "teammates": [],
        }
        context = self.browser.new_context()
        self.addCleanup(context.close)
        context.add_init_script(f"localStorage.setItem('agent-team-draft', {json.dumps(json.dumps(team))});")
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"{self.base}/?token={self.TOKEN}")
        return page

    def test_key_team_run_and_follow_up_work_end_to_end(self):
        page = self.open_page()

        page.click("#keys-details summary")
        page.fill("#key-provider", "stub")
        page.fill("#key-value", "sk-ui-1234")
        page.click("#keys-details button[type=submit]")
        expect(page.locator("#notice")).to_have_text("Key saved.")
        expect(page.locator("#key-list")).to_contain_text("••••1234")

        expect(page.locator("#start")).to_be_disabled()
        page.fill("#goal", "say hi")
        expect(page.locator("#start")).to_be_enabled()

        page.click("#start")
        expect(page.locator("#run-status")).to_contain_text("Status: done")
        expect(page.locator("#feed")).to_contain_text("hello from stub")
        expect(page.locator("#cards .card")).to_have_count(1)

        page.fill("#human-text", "and once more")
        page.click("#human-form button[type=submit]")
        expect(page.locator("#feed")).to_contain_text("and once more")
        expect(page.locator("#feed .text", has_text="hello from stub")).to_have_count(2)

        page.click("#key-list li:has(strong:text-is('stub')) button:text('Test')")
        expect(page.locator("#notice")).to_contain_text("works with m")

        self.assertNotIn("sk-ui-1234", page.content())
        self.assertEqual(self.errors, [])

    def test_templates_and_presets_work_in_the_browser(self):
        page = self.open_page()
        expect(page.locator("#members .member")).to_have_count(1)

        page.select_option("#template-select", "writer")
        expect(page.locator("#members .member")).to_have_count(2)
        self.assertIn("Draft the content", page.locator("#members .member").nth(1).locator("textarea").input_value())
        self.assertTrue(page.locator("#members .member").nth(1).locator("input[type=checkbox]").first.is_checked())
        self.assertTrue(page.locator("#members .member").nth(1).locator("input[type=checkbox]").last.is_checked())

        page.evaluate("document.querySelector('details:has(#preset-select)').open = true")
        page.fill("#preset-name", "my stub team")
        page.click("#preset-form button[type=submit]")
        expect(page.locator("#notice")).to_contain_text("Saved “my stub team”")
        expect(page.locator("#preset-select option")).to_contain_text(["my stub team"])

        page.click("#reset-team")
        expect(page.locator("#members .member")).to_have_count(4)  # the example team: lead and three teammates
        page.select_option("#preset-select", "my stub team")
        page.click("#preset-load")
        expect(page.locator("#members .member")).to_have_count(2)
        self.assertEqual(self.errors, [])

    def test_a_run_continues_from_the_page_after_a_restart(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        data, master = root / "data", root / "master.key"

        first_store = Store(data / "agent_team.db")
        self.addCleanup(first_store.db.close)
        first = Server(0, App(first_store, KeyVault(data, master_key_path=master), self.TOKEN))
        threading.Thread(target=first.serve_forever, daemon=True).start()
        self.addCleanup(first.server_close)
        self.addCleanup(first.shutdown)
        first_base = f"http://127.0.0.1:{first.server_port}"

        def call(method, path, body=None):
            request = urllib.request.Request(
                first_base + path,
                data=json.dumps(body).encode() if body is not None else None,
                method=method,
                headers={"X-Session-Token": self.TOKEN, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.read().decode()

        config = {
            "providers": {"stub": {"type": "openai_compatible", "base_url": self.stub_base}},
            "lead": {"name": "lead", "provider": "stub", "model": "m", "role": "answer the user"},
            "teammates": [],
        }
        call("POST", "/api/keys", {"provider": "stub", "key": "sk-restart-ui-1", "persist": True})
        run_id = json.loads(call("POST", "/api/runs", {"config": config, "goal": "restart goal"}))["id"]
        call("GET", f"/api/runs/{run_id}/events")  # returns once the run has finished

        # The restarted server: same data directory, nothing held in memory.
        second_store = Store(data / "agent_team.db")
        self.addCleanup(second_store.db.close)
        second_store.mark_interrupted()
        second = Server(0, App(second_store, KeyVault(data, master_key_path=master), self.TOKEN))
        threading.Thread(target=second.serve_forever, daemon=True).start()
        self.addCleanup(second.server_close)
        self.addCleanup(second.shutdown)

        context = self.browser.new_context()
        self.addCleanup(context.close)
        page = context.new_page()
        self.errors = []
        page.on("pageerror", lambda error: self.errors.append(str(error)))
        page.goto(f"http://127.0.0.1:{second.server_port}/?token={self.TOKEN}")
        page.click("#run-list button:has-text('restart goal')")
        expect(page.locator("#run-status")).to_contain_text("Status: done")

        page.fill("#human-text", "after the restart")
        page.click("#human-form button[type=submit]")
        expect(page.locator("#feed")).to_contain_text("after the restart")
        expect(page.locator("#feed .text", has_text="hello from stub")).to_have_count(2)
        self.assertEqual(self.errors, [])

    def test_the_timeline_rewinds_and_branches_from_the_page(self):
        page = self.open_page()
        page.click("#keys-details summary")
        page.fill("#key-provider", "stub")
        page.fill("#key-value", "sk-timeline-3333")
        page.click("#keys-details button[type=submit]")
        expect(page.locator("#notice")).to_have_text("Key saved.")
        page.fill("#goal", "say hi")
        page.click("#start")
        expect(page.locator("#run-status")).to_contain_text("Status: done")

        page.click("#timeline button:has-text('Turn 1')")
        expect(page.locator("#time-actions")).to_be_visible()
        expect(page.locator("#rewind")).to_be_enabled()

        page.fill("#time-message", "one more time")
        page.click("#rewind")
        expect(page.locator("#rewind")).to_contain_text("Confirm")
        page.click("#rewind")
        expect(page.locator("#feed")).to_contain_text("Rewound to turn 1")
        expect(page.locator("#feed .text", has_text="hello from stub")).to_have_count(2)

        page.fill("#time-message", "a different path")
        page.click("#branch")
        expect(page.locator("#time-note")).to_contain_text("Started a branch")
        expect(page.locator("#feed")).to_contain_text("Branched from another run at turn 1")
        self.assertEqual(self.errors, [])

    def test_an_approval_and_a_file_review_work_from_the_page(self):
        from test_app import FileStub

        file_server = start_http_server(("127.0.0.1", 0), FileStub)
        self.addCleanup(file_server.server_close)
        self.addCleanup(file_server.shutdown)
        base = f"http://127.0.0.1:{file_server.server_port}"
        team = {
            "providers": {"fstub": {"type": "openai_compatible", "base_url": base}},
            "lead": {"name": "lead", "provider": "fstub", "model": "m", "role": "plan the work"},
            "teammates": [{"name": "writer", "provider": "fstub", "model": "m", "role": "write notes", "tools": ["files"], "approve": ["write_file"]}],
        }
        page = self.open_page(team=team)
        page.click("#keys-details summary")
        page.fill("#key-provider", "fstub")
        page.fill("#key-value", "sk-ui-review-5555")
        page.click("#keys-details button[type=submit]")
        expect(page.locator("#notice")).to_have_text("Key saved.")

        page.fill("#goal", "write a note")
        page.click("#start")
        expect(page.locator("#approvals .approval")).to_contain_text("write_file")
        page.click("#approvals button.approve")
        expect(page.locator("#run-status")).to_contain_text("Status: done")
        expect(page.locator("#changes .change")).to_contain_text("note.md")
        page.click("#changes button.accept")
        expect(page.locator("#changes")).to_contain_text("Nothing waiting for review")
        self.assertEqual(self.errors, [])

    def test_step_mode_trace_and_graph_work_from_the_page(self):
        from test_app import FileStub

        file_server = start_http_server(("127.0.0.1", 0), FileStub)
        self.addCleanup(file_server.server_close)
        self.addCleanup(file_server.shutdown)
        base = f"http://127.0.0.1:{file_server.server_port}"
        team = {
            "providers": {"tstub": {"type": "openai_compatible", "base_url": base}},
            "lead": {"name": "lead", "provider": "tstub", "model": "m", "role": "plan the work"},
            "teammates": [{"name": "writer", "provider": "tstub", "model": "m", "role": "write notes", "tools": ["files"]}],
            "step_mode": True,
        }
        page = self.open_page(team=team)
        page.click("#keys-details summary")
        page.fill("#key-provider", "tstub")
        page.fill("#key-value", "sk-ui-trace-6666")
        page.click("#keys-details button[type=submit]")
        expect(page.locator("#notice")).to_have_text("Key saved.")

        page.fill("#goal", "write a note")
        page.click("#start")
        expect(page.locator("#run-status")).to_contain_text("paused")
        for _ in range(8):
            if "Status: done" in page.locator("#run-status").inner_text():
                break
            if page.locator("#next-step").is_enabled():
                page.click("#next-step")
            page.wait_for_timeout(400)

        expect(page.locator("#run-status")).to_contain_text("Status: done")
        expect(page.locator("#trace")).to_contain_text("invoke_agent")
        expect(page.locator("#trace")).to_contain_text("execute_tool")
        expect(page.locator("#graph text").first).to_be_visible()
        self.assertEqual(self.errors, [])

    def test_missing_key_blocks_start_with_a_clear_message(self):
        page = self.open_page(provider="nokey")
        page.fill("#goal", "say hi")

        expect(page.locator("#readiness")).to_contain_text("No API key for nokey")
        expect(page.locator("#start")).to_be_disabled()
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
