import pytest

from auc_stt_client import AucSTTClient, AucSTTError, AucSTTResult, AucSTTTimeoutError


def _make_client():
    return AucSTTClient(
        app_id="app-id",
        access_token="access-token",
        resource_id="volc.seedasr.auc",
        task_timeout_ms=2000,
        poll_interval_ms=200,
    )


def test_transcribe_audio_url_success_with_utterances(monkeypatch):
    client = _make_client()
    submitted = {"called": False}
    queries = {"count": 0}

    def _fake_submit(*, task_id, payload):
        submitted["called"] = True
        assert task_id
        assert payload["audio"]["format"] == "wav"

    def _fake_query(*, task_id):
        assert task_id
        queries["count"] += 1
        if queries["count"] == 1:
            return ({}, "20000000", "OK")
        return (
            {
                "result": [
                    {
                        "text": "",
                        "utterances": [
                            {"text": "第一句"},
                            {"text": "第二句"},
                        ],
                    }
                ]
            },
            "20000000",
            "OK",
        )

    monkeypatch.setattr(client, "_submit", _fake_submit)
    monkeypatch.setattr(client, "_query", _fake_query)
    monkeypatch.setattr("auc_stt_client.time.sleep", lambda _x: None)

    result = client.transcribe_audio_url(
        audio_url="https://example.com/a.wav",
        audio_format="wav",
    )
    assert submitted["called"] is True
    assert isinstance(result, AucSTTResult)
    assert result.text == "第一句第二句"
    assert result.utterances == ["第一句", "第二句"]


def test_transcribe_audio_url_raises_on_query_error_code(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_submit", lambda **_kwargs: None)
    monkeypatch.setattr(
        client,
        "_query",
        lambda **_kwargs: ({}, "50001234", "bad request"),
    )

    with pytest.raises(AucSTTError):
        client.transcribe_audio_url(
            audio_url="https://example.com/a.wav",
            audio_format="wav",
        )


def test_transcribe_audio_url_timeout(monkeypatch):
    client = _make_client()
    monkeypatch.setattr(client, "_submit", lambda **_kwargs: None)
    monkeypatch.setattr(client, "_query", lambda **_kwargs: ({}, "20000000", "OK"))

    timeline = {"now": 100.0}

    def _fake_monotonic():
        timeline["now"] += 1.2
        return timeline["now"]

    monkeypatch.setattr("auc_stt_client.time.monotonic", _fake_monotonic)
    monkeypatch.setattr("auc_stt_client.time.sleep", lambda _x: None)

    with pytest.raises(AucSTTTimeoutError):
        client.transcribe_audio_url(
            audio_url="https://example.com/a.wav",
            audio_format="wav",
        )


def test_extract_result_handles_empty_payload():
    result = AucSTTClient._extract_result(task_id="task-1", payload={})
    assert result is None
