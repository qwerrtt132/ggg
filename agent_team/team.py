"""Agents backed by model providers. They message each other until the lead's answer passes review."""

import queue
import re
import threading
import time
from collections import deque
from dataclasses import dataclass, field

ROUTE = re.compile(r"^TO (\w+):[ \t]*", re.MULTILINE)
FINAL = re.compile(r"^FINAL:[ \t]*", re.MULTILINE)
PASS = re.compile(r"^VERDICT:\s*PASS\b", re.MULTILINE | re.IGNORECASE)
STALL_REPEATS = 3  # the same agent sending the same reply this many times in a row counts as stuck


@dataclass
class Agent:
    name: str
    role: str
    provider: object
    model: str
    history: list = field(default_factory=list)


def parse_outgoing(text, default_recipient):
    """Split a reply into (recipient, message) pairs. Replies with no TO line go to the default recipient."""
    matches = list(ROUTE.finditer(text))
    if not matches:
        return [(default_recipient, text.strip())] if text.strip() else []
    messages = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        messages.append((match.group(1), text[match.end():end].strip()))
    return messages


def final_answer(text):
    match = FINAL.search(text)
    return text[match.end():].strip() if match else None


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

    def run(self, goal):
        """Work on a goal. Returns the answer if the team finished, otherwise None."""
        self.goal = goal
        self.inboxes = {name: [] for name in self.agents}
        self.ready = deque()
        self.status = "running"
        self._enqueue(self.lead, f"From the user:\n{goal}")
        started = self.clock()
        recent = deque(maxlen=STALL_REPEATS)
        pending_answer = None
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
            incoming = "\n\n".join(self.inboxes[name])
            self.inboxes[name] = []
            agent.history.append({"role": "user", "content": incoming})
            reply = agent.provider.complete(agent.model, self._system_prompt(agent), agent.history)
            agent.history.append({"role": "assistant", "content": reply})
            turns += 1
            tokens = agent.provider.last_tokens
            self.tokens_used += tokens
            self.emit("turn", agent=name, tokens=tokens, total_tokens=self.tokens_used)

            recent.append((name, reply.strip()))
            if len(recent) == STALL_REPEATS and len(set(recent)) == 1:
                return self._finish("stalled")

            answer = final_answer(reply) if name == self.lead else None
            if answer is not None and self.verifier is None:
                return self._finish("done", answer)
            if answer is not None:
                pending_answer = answer
                self._send(self.lead, self.verifier, self._verification_request(answer))
                continue
            if name == self.verifier and pending_answer is not None:
                if PASS.search(reply):
                    return self._finish("done", pending_answer)
                pending_answer = None
            for recipient, text in parse_outgoing(reply, self.lead):
                self._send(name, recipient, text)

    def _finish(self, status, answer=None):
        self.status = status
        return answer

    def _verification_request(self, answer):
        return (
            "Check this candidate final answer against the user's goal. "
            "Reply starting with VERDICT: PASS or VERDICT: FAIL.\n\n"
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
        if recipient in self.agents:
            self._enqueue(recipient, f"From {sender}:\n{text}")
        else:
            members = ", ".join(name for name in self.agents if name != sender)
            self._enqueue(sender, f"System: there is no team member named {recipient!r}. You can message: {members}.")

    def _system_prompt(self, agent):
        others = ", ".join(name for name in self.agents if name != agent.name)
        lines = [
            f"You are {agent.name!r}, one member of a team working on a task for a user.",
            f"Your role: {agent.role}",
            f"Teammates: {others}. The lead is {self.lead!r}.",
            "Address every message you send with a line that starts with `TO <name>:`. "
            "A message runs until the next TO line. Text before the first TO line is not delivered. "
            "You only see messages sent to you.",
        ]
        if agent.name == self.lead:
            lines.append(
                "When the user's goal is complete, reply with a line that starts with `FINAL:` "
                "followed by the full answer for the user."
            )
            if self.verifier is not None:
                lines.append(f"Your final answer is checked by {self.verifier!r}. If it fails, revise and try again.")
        if agent.name == self.verifier:
            lines.append(
                "You check the lead's candidate final answer against the user's goal. "
                "Start your reply with `VERDICT: PASS` only if the answer fully meets the goal. "
                "Otherwise start with `VERDICT: FAIL` and list the specific problems, addressed TO the lead."
            )
        return "\n".join(lines)
