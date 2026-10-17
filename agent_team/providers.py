"""Provider adapters. Each one converts a neutral chat history into one HTTP call and returns a Reply.

With on_text, the adapter streams: on_text(text_so_far) runs as text arrives, so a UI can show progress.
"""

import http.client
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

    def complete(self, model, system, messages, tools, on_text=None):
        raise NotImplementedError

    def _post(self, url, headers, body):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._send(url, headers, body)
            except ProviderError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise
                self.sleep(error.retry_after or min(MAX_BACKOFF_SECONDS, 2**attempt))

    def _post_stream(self, url, headers, body, make_parser):
        for attempt in range(1, self.max_attempts + 1):
            try:
                return self._stream_once(url, headers, {**body, "stream": True}, make_parser())
            except ProviderError as error:
                if not error.retryable or attempt == self.max_attempts:
                    raise
                self.sleep(error.retry_after or min(MAX_BACKOFF_SECONDS, 2**attempt))

    def _request(self, url, headers, body):
        return urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            headers={"content-type": "application/json", **headers},
            method="POST",
        )

    def _send(self, url, headers, body):
        try:
            with urllib.request.urlopen(self._request(url, headers, body), timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise self._http_error(error) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError(f"{self.name}: request failed: {error}", retryable=True) from None

    def _stream_once(self, url, headers, body, parser):
        try:
            with urllib.request.urlopen(self._request(url, headers, body), timeout=self.timeout) as response:
                for event, data in _server_sent_events(response):
                    parser.feed(event, data)
        except urllib.error.HTTPError as error:
            raise self._http_error(error) from None
        except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as error:
            raise ProviderError(f"{self.name}: stream interrupted: {error}", retryable=True) from None
        return parser.result()

    def _http_error(self, error):
        detail = error.read().decode(errors="replace")[:500]
        return ProviderError(
            f"{self.name}: HTTP {error.code}: {detail}",
            retryable=error.code in RETRY_STATUSES,
            retry_after=_retry_after(error.headers.get("retry-after")),
        )


def _server_sent_events(response):
    """Yields (event name or None, data) for each server-sent event in the response."""
    event, data_lines = None, []
    for raw in response:
        line = raw.decode("utf-8").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield event, "\n".join(data_lines)
            event, data_lines = None, []
        elif line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    if data_lines:
        yield event, "\n".join(data_lines)


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

    def complete(self, model, system, messages, tools, on_text=None):
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
        if on_text is None:
            return _anthropic_reply(self._post(self.url, headers, body))
        return self._post_stream(self.url, headers, body, lambda: _AnthropicStream(on_text))


def _anthropic_reply(data):
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


class _AnthropicStream:
    def __init__(self, on_text):
        self.on_text = on_text
        self.blocks = {}
        self.stop_reason = None
        self.tokens_in = 0
        self.tokens_out = 0

    def feed(self, event, data):
        payload = json.loads(data)
        kind = payload.get("type", event)
        if kind == "message_start":
            self.tokens_in = payload["message"].get("usage", {}).get("input_tokens", 0)
        elif kind == "content_block_start":
            block = payload["content_block"]
            self.blocks[payload["index"]] = {
                "type": block["type"],
                "id": block.get("id"),
                "name": block.get("name"),
                "text": block.get("text", ""),
                "json": "",
            }
        elif kind == "content_block_delta":
            block = self.blocks[payload["index"]]
            delta = payload["delta"]
            if delta["type"] == "text_delta":
                block["text"] += delta["text"]
                self.on_text(self._text())
            elif delta["type"] == "input_json_delta":
                block["json"] += delta.get("partial_json", "")
        elif kind == "message_delta":
            self.stop_reason = payload["delta"].get("stop_reason")
            self.tokens_out = payload.get("usage", {}).get("output_tokens", 0)
        elif kind == "error":
            message = payload.get("error", {}).get("message", "unknown error")
            raise ProviderError(f"anthropic: stream error: {message}")

    def _text(self):
        return "".join(block["text"] for _, block in sorted(self.blocks.items()) if block["type"] == "text")

    def result(self):
        calls = []
        for _, block in sorted(self.blocks.items()):
            if block["type"] == "tool_use":
                try:
                    arguments, error = json.loads(block["json"] or "{}"), None
                except json.JSONDecodeError as parse_error:
                    arguments, error = {}, str(parse_error)
                if error is None and not isinstance(arguments, dict):
                    arguments, error = {}, "arguments must be a JSON object"
                calls.append(ToolCall(block["id"], block["name"], arguments, error))
        return Reply(
            text=self._text(),
            tool_calls=calls,
            truncated=self.stop_reason == "max_tokens",
            tokens=self.tokens_in + self.tokens_out,
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

    def complete(self, model, system, messages, tools, on_text=None):
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
        headers = {"authorization": f"Bearer {self.api_key}"}
        if on_text is None:
            return _openai_reply(self._post(self.url, headers, body))
        # Ask for token usage at the end of the stream. Servers that do not know this option may refuse it.
        body["stream_options"] = {"include_usage": True}
        return self._post_stream(self.url, headers, body, lambda: _OpenAIStream(on_text))


def _openai_reply(data):
    choice = data["choices"][0]
    message = choice["message"]
    return Reply(
        text=message.get("content") or "",
        tool_calls=[_openai_call(raw) for raw in message.get("tool_calls") or []],
        truncated=choice.get("finish_reason") == "length",
        tokens=data.get("usage", {}).get("total_tokens", 0),
    )


class _OpenAIStream:
    def __init__(self, on_text):
        self.on_text = on_text
        self.text = ""
        self.calls = {}
        self.finish_reason = None
        self.tokens = 0

    def feed(self, event, data):
        if data.strip() == "[DONE]":
            return
        payload = json.loads(data)
        if payload.get("usage"):
            self.tokens = payload["usage"].get("total_tokens", 0)
        for choice in payload.get("choices", []):
            if choice.get("finish_reason"):
                self.finish_reason = choice["finish_reason"]
            delta = choice.get("delta", {})
            if delta.get("content"):
                self.text += delta["content"]
                self.on_text(self.text)
            for part in delta.get("tool_calls") or []:
                call = self.calls.setdefault(part.get("index", 0), {"id": "", "name": "", "arguments": ""})
                call["id"] = part.get("id") or call["id"]
                function = part.get("function", {})
                call["name"] = function.get("name") or call["name"]
                call["arguments"] += function.get("arguments") or ""

    def result(self):
        calls = [
            _openai_call({"id": call["id"], "function": {"name": call["name"], "arguments": call["arguments"]}})
            for _, call in sorted(self.calls.items())
        ]
        return Reply(
            text=self.text,
            tool_calls=calls,
            truncated=self.finish_reason == "length",
            tokens=self.tokens,
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
