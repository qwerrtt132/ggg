"""Agents backed by model providers. They act through tools until the lead's answer passes review."""

import difflib
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field

STALL_REPEATS = 3  # this many near-identical turns in a row counts as stuck
STALL_SIMILARITY = 0.9
STALL_MIN_LENGTH = 80  # shorter turns are compared exactly, so "try 1" and "try 2" are not the same turn
SILENT_TURNS_LIMIT = 2  # turns without any tool call before the run gives up
HISTORY_WINDOW = 16  # recent items sent to the model; older ones are dropped
POLL_SECONDS = 0.05

SEND_MESSAGE = {
    "name": "send_message",
    "description": "Send a message to a teammate by name. Their reply arrives later as a new message to you.",
    "input_schema": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Name of a teammate"},
            "message": {"type": "string", "description": "What to send"},
        },
        "required": ["to", "message"],
    },
}
FINISH = {
    "name": "finish",
    "description": "Lead only. Give the complete final answer to the user once the goal is met.",
    "input_schema": {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    },
}
SUBMIT_VERDICT = {
    "name": "submit_verdict",
    "description": "Verifier only. Report whether the lead's candidate answer fully meets the goal.",
    "input_schema": {
        "type": "object",
        "properties": {
            "passed": {"type": "boolean"},
            "feedback": {"type": "string", "description": "When failed, the specific problems to fix"},
        },
        "required": ["passed", "feedback"],
    },
}


@dataclass
class Agent:
    name: str
    role: str
    provider: object
    model: str
    history: list = field(default_factory=list)


def normalize(text):
    return " ".join(text.lower().split())


def same_turn(first, second):
    """Exact match, or near-match for long turns. Short turns differ by a word, so they must match exactly."""
    if first == second:
        return True
    long_enough = min(len(first), len(second)) >= STALL_MIN_LENGTH
    return long_enough and difflib.SequenceMatcher(None, first, second).ratio() >= STALL_SIMILARITY


def context_view(history, window=HISTORY_WINDOW):
    """The part of an agent's history the model sees. Long histories keep the first item and a tail that starts
    at a user message, so a tool result never loses the call it answers."""
    if len(history) <= window:
        return list(history)
    start = next(
        (index for index in range(len(history) - window, len(history)) if history[index]["role"] == "user"),
        None,
    )
    if start is None or start <= 1:
        return list(history)
    omitted = {"role": "user", "text": "[Earlier messages were removed to save context.]"}
    return [history[0], omitted, *history[start:]]


class Team:
    """Runs agents one turn at a time. self.status records why the run ended."""

    def __init__(
        self,
        agents,
        lead,
        *,
        verifier=None,
        max_turns=30,
        max_seconds=600,
        max_tokens=None,
        emit=None,
        human_inbox=None,
        stop_event=None,
        clock=time.monotonic,
    ):
        names = [agent.name for agent in agents]
        if len(set(names)) != len(names):
            raise ValueError(f"team member names must be unique: {names}")
        if lead not in names:
            raise ValueError(f"lead {lead!r} is not a team member")
        if verifier is not None and (verifier not in names or verifier == lead):
            raise ValueError(f"verifier {verifier!r} must be a teammate other than the lead")
        self.agents = {agent.name: agent for agent in agents}
        self.lead = lead
        self.verifier = verifier
        self.max_turns = max_turns
        self.max_seconds = max_seconds
        self.max_tokens = max_tokens
        self.emit = emit or (lambda kind, **data: None)
        self.human_inbox = human_inbox
        self.stop_event = stop_event or threading.Event()
        self.clock = clock
        self.status = None
        self.tokens_used = 0
        self.goal = ""
        self.inboxes = {}
        self.ready = deque()
        self.silent_turns = {}
        self.pending_answer = None

    def run(self, goal):
        """Work on a goal. Returns the answer if the team finished, otherwise None."""
        self.goal = goal
        self.inboxes = {name: [] for name in self.agents}
        self.ready = deque()
        self.silent_turns = {name: 0 for name in self.agents}
        self.pending_answer = None
        self.status = "running"
        self._enqueue(self.lead, f"From the user:\n{goal}")
        started = self.clock()
        recent = deque(maxlen=STALL_REPEATS)
        turns = 0
        while True:
            self._drain_human()
            if self.stop_event.is_set():
                return self._finish("stopped")
            if self.clock() - started > self.max_seconds:
                return self._finish("timeout")
            if self.max_tokens is not None and self.tokens_used >= self.max_tokens:
                return self._finish("token_budget")
            if turns >= self.max_turns:
                return self._finish("max_turns")
            if not self.ready:
                return self._finish("idle")

            name = self.ready.popleft()
            agent = self.agents[name]
            agent.history.append({"role": "user", "text": "\n\n".join(self.inboxes[name])})
            self.inboxes[name] = []
            reply = self._call(agent)
            if reply is None:
                return self._finish("stopped")
            turns += 1
            self.tokens_used += reply.tokens
            self.emit("turn", agent=name, tokens=reply.tokens, total_tokens=self.tokens_used, truncated=reply.truncated)
            agent.history.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls})

            results, decision, failed = self._handle_calls(name, reply.tool_calls)
            agent.history.extend(results)
            if decision is not None:
                return self._finish("done", decision)
            if reply.truncated:
                self._enqueue(name, "System: your last reply was cut off by the output limit. Keep messages shorter.")
            if failed:
                self._enqueue(name, "System: some tool calls failed. Read the results above, fix them, and try again.")
            if reply.tool_calls:
                self.silent_turns[name] = 0
            else:
                self.silent_turns[name] += 1
                if self.silent_turns[name] >= SILENT_TURNS_LIMIT:
                    return self._finish("no_progress")
                self._enqueue(
                    name,
                    "System: your reply had no tool calls, so nothing was sent. Use send_message to talk to a "
                    "teammate, or finish if you are the lead.",
                )
            fingerprint = normalize(reply.text + " " + " ".join(f"{c.name} {c.arguments}" for c in reply.tool_calls))
            recent.append(fingerprint)
            if len(recent) == STALL_REPEATS and all(same_turn(recent[0], other) for other in recent):
                return self._finish("stalled")

    def _finish(self, status, answer=None):
        self.status = status
        return answer

    def _call(self, agent):
        """Run one model call on a worker thread so a stop takes effect at once. Returns None if stopped."""
        outcome = {}

        def work():
            try:
                outcome["reply"] = agent.provider.complete(
                    agent.model,
                    self._system_prompt(agent),
                    context_view(agent.history),
                    self._tools_for(agent.name),
                )
            except Exception as error:
                outcome["error"] = error

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            if self.stop_event.is_set():
                return None
            worker.join(POLL_SECONDS)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _tools_for(self, name):
        if name == self.verifier:
            return [SEND_MESSAGE, SUBMIT_VERDICT]
        if name == self.lead:
            return [SEND_MESSAGE, FINISH]
        return [SEND_MESSAGE]

    def _handle_calls(self, sender, calls):
        """Run one turn's tool calls. Returns (tool result items, decision or None, whether any call failed)."""
        results, decision, failed = [], None, False
        allowed = {tool["name"] for tool in self._tools_for(sender)}
        for call in calls:
            if call.error:
                content, is_error, outcome = f"The arguments were not valid JSON ({call.error}). Send the call again.", True, None
            elif call.name not in allowed:
                available = ", ".join(sorted(allowed))
                content, is_error, outcome = f"The tool {call.name!r} is not available to you. Available: {available}.", True, None
            else:
                content, is_error, outcome = self._execute(sender, call)
            if outcome is not None:
                decision = outcome
            failed = failed or is_error
            results.append({"role": "tool", "tool_call_id": call.id, "content": content, "is_error": is_error})
        return results, decision, failed

    def _execute(self, sender, call):
        """Returns (result text, is_error, final answer or None)."""
        args = call.arguments
        if call.name == "send_message":
            recipient = str(args.get("to", ""))
            message = str(args.get("message", "")).strip()
            if recipient not in self.agents or recipient == sender:
                return f"Unknown recipient {recipient!r}. You can message: {self._teammates(sender)}.", True, None
            if not message:
                return "The message is empty.", True, None
            self._send(sender, recipient, message)
            return f"Delivered to {recipient}.", False, None
        if call.name == "finish":
            answer = str(args.get("answer", "")).strip()
            if not answer:
                return "The answer is empty.", True, None
            if self.verifier is None:
                return "Final answer delivered.", False, answer
            self.pending_answer = answer
            self._send(sender, self.verifier, self._verification_request(answer))
            return "Sent to the verifier. Wait for its verdict.", False, None
        if call.name == "submit_verdict":
            if self.pending_answer is None:
                return "There is no candidate answer to check.", True, None
            if str(args.get("passed", "")).lower() == "true":
                return "Verdict recorded: PASS.", False, self.pending_answer
            feedback = str(args.get("feedback", "")).strip() or "The answer does not meet the goal."
            self.pending_answer = None
            self._send(sender, self.lead, f"VERDICT: FAIL\n\n{feedback}")
            return "Verdict recorded: FAIL. The lead will revise.", False, None
        return "Unknown tool.", True, None

    def _verification_request(self, answer):
        return (
            "Check this candidate final answer against the user's goal. Call submit_verdict with passed=true "
            "only if it fully meets the goal.\n\n"
            f"Goal:\n{self.goal}\n\nCandidate answer:\n{answer}"
        )

    def _drain_human(self):
        if self.human_inbox is None:
            return
        while True:
            try:
                text = self.human_inbox.get_nowait()
            except queue.Empty:
                return
            self._enqueue(self.lead, f"From the user, during the run:\n{text}")

    def _enqueue(self, name, text):
        self.inboxes[name].append(text)
        if name not in self.ready:
            self.ready.append(name)

    def _send(self, sender, recipient, text):
        self.emit("message", sender=sender, recipient=recipient, text=text)
        self._enqueue(recipient, f"From {sender}:\n{text}")

    def _teammates(self, sender):
        return ", ".join(name for name in self.agents if name != sender)

    def _system_prompt(self, agent):
        lines = [
            f"You are {agent.name!r}, one member of a team working on a task for a user.",
            f"Your role: {agent.role}",
            f"Teammates: {self._teammates(agent.name)}. The lead is {self.lead!r}.",
            "Talk only through tools. Use send_message(to, message) to reach a teammate. Plain text in your reply "
            "is not delivered to anyone. You only see messages sent to you.",
        ]
        if agent.name == self.lead:
            lines.append("When the user's goal is complete, call finish with the full answer for the user.")
            if self.verifier is not None:
                lines.append(f"Your final answer is checked by {self.verifier!r}. If it fails, revise and finish again.")
        if agent.name == self.verifier:
            lines.append(
                "You check the lead's candidate answer against the user's goal. Call submit_verdict with "
                "passed=true only if the answer fully meets the goal. Otherwise use passed=false and list the "
                "specific problems."
            )
        return "\n".join(lines)
