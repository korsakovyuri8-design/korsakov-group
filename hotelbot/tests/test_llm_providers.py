import json

import httpx
import pytest

from app.llm.anthropic_provider import AnthropicProvider
from app.llm.base import LLMError, extract_json
from app.llm.factory import build_llm
from app.llm.openai_provider import OpenAICompatibleProvider
from tests.conftest import make_settings


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_openai_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "hi there"}}]})

    p = OpenAICompatibleProvider("sk-test", "gpt-test", "http://llm.local/v1/", 5, 100, client=_client(handler))
    out = p.complete("SYS", [{"role": "user", "content": "hello"}])
    assert out == "hi there"
    assert seen["url"] == "http://llm.local/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    assert seen["body"]["messages"][0] == {"role": "system", "content": "SYS"}
    assert seen["body"]["model"] == "gpt-test"


def test_anthropic_request_shape_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "zdravo"}]})

    p = AnthropicProvider("ak-test", "claude-test", "http://anthropic.local", 5, 100, client=_client(handler))
    out = p.complete("SYS", [{"role": "assistant", "content": "a"}, {"role": "user", "content": "b"},
                             {"role": "user", "content": "c"}])
    assert out == "zdravo"
    assert seen["url"] == "http://anthropic.local/v1/messages"
    assert seen["headers"]["x-api-key"] == "ak-test"
    assert seen["headers"]["anthropic-version"]
    assert seen["body"]["system"] == "SYS"
    roles = [m["role"] for m in seen["body"]["messages"]]
    assert roles[0] == "user" and all(a != b for a, b in zip(roles, roles[1:]))


@pytest.mark.parametrize("provider_cls", [OpenAICompatibleProvider, AnthropicProvider])
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, json={"error": "boom"}),
        httpx.Response(429, json={}),
        httpx.Response(200, content=b"<html>not json</html>"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json={"choices": [], "content": []}),
    ],
)
def test_providers_raise_llm_error_on_bad_responses(provider_cls, response):
    p = provider_cls("k", "m", "http://x", 5, 100, client=_client(lambda r: response))
    with pytest.raises(LLMError):
        p.complete("s", [{"role": "user", "content": "q"}])


@pytest.mark.parametrize("provider_cls", [OpenAICompatibleProvider, AnthropicProvider])
def test_providers_wrap_transport_errors(provider_cls):
    def handler(request):
        raise httpx.ConnectTimeout("slow")

    p = provider_cls("k", "m", "http://x", 5, 100, client=_client(handler))
    with pytest.raises(LLMError) as exc:
        p.complete("s", [{"role": "user", "content": "q"}])
    assert "k" not in str(exc.value).split()  # no secrets in error messages


def test_factory_selects_provider_from_settings():
    assert build_llm(make_settings(llm_provider="none")) is None
    assert isinstance(build_llm(make_settings(llm_provider="openai", llm_model="m", openai_api_key="k")),
                      OpenAICompatibleProvider)
    assert isinstance(build_llm(make_settings(llm_provider="anthropic", llm_model="m", anthropic_api_key="k")),
                      AnthropicProvider)
    with pytest.raises(ValueError):
        build_llm(make_settings(llm_provider="anthropic", llm_model="m"))  # missing key


def test_settings_repr_hides_secrets():
    s = make_settings(anthropic_api_key="super-secret-value")
    assert "super-secret-value" not in repr(s)


@pytest.mark.parametrize(
    "text",
    ['{"a": 1}', 'Sure! ```json\n{"a": 1}\n```', 'Here you go: {"a": 1} hope that helps'],
)
def test_extract_json_tolerates_wrapping(text):
    assert extract_json(text) == {"a": 1}


def test_extract_json_raises_without_object():
    with pytest.raises(ValueError):
        extract_json("no json here")
