"""Agents backed by model providers. They act through tools until the lead's answer passes review."""

import difflib
import queue
import threading
import time
from collections import deque
from dataclasses import asdict, dataclass, field

from .providers import ToolCall

STALL_REPEATS = 3  # this many near-identical turns in a row counts as stuck
STALL_SIMILARITY = 0.9
STALL_MIN_LENGTH = 80  # shorter turns are compared exactly, so "try 1" and "try 2" are not the same turn
DEFAULT_MAX_REPLANS = 2  # how many times a stalled run may get a new plan before it ends
DEFAULT_STALL_TURNS = 4  # turns in a row without progress that count as a stall
SILENT_TURNS_LIMIT = 2  # turns without any tool call before the run gives up
HISTORY_WINDOW = 16  # recent items sent to the model; older ones are dropped
POLL_SECONDS = 0.05


class _Deadline(Exception):
    """The time limit ended a model call before it returned."""


class _Interrupt(Exception):
    """A stop or the time limit ended the run while a tool call was waiting."""

    def __init__(self, status):
        super().__init__(status)
        self.status = status


class _Waiting:
    """One approval waiting for a decision."""

    def __init__(self):
        self.event = threading.Event()
        self.decision = None


def _result(call, content, is_error):
    return {"role": "tool", "tool_call_id": call.id, "content": content, "is_error": is_error}
LIVE_INTERVAL_SECONDS = 0.15  # at most one live-text update per agent this often

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
UPDATE_PLAN = {
    "name": "update_plan",
    "description": "Lead only. Write or replace the team's plan: the steps, who does each, and what done looks like.",
    "input_schema": {
        "type": "object",
        "properties": {"plan": {"type": "string"}},
        "required": ["plan"],
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
    tools: list = field(default_factory=list)  # names of extra tools this member may use
    approve: list = field(default_factory=list)  # tools whose calls wait for a person's decision


def normalize(text):
    return " ".join(text.lower().split())


def same_turn(first, second):
    """Exact match, or near-match for long turns. Short turns differ by a word, so they must match exactly."""
    if first == second:
        return True
    long_enough = min(len(first), len(second)) >= STALL_MIN_LENGTH
    return long_enough and difflib.SequenceMatcher(None, first, second).ratio() >= STALL_SIMILARITY


def history_to_json(history):
    """Plain data for storing a history. Tool calls become dicts; everything else is already plain."""
    return [
        {**item, "tool_calls": [asdict(call) for call in item["tool_calls"]]} if item.get("tool_calls") else item
        for item in history
    ]


def history_from_json(items):
    return [
        {**item, "tool_calls": [ToolCall(**call) for call in item["tool_calls"]]} if item.get("tool_calls") else item
        for item in items
    ]


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
    """Runs agents one turn at a time. self.status records why the last run or continuation ended."""

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
        save_state=None,
        extra_tools=None,
        on_live=None,
        step_mode=False,
        max_replans=DEFAULT_MAX_REPLANS,
        stall_turns=DEFAULT_STALL_TURNS,
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
        self.save_state = save_state  # called with a snapshot after every turn, so a restart can resume
        self.extra_tools = extra_tools or {}  # name -> (definition, handler), see tools.build_tools
        self.on_live = on_live  # called with (agent, text so far) while a reply streams, and (agent, "") when it ends
        self._last_live = {}
        self._call_id = 0  # identifies the current model call, so an abandoned call's late text is ignored
        self.turn_no = 0  # turns taken so far, across runs and continuations; checkpoints are keyed by it
        self.pending_approvals = {}  # call id -> _Waiting
        self.step_mode = bool(step_mode)
        self.max_replans = max_replans
        self.stall_turns = stall_turns
        self.plan = None
        self.replans_used = 0
        self._idle_turns = 0
        self._seen_calls = set()
        self._recent_messages = deque(maxlen=3)
        self._gate = threading.Event()
        self._paused = False
        self._deadline = float("inf")
        self.last_agent = None
        self.status = None
        self.tokens_used = 0
        self.goal = ""
        self.inboxes = {}
        self.ready = deque()
        self.silent_turns = {}
        self.pending_answer = None

    def announce(self):
        """Tell the event log who is on the team. The page uses this to draw the agent cards."""
        self.emit(
            "roster",
            lead=self.lead,
            verifier=self.verifier,
            max_tokens=self.max_tokens,
            step_mode=self.step_mode,
            members=[
                {"name": agent.name, "role": agent.role, "model": agent.model, "provider": agent.provider.name}
                for agent in self.agents.values()
            ],
        )

    def run(self, goal):
        """Start work on a goal. Returns the answer if the team finished, otherwise None."""
        self.goal = goal
        self.plan = None
        self.replans_used = 0
        self._idle_turns = 0
        self._seen_calls = set()
        self.announce()
        self._reset()
        self._enqueue(self.lead, f"From the user:\n{goal}")
        return self._loop()

    def continue_run(self, text):
        """Continue after the run ended. Histories and waiting messages are kept, so the team picks up where it left."""
        self.status = "running"
        self.silent_turns = {name: 0 for name in self.agents}
        for name in self.agents:
            self.inboxes.setdefault(name, [])
        self._enqueue(self.lead, f"From the user, as a follow-up:\n{text}")
        return self._loop()

    def _reset(self):
        self.inboxes = {name: [] for name in self.agents}
        self.ready = deque()
        self.silent_turns = {name: 0 for name in self.agents}
        self.pending_answer = None
        self.status = "running"

    def _loop(self):
        started = self.clock()
        self._deadline = time.monotonic() + self.max_seconds  # approvals wait within the same time limit
        recent = deque(maxlen=STALL_REPEATS)
        turns = 0
        while True:
            self._drain_human()
            if self.stop_event.is_set():
                return self._finish("stopped")
            elapsed = self.clock() - started
            if elapsed > self.max_seconds:
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
            self.emit("agent", name=name, state="thinking")
            timed_out = False
            try:
                reply = self._call(agent, self.max_seconds - elapsed)
            except _Deadline:
                reply, timed_out = None, True
            self._live(name, "", force=True)
            if reply is None:
                return self._finish("timeout" if timed_out else "stopped")
            turns += 1
            self.turn_no += 1
            self.last_agent = name
            self.tokens_used += reply.tokens
            self.emit("agent", name=name, state="idle")
            self.emit(
                "turn",
                agent=name,
                turn=self.turn_no,
                tokens=reply.tokens,
                total_tokens=self.tokens_used,
                truncated=reply.truncated,
            )
            agent.history.append({"role": "assistant", "text": reply.text, "tool_calls": reply.tool_calls})

            results, decision, failed, interrupt = self._handle_calls(name, reply.tool_calls)
            agent.history.extend(results)
            if interrupt is not None:
                return self._finish(interrupt.status)
            self._checkpoint()
            if decision is not None:
                return self._finish("done", decision)
            if reply.truncated:
                self._enqueue(name, "System: your last reply was cut off by the output limit. Keep messages shorter.")
            if failed:
                self._enqueue(name, "System: some tool calls failed. Read the results above, fix them, and try again.")
            elif any(call.name not in ("send_message", "finish", "submit_verdict") for call in reply.tool_calls):
                # Work tools (files, fetch, approved calls, the plan) need a turn to act on their results.
                self._enqueue(name, "System: the tool results above are ready. Continue your work.")
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
            if self._made_progress(reply.tool_calls, results):
                self._idle_turns = 0
            else:
                self._idle_turns += 1
            repeated = len(recent) == STALL_REPEATS and all(same_turn(recent[0], other) for other in recent)
            if repeated or self._idle_turns >= self.stall_turns:
                if self.replans_used >= self.max_replans:
                    return self._finish("stalled")
                self._replan("the same turns keep repeating" if repeated else f"no new progress for {self._idle_turns} turns")
                recent.clear()
                self._idle_turns = 0
            if self.step_mode and not self._wait_for_step():
                return self._finish("stopped")

    def _finish(self, status, answer=None):
        self.status = status
        return answer

    def _made_progress(self, calls, results):
        """A successful call with arguments the run has not used before counts as progress."""
        progressed = False
        for call, result in zip(calls, results):
            if result["is_error"]:
                continue
            signature = f"{call.name} {sorted(call.arguments.items(), key=lambda item: str(item[0]))}"
            if signature not in self._seen_calls:
                self._seen_calls.add(signature)
                progressed = True
        return progressed

    def _replan(self, reason):
        """The lead gets a progress ledger and is asked for a new plan. Each run gets a limited number."""
        self.replans_used += 1
        self.emit("replan", count=self.replans_used, limit=self.max_replans, reason=reason)
        recent = [f"- {m['sender']} → {m['recipient']}: {m['text'][:120]}" for m in self._recent_messages] or ["- none yet"]
        ledger = [
            "System: The team has stalled. Progress ledger:",
            f"- Goal: {self.goal}",
            f"- Why this was flagged: {reason}",
            f"- Turns without new progress: {self._idle_turns}",
            f"- Current plan: {self.plan or 'none recorded yet'}",
            "- Latest messages:",
            *recent,
            "Write a new plan with update_plan, then give the next steps with send_message. Do not repeat the steps that did not work.",
        ]
        self._enqueue(self.lead, "\n".join(ledger))

    def set_step_mode(self, enabled):
        """In step mode the run pauses after every turn until step_next() is called. Turning it off resumes."""
        self.step_mode = bool(enabled)
        if not self.step_mode:
            self._gate.set()

    def step_next(self):
        """Let a paused run take one more turn. Returns False if the run is not paused."""
        if not self._paused:
            return False
        self._gate.set()
        return True

    def _wait_for_step(self):
        """Pause until someone advances the run. Returns False if the run was stopped while paused."""
        self._gate.clear()
        self._paused = True
        self.emit("paused", turn=self.turn_no)
        try:
            while not self._gate.wait(POLL_SECONDS):
                if self.stop_event.is_set():
                    return False
        finally:
            self._paused = False
        self.emit("resumed", turn=self.turn_no)
        return True

    def _live(self, name, text, force=False):
        """Report a reply's text as it streams. Updates are throttled, except the final clearing one."""
        if self.on_live is None:
            return
        now = time.monotonic()
        if not force and text and now - self._last_live.get(name, 0.0) < LIVE_INTERVAL_SECONDS:
            return
        self._last_live[name] = now
        self.on_live(name, text)

    def snapshot(self):
        """Everything needed to continue this team from this exact point: histories, waiting messages, counters."""
        return {
            "turn": self.turn_no,
            "agent": self.last_agent,
            "agents": {name: history_to_json(agent.history) for name, agent in self.agents.items()},
            "inboxes": {name: list(items) for name, items in self.inboxes.items()},
            "ready": list(self.ready),
            "silent_turns": dict(self.silent_turns),
            "pending_answer": self.pending_answer,
            "tokens_used": self.tokens_used,
            "plan": self.plan,
            "replans_used": self.replans_used,
        }

    def restore(self, snapshot):
        for name, items in snapshot.get("agents", {}).items():
            if name in self.agents:
                self.agents[name].history = history_from_json(items)
        self.turn_no = snapshot.get("turn", 0)
        self.last_agent = snapshot.get("agent")
        self.inboxes = {name: list(snapshot.get("inboxes", {}).get(name, [])) for name in self.agents}
        self.ready = deque(name for name in snapshot.get("ready", []) if name in self.agents)
        self.silent_turns = {name: snapshot.get("silent_turns", {}).get(name, 0) for name in self.agents}
        self.pending_answer = snapshot.get("pending_answer")
        self.tokens_used = snapshot.get("tokens_used", 0)
        self.plan = snapshot.get("plan")
        self.replans_used = snapshot.get("replans_used", 0)

    def _checkpoint(self):
        if self.save_state is not None:
            self.save_state(self.snapshot())

    def _call(self, agent, remaining_seconds):
        """Run one model call on a worker thread, so a stop or the time limit takes effect at once.

        Returns None if stopped. Raises _Deadline if the time limit ends the call first. An abandoned call's
        later text is dropped, because its call id no longer matches.
        """
        self._call_id += 1
        call_id = self._call_id
        deadline = time.monotonic() + remaining_seconds
        outcome = {}

        def work():
            try:
                outcome["reply"] = agent.provider.complete(
                    agent.model,
                    self._system_prompt(agent),
                    context_view(agent.history),
                    self._tools_for(agent.name),
                    on_text=lambda text: self._stream_text(call_id, agent.name, text),
                )
            except Exception as error:
                outcome["error"] = error

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        while worker.is_alive():
            if self.stop_event.is_set():
                self._call_id += 1
                return None
            if time.monotonic() >= deadline:
                self._call_id += 1
                raise _Deadline()
            worker.join(POLL_SECONDS)
        if "error" in outcome:
            raise outcome["error"]
        return outcome["reply"]

    def _stream_text(self, call_id, name, text):
        if call_id == self._call_id:
            self._live(name, text)

    def _tools_for(self, name):
        if name == self.verifier:
            base = [SEND_MESSAGE, SUBMIT_VERDICT]
        elif name == self.lead:
            base = [SEND_MESSAGE, FINISH, UPDATE_PLAN]
        else:
            base = [SEND_MESSAGE]
        extra = [self.extra_tools[tool][0] for tool in self.agents[name].tools if tool in self.extra_tools]
        return base + extra

    def _handle_calls(self, sender, calls):
        """Run one turn's tool calls. Returns (tool result items, decision or None, whether any call failed,
        the interrupt or None). Every call gets a result, even when the run is interrupted part way."""
        results, decision, failed = [], None, False
        allowed = {tool["name"] for tool in self._tools_for(sender)}
        for index, call in enumerate(calls):
            began = time.monotonic()
            try:
                if call.error:
                    content, is_error, outcome = f"The arguments were not valid JSON ({call.error}). Send the call again.", True, None
                elif call.name not in allowed:
                    available = ", ".join(sorted(allowed))
                    content, is_error, outcome = f"The tool {call.name!r} is not available to you. Available: {available}.", True, None
                else:
                    content, is_error, outcome = self._execute(sender, call)
            except _Interrupt as interrupt:
                for skipped in calls[index:]:
                    results.append(_result(skipped, f"The run {interrupt.status} before this call ran.", True))
                return results, decision, True, interrupt
            if outcome is not None:
                decision = outcome
            failed = failed or is_error
            results.append(_result(call, content, is_error))
            self.emit(
                "tool",
                agent=sender,
                turn=self.turn_no,
                tool=call.name,
                is_error=is_error,
                duration_ms=round((time.monotonic() - began) * 1000),
            )
        return results, decision, failed, None

    def _await_approval(self, sender, call):
        """Wait for a person to approve, change, or reject a call. Stops early on a stop or the time limit."""
        waiting = _Waiting()
        self.pending_approvals[call.id] = waiting
        self.emit("approval", id=call.id, agent=sender, tool=call.name, arguments=call.arguments)
        decided = False
        try:
            while not waiting.event.wait(POLL_SECONDS):
                if self.stop_event.is_set():
                    raise _Interrupt("stopped")
                if time.monotonic() >= self._deadline:
                    raise _Interrupt("timeout")
            decided = True
        finally:
            self.pending_approvals.pop(call.id, None)
            self.emit("approval_result", id=call.id, action=waiting.decision["action"] if decided else "cancelled")
        return waiting.decision

    def decide_approval(self, call_id, decision):
        """Called by the page through the server. Returns False if no approval is waiting with that id."""
        waiting = self.pending_approvals.get(call_id)
        if waiting is None or waiting.event.is_set():
            return False
        waiting.decision = decision
        waiting.event.set()
        return True

    def _execute(self, sender, call):
        """Returns (result text, is_error, final answer or None)."""
        args = call.arguments
        if call.name in self.extra_tools:
            if call.name in self.agents[sender].approve:
                decision = self._await_approval(sender, call)
                if decision.get("action") != "approve":
                    reason = str(decision.get("reason", "")).strip()
                    return f"The user did not approve this call.{' Reason: ' + reason if reason else ''}", True, None
                if isinstance(decision.get("arguments"), dict):
                    args = decision["arguments"]
            content, is_error = self.extra_tools[call.name][1](args)
            return content, is_error, None
        if call.name == "send_message":
            recipient = str(args.get("to", ""))
            message = str(args.get("message", "")).strip()
            if recipient not in self.agents or recipient == sender:
                return f"Unknown recipient {recipient!r}. You can message: {self._teammates(sender)}.", True, None
            if not message:
                return "The message is empty.", True, None
            self._send(sender, recipient, message)
            return f"Delivered to {recipient}.", False, None
        if call.name == "update_plan":
            plan = str(args.get("plan", "")).strip()
            if not plan:
                return "The plan is empty.", True, None
            self.plan = plan
            self.emit("plan", text=plan, turn=self.turn_no)
            return "Plan recorded.", False, None
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
        self._recent_messages.append({"sender": sender, "recipient": recipient, "text": text})
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
