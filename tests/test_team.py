import itertools
import json
import os
import queue
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from agent_team.providers import (
    AnthropicProvider,
    ProviderError,
    Reply,
    ToolCall,
    build_provider,
)
from agent_team.team import Agent, Team, context_view

_call_ids = itertools.count(1)


def call(name, **arguments):
    return ToolCall(id=f"call_{next(_call_ids)}", name=name, arguments=arguments)


def reply(*calls, text="", truncated=False, tokens=0):
    return Reply(text=text, tool_calls=list(calls), truncated=truncated, tokens=tokens)


class ScriptedProvider:
    """Returns canned replies in order and records the messages and tool names each call saw."""

    name = "scripted"

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []
        self.tools = []

    def complete(self, model, system, messages, tools, on_text=None):
        self.calls.append(list(messages))
        self.tools.append([tool["name"] for tool in tools])
        return self.replies.pop(0) if self.replies else reply(text="(no scripted reply left)")


class ContextTests(unittest.TestCase):
    def test_short_history_is_sent_unchanged(self):
        history = [{"role": "user", "text": "goal"}, {"role": "assistant", "text": "ok", "tool_calls": []}]
        self.assertEqual(context_view(history, window=10), history)

    def test_long_history_keeps_the_goal_and_cuts_at_a_user_message(self):
        history = [{"role": "user", "text": "goal"}]
        for index in range(30):
            pending = call("send_message", to="x", message=str(index))
            history.append({"role": "assistant", "text": "", "tool_calls": [pending]})
            history.append({"role": "tool", "tool_call_id": pending.id, "content": "ok", "is_error": False})
            history.append({"role": "user", "text": f"update {index}"})

        view = context_view(history, window=10)

        self.assertEqual(view[0]["text"], "goal")
        self.assertIn("removed to save context", view[1]["text"])
        self.assertEqual(view[2]["role"], "user")
        answered = {item["tool_call_id"] for item in view if item["role"] == "tool"}
        called = {c.id for item in view if item["role"] == "assistant" for c in item.get("tool_calls", [])}
        self.assertTrue(answered <= called)


class TeamTests(unittest.TestCase):
    def test_lead_hands_work_to_teammate_and_finishes(self):
        lead = ScriptedProvider(
            reply(call("send_message", to="writer", message="draft the intro")),
            reply(call("finish", answer="intro is done")),
        )
        writer = ScriptedProvider(reply(call("send_message", to="lead", message="here is the intro")))
        team = Team([Agent("lead", "plans", lead, "m1"), Agent("writer", "writes", writer, "m2")], lead="lead")

        self.assertEqual(team.run("write an intro"), "intro is done")
        self.assertEqual(team.status, "done")
        self.assertEqual(writer.calls[0][-1]["text"], "From lead:\ndraft the intro")
        self.assertEqual(lead.calls[1][-1]["text"], "From writer:\nhere is the intro")
        self.assertEqual(lead.tools[0], ["send_message", "finish"])
        self.assertEqual(writer.tools[0], ["send_message"])

    def test_teammates_cannot_use_lead_only_tools(self):
        lead = ScriptedProvider(reply(call("send_message", to="writer", message="go")), reply(call("finish", answer="ok")))
        writer = ScriptedProvider(reply(call("finish", answer="my own answer")), reply(call("send_message", to="lead", message="done")))
        team = Team([Agent("lead", "plans", lead, "m"), Agent("writer", "writes", writer, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "ok")
        denied = [item for item in writer.calls[1] if item["role"] == "tool"][0]
        self.assertTrue(denied["is_error"])
        self.assertIn("not available to you", denied["content"])

    def test_unknown_recipient_is_reported_back_to_the_sender(self):
        lead = ScriptedProvider(reply(call("send_message", to="ghost", message="hi")), reply(call("finish", answer="gave up")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "gave up")
        result = [item for item in lead.calls[1] if item["role"] == "tool"][0]
        self.assertTrue(result["is_error"])
        self.assertIn("'ghost'", result["content"])
        self.assertTrue(lead.calls[1][-1]["text"].startswith("System: some tool calls failed"))

    def test_malformed_arguments_are_sent_back_to_the_model(self):
        broken = ToolCall(id="c1", name="finish", arguments={}, error="Expecting value")
        lead = ScriptedProvider(reply(broken), reply(call("finish", answer="ok")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "ok")
        result = [item for item in lead.calls[1] if item["role"] == "tool"][0]
        self.assertIn("not valid JSON", result["content"])

    def test_plain_text_replies_are_nudged_and_then_end_the_run(self):
        lead = ScriptedProvider(reply(text="just thinking"), reply(text="still thinking"))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "no_progress")
        self.assertIn("had no tool calls", lead.calls[1][-1]["text"])

    def test_truncated_reply_gets_a_nudge(self):
        lead = ScriptedProvider(reply(text="a very long answer", truncated=True), reply(call("finish", answer="short")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "short")
        self.assertIn("cut off by the output limit", lead.calls[1][-1]["text"])

    def test_stops_at_max_turns(self):
        lead = ScriptedProvider(*[reply(call("send_message", to="ghost", message=f"try {i}")) for i in range(3)])
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_turns=3)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "max_turns")
        self.assertEqual(len(lead.calls), 3)

    def test_identical_turns_are_a_stall(self):
        lead = ScriptedProvider(*[reply(call("send_message", to="ghost", message="same")) for _ in range(3)])
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_turns=10)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "stalled")

    def test_near_identical_turns_are_also_a_stall(self):
        base = "Please draft the introduction paragraph for the report, covering the scope and the main goals."
        lead = ScriptedProvider(
            reply(call("send_message", to="ghost", message=base)),
            reply(call("send_message", to="ghost", message=base + ".")),
            reply(call("send_message", to="ghost", message=base + "!")),
        )
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_turns=10)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "stalled")

    def test_final_answer_must_pass_the_verifier(self):
        lead = ScriptedProvider(
            reply(call("send_message", to="writer", message="draft the intro")),
            reply(call("finish", answer="draft v1")),
            reply(call("finish", answer="draft v2")),
        )
        writer = ScriptedProvider(reply(call("send_message", to="lead", message="here is the intro")))
        verifier = ScriptedProvider(
            reply(call("submit_verdict", passed=False, feedback="the intro is missing")),
            reply(call("submit_verdict", passed=True, feedback="looks right")),
        )
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
        self.assertIn("Candidate answer:\ndraft v1", verifier.calls[0][-1]["text"])
        self.assertEqual(verifier.tools[0], ["send_message", "submit_verdict"])
        self.assertEqual(lead.calls[2][-1]["text"], "From verifier:\nVERDICT: FAIL\n\nthe intro is missing")

    def test_token_budget_stops_the_run(self):
        lead = ScriptedProvider(reply(call("send_message", to="ghost", message="x"), tokens=50), reply(call("finish", answer="no")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_tokens=40)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "token_budget")
        self.assertEqual(len(lead.calls), 1)

    def test_time_budget_uses_the_injected_clock(self):
        ticks = iter([0, 0, 100])
        lead = ScriptedProvider(reply(call("send_message", to="ghost", message="keep going")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", max_seconds=10, clock=lambda: next(ticks))

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "timeout")

    def test_stop_before_the_first_turn_runs_nothing(self):
        lead = ScriptedProvider(reply(call("finish", answer="never")))
        stop = threading.Event()
        stop.set()
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", stop_event=stop)

        self.assertIsNone(team.run("goal"))
        self.assertEqual(team.status, "stopped")
        self.assertEqual(lead.calls, [])

    def test_a_stop_during_a_slow_call_returns_without_waiting_for_it(self):
        release = threading.Event()

        class SlowProvider:
            name = "slow"

            def complete(self, model, system, messages, tools, on_text=None):
                release.wait(5)
                return reply(call("finish", answer="late"))

        stop = threading.Event()
        team = Team([Agent("lead", "plans", SlowProvider(), "m")], lead="lead", stop_event=stop)
        timer = threading.Timer(0.2, stop.set)
        timer.start()
        self.addCleanup(release.set)
        self.addCleanup(timer.cancel)

        worker = threading.Thread(target=team.run, args=("goal",))
        worker.start()
        worker.join(3)

        self.assertFalse(worker.is_alive())
        self.assertEqual(team.status, "stopped")

    def test_live_text_is_reported_during_a_reply_and_cleared_after(self):
        class Streaming:
            name = "streaming"

            def complete(self, model, system, messages, tools, on_text=None):
                on_text("part")
                on_text("partial reply")
                return reply(call("finish", answer="ok"))

        live = []
        team = Team([Agent("lead", "plans", Streaming(), "m")], lead="lead", on_live=lambda name, text: live.append((name, text)))

        self.assertEqual(team.run("goal"), "ok")
        self.assertEqual(live[-1], ("lead", ""))
        self.assertTrue(set(text for _, text in live) <= {"", "part", "partial reply"})

    def test_human_message_reaches_the_lead_during_the_run(self):
        lead = ScriptedProvider(reply(call("finish", answer="done")))
        human = queue.Queue()
        human.put("make the title shorter")
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead", human_inbox=human)

        team.run("goal")
        self.assertIn("make the title shorter", lead.calls[0][0]["text"])

    def test_run_announces_the_roster_and_agent_states(self):
        events = []
        lead = ScriptedProvider(reply(call("finish", answer="ok")))
        team = Team(
            [Agent("lead", "plans", lead, "m1")],
            lead="lead",
            max_tokens=500,
            emit=lambda kind, **data: events.append((kind, data)),
        )

        team.run("goal")
        kinds = [kind for kind, _ in events]
        self.assertEqual(kinds[0], "roster")
        roster = events[0][1]
        self.assertEqual(roster["lead"], "lead")
        self.assertEqual(roster["max_tokens"], 500)
        self.assertEqual(roster["members"], [{"name": "lead", "role": "plans", "model": "m1", "provider": "scripted"}])
        self.assertEqual(events[1], ("agent", {"name": "lead", "state": "thinking"}))
        self.assertEqual(events[2], ("agent", {"name": "lead", "state": "idle"}))

    def test_continue_run_keeps_every_agent_history(self):
        lead = ScriptedProvider(reply(call("finish", answer="first answer")), reply(call("finish", answer="second answer")))
        team = Team([Agent("lead", "plans", lead, "m")], lead="lead")

        self.assertEqual(team.run("goal"), "first answer")
        self.assertEqual(team.continue_run("make it shorter"), "second answer")
        self.assertEqual(team.status, "done")
        second_call = lead.calls[1]
        self.assertEqual(second_call[0]["text"], "From the user:\ngoal")
        self.assertIn("make it shorter", second_call[-1]["text"])

    def test_rejects_bad_team_shapes(self):
        provider = ScriptedProvider()
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m"), Agent("a", "r", provider, "m")], lead="a")
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m")], lead="b")
        with self.assertRaises(ValueError):
            Team([Agent("a", "r", provider, "m")], lead="a", verifier="a")


class _Handler(BaseHTTPRequestHandler):
    responses = []  # queue of (status, payload, headers) to send, in order
    seen = []  # one record per request received

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        _Handler.seen.append({"path": self.path, "headers": self.headers, "body": body})
        status, payload, headers = _Handler.responses.pop(0)
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("content-type", "application/json")
        for name, value in headers.items():
            self.send_header(name, value)
        self.send_header("content-length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):
        pass


def respond(payload, status=200, headers=None):
    return (status, payload, headers or {})


def events(*pairs):
    """Build a server-sent event body from (event name, data) pairs. A name of None means no event line."""
    lines = []
    for name, data in pairs:
        if name:
            lines.append(f"event: {name}")
        lines.append(f"data: {data if isinstance(data, str) else json.dumps(data)}")
        lines.append("")
    return "\n".join(lines).encode() + b"\n"


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
        _Handler.responses = []
        _Handler.seen = []
        self.sleeps = []

    def openai(self, **kwargs):
        return build_provider(
            "local",
            {"type": "openai_compatible", "base_url": self.base + "/v1"},
            "secret",
        )

    def test_openai_request_converts_tools_and_history(self):
        _Handler.responses = [
            respond(
                {
                    "choices": [
                        {
                            "message": {
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "type": "function",
                                        "function": {"name": "send_message", "arguments": '{"to": "writer", "message": "hi"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"total_tokens": 9},
                }
            )
        ]
        history = [
            {"role": "user", "text": "goal"},
            {"role": "assistant", "text": "", "tool_calls": [ToolCall("p1", "send_message", {"to": "w", "message": "m"})]},
            {"role": "tool", "tool_call_id": "p1", "content": "Delivered to w.", "is_error": False},
            {"role": "user", "text": "new message"},
        ]
        tools = [{"name": "send_message", "description": "d", "input_schema": {"type": "object"}}]

        reply_ = self.openai().complete("some-model", "be brief", history, tools)

        sent = _Handler.seen[0]
        self.assertEqual(sent["path"], "/v1/chat/completions")
        self.assertEqual(sent["headers"]["authorization"], "Bearer secret")
        self.assertEqual(sent["body"]["tools"][0]["function"]["name"], "send_message")
        roles = [message["role"] for message in sent["body"]["messages"]]
        self.assertEqual(roles, ["system", "user", "assistant", "tool", "user"])
        self.assertEqual(sent["body"]["messages"][2]["tool_calls"][0]["function"]["name"], "send_message")
        self.assertEqual(reply_.tool_calls[0].arguments, {"to": "writer", "message": "hi"})
        self.assertEqual(reply_.tokens, 9)
        self.assertFalse(reply_.truncated)

    def test_openai_length_finish_is_reported_as_truncated(self):
        _Handler.responses = [respond({"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}]})]

        reply_ = self.openai().complete("m", "s", [{"role": "user", "text": "hi"}], [])

        self.assertTrue(reply_.truncated)
        self.assertEqual(reply_.text, "cut")

    def test_openai_malformed_arguments_become_an_error_not_a_crash(self):
        _Handler.responses = [
            respond(
                {
                    "choices": [
                        {
                            "message": {
                                "tool_calls": [
                                    {"id": "c1", "type": "function", "function": {"name": "finish", "arguments": "{oops"}}
                                ]
                            }
                        }
                    ]
                }
            )
        ]

        reply_ = self.openai().complete("m", "s", [{"role": "user", "text": "hi"}], [])

        self.assertIsNotNone(reply_.tool_calls[0].error)

    def test_anthropic_request_merges_tool_results_and_reports_truncation(self):
        _Handler.responses = [
            respond(
                {
                    "content": [
                        {"type": "text", "text": "working"},
                        {"type": "tool_use", "id": "t1", "name": "finish", "input": {"answer": "x"}},
                    ],
                    "stop_reason": "max_tokens",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                }
            )
        ]
        history = [
            {"role": "user", "text": "goal"},
            {"role": "assistant", "text": "", "tool_calls": [ToolCall("p1", "send_message", {"to": "w", "message": "m"})]},
            {"role": "tool", "tool_call_id": "p1", "content": "Delivered.", "is_error": False},
            {"role": "user", "text": "reply from w"},
        ]
        provider = AnthropicProvider("claude", "secret", base_url=self.base)

        reply_ = provider.complete("claude-haiku-5-5", "be brief", history, [])

        sent = _Handler.seen[0]["body"]["messages"]
        self.assertEqual([message["role"] for message in sent], ["user", "assistant", "user"])
        self.assertEqual(sent[1]["content"][0]["type"], "tool_use")
        self.assertEqual([block["type"] for block in sent[2]["content"]], ["tool_result", "text"])
        self.assertEqual(reply_.text, "working")
        self.assertEqual(reply_.tool_calls[0].name, "finish")
        self.assertTrue(reply_.truncated)
        self.assertEqual(reply_.tokens, 6)

    def test_rate_limits_and_server_errors_are_retried_with_backoff(self):
        _Handler.responses = [
            respond({"error": "slow down"}, status=429, headers={"retry-after": "1"}),
            respond({"error": "busy"}, status=503),
            respond({"choices": [{"message": {"content": "ok"}}]}),
        ]
        provider = build_provider(
            "local",
            {"type": "openai_compatible", "base_url": self.base + "/v1"},
            "secret",
        )
        provider.sleep = self.sleeps.append

        reply_ = provider.complete("m", "s", [{"role": "user", "text": "hi"}], [])

        self.assertEqual(reply_.text, "ok")
        self.assertEqual(len(_Handler.seen), 3)
        self.assertEqual(self.sleeps, [1.0, 4])

    def test_bad_requests_are_not_retried(self):
        _Handler.responses = [respond({"error": "bad model"}, status=400)]
        provider = self.openai()
        provider.sleep = self.sleeps.append

        with self.assertRaisesRegex(ProviderError, "HTTP 400"):
            provider.complete("m", "s", [{"role": "user", "text": "hi"}], [])
        self.assertEqual(len(_Handler.seen), 1)
        self.assertEqual(self.sleeps, [])

    def test_anthropic_stream_builds_the_reply_and_reports_text_as_it_grows(self):
        _Handler.responses = [
            respond(
                events(
                    ("message_start", {"type": "message_start", "message": {"usage": {"input_tokens": 10}}}),
                    ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Working"}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": " on it"}}),
                    ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                    ("content_block_start", {"type": "content_block_start", "index": 1, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "send_message", "input": {}}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"to": "writer",'}}),
                    ("content_block_delta", {"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": ' "message": "hi"}'}}),
                    ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 7}}),
                    ("message_stop", {"type": "message_stop"}),
                )
            )
        ]
        seen = []
        provider = AnthropicProvider("claude", "secret", base_url=self.base)

        reply_ = provider.complete("m", "s", [{"role": "user", "text": "hi"}], [], on_text=seen.append)

        self.assertEqual(reply_.text, "Working on it")
        self.assertEqual(reply_.tool_calls[0].arguments, {"to": "writer", "message": "hi"})
        self.assertEqual(reply_.tokens, 17)
        self.assertEqual(seen, ["Working", "Working on it"])
        self.assertTrue(_Handler.seen[0]["body"]["stream"])

    def test_openai_stream_joins_split_tool_calls_and_reads_usage(self):
        chunks = [
            {"choices": [{"delta": {"content": "Let me "}}]},
            {"choices": [{"delta": {"content": "check."}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "send_message", "arguments": '{"to": "wri'}}]}}]},
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": 'ter", "message": "go"}'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"total_tokens": 42}},
        ]
        _Handler.responses = [respond(events(*[(None, chunk) for chunk in chunks], (None, "[DONE]")))]
        seen = []

        reply_ = self.openai().complete("m", "s", [{"role": "user", "text": "hi"}], [], on_text=seen.append)

        self.assertEqual(reply_.text, "Let me check.")
        self.assertEqual(reply_.tool_calls[0].arguments, {"to": "writer", "message": "go"})
        self.assertEqual(reply_.tokens, 42)
        self.assertEqual(seen, ["Let me ", "Let me check."])

    def test_a_stream_that_fails_before_starting_is_retried(self):
        body = events((None, {"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]}), (None, "[DONE]"))
        _Handler.responses = [respond({"error": "busy"}, status=503), respond(body)]
        provider = self.openai()
        provider.sleep = self.sleeps.append

        reply_ = provider.complete("m", "s", [{"role": "user", "text": "hi"}], [], on_text=lambda text: None)

        self.assertEqual(reply_.text, "ok")
        self.assertEqual(len(_Handler.seen), 2)
        self.assertEqual(len(self.sleeps), 1)

    def test_an_error_event_in_a_stream_is_reported(self):
        _Handler.responses = [respond(events(("error", {"type": "error", "error": {"message": "overloaded here"}})))]
        provider = AnthropicProvider("claude", "secret", base_url=self.base)

        with self.assertRaisesRegex(ProviderError, "overloaded here"):
            provider.complete("m", "s", [{"role": "user", "text": "hi"}], [], on_text=lambda text: None)

    def test_missing_key_is_a_clear_error(self):
        with self.assertRaisesRegex(ProviderError, "no API key configured"):
            build_provider("x", {"type": "openai_compatible", "base_url": "http://example.invalid"}, "")

    def test_unknown_provider_type_is_rejected(self):
        with self.assertRaisesRegex(ProviderError, "unknown provider type"):
            build_provider("x", {"type": "carrier_pigeon"}, "secret")


if __name__ == "__main__":
    unittest.main()
