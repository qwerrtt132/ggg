import unittest

from agent_team.trace import build_trace

RUN = {"id": "run1", "goal": "write a note", "status": "done"}


def event(kind, created_at, **data):
    return {"kind": kind, "data": data, "created_at": created_at}


class TraceTests(unittest.TestCase):
    def events(self):
        return [
            event("roster", 0.0, lead="lead", members=[{"name": "lead", "model": "m", "provider": "p", "role": "plan"}]),
            event("agent", 1.0, name="lead", state="thinking"),
            event("agent", 2.0, name="lead", state="idle"),
            event("turn", 2.0, agent="lead", turn=1, tokens=40, total_tokens=40, truncated=False),
            event("tool", 2.3, agent="lead", turn=1, tool="send_message", is_error=False, duration_ms=250),
            event("agent", 3.0, name="writer", state="thinking"),
            event("turn", 5.0, agent="writer", turn=2, tokens=60, total_tokens=100, truncated=True),
            event("tool", 5.1, agent="writer", turn=2, tool="write_file", is_error=True, duration_ms=50),
        ]

    def test_each_turn_holds_its_model_call_and_its_tool_calls(self):
        turns = build_trace(RUN, self.events())["turns"]

        self.assertEqual([turn["turn"] for turn in turns], [1, 2])
        self.assertEqual((turns[0]["agent"], turns[0]["tokens"]), ("lead", 40))
        self.assertEqual(turns[0]["duration_ms"], 1000)
        self.assertEqual([child["name"] for child in turns[0]["children"]], ["chat", "execute_tool"])
        self.assertEqual(turns[0]["children"][1]["tool"], "send_message")
        self.assertEqual(turns[0]["children"][1]["duration_ms"], 250)
        self.assertTrue(turns[1]["truncated"])
        self.assertTrue(turns[1]["children"][1]["is_error"])

    def test_span_names_follow_the_operation_names_and_say_so(self):
        trace = build_trace(RUN, self.events())

        self.assertEqual(trace["turns"][0]["name"], "invoke_agent")
        self.assertEqual(trace["turns"][0]["children"][0]["name"], "chat")
        self.assertEqual(trace["turns"][0]["children"][1]["name"], "execute_tool")
        self.assertIn("labels", trace["conventions"])
        self.assertEqual(trace["run"]["id"], "run1")

    def test_a_run_with_no_turns_has_an_empty_trace(self):
        self.assertEqual(build_trace(RUN, [])["turns"], [])


if __name__ == "__main__":
    unittest.main()
