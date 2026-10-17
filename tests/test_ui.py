"""Browser tests for the web UI. They run only when Playwright and a Chromium binary are available.

Set AGENT_TEAM_CHROME to the Chromium executable to choose one explicitly.
"""

import glob
import json
import os
import tempfile
import threading
import unittest
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
        app = App(cls.store, KeyVault(root / "data", master_key_path=root / "master.key"), cls.TOKEN)
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

    def open_page(self, provider="stub"):
        team = {
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

        page.click("#key-list li:first-child button:text('Test')")
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

    def test_missing_key_blocks_start_with_a_clear_message(self):
        page = self.open_page(provider="nokey")
        page.fill("#goal", "say hi")

        expect(page.locator("#readiness")).to_contain_text("No API key for nokey")
        expect(page.locator("#start")).to_be_disabled()
        self.assertEqual(self.errors, [])


if __name__ == "__main__":
    unittest.main()
