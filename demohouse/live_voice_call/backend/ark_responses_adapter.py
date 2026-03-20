from dataclasses import dataclass
from typing import Any, AsyncIterable, Dict, List, Optional, Tuple

from volcenginesdkarkruntime import AsyncArk

ARK_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
THINKING_TYPE_DISABLED = "disabled"
THINKING_TYPE_ENABLED = "enabled"
THINKING_TYPE_AUTO = "auto"
ALLOWED_THINKING_TYPES = {
    THINKING_TYPE_DISABLED,
    THINKING_TYPE_ENABLED,
    THINKING_TYPE_AUTO,
}
ALLOWED_REASONING_EFFORTS = {"minimal", "low", "medium", "high"}


@dataclass(frozen=True)
class LLMStageConfig:
    endpoint_id: str
    thinking_type: str = THINKING_TYPE_DISABLED
    reasoning_effort: Optional[str] = None


def normalize_stage_config(
    stage_name: str,
    endpoint_id: Optional[str],
    thinking_type: Optional[str],
    reasoning_effort: Optional[str],
) -> Tuple[Optional[LLMStageConfig], Optional[str]]:
    endpoint = (endpoint_id or "").strip()
    if not endpoint:
        return None, f"missing {stage_name}_ENDPOINT_ID"
    if not endpoint.startswith("ep-"):
        return None, (
            f"invalid {stage_name}_ENDPOINT_ID: must start with ep- "
            f"(got {endpoint})"
        )

    normalized_thinking = (thinking_type or THINKING_TYPE_DISABLED).strip().lower()
    if normalized_thinking not in ALLOWED_THINKING_TYPES:
        return None, (
            f"invalid {stage_name}_THINKING_TYPE: {normalized_thinking}. "
            f"allowed={sorted(ALLOWED_THINKING_TYPES)}"
        )

    normalized_reasoning = (reasoning_effort or "").strip().lower() or None
    if normalized_thinking == THINKING_TYPE_ENABLED:
        if normalized_reasoning is None:
            normalized_reasoning = "minimal"
        elif normalized_reasoning not in ALLOWED_REASONING_EFFORTS:
            return None, (
                f"invalid {stage_name}_REASONING_EFFORT: {normalized_reasoning}. "
                f"allowed={sorted(ALLOWED_REASONING_EFFORTS)}"
            )
    else:
        normalized_reasoning = None

    return (
        LLMStageConfig(
            endpoint_id=endpoint,
            thinking_type=normalized_thinking,
            reasoning_effort=normalized_reasoning,
        ),
        None,
    )


def build_input_messages(messages: List[Any]) -> List[Dict[str, str]]:
    inputs: List[Dict[str, str]] = []
    for message in messages:
        role = str(getattr(message, "role", "user") or "user")
        if role not in {"user", "assistant", "system", "developer"}:
            role = "user"
        content = str(getattr(message, "content", "") or "")
        inputs.append({"role": role, "content": content})
    return inputs


def extract_text_from_response(response: Any) -> str:
    output_items = getattr(response, "output", None) or []
    chunks: List[str] = []
    for item in output_items:
        if getattr(item, "type", None) != "message":
            continue
        for content_part in (getattr(item, "content", None) or []):
            if getattr(content_part, "type", None) != "output_text":
                continue
            text = getattr(content_part, "text", "") or ""
            if text:
                chunks.append(text)
    return "".join(chunks)


class ArkResponsesAdapter:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = ARK_BASE_URL,
        client: Optional[Any] = None,
    ):
        self._client = client or AsyncArk(base_url=base_url, api_key=api_key)

    @staticmethod
    def _thinking_and_reasoning(
        thinking_type: str, reasoning_effort: Optional[str]
    ) -> Tuple[Dict[str, str], Optional[Dict[str, str]]]:
        thinking = {"type": thinking_type}
        reasoning: Optional[Dict[str, str]] = None
        if thinking_type == THINKING_TYPE_ENABLED:
            reasoning = {"effort": reasoning_effort or "minimal"}
        return thinking, reasoning

    async def complete_text(
        self,
        *,
        model: str,
        instructions: str,
        messages: List[Dict[str, str]],
        thinking_type: str,
        reasoning_effort: Optional[str],
    ) -> str:
        thinking, reasoning = self._thinking_and_reasoning(
            thinking_type, reasoning_effort
        )
        response = await self._client.responses.create(
            model=model,
            input=messages,
            instructions=instructions,
            thinking=thinking,
            reasoning=reasoning,
            stream=False,
        )
        return extract_text_from_response(response)

    async def stream_text(
        self,
        *,
        model: str,
        instructions: str,
        messages: List[Dict[str, str]],
        thinking_type: str,
        reasoning_effort: Optional[str],
    ) -> AsyncIterable[str]:
        thinking, reasoning = self._thinking_and_reasoning(
            thinking_type, reasoning_effort
        )
        stream = await self._client.responses.create(
            model=model,
            input=messages,
            instructions=instructions,
            thinking=thinking,
            reasoning=reasoning,
            stream=True,
        )
        saw_delta = False
        async for event in stream:
            event_type = getattr(event, "type", None)
            if event_type == "response.output_text.delta":
                delta = getattr(event, "delta", "") or ""
                if delta:
                    saw_delta = True
                    yield delta
            elif event_type == "response.output_text.done":
                text = getattr(event, "text", "") or ""
                if text and not saw_delta:
                    yield text
                saw_delta = False
