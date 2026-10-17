"""Agents backed by model providers. They pass messages until the lead writes a FINAL answer."""

import re
from dataclasses import dataclass, field

ROUTE = re.compile(r"^TO (\w+):[ \t]*", re.MULTILINE)
FINAL = re.compile(r"^FINAL:[ \t]*", re.MULTILINE)


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
    def __init__(self, agents, lead, max_turns=30, on_message=None):
        names = [agent.name for agent in agents]
        if len(set(names)) != len(names):
            raise ValueError(f"team member names must be unique: {names}")
        if lead not in names:
            raise ValueError(f"lead {lead!r} is not a team member")
        self.agents = {agent.name: agent for agent in agents}
        self.lead = lead
        self.max_turns = max_turns
        self.on_message = on_message or (lambda sender, recipient, text: None)
        self.inboxes = {}

    def run(self, goal):
        """Work on a goal. Returns the lead's FINAL answer, or None if the team stopped without one."""
        self.inboxes = {name: [] for name in self.agents}
        self.inboxes[self.lead].append(f"From the user:\n{goal}")
        turns = 0
        while turns < self.max_turns:
            ready = [name for name, inbox in self.inboxes.items() if inbox]
            if not ready:
                return None
            for name in ready:
                if turns >= self.max_turns:
                    break
                turns += 1
                agent = self.agents[name]
                incoming = "\n\n".join(self.inboxes[name])
                self.inboxes[name] = []
                agent.history.append({"role": "user", "content": incoming})
                reply = agent.provider.complete(agent.model, self._system_prompt(agent), agent.history)
                agent.history.append({"role": "assistant", "content": reply})
                if name == self.lead:
                    answer = final_answer(reply)
                    if answer is not None:
                        return answer
                for recipient, text in parse_outgoing(reply, self.lead):
                    self._deliver(name, recipient, text)
        return None

    def _deliver(self, sender, recipient, text):
        self.on_message(sender, recipient, text)
        if recipient in self.inboxes:
            self.inboxes[recipient].append(f"From {sender}:\n{text}")
        else:
            members = ", ".join(name for name in self.agents if name != sender)
            self.inboxes[sender].append(
                f"System: there is no team member named {recipient!r}. You can message: {members}."
            )

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
        return "\n".join(lines)
