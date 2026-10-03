"""Provider-agnostic LLM wrapper with structured (Pydantic-validated) outputs.

Providers are plain httpx clients, so all three share one timeout/retry policy
and report token usage the same way:

- ``gemini``: Google Generative Language REST API, JSON response mode
- ``openai`` and ``groq``: OpenAI-compatible chat completions, JSON mode

``StructuredLLM.generate`` renders a Markdown prompt from ``app/prompts/``,
appends the output JSON schema, validates the reply, retries once with the
validation errors, and raises ``LLMOutputError`` if the retry also fails.
"""

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ValidationError

PROMPTS_DIR = Path(__file__).parent / "prompts"
LLM_TIMEOUT_S = 60.0
LLM_MAX_RETRIES = 2
LLM_BACKOFF_S = 1.0
MAX_RATE_LIMIT_WAIT_S = 60.0  # longer waits (e.g. a daily quota) fail fast instead

DEFAULT_MODELS = {
    "gemini": "gemini-2.5-flash",
    "openai": "gpt-4o-mini",
    "groq": "llama-3.3-70b-versatile",
}
API_KEY_ENV = {"gemini": "GEMINI_API_KEY", "openai": "OPENAI_API_KEY", "groq": "GROQ_API_KEY"}
OPENAI_COMPATIBLE_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
}
GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"


class LLMError(Exception):
    """The provider could not be reached or returned an error."""


class LLMConfigError(LLMError):
    """Provider, model or API key is missing or invalid."""


class LLMOutputError(LLMError):
    """The model's reply failed schema validation twice."""


class LLMUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass(frozen=True)
class LLMRequest:
    prompt_name: str
    system: str
    user: str


@dataclass(frozen=True)
class LLMResponse:
    text: str
    usage: LLMUsage


@dataclass(frozen=True)
class LLMCallRecord:
    """One provider call, for step logging."""

    prompt_name: str
    attempt: int
    request: LLMRequest
    response_text: str | None
    usage: LLMUsage
    latency_ms: int
    error: str | None


class LLMProvider(Protocol):
    name: str
    model: str

    def complete(self, request: LLMRequest) -> LLMResponse: ...


# --- HTTP providers --------------------------------------------------------------


class _HTTPProvider:
    name = "base"

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str,
        *,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise LLMConfigError(f"{API_KEY_ENV.get(self.name, 'API key')} is not set")
        self.model = model
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url, timeout=LLM_TIMEOUT_S, headers=self._auth_headers(api_key), transport=transport
        )

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        raise NotImplementedError

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """POST with retries on timeouts, 429 and 5xx. LLM calls have no side effects."""
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                response = self._http.post(path, json=body)
            except httpx.TransportError as exc:
                problem = f"{exc.__class__.__name__}: {exc}"
            else:
                if response.is_success:
                    return response.json()
                if response.status_code != 429 and response.status_code < 500:
                    raise LLMError(f"{self.name} returned HTTP {response.status_code}: {response.text[:300]}")
                problem = f"HTTP {response.status_code}"
                if response.status_code == 429:
                    wait = _server_retry_delay(response)
                    if wait is not None and wait > MAX_RATE_LIMIT_WAIT_S:
                        raise LLMError(f"{self.name} rate limit: provider asks to retry in {wait:.0f}s")
                    if attempt < LLM_MAX_RETRIES:
                        self._sleep(wait if wait is not None else LLM_BACKOFF_S * 2**attempt)
                        continue
            if attempt < LLM_MAX_RETRIES:
                self._sleep(LLM_BACKOFF_S * 2**attempt)
        raise LLMError(f"{self.name} failed after {LLM_MAX_RETRIES + 1} attempts: {problem}")


def _server_retry_delay(response: httpx.Response) -> float | None:
    """Seconds the provider asks us to wait: Retry-After, or Gemini's RetryInfo.retryDelay ("17s")."""
    header = response.headers.get("retry-after")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    try:
        details = response.json()["error"]["details"]
    except (ValueError, KeyError, TypeError):
        return None
    for detail in details if isinstance(details, list) else []:
        delay = detail.get("retryDelay") if isinstance(detail, dict) else None
        if isinstance(delay, str) and delay.endswith("s"):
            try:
                return float(delay[:-1])
            except ValueError:
                return None
    return None


class GeminiProvider(_HTTPProvider):
    name = "gemini"

    def __init__(self, model: str, api_key: str, base_url: str = GEMINI_BASE_URL, **kwargs: Any) -> None:
        super().__init__(model, api_key, base_url, **kwargs)

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"x-goog-api-key": api_key}  # header, never the URL, so keys stay out of logs

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "systemInstruction": {"parts": [{"text": request.system}]},
            "contents": [{"role": "user", "parts": [{"text": request.user}]}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }
        data = self._post(f"/models/{self.model}:generateContent", body)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            text = "".join(part.get("text", "") for part in parts)
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"gemini returned no text: {json.dumps(data)[:300]}") from exc
        usage = data.get("usageMetadata", {})
        return LLMResponse(
            text=text,
            usage=LLMUsage(
                input_tokens=usage.get("promptTokenCount", 0),
                output_tokens=usage.get("candidatesTokenCount", 0),
            ),
        )


class OpenAICompatibleProvider(_HTTPProvider):
    """OpenAI chat completions; Groq serves the same API at a different base URL."""

    def __init__(
        self, name: str, model: str, api_key: str, base_url: str | None = None, **kwargs: Any
    ) -> None:
        self.name = name
        super().__init__(model, api_key, base_url or OPENAI_COMPATIBLE_BASE_URLS[name], **kwargs)

    def _auth_headers(self, api_key: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {api_key}"}

    def complete(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self.model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
        }
        data = self._post("/chat/completions", body)
        try:
            text = data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"{self.name} returned no message: {json.dumps(data)[:300]}") from exc
        usage = data.get("usage", {})
        return LLMResponse(
            text=text,
            usage=LLMUsage(
                input_tokens=usage.get("prompt_tokens", 0), output_tokens=usage.get("completion_tokens", 0)
            ),
        )


def provider_from_env(env: dict[str, str] | None = None) -> LLMProvider:
    """Build the provider named by LLM_PROVIDER (default gemini) and LLM_MODEL."""
    env = dict(os.environ if env is None else env)
    name = (env.get("LLM_PROVIDER") or "gemini").strip().lower()
    if name not in DEFAULT_MODELS:
        raise LLMConfigError(f"LLM_PROVIDER must be one of {sorted(DEFAULT_MODELS)}, got {name!r}")
    model = env.get("LLM_MODEL") or DEFAULT_MODELS[name]
    api_key = env.get(API_KEY_ENV[name], "")
    if name == "gemini":
        return GeminiProvider(model, api_key)
    return OpenAICompatibleProvider(name, model, api_key)


# --- prompts and structured output -----------------------------------------------


def load_prompt(name: str) -> tuple[str, str]:
    """Split ``prompts/<name>.md`` into its ``# System`` and ``# User`` sections."""
    text = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
    match = re.search(r"^# System\s*\n(.*?)^# User\s*\n(.*)\Z", text, flags=re.S | re.M)
    if not match:
        raise LLMConfigError(f"prompt {name}.md needs '# System' and '# User' sections")
    return match.group(1).strip(), match.group(2).strip()


def render(template: str, variables: dict[str, Any]) -> str:
    def value(match: re.Match[str]) -> str:
        key = match.group(1)
        if key not in variables:
            raise KeyError(f"prompt variable {key!r} not provided")
        item = variables[key]
        return item if isinstance(item, str) else json.dumps(item, ensure_ascii=False, default=str)

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", value, template)


def _extract_json(text: str) -> str:
    """Tolerate ```json fences around the object."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    return (fenced.group(1) if fenced else text).strip()


def _schema_instructions(schema: type[BaseModel]) -> str:
    return "Reply with a single JSON object, and nothing else, that matches this JSON schema:\n" + json.dumps(
        schema.model_json_schema(), ensure_ascii=False
    )


def _validation_summary(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc'])) or 'object'}: {e['msg']}" for e in exc.errors())


class StructuredLLM:
    def __init__(self, provider: LLMProvider, on_call: Callable[[LLMCallRecord], None] | None = None) -> None:
        self.provider = provider
        self.on_call = on_call

    def generate[M: BaseModel](
        self,
        prompt_name: str,
        variables: dict[str, Any],
        schema: type[M],
        *,
        context: dict[str, Any] | None = None,
    ) -> M:
        system_template, user_template = load_prompt(prompt_name)
        system = render(system_template, variables) + "\n\n" + _schema_instructions(schema)
        user = render(user_template, variables)
        problem = ""
        for attempt in (1, 2):
            request = LLMRequest(prompt_name=prompt_name, system=system, user=user)
            started = time.perf_counter()
            response: LLMResponse | None = None
            try:
                response = self.provider.complete(request)
                parsed = schema.model_validate_json(_extract_json(response.text), context=context)
            except ValidationError as exc:
                problem = _validation_summary(exc)
                self._record(request, attempt, started, response, f"invalid output: {problem}")
                user = (
                    f"{render(user_template, variables)}\n\n"
                    f"Your previous reply was rejected: {problem}\n"
                    "Reply again with only a JSON object that fixes these problems."
                )
                continue
            except LLMError as exc:
                self._record(request, attempt, started, response, str(exc))
                raise
            self._record(request, attempt, started, response, None)
            return parsed
        raise LLMOutputError(f"{prompt_name}: model output failed validation twice ({problem})")

    def _record(
        self,
        request: LLMRequest,
        attempt: int,
        started: float,
        response: LLMResponse | None,
        error: str | None,
    ) -> None:
        if self.on_call is None:
            return
        self.on_call(
            LLMCallRecord(
                prompt_name=request.prompt_name,
                attempt=attempt,
                request=request,
                response_text=response.text if response else None,
                usage=response.usage if response else LLMUsage(),
                latency_ms=round((time.perf_counter() - started) * 1000),
                error=error,
            )
        )
