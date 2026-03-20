import asyncio
from types import SimpleNamespace

from ark_responses_adapter import (
    ArkResponsesAdapter,
    extract_text_from_response,
    normalize_stage_config,
)


class _FakeResponsesAPI:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


class _FakeClient:
    def __init__(self, result):
        self.responses = _FakeResponsesAPI(result)


class _AsyncStream:
    def __init__(self, events):
        self._events = iter(events)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._events)
        except StopIteration:
            raise StopAsyncIteration


def test_normalize_stage_config_defaults_and_validation():
    cfg, err = normalize_stage_config("LLM1", "ep-abc", None, None)
    assert err is None
    assert cfg is not None
    assert cfg.thinking_type == "disabled"
    assert cfg.reasoning_effort is None

    cfg2, err2 = normalize_stage_config("LLM2", "ep-m-abc", "enabled", None)
    assert err2 is None
    assert cfg2 is not None
    assert cfg2.reasoning_effort == "minimal"

    cfg3, err3 = normalize_stage_config("LLM2", "ep-m-abc", "disabled", "xxx")
    assert err3 is None
    assert cfg3 is not None
    assert cfg3.reasoning_effort is None

    _, err4 = normalize_stage_config("LLM1", "model-name", "disabled", None)
    assert "invalid LLM1_ENDPOINT_ID" in (err4 or "")

    _, err5 = normalize_stage_config("LLM1", "ep-abc", "bad", None)
    assert "invalid LLM1_THINKING_TYPE" in (err5 or "")

    _, err6 = normalize_stage_config("LLM1", "ep-abc", "enabled", "bad")
    assert "invalid LLM1_REASONING_EFFORT" in (err6 or "")


def test_extract_text_from_response_collects_output_text_only():
    response = SimpleNamespace(
        output=[
            SimpleNamespace(
                type="message",
                content=[
                    SimpleNamespace(type="output_text", text="hello "),
                    SimpleNamespace(type="output_text", text="world"),
                ],
            ),
            SimpleNamespace(type="function_call", content=[]),
        ]
    )
    assert extract_text_from_response(response) == "hello world"


def test_complete_text_calls_responses_create_with_expected_params():
    async def _run():
        response = SimpleNamespace(
            output=[
                SimpleNamespace(
                    type="message",
                    content=[SimpleNamespace(type="output_text", text="ok")],
                )
            ]
        )
        fake_client = _FakeClient(response)
        adapter = ArkResponsesAdapter(api_key="k", client=fake_client)
        text = await adapter.complete_text(
            model="ep-abc",
            instructions="ins",
            messages=[{"role": "user", "content": "hi"}],
            thinking_type="enabled",
            reasoning_effort="low",
        )
        assert text == "ok"
        kwargs = fake_client.responses.calls[0]
        assert kwargs["model"] == "ep-abc"
        assert kwargs["stream"] is False
        assert kwargs["thinking"] == {"type": "enabled"}
        assert kwargs["reasoning"] == {"effort": "low"}

    asyncio.run(_run())


def test_stream_text_uses_delta_and_done_fallback():
    async def _run():
        events = _AsyncStream(
            [
                SimpleNamespace(type="response.output_text.delta", delta="a"),
                SimpleNamespace(type="response.output_text.delta", delta="b"),
                SimpleNamespace(type="response.output_text.done", text="ab"),
                SimpleNamespace(type="response.output_text.done", text="fallback"),
            ]
        )
        fake_client = _FakeClient(events)
        adapter = ArkResponsesAdapter(api_key="k", client=fake_client)
        chunks = []
        async for chunk in adapter.stream_text(
            model="ep-abc",
            instructions="ins",
            messages=[{"role": "user", "content": "hi"}],
            thinking_type="disabled",
            reasoning_effort=None,
        ):
            chunks.append(chunk)
        assert chunks == ["a", "b", "fallback"]
        kwargs = fake_client.responses.calls[0]
        assert kwargs["stream"] is True
        assert kwargs["reasoning"] is None

    asyncio.run(_run())
