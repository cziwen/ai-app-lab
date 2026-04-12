import asyncio
import json
import logging
from pathlib import Path
import re
from types import SimpleNamespace

import admin_store
import handler
import service


class _FakeExpiryIndex:
    def ensure_ready(self):
        return True

    def schedule(self, token: str, expires_at: str):
        return True

    def remove(self, token: str):
        return True

    def due_tokens(self, now_ms=None, *, limit=200):
        return []


def _setup_tmp_store(monkeypatch, tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    storage_dir = data_dir / "storage"
    audio_dir = storage_dir / "audio"
    interview_log_dir = storage_dir / "interview_logs"
    db_path = data_dir / "app.db"
    monkeypatch.setattr(admin_store, "DATA_DIR", data_dir)
    monkeypatch.setattr(admin_store, "STORAGE_DIR", storage_dir)
    monkeypatch.setattr(admin_store, "AUDIO_DIR", audio_dir)
    monkeypatch.setattr(admin_store, "INTERVIEW_LOG_DIR", interview_log_dir)
    monkeypatch.setattr(admin_store, "DB_PATH", db_path)
    monkeypatch.setattr(admin_store, "INTERVIEW_EXPIRY_INDEX", _FakeExpiryIndex())


def _reset_handler_loggers(monkeypatch, tmp_path: Path) -> None:
    interview_log_dir = tmp_path / "data" / "storage" / "interview_logs"
    monkeypatch.setattr(handler, "INTERVIEW_LOG_DIR", interview_log_dir)
    for logger in handler._INTERVIEW_LOGGER_CACHE.values():
        for logger_handler in logger.handlers:
            logger_handler.close()
        logger.handlers.clear()
    handler._INTERVIEW_LOGGER_CACHE.clear()
    handler._INTERVIEW_LOGGER_LAST_USED.clear()


def _create_interview_fixture() -> str:
    job = admin_store.create_job(
        name="后端工程师",
        duties="负责服务端开发",
        requirements="熟悉 Python",
        notes=None,
        csv_filename="questions.csv",
        questions=[("介绍一个项目", "背景 职责 结果")],
    )
    detail = admin_store.get_job_detail(job["job_uid"])
    assert detail is not None and detail["questions"]
    first_question_id = int(detail["questions"][0]["id"])
    interview = admin_store.create_interview(
        candidate_name="测试候选人",
        job_uid=job["job_uid"],
        notes=None,
        question_followups=[
            {
                "question_id": first_question_id,
                "max_followups": 0,
                "max_clarifies": 0,
            }
        ],
    )
    return interview["token"]


async def _send_frontend_logs_request(port: int, path: str, payload) -> str:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    body = json.dumps(payload).encode("utf-8")
    request = (
        f"POST {path} HTTP/1.1\r\n"
        "Host: 127.0.0.1\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "\r\n"
    ).encode("utf-8") + body
    writer.write(request)
    await writer.drain()
    response = await reader.read()
    writer.close()
    await writer.wait_closed()
    return response.decode("utf-8", errors="replace")


def test_frontend_log_endpoint_requires_valid_token(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            missing_token_response = await _send_frontend_logs_request(
                port, "/api/frontend-logs", ["entry"]
            )
            invalid_token_response = await _send_frontend_logs_request(
                port, "/api/frontend-logs?token=INT-NOT-FOUND", ["entry"]
            )
        finally:
            server.close()
            await server.wait_closed()
        return missing_token_response, invalid_token_response

    missing_token_response, invalid_token_response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in missing_token_response
    assert "HTTP/1.1 400 Bad Request" in invalid_token_response


def test_frontend_log_endpoint_writes_interview_frontend_log(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1", "line-2"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    log_file = tmp_path / "data" / "storage" / "interview_logs" / token / "frontend.log"
    assert "HTTP/1.1 200 OK" in response
    assert log_file.exists()
    content = log_file.read_text(encoding="utf-8")
    assert "line-1" in content
    assert "line-2" in content


def test_frontend_log_endpoint_rejects_oversized_body(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_BODY_BYTES", 20)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["0123456789", "9876543210"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_frontend_log_endpoint_rejects_too_many_entries(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_ENTRIES", 1)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1", "line-2"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_frontend_log_endpoint_rejects_too_long_entry(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "FRONTEND_LOG_MAX_ENTRY_CHARS", 4)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    async def _run():
        server = await asyncio.start_server(
            handler.handle_frontend_log_request, host="127.0.0.1", port=0
        )
        port = server.sockets[0].getsockname()[1]
        try:
            response = await _send_frontend_logs_request(
                port,
                f"/api/frontend-logs?token={token}",
                ["line-1"],
            )
        finally:
            server.close()
            await server.wait_closed()
        return response

    response = asyncio.run(_run())
    assert "HTTP/1.1 400 Bad Request" in response


def test_interview_logger_release_clears_cache(monkeypatch, tmp_path):
    _reset_handler_loggers(monkeypatch, tmp_path)
    token = "INT-TEST-RELEASE"
    cache_key = (token, "frontend")
    logger = handler._get_interview_logger(token, "frontend")
    assert cache_key in handler._INTERVIEW_LOGGER_CACHE
    assert cache_key in handler._INTERVIEW_LOGGER_LAST_USED

    released = handler._release_interview_loggers_for_token(token)

    assert released == 1
    assert cache_key not in handler._INTERVIEW_LOGGER_CACHE
    assert cache_key not in handler._INTERVIEW_LOGGER_LAST_USED
    assert logger.handlers == []


def test_interview_logger_cache_prunes_overflow(monkeypatch, tmp_path):
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setattr(handler, "INTERVIEW_LOGGER_CACHE_MAX", 1)
    monkeypatch.setattr(handler, "INTERVIEW_LOGGER_IDLE_SECONDS", 999999)

    handler._get_interview_logger("INT-OLD", "frontend")
    handler._get_interview_logger("INT-NEW", "frontend")

    assert len(handler._INTERVIEW_LOGGER_CACHE) == 1
    assert ("INT-NEW", "frontend") in handler._INTERVIEW_LOGGER_CACHE
    assert ("INT-OLD", "frontend") not in handler._INTERVIEW_LOGGER_CACHE


def test_persistence_process_logs_error_on_final_failure(monkeypatch):
    captured_records = []

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    capture_logger = logging.getLogger("test.persistence.logger")
    capture_logger.setLevel(logging.INFO)
    capture_logger.handlers.clear()
    capture_logger.addHandler(CaptureHandler())
    capture_logger.propagate = False

    def _raise_save(*args, **kwargs):
        raise RuntimeError("save failed")

    monkeypatch.setattr(handler, "save_interview_turns", _raise_save)

    queue = handler.PersistenceQueue(capture_logger)
    task = handler.PersistenceTask(
        token="INT-PERSIST-ERR",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        score_inputs=[],
        should_mark_completed=True,
        completed_reason="disconnect",
        expected_owner_id="OWNER-PERSIST-ERR",
        candidate_frame_prefixed_count=0,
        candidate_frame_raw_count=0,
        candidate_audio_dropped_frames=0,
        retries=handler.PERSISTENCE_MAX_RETRIES,
    )

    asyncio.run(queue._process(task))

    assert any(
        record.levelno == logging.ERROR
        and "event=interview_persist.failed" in record.getMessage()
        for record in captured_records
    )


def test_persistence_process_enqueues_scoring_after_completion(monkeypatch):
    captured = {
        "init_scorecard": [],
        "mark_completed": [],
        "scoring_tasks": [],
    }

    class _FakeScoringQueue:
        async def submit(self, task):
            captured["scoring_tasks"].append(task)

    monkeypatch.setattr(handler, "save_interview_turns", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler, "persist_interview_audio", lambda **kwargs: {})
    monkeypatch.setattr(
        handler,
        "mark_interview_completed",
        lambda token, reason: captured["mark_completed"].append((token, reason)),
    )
    monkeypatch.setattr(
        handler,
        "init_interview_scorecard",
        lambda token: captured["init_scorecard"].append(token),
    )
    monkeypatch.setattr(
        handler,
        "OCCUPANCY",
        SimpleNamespace(current_owner=lambda _token: "OWNER-PERSIST-SCORING"),
    )
    monkeypatch.setattr(handler, "SCORING", _FakeScoringQueue())

    queue = handler.PersistenceQueue(logging.getLogger("test.persistence.scoring"))
    task = handler.PersistenceTask(
        token="INT-PERSIST-SCORING",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        score_inputs=[{"question_id": "q1"}],
        should_mark_completed=True,
        completed_reason="normal_end",
        expected_owner_id="OWNER-PERSIST-SCORING",
        candidate_frame_prefixed_count=0,
        candidate_frame_raw_count=0,
        candidate_audio_dropped_frames=0,
    )

    asyncio.run(queue._process(task))

    assert captured["mark_completed"] == [("INT-PERSIST-SCORING", "normal_end")]
    assert captured["init_scorecard"] == ["INT-PERSIST-SCORING"]
    assert len(captured["scoring_tasks"]) == 1
    assert captured["scoring_tasks"][0].token == "INT-PERSIST-SCORING"


def test_persistence_process_skips_completion_when_owner_changed(monkeypatch):
    captured_records = []
    captured = {
        "mark_completed": [],
        "init_scorecard": [],
        "scoring_tasks": [],
    }

    class CaptureHandler(logging.Handler):
        def emit(self, record):
            captured_records.append(record)

    class _FakeScoringQueue:
        async def submit(self, task):
            captured["scoring_tasks"].append(task)

    capture_logger = logging.getLogger("test.persistence.owner_changed")
    capture_logger.setLevel(logging.INFO)
    capture_logger.handlers.clear()
    capture_logger.addHandler(CaptureHandler())
    capture_logger.propagate = False

    monkeypatch.setattr(handler, "save_interview_turns", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler, "persist_interview_audio", lambda **kwargs: {})
    monkeypatch.setattr(
        handler,
        "mark_interview_completed",
        lambda token, reason: captured["mark_completed"].append((token, reason)),
    )
    monkeypatch.setattr(
        handler,
        "init_interview_scorecard",
        lambda token: captured["init_scorecard"].append(token),
    )
    monkeypatch.setattr(
        handler,
        "OCCUPANCY",
        SimpleNamespace(current_owner=lambda _token: "OWNER-NEW"),
    )
    monkeypatch.setattr(handler, "SCORING", _FakeScoringQueue())

    queue = handler.PersistenceQueue(capture_logger)
    task = handler.PersistenceTask(
        token="INT-PERSIST-SKIP",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        score_inputs=[{"question_id": "q1"}],
        should_mark_completed=True,
        completed_reason="normal_end",
        expected_owner_id="OWNER-OLD",
        candidate_frame_prefixed_count=0,
        candidate_frame_raw_count=0,
        candidate_audio_dropped_frames=0,
    )

    asyncio.run(queue._process(task))

    assert captured["mark_completed"] == []
    assert captured["init_scorecard"] == []
    assert captured["scoring_tasks"] == []
    assert any(
        "event=interview_persist.complete_skipped reason=token_reacquired"
        in record.getMessage()
        for record in captured_records
    )


def test_persistence_process_completes_when_owner_is_missing_or_unchanged(monkeypatch):
    captured = {
        "mark_completed": [],
        "init_scorecard": [],
        "scoring_tasks": [],
    }

    class _FakeScoringQueue:
        async def submit(self, task):
            captured["scoring_tasks"].append(task)

    monkeypatch.setattr(handler, "save_interview_turns", lambda *args, **kwargs: None)
    monkeypatch.setattr(handler, "persist_interview_audio", lambda **kwargs: {})
    monkeypatch.setattr(
        handler,
        "mark_interview_completed",
        lambda token, reason: captured["mark_completed"].append((token, reason)),
    )
    monkeypatch.setattr(
        handler,
        "init_interview_scorecard",
        lambda token: captured["init_scorecard"].append(token),
    )
    monkeypatch.setattr(handler, "SCORING", _FakeScoringQueue())

    owner_map = {
        "INT-PERSIST-OWNER-NONE": None,
        "INT-PERSIST-OWNER-SAME": "OWNER-SAME",
    }
    monkeypatch.setattr(
        handler,
        "OCCUPANCY",
        SimpleNamespace(current_owner=lambda token: owner_map.get(token)),
    )

    queue = handler.PersistenceQueue(logging.getLogger("test.persistence.owner_allow"))
    task_none = handler.PersistenceTask(
        token="INT-PERSIST-OWNER-NONE",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        score_inputs=[{"question_id": "q1"}],
        should_mark_completed=True,
        completed_reason="normal_end",
        expected_owner_id="OWNER-NONE",
        candidate_frame_prefixed_count=0,
        candidate_frame_raw_count=0,
        candidate_audio_dropped_frames=0,
    )
    task_same = handler.PersistenceTask(
        token="INT-PERSIST-OWNER-SAME",
        turns=[],
        candidate_pcm_bytes=b"",
        interviewer_encoded_bytes=b"",
        score_inputs=[{"question_id": "q2"}],
        should_mark_completed=True,
        completed_reason="normal_end",
        expected_owner_id="OWNER-SAME",
        candidate_frame_prefixed_count=0,
        candidate_frame_raw_count=0,
        candidate_audio_dropped_frames=0,
    )

    asyncio.run(queue._process(task_none))
    asyncio.run(queue._process(task_same))

    assert captured["mark_completed"] == [
        ("INT-PERSIST-OWNER-NONE", "normal_end"),
        ("INT-PERSIST-OWNER-SAME", "normal_end"),
    ]
    assert captured["init_scorecard"] == [
        "INT-PERSIST-OWNER-NONE",
        "INT-PERSIST-OWNER-SAME",
    ]
    assert [task.token for task in captured["scoring_tasks"]] == [
        "INT-PERSIST-OWNER-NONE",
        "INT-PERSIST-OWNER-SAME",
    ]


def test_scoring_queue_process_marks_failed_when_scorer_errors(monkeypatch):
    captured = {"failed": []}
    captured_lines = []

    class _CaptureInterviewLogger:
        def info(self, msg, *args, **kwargs):
            text = msg % args if args else msg
            captured_lines.append(text)

    class _BrokenScorer:
        def __init__(self, **_kwargs):
            pass

        async def score_interview(self, _inputs):
            err = RuntimeError("mock score failure")
            setattr(
                err,
                "scoring_debug_meta",
                {
                    "stage": "parse",
                    "question_id": "q1",
                    "raw_output_preview": "invalid",
                },
            )
            raise err

    monkeypatch.setattr(handler, "InterviewScorer", _BrokenScorer)
    monkeypatch.setattr(handler, "SCORING_DEBUG_LOG_ENABLED", True)
    monkeypatch.setattr(handler, "_get_interview_logger", lambda *_args: _CaptureInterviewLogger())
    monkeypatch.setattr(
        handler,
        "save_interview_scorecard_failed",
        lambda token, error: captured["failed"].append((token, error)),
    )

    queue = handler.ScoringQueue(logging.getLogger("test.scoring.queue"))
    task = handler.ScoringTask(
        token="INT-SCORE-FAILED",
        score_inputs=[{"question_id": "q1", "aggregated_answer": "abc"}],
    )

    asyncio.run(queue._process(task))

    assert len(captured["failed"]) == 1
    assert captured["failed"][0][0] == "INT-SCORE-FAILED"
    assert any('"stage": "failed"' in line for line in captured_lines)
    assert any('"debug_meta": {' in line for line in captured_lines)


def test_scoring_queue_process_writes_interview_debug_logs_on_success(monkeypatch):
    captured = {"success": []}
    captured_lines = []

    class _CaptureInterviewLogger:
        def info(self, msg, *args, **kwargs):
            text = msg % args if args else msg
            captured_lines.append(text)

    class _FakeScorer:
        def __init__(self, **_kwargs):
            pass

        async def score_interview(self, _inputs):
            return SimpleNamespace(
                overall_score=None,
                total_score=4.2,
                total_max_score=5.0,
                question_scores=[
                    SimpleNamespace(
                        question_id="q1",
                        sort_order=1,
                        question="Q1",
                        ability_dimension="ownership",
                        score_format="0-5",
                        comment_requirement="一句话点评",
                        aggregated_answer="候选人回答",
                        max_score=5.0,
                        score_error="",
                        numeric_score=4.2,
                        comment="覆盖较完整",
                        debug_meta={
                            "elapsed_ms": 123,
                            "parse_fallback_used": True,
                            "numeric_score_was_clamped": False,
                            "raw_output_truncated": True,
                            "raw_output_preview": "raw-preview",
                        },
                    )
                ],
            )

    monkeypatch.setattr(handler, "InterviewScorer", _FakeScorer)
    monkeypatch.setattr(handler, "SCORING_DEBUG_LOG_ENABLED", True)
    monkeypatch.setattr(handler, "_get_interview_logger", lambda *_args: _CaptureInterviewLogger())
    monkeypatch.setattr(
        handler,
        "save_interview_scorecard_success",
        lambda token, total_score, total_max_score, payload: captured["success"].append(
            (token, total_score, total_max_score, payload)
        ),
    )

    queue = handler.ScoringQueue(logging.getLogger("test.scoring.queue.success"))
    task = handler.ScoringTask(
        token="INT-SCORE-SUCCESS",
        score_inputs=[
            {
                "question_id": "q1",
                "sort_order": 1,
                "aggregated_answer": "候选人回答",
                "scoring_boundary": "边界",
                "best_standard": "优秀标准",
                "medium_standard": "中等标准",
                "worst_standard": "较差标准",
            }
        ],
    )

    asyncio.run(queue._process(task))

    assert len(captured["success"]) == 1
    assert captured["success"][0][0] == "INT-SCORE-SUCCESS"
    assert any('"stage": "start"' in line for line in captured_lines)
    assert any('"stage": "question"' in line for line in captured_lines)
    assert any('"stage": "success"' in line for line in captured_lines)
    assert any('"raw_output_preview": "raw-preview"' in line for line in captured_lines)


def test_backend_log_includes_segment_context_length_and_stats_line(monkeypatch, tmp_path):
    _setup_tmp_store(monkeypatch, tmp_path)
    _reset_handler_loggers(monkeypatch, tmp_path)
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "password123")
    admin_store.ensure_default_admin()
    token = _create_interview_fixture()

    interview_logger = handler._get_interview_logger(token, "backend")
    svc = service.VoiceBotService(
        ark_api_key="ark-key",
        llm1_endpoint_id="ep-judge",
        llm2_endpoint_id="ep-interviewer",
        asr_app_key="asr-app",
        asr_access_key="asr-token",
        tts_app_key="tts-app",
        tts_access_key="tts-token",
        session_id=token,
        log_fn=interview_logger.info,
    )
    svc._start_turn()
    svc._log(
        "[Interview] LLM #2 context stats: "
        "segment_id=scene:0:项目复盘 context_len=40 segment_context_len=88"
    )
    svc._log_turn_event(
        "interviewer_llm_start",
        extra={
            "context_len": 40,
            "segment_context_len": 88,
            "segment_id": "scene:0:项目复盘",
        },
    )
    handler._release_interview_loggers_for_token(token)

    log_file = tmp_path / "data" / "storage" / "interview_logs" / token / "backend.log"
    content = log_file.read_text(encoding="utf-8")
    assert "[Interview] LLM #2 context stats:" in content
    assert "segment_context_len=88" in content

    match = re.search(r"\[TurnTrace\]\s+(\{.*\"event\": \"interviewer_llm_start\".*\})", content)
    assert match is not None
    payload = json.loads(match.group(1))
    extra = payload["extra"]
    assert isinstance(extra["context_len"], int)
    assert isinstance(extra["segment_context_len"], int)
