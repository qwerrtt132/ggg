"""Provider adapters. Each one converts a neutral chat history into one HTTP call and returns a Reply."""

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

ANTHROPIC_URL = "https://api.anthropic.com"
RETRY_STATUSES = {429, 500, 502, 503, 529}
MAX_BACKOFF_SECONDS = 30

# Neutral history items, shared by every adapter:
#   {"role": "user", "text": str}
#   {"role": "assistant", "text": str, "tool_calls": [ToolCall, ...]}
#   {"role": "tool", "tool_call_id": str, "content": str, "is_error": bool}
# Tool definitions: {"name": str, "description": str, "input_schema": JSON schema}


class ProviderError(RuntimeError):
    def __init__(self, message, retryable=False, retry_after=None):
        super().__init__(message)
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict = field(default_factory=dict)
    error: str = None  # set when the model's arguments could not be parsed


@dataclass
class Reply:
    text: str = ""
    tool_calls: list = field(default_factory=list)
    truncated: bool = False  # the output limit cut the reply short
    tokens: int = 0


class Provider:
    def __init__(self, name, api_key, timeout=120, max_attempts=4, sleep=time.sleep):
        if not api_key:
            raise ProviderError(f"{name}: no API key configured")
        self.name = name
        self.api_key = api_key
        self.timeout = timeout
        self.max_attempts = max_attempts
        self.sleep = sleep

    def complete(self, model, system, messages, tools):
        raise NotImplementedError

    def _post(self, url, headers, body):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._send(url, headers, body)
            except ProviderError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise
                self.sleep(error.retry_after or min(MAX_BACKOFF_SECONDS, 2**attempt))

    def _send(self, url, headers, body):
        request = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", **headers},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode(errors="replace")[:500]
            raise ProviderError(
                f"{self.name}: HTTP {error.code}: {detail}",
                retryable=error.code in RETRY_STATUSES,
                retry_after=_retry_after(error.headers.get("retry-after")),
            ) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError(f"{self.name}: request failed: {error}", retryable=True) from None


def _retry_after(value):
    try:
        return min(MAX_BACKOFF_SECONDS, float(value))
    except (TypeError, ValueError):
        return None


class AnthropicProvider(Provider):
    def __init__(self, name, api_key, base_url=ANTHROPIC_URL, max_tokens=4096, **kwargs):
        super().__init__(name, api_key, **kwargs)
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.max_tokens = max_tokens

    def complete(self, model, system, messages, tools):
        body = {
            "model": model,
            "max_tokens": self.max_tokens,
            "system": system,
            "messages": _anthropic_messages(messages),
            "tools": [
                {"name": tool["name"], "description": tool["description"], "input_schema": tool["input_schema"]}
                for tool in tools
            ],
        }
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = self._post(self.url, headers, body)
        blocks = data["content"]
        usage = data.get("usage", {})
        return Reply(
            text="".join(block.get("text", "") for block in blocks if block["type"] == "text"),
            tool_calls=[
                ToolCall(block["id"], block["name"], block.get("input") or {})
                for block in blocks
                if block["type"] == "tool_use"
            ],
            truncated=data.get("stop_reason") == "max_tokens",
            tokens=usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
        )


def _anthropic_messages(history):
    """Anthropic wants alternating roles, with tool results inside the user message that follows the call."""
    converted = []
    for item in history:
        if item["role"] == "user":
            role = "user"
            blocks = [{"type": "text", "text": item["text"]}] if item["text"].strip() else []
        elif item["role"] == "assistant":
            role = "assistant"
            blocks = [{"type": "text", "text": item["text"]}] if item.get("text", "").strip() else []
            blocks += [
                {"type": "tool_use", "id": call.id, "name": call.name, "input": call.arguments}
                for call in item.get("tool_calls", [])
            ]
        else:
            role = "user"
            blocks = [
                {
                    "type": "tool_result",
                    "tool_use_id": item["tool_call_id"],
                    "content": item["content"],
                    "is_error": item.get("is_error", False),
                }
            ]
        if not blocks:
            continue
        if converted and converted[-1]["role"] == role:
            converted[-1]["content"].extend(blocks)
        else:
            converted.append({"role": role, "content": blocks})
    return converted


class OpenAICompatibleProvider(Provider):
    """Works with any server that implements POST {base_url}/chat/completions with tool calling."""

    def __init__(self, name, api_key, base_url, **kwargs):
        super().__init__(name, api_key, **kwargs)
        self.url = base_url.rstrip("/") + "/chat/completions"

    def complete(self, model, system, messages, tools):
        body = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *_openai_messages(messages)],
            "tools": [
                {
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "description": tool["description"],
                        "parameters": tool["input_schema"],
                    },
                }
                for tool in tools
            ],
        }
        data = self._post(self.url, {"authorization": f"Bearer {self.api_key}"}, body)
        choice = data["choices"][0]
        message = choice["message"]
        return Reply(
            text=message.get("content") or "",
            tool_calls=[_openai_call(raw) for raw in message.get("tool_calls") or []],
            truncated=choice.get("finish_reason") == "length",
            tokens=data.get("usage", {}).get("total_tokens", 0),
        )


def _openai_messages(history):
    converted = []
    for item in history:
        if item["role"] == "user":
            converted.append({"role": "user", "content": item["text"]})
        elif item["role"] == "assistant":
            message = {"role": "assistant", "content": item.get("text", "")}
            if item.get("tool_calls"):
                message["tool_calls"] = [
                    {
                        "id": call.id,
                        "type": "function",
                        "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                    }
                    for call in item["tool_calls"]
                ]
            converted.append(message)
        else:
            converted.append({"role": "tool", "tool_call_id": item["tool_call_id"], "content": item["content"]})
    return converted


def _openai_call(raw):
    function = raw["function"]
    try:
        arguments = json.loads(function.get("arguments") or "{}")
    except json.JSONDecodeError as error:
        return ToolCall(raw["id"], function["name"], {}, error=str(error))
    if not isinstance(arguments, dict):
        return ToolCall(raw["id"], function["name"], {}, error="arguments must be a JSON object")
    return ToolCall(raw["id"], function["name"], arguments)


PROVIDER_TYPES = {
    "anthropic": AnthropicProvider,
    "openai_compatible": OpenAICompatibleProvider,
}


def build_provider(name, spec, api_key):
    """spec is one entry of the config's "providers" map. api_key_env is read by the caller, not here."""
    options = {key: value for key, value in spec.items() if key != "api_key_env"}
    kind = options.pop("type", None)
    if kind not in PROVIDER_TYPES:
        raise ProviderError(f"{name}: unknown provider type {kind!r}; expected one of {sorted(PROVIDER_TYPES)}")
    return PROVIDER_TYPES[kind](name, api_key, **options)
