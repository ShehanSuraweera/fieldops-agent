"""LLM wrapper: provider wire formats, retries, config and structured-output validation."""

import json

import httpx
import pytest
from pydantic import BaseModel

from app.llm import (
    GeminiProvider,
    LLMConfigError,
    LLMError,
    LLMOutputError,
    LLMRequest,
    OpenAICompatibleProvider,
    StructuredLLM,
    load_prompt,
    provider_from_env,
    render,
)
from app.state import Diagnosis
from tests.fakes import FakeLLM

REQUEST = LLMRequest(prompt_name="intake", system="be precise", user="hello")


def _transport(responses: list[httpx.Response], seen: list[httpx.Request]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return responses.pop(0) if len(responses) > 1 else responses[0]

    return httpx.MockTransport(handler)


def test_gemini_wire_format_and_usage() -> None:
    seen: list[httpx.Request] = []
    reply = {
        "candidates": [{"content": {"parts": [{"text": '{"a": 1}'}]}}],
        "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 7},
    }
    provider = GeminiProvider(
        "gemini-2.5-flash", "secret", transport=_transport([httpx.Response(200, json=reply)], seen)
    )
    response = provider.complete(REQUEST)

    assert response.text == '{"a": 1}'
    assert (response.usage.input_tokens, response.usage.output_tokens) == (42, 7)
    request = seen[0]
    assert request.url.path.endswith("/models/gemini-2.5-flash:generateContent")
    assert request.headers["x-goog-api-key"] == "secret"
    assert "secret" not in str(request.url)  # key never in the URL
    body = json.loads(request.content)
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["systemInstruction"]["parts"][0]["text"] == "be precise"


@pytest.mark.parametrize(("name", "host"), [("openai", "api.openai.com"), ("groq", "api.groq.com")])
def test_openai_compatible_wire_format(name: str, host: str) -> None:
    seen: list[httpx.Request] = []
    reply = {
        "choices": [{"message": {"content": '{"a": 1}'}}],
        "usage": {"prompt_tokens": 30, "completion_tokens": 5},
    }
    provider = OpenAICompatibleProvider(
        name, "m-1", "sk-test", transport=_transport([httpx.Response(200, json=reply)], seen)
    )
    response = provider.complete(REQUEST)

    assert response.usage.input_tokens == 30
    assert seen[0].url.host == host
    assert seen[0].url.path.endswith("/chat/completions")
    assert seen[0].headers["Authorization"] == "Bearer sk-test"
    body = json.loads(seen[0].content)
    assert body["response_format"] == {"type": "json_object"}
    assert [m["role"] for m in body["messages"]] == ["system", "user"]


def test_provider_retries_rate_limits_then_succeeds() -> None:
    seen: list[httpx.Request] = []
    ok = httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})
    sleeps: list[float] = []
    provider = OpenAICompatibleProvider(
        "openai",
        "m",
        "k",
        transport=_transport([httpx.Response(429), httpx.Response(503), ok], seen),
        sleep=sleeps.append,
    )
    provider.complete(REQUEST)
    assert len(seen) == 3
    assert sleeps == [1.0, 2.0]


def test_rate_limit_waits_as_long_as_the_provider_asks() -> None:
    seen: list[httpx.Request] = []
    gemini_429 = httpx.Response(
        429,
        json={
            "error": {
                "code": 429,
                "details": [{"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "17s"}],
            }
        },
    )
    header_429 = httpx.Response(429, headers={"Retry-After": "5"})
    ok = httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": "{}"}]}}]})
    sleeps: list[float] = []
    provider = GeminiProvider(
        "m", "k", transport=_transport([gemini_429, header_429, ok], seen), sleep=sleeps.append
    )
    provider.complete(REQUEST)
    assert sleeps == [17.0, 5.0]


def test_rate_limit_with_a_long_wait_fails_fast() -> None:
    seen: list[httpx.Request] = []
    sleeps: list[float] = []
    daily = httpx.Response(429, headers={"Retry-After": "3600"})
    provider = GeminiProvider("m", "k", transport=_transport([daily], seen), sleep=sleeps.append)
    with pytest.raises(LLMError, match="retry in 3600s"):
        provider.complete(REQUEST)
    assert sleeps == [] and len(seen) == 1


def test_provider_client_errors_are_not_retried() -> None:
    seen: list[httpx.Request] = []
    provider = OpenAICompatibleProvider(
        "openai", "m", "k", transport=_transport([httpx.Response(400, text="bad")], seen)
    )
    with pytest.raises(LLMError, match="HTTP 400"):
        provider.complete(REQUEST)
    assert len(seen) == 1


def test_provider_from_env() -> None:
    gemini = provider_from_env({"GEMINI_API_KEY": "g"})
    assert (gemini.name, gemini.model) == ("gemini", "gemini-2.5-flash")
    groq = provider_from_env({"LLM_PROVIDER": "groq", "LLM_MODEL": "llama-x", "GROQ_API_KEY": "q"})
    assert (groq.name, groq.model) == ("groq", "llama-x")
    with pytest.raises(LLMConfigError, match="GEMINI_API_KEY"):
        provider_from_env({})
    with pytest.raises(LLMConfigError, match="LLM_PROVIDER"):
        provider_from_env({"LLM_PROVIDER": "nope"})


def test_every_prompt_has_system_and_user_sections() -> None:
    for name in ("intake", "clarify", "diagnose", "customer_message"):
        system, user = load_prompt(name)
        assert system and user


def test_render_requires_every_variable() -> None:
    assert render("Hi {{name}}: {{items}}", {"name": "Ann", "items": ["a"]}) == 'Hi Ann: ["a"]'
    with pytest.raises(KeyError):
        render("{{missing}}", {})


class Echo(BaseModel):
    value: int


def test_structured_output_accepts_fenced_json() -> None:
    llm = StructuredLLM(FakeLLM({"intake": '```json\n{"value": 3}\n```'}))
    assert llm.generate("intake", {"complaint": "x"}, Echo).value == 3


def test_structured_output_retries_once_then_raises() -> None:
    calls = []
    fake = FakeLLM({"intake": ['{"value": "nope"}', '{"value": "still nope"}']})
    llm = StructuredLLM(fake, on_call=calls.append)
    with pytest.raises(LLMOutputError, match="failed validation twice"):
        llm.generate("intake", {"complaint": "x"}, Echo)
    assert [c.attempt for c in calls] == [1, 2]
    assert all(c.error and c.error.startswith("invalid output") for c in calls)
    assert "value" in fake.requests[1].user  # the retry explains what was wrong


def test_schema_is_appended_to_system_prompt() -> None:
    fake = FakeLLM({"intake": '{"value": 1}'})
    StructuredLLM(fake).generate("intake", {"complaint": "x"}, Echo)
    assert '"value"' in fake.requests[0].system


def test_diagnosis_rejects_codes_outside_the_catalog() -> None:
    fake = FakeLLM({"diagnose": ['{"fault_code": "X", "confidence": 0.9, "reasoning": "because"}']})
    variables = {
        "asset_name": "a",
        "model": "m",
        "complaint": "c",
        "symptoms": [],
        "history": "",
        "catalog": [],
    }
    with pytest.raises(LLMOutputError, match="fault_code must be one of"):
        StructuredLLM(fake).generate(
            "diagnose", variables, Diagnosis, context={"allowed_codes": {"COMP_FAIL"}}
        )
