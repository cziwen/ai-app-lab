import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib import error as urllib_error
from urllib import request as urllib_request

DEFAULT_STT_SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
DEFAULT_STT_QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"


class AucSTTError(RuntimeError):
    """Raised when STT API call failed."""


class AucSTTTimeoutError(AucSTTError):
    """Raised when STT query polling timed out."""


@dataclass
class AucSTTResult:
    task_id: str
    text: str
    utterances: List[str]
    raw_payload: Dict[str, Any]


class AucSTTClient:
    def __init__(
        self,
        *,
        app_id: str,
        access_token: str,
        resource_id: str,
        submit_url: str = DEFAULT_STT_SUBMIT_URL,
        query_url: str = DEFAULT_STT_QUERY_URL,
        task_timeout_ms: int = 90000,
        poll_interval_ms: int = 1000,
        request_timeout_seconds: int = 10,
        log_fn: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.app_id = str(app_id or "").strip()
        self.access_token = str(access_token or "").strip()
        self.resource_id = str(resource_id or "").strip()
        self.submit_url = str(submit_url or "").strip() or DEFAULT_STT_SUBMIT_URL
        self.query_url = str(query_url or "").strip() or DEFAULT_STT_QUERY_URL
        self.task_timeout_ms = max(5000, int(task_timeout_ms or 90000))
        self.poll_interval_ms = max(200, int(poll_interval_ms or 1000))
        self.request_timeout_seconds = max(3, int(request_timeout_seconds or 10))
        self._log_fn = log_fn or (lambda _msg: None)

        if not self.app_id:
            raise ValueError("STT app_id is required")
        if not self.access_token:
            raise ValueError("STT access_token is required")
        if not self.resource_id:
            raise ValueError("STT resource_id is required")

    def transcribe_audio_url(
        self,
        *,
        audio_url: str,
        audio_format: str,
        language: str = "",
        enable_itn: bool = True,
        enable_punc: bool = True,
        show_utterances: bool = True,
        poll_observer: Optional[Callable[[int, str, str, bool], None]] = None,
    ) -> AucSTTResult:
        normalized_audio_url = str(audio_url or "").strip()
        if not normalized_audio_url:
            raise ValueError("audio_url is required")

        normalized_format = str(audio_format or "").strip().lower()
        if normalized_format not in {"wav", "mp3", "ogg", "raw"}:
            raise ValueError(f"unsupported audio format: {audio_format}")

        task_id = str(uuid.uuid4())
        payload = {
            "user": {"uid": "live_voice_call"},
            "audio": {
                "url": normalized_audio_url,
                "format": normalized_format,
            },
            "request": {
                "model_name": "bigmodel",
                "enable_itn": bool(enable_itn),
                "enable_punc": bool(enable_punc),
                "show_utterances": bool(show_utterances),
            },
        }
        normalized_language = str(language or "").strip()
        if normalized_language:
            payload["audio"]["language"] = normalized_language

        self._submit(task_id=task_id, payload=payload)

        deadline = time.monotonic() + (self.task_timeout_ms / 1000.0)
        poll_count = 0
        while True:
            query_payload, status_code, status_message = self._query(task_id=task_id)
            result = self._extract_result(task_id=task_id, payload=query_payload)
            if poll_observer is not None:
                try:
                    poll_observer(
                        poll_count,
                        str(status_code or "").strip(),
                        str(status_message or "").strip(),
                        result is not None,
                    )
                except Exception:
                    # Keep STT polling robust even if observer callback fails.
                    pass
            if result is not None:
                return result

            if status_code and status_code not in {
                "20000000",
                "20000001",
                "20000002",
                "20000003",
            }:
                raise AucSTTError(
                    "stt_query_failed "
                    f"code={status_code} message={status_message or '-'} task_id={task_id}"
                )

            if time.monotonic() >= deadline:
                raise AucSTTTimeoutError(f"stt_query_timeout task_id={task_id}")

            time.sleep(self.poll_interval_ms / 1000.0)
            poll_count += 1

    def _build_headers(self, *, task_id: str, include_sequence: bool) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Api-App-Key": self.app_id,
            "X-Api-Access-Key": self.access_token,
            "X-Api-Resource-Id": self.resource_id,
            "X-Api-Request-Id": task_id,
        }
        if include_sequence:
            headers["X-Api-Sequence"] = "-1"
        return headers

    def _submit(self, *, task_id: str, payload: Dict[str, Any]) -> None:
        body, headers = self._post_json(
            url=self.submit_url,
            headers=self._build_headers(task_id=task_id, include_sequence=True),
            payload=payload,
        )
        status_code = str(headers.get("x-api-status-code", "") or "").strip()
        status_message = str(headers.get("x-api-message", "") or "").strip()
        if status_code and status_code != "20000000":
            raise AucSTTError(
                "stt_submit_failed "
                f"code={status_code} message={status_message or '-'} task_id={task_id}"
            )
        self._log_fn(
            f"[STT] submit accepted task_id={task_id} code={status_code or '-'} "
            f"message={status_message or '-'} body_keys={','.join(sorted(body.keys())) if isinstance(body, dict) else '-'}"
        )

    def _query(self, *, task_id: str) -> Tuple[Dict[str, Any], str, str]:
        body, headers = self._post_json(
            url=self.query_url,
            headers=self._build_headers(task_id=task_id, include_sequence=False),
            payload={},
        )
        status_code = str(headers.get("x-api-status-code", "") or "").strip()
        status_message = str(headers.get("x-api-message", "") or "").strip()
        return body, status_code, status_message

    def _post_json(
        self,
        *,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
    ) -> Tuple[Dict[str, Any], Dict[str, str]]:
        req = urllib_request.Request(
            url=url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib_request.urlopen(
                req,
                timeout=self.request_timeout_seconds,
            ) as rsp:
                rsp_body_bytes = rsp.read() or b"{}"
                try:
                    rsp_payload = json.loads(rsp_body_bytes.decode("utf-8"))
                except json.JSONDecodeError:
                    rsp_payload = {}
                normalized_headers = {
                    str(k or "").lower(): str(v or "")
                    for k, v in rsp.headers.items()
                }
                return (
                    rsp_payload if isinstance(rsp_payload, dict) else {},
                    normalized_headers,
                )
        except urllib_error.HTTPError as http_err:
            error_body = b""
            try:
                error_body = http_err.read() or b""
            except Exception:
                error_body = b""
            preview = error_body.decode("utf-8", errors="replace")[:300]
            raise AucSTTError(
                f"stt_http_error code={http_err.code} reason={http_err.reason} body={preview}"
            ) from http_err
        except urllib_error.URLError as url_err:
            raise AucSTTError(f"stt_network_error reason={url_err.reason}") from url_err

    @staticmethod
    def _extract_result(*, task_id: str, payload: Dict[str, Any]) -> Optional[AucSTTResult]:
        raw_result = payload.get("result")
        if isinstance(raw_result, dict):
            first = raw_result
        elif isinstance(raw_result, list) and raw_result:
            first = raw_result[0]
        else:
            return None
        if not isinstance(first, dict):
            return None

        text = str(first.get("text", "") or "").strip()
        utterances_raw = first.get("utterances")
        utterances: List[str] = []
        if isinstance(utterances_raw, list):
            for item in utterances_raw:
                if not isinstance(item, dict):
                    continue
                utter_text = str(item.get("text", "") or "").strip()
                if utter_text:
                    utterances.append(utter_text)

        if not text and utterances:
            text = "".join(utterances).strip()
        if not text and not utterances:
            return None

        return AucSTTResult(
            task_id=task_id,
            text=text,
            utterances=utterances,
            raw_payload=first,
        )
