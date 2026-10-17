import json
import os
import queue
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from agent_team.providers import AnthropicProvider, ProviderError, build_provider
from agent_team.team import Agent, Team, final_answer, parse_outgoing


class ScriptedProvider:
    """Returns canned replies in order and records the history each call received."""

    def __init__(self, *replies, tokens=0):
        self.replies = list(replies)
        self.calls = []
        self.last_tokens = tokens

    def complete(self, model, system, messages):
        self.calls.append(list(messages))
        return self.replies.pop(0)


class ParsingTests(unittest.TestCase):
    def test_splits_messages_on_to_lines(self):
        text = "TO writer: draft the intro\nwith two paragraphs\nTO critic: wait for the draft"
        self.assertEqual(
            parse_outgoing(text, "lead"),
            [("writer", "draft the intro\nwith two paragraphs"), ("critic", "wait for the draft")],
        )

    def test_reply_without_to_line_goes_to_default_recipient(self):
        self.assertEqual(parse_outgoing("just thinking aloud", "lead"), [("lead", "just thinking aloud")])

    def test_final_answer_is_text_after_marker(self):
        self.assertEqual(final_answer("notes\nFINAL: the answer"), "the answer")
        self.assertIsNone(final_answer("TO writer: keep going"))


class TeamTests(unittest.TestCase):
    def test_lead_hands_work_to_teammate_and_finishes(self):
        lead = ScriptedProvider("TO writer: draft the intro", "FINAL: intro is done")
        writer = ScriptedProvider("TO lead: here is the intro")
        team = Team([Agent("lead", "plans", lead, "m1"), Agent("writer", "writes", writer, "m2")], lead="lead")

        self.assertEqual(team.run("write an intro"), "intro is done")
        self.assertEqual(team.status, "done")
        self.assertEqual(writer.calls, [[{"role": "user", "content": "From lead:\ndraft the intro"}]])
        self.assertEqual(lead.calls[1][-1], {"role": "user", "content": "From writer:\nhere is the intro"})

    def test_unknown_recipient_is_reported_back_to_sender(self):
        lead = ScriptedProvider("TO ghost: hello", "FINAL: gave up")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "gave up")
        self.assertIn("no team member named 'ghost'", lead.calls[1][-1]["content"])

    def test_stops_at_max_turns_without_final(self):
        lead = ScriptedProvider("TO lead: again 1", "TO lead: again 2", "TO lead: again 3")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_turns=3)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "max_turns")
        self.assertEqual(len(lead.calls), 3)

    def test_identical_replies_are_treated_as_a_stall(self):
        lead = ScriptedProvider("TO lead: same", "TO lead: same", "TO lead: same")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_turns=10)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "stalled")
        self.assertEqual(len(lead.calls), 3)

    def test_final_answer_must_pass_the_verifier(self):
        lead = ScriptedProvider("TO writer: draft the intro", "FINAL: draft v1", "FINAL: draft v2")
        writer = ScriptedProvider("TO lead: here is the intro")
        verifier = ScriptedProvider("VERDICT: FAIL the intro is missing", "VERDICT: PASS")
        team = Team(
            [
                Agent("lead", "plans", lead, "m"),
                Agent("writer", "writes", writer, "m"),
                Agent("verifier", "checks", verifier, "m"),
            ],
            lead="lead",
            verifier="verifier",
        )

        self.assertEqual(team.run("write an intro"), "draft v2")
        self.assertEqual(team.status, "done")
        self.assertIn("Candidate answer:\ndraft v1", verifier.calls[0][0]["content"])
        self.assertIn("VERDICT: FAIL", lead.calls[2][-1]["content"])

    def test_token_budget_stops_the_run(self):
        lead = ScriptedProvider("TO lead: keep going", "TO lead: keep going", tokens=50)
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_tokens=40)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "token_budget")
        self.assertEqual(len(lead.calls), 1)

    def test_time_budget_uses_the_injected_clock(self):
        ticks = iter([0, 0, 100])
        lead = ScriptedProvider("TO lead: keep going")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_seconds=10, clock=lambda: next(ticks))

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "timeout")

    def test_stop_before_the_first_turn_runs_nothing(self):
        lead = ScriptedProvider("FINAL: never")
        stop = threading.Event()
        stop.set()
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", stop_event=stop)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "stopped")
        self.assertEqual(lead.calls, [])

    def test_human_message_reaches_the_lead_during_the_run(self):
        lead = ScriptedProvider("FINAL: done")
        human = queue.Queue()
        human.put("make the title shorter")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", human_inbox=human)

        team.run("goal")
        self.assertIn("make the title shorter", lead.calls[0][0]["content"])

    def test_rejects_bad_team_shapes(self):
        provider = ScriptedProvider()
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m"), Agent("a", "r", provider, "m")], lead="a")
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m")], lead="b")
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m")], lead="a", verifier="a")


class _Handler(BaseHTTPRequestHandler):
    response = {}
    seen = {}

    def do_POST(self):
        length = int(self.headers["content-length"])
        _Handler.seen = {
            "path": self.path,
            "headers": self.headers,
            "body": json.loads(self.rfile.read(length)),
        }
        payload = json.dumps(_Handler.response).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):
        pass


class ProviderHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), _Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        # Keep requests to the local stub away from any configured HTTP proxy.
        env = mock.patch.dict(os.environ, {"NO_PROXY": "127.0.0.1", "no_proxy": "127.0.0.1"})
        env.start()
        self.addCleanup(env.stop)

    def test_openai_compatible_request_and_reply(self):
        _Handler.response = {"choices": [{"message": {"content": "hi there"}}], "usage": {"total_tokens": 9}}
        provider = build_provider("local", {"type": "openai_compatible", "base_url": self.base + "/v1"}, "secret")

        reply = provider.complete("some-model", "be brief", [{"role": "user", "content": "hello"}])

        self.assertEqual(reply, "hi there")
        self.assertEqual(provider.last_tokens, 9)
        self.assertEqual(_Handler.seen["path"], "/v1/chat/completions")
        self.assertEqual(_Handler.seen["headers"]["authorization"], "Bearer secret")
        self.assertEqual(_Handler.seen["body"]["messages"][0], {"role": "system", "content": "be brief"})
        self.assertEqual(_Handler.seen["body"]["model"], "some-model")

    def test_anthropic_request_and_reply(self):
        _Handler.response = {
            "content": [{"type": "text", "text": "hello!"}],
            "usage": {"input_tokens": 4, "output_tokens": 2},
        }
        provider = AnthropicProvider("claude", "secret", base_url=self.base)

        reply = provider.complete("claude-haiku-5-5", "be brief", [{"role": "user", "content": "hello"}])

        self.assertEqual(reply, "hello!")
        self.assertEqual(provider.last_tokens, 6)
        self.assertEqual(_Handler.seen["path"], "/v1/messages")
        self.assertEqual(_Handler.seen["headers"]["x-api-key"], "secret")
        self.assertEqual(_Handler.seen["headers"]["anthropic-version"], "2023-06-01")
        self.assertEqual(_Handler.seen["body"]["system"], "be brief")

    def test_missing_key_is_a_clear_error(self):
        with self.assertRaisesRegex(ProviderError, "no API key configured"):
            build_provider("x", {"type": "openai_compatible", "base_url": "http://example.invalid"}, "")

    def test_unknown_provider_type_is_rejected(self):
        with self.assertRaisesRegex(ProviderError, "unknown provider type"):
            build_provider("x", {"type": "carrier_pigeon"}, "secret")


if __name__ == "__main__":
    unittest.main()
