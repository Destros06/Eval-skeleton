"""Model backends. Anything with .complete(system, prompt) -> str | Completion works.

Specs used on the command line:
    openrouter:<model-id>     e.g. openrouter:meta/some-model:free
    openrouter:<id>#high      same model, asking the provider for high reasoning effort
                              (#low | #medium | #high). The suffix is part of the model's name in
                              results, so the same model can be compared with and without reasoning.
    mock:naive                offline stand-in, for testing the pipeline
"""
from __future__ import annotations

import json
import os
import random
import time
import urllib.error
import urllib.request
from typing import Protocol

from .schema import Completion

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
RETRYABLE_HTTP = {408, 409, 429, 500, 502, 503, 504}


class ModelError(RuntimeError):
    """The model call failed (after retries). Recorded separately from a wrong answer."""


class Model(Protocol):
    name: str

    def complete(self, system: str | None, prompt: str) -> "str | Completion": ...


def as_completion(x) -> Completion:
    return x if isinstance(x, Completion) else Completion(text=str(x))


class OpenRouterModel:
    def __init__(self, model_id: str, temperature: float = 0.0, max_tokens: int = 2048,
                 timeout: float = 180.0, retries: int = 4, empty_retries: int = 1,
                 api_key: str | None = None, reasoning_effort: str | None = None):
        self.name = f"openrouter:{model_id}" + (f"#{reasoning_effort}" if reasoning_effort else "")
        self.reasoning_effort = reasoning_effort
        self.model_id = model_id
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.retries = retries
        self.empty_retries = empty_retries  # empty answers are retried only this many times
        self.api_key = api_key or os.environ.get("OPENROUTER_API_KEY", "")
        if not self.api_key:
            raise ModelError("OPENROUTER_API_KEY is not set or empty")

    def complete(self, system: str | None, prompt: str) -> Completion:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload = {
            "model": self.model_id,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }
        if self.reasoning_effort:  # only sent when explicitly requested (spec suffix #low/#medium/#high)
            payload["reasoning"] = {"effort": self.reasoning_effort}
        body = json.dumps(payload).encode()
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

        last: Exception | None = None
        empties = 0
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(OPENROUTER_URL, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read())
                choice = data["choices"][0]
                msg = choice.get("message") or {}
                text = msg.get("content") or ""
                if isinstance(text, list):  # content delivered as parts
                    text = "".join(p.get("text", "") for p in text if isinstance(p, dict))
                comp = Completion(
                    text=text,
                    finish_reason=choice.get("finish_reason"),
                    usage=data.get("usage"),
                    reasoning_chars=len(msg.get("reasoning") or ""),
                )
                if text.strip():
                    return comp
                if comp.finish_reason == "content_filter":
                    # The provider's safety filter stopped the answer. Retrying the same prompt
                    # would be blocked (and billed) again: report it as its own outcome.
                    return comp
                if comp.finish_reason == "length":
                    # The token budget was used up (typically by hidden reasoning) before any
                    # visible answer. Retrying would just burn quota: report it as truncated.
                    return comp
                empties += 1
                if empties > self.empty_retries:
                    raise ModelError(
                        f"empty completion (finish_reason={comp.finish_reason!r}, "
                        f"reasoning_chars={comp.reasoning_chars}) after {empties} attempts")
                last = ValueError("empty completion")
            except urllib.error.HTTPError as e:
                if e.code not in RETRYABLE_HTTP:
                    raise ModelError(f"HTTP {e.code}: {e.read()[:300]!r}") from e
                last = e
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, KeyError,
                    IndexError, ValueError, AttributeError, TypeError) as e:
                last = e  # includes 200-OK responses that carry an {"error": ...} payload
            if attempt < self.retries:
                time.sleep(min(60.0, 2 ** attempt + random.random()))
        raise ModelError(f"gave up after {self.retries + 1} attempts: {last!r}")


class MockNaiveModel:
    """Offline stand-in: always claims 'SQL injection on line 2'."""

    name = "mock:naive"

    def complete(self, system: str | None, prompt: str) -> str:
        return '{"vulnerable": true, "vuln_type": "sql_injection", "line": 2}'


class MockRefuseModel:
    """Offline stand-in that always declines in plain language."""

    name = "mock:refuse"

    def complete(self, system: str | None, prompt: str) -> Completion:
        return Completion(text="I'm sorry, but I can't help with that request.", finish_reason="stop")


class MockBlockedModel:
    """Offline stand-in that behaves like a provider content filter."""

    name = "mock:blocked"

    def complete(self, system: str | None, prompt: str) -> Completion:
        return Completion(text="", finish_reason="content_filter")


def get_model(spec: str, temperature: float = 0.0, max_tokens: int = 2048) -> Model:
    kind, _, rest = spec.partition(":")
    if kind == "openrouter" and rest:
        model_id, _, effort = rest.partition("#")
        if not model_id:
            raise ValueError(f"bad model spec {spec!r}: missing model id")
        if effort and effort not in ("low", "medium", "high"):
            raise ValueError(f"bad reasoning effort {effort!r} in {spec!r}; use #low, #medium or #high")
        return OpenRouterModel(model_id, temperature=temperature, max_tokens=max_tokens,
                               reasoning_effort=effort or None)
    if kind == "mock" and rest == "naive":
        return MockNaiveModel()
    if kind == "mock" and rest == "refuse":
        return MockRefuseModel()
    if kind == "mock" and rest == "blocked":
        return MockBlockedModel()
    raise ValueError(f"bad model spec {spec!r}; use openrouter:<id> or mock:naive|refuse|blocked")
