"""Provider adapters. Each one turns a chat history into one HTTP call and returns the reply text."""

import json
import urllib.error
import urllib.request

ANTHROPIC_URL = "https://api.anthropic.com"


class ProviderError(RuntimeError):
    pass


class Provider:
    def __init__(self, name, api_key, timeout=120):
        if not api_key:
            raise ProviderError(f"{name}: no API key configured")
        self.name = name
        self.api_key = api_key
        self.timeout = timeout
        self.last_tokens = 0  # tokens used by the most recent call, when the API reports them

    def complete(self, model, system, messages):
        """messages alternate user/assistant, starting with user. Returns the reply text."""
        raise NotImplementedError

    def _post(self, url, headers, body):
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
            raise ProviderError(f"{self.name}: HTTP {error.code}: {detail}") from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ProviderError(f"{self.name}: request failed: {error}") from None


class AnthropicProvider(Provider):
    def __init__(self, name, api_key, base_url=ANTHROPIC_URL, max_tokens=4096, **kwargs):
        super().__init__(name, api_key, **kwargs)
        self.url = base_url.rstrip("/") + "/v1/messages"
        self.max_tokens = max_tokens

    def complete(self, model, system, messages):
        body = {"model": model, "max_tokens": self.max_tokens, "system": system, "messages": messages}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"}
        data = self._post(self.url, headers, body)
        usage = data.get("usage", {})
        self.last_tokens = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        return "".join(block.get("text", "") for block in data["content"] if block["type"] == "text")


class OpenAICompatibleProvider(Provider):
    """Works with any server that implements POST {base_url}/chat/completions."""

    def __init__(self, name, api_key, base_url, **kwargs):
        super().__init__(name, api_key, **kwargs)
        self.url = base_url.rstrip("/") + "/chat/completions"

    def complete(self, model, system, messages):
        body = {"model": model, "messages": [{"role": "system", "content": system}, *messages]}
        data = self._post(self.url, {"authorization": f"Bearer {self.api_key}"}, body)
        self.last_tokens = data.get("usage", {}).get("total_tokens", 0)
        return data["choices"][0]["message"]["content"] or ""


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
