import asyncio
import csv
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import pytest

from ark_responses_adapter import ArkResponsesAdapter
from prompt import INTERVIEWER_SYSTEM_PROMPT


def _load_questions_from_csv(csv_path: Path) -> List[Dict[str, str]]:
    raw = csv_path.read_bytes()
    decoded = None
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            decoded = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if decoded is None:
        raise ValueError(f"无法解析 CSV 编码: {csv_path}")

    rows: List[Dict[str, str]] = []
    reader = csv.DictReader(decoded.splitlines())
    if not reader.fieldnames:
        raise ValueError("CSV 缺少表头")

    scenario_col = None
    question_col = None
    for name in reader.fieldnames:
        if name is None:
            continue
        key = name.strip()
        if key in {"场景", "scenario"}:
            scenario_col = name
        if key in {"问题", "question"}:
            question_col = name

    if not question_col:
        raise ValueError("CSV 缺少“问题/question”列")
    if not scenario_col:
        scenario_col = ""

    current_scenario = ""
    for idx, row in enumerate(reader, start=2):
        question = (row.get(question_col, "") or "").strip()
        if not question:
            continue
        if scenario_col:
            raw_scene = (row.get(scenario_col, "") or "").strip()
            if raw_scene:
                current_scenario = raw_scene
        rows.append(
            {
                "index": str(idx),
                "scenario": current_scenario,
                "question": question,
            }
        )
    return rows


def _build_interview_context(scenario: str, question: str) -> str:
    parts: List[str] = ["[指令] 请继续当前场景提问。"]
    if scenario:
        parts.append(f"[场景] {scenario}")
    parts.append(f"[下一步内容] {question}")
    return "\n".join(parts)


def _extract_numbers(text: str) -> List[str]:
    return re.findall(r"\d+(?:\.\d+)?%?|\d+天", text or "")


def _first_sentence(text: str) -> str:
    content = (text or "").strip()
    if not content:
        return ""
    for sep in ("。", "！", "？", ".", "!", "?", "\n"):
        pos = content.find(sep)
        if pos != -1:
            return content[: pos + 1].strip()
    return content


def _write_report(
    output_path: Path,
    csv_path: Path,
    model: str,
    rows: Sequence[Tuple[Dict[str, str], str]],
) -> None:
    lines: List[str] = []
    lines.append(f"# Prompt 质量检查报告")
    lines.append("")
    lines.append(f"- 时间: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"- CSV: {csv_path}")
    lines.append(f"- 模型: {model}")
    lines.append(f"- 题目数: {len(rows)}")
    lines.append("")

    for i, (item, generated) in enumerate(rows, start=1):
        source_question = item["question"]
        first = _first_sentence(generated)
        nums = _extract_numbers(source_question)
        missing_nums = [n for n in nums if n not in (generated or "")]
        exact_replay = source_question in first

        lines.append(f"## {i}. CSV行 {item['index']}")
        if item["scenario"]:
            lines.append(f"- 场景: {item['scenario']}")
        lines.append(f"- 题干: {source_question}")
        lines.append(f"- 生成: {generated}")
        lines.append(f"- 首句含完整题干: {'是' if exact_replay else '否'}")
        lines.append(f"- 题干数字: {nums}")
        lines.append(f"- 缺失数字: {missing_nums}")
        lines.append("")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def test_prompt_quality_live_api_from_job_csv() -> None:
    """
    手工端到端质检（真实 API）：
    - 默认跳过，需显式开启 RUN_LIVE_PROMPT_QA=1
    - 输入 CSV: PROMPT_QA_CSV=/abs/path/to/job.csv
    - 可选 strict: PROMPT_QA_STRICT=1（发现缺失数字或首句未含完整题干则失败）
    """
    if os.getenv("RUN_LIVE_PROMPT_QA", "0") != "1":
        pytest.skip("set RUN_LIVE_PROMPT_QA=1 to run live prompt quality check")

    api_key = (os.getenv("ARK_API_KEY") or "").strip()
    endpoint = (os.getenv("LLM2_ENDPOINT_ID") or "").strip()
    csv_input = (os.getenv("PROMPT_QA_CSV") or "").strip()
    if not api_key:
        raise AssertionError("missing ARK_API_KEY")
    if not endpoint:
        raise AssertionError("missing LLM2_ENDPOINT_ID")
    if not csv_input:
        raise AssertionError("missing PROMPT_QA_CSV")

    csv_path = Path(csv_input).expanduser().resolve()
    if not csv_path.exists():
        raise AssertionError(f"csv not found: {csv_path}")

    questions = _load_questions_from_csv(csv_path)
    if not questions:
        raise AssertionError("csv has no valid questions")

    limit_raw = (os.getenv("PROMPT_QA_LIMIT") or "").strip()
    if limit_raw:
        questions = questions[: max(1, int(limit_raw))]

    thinking_type = (os.getenv("LLM2_THINKING_TYPE") or "disabled").strip().lower()
    reasoning_effort = (os.getenv("LLM2_REASONING_EFFORT") or "").strip().lower() or None
    strict = os.getenv("PROMPT_QA_STRICT", "0") == "1"

    async def _run() -> List[Tuple[Dict[str, str], str]]:
        adapter = ArkResponsesAdapter(api_key=api_key)
        result: List[Tuple[Dict[str, str], str]] = []
        for item in questions:
            context = _build_interview_context(item["scenario"], item["question"])
            generated = await adapter.complete_text(
                model=endpoint,
                instructions=INTERVIEWER_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": context}],
                thinking_type=thinking_type,
                reasoning_effort=reasoning_effort,
            )
            result.append((item, (generated or "").strip()))
        return result

    pairs = asyncio.run(_run())

    default_report = (
        Path("backend/artifacts")
        / f"prompt_quality_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    )
    output_override = (os.getenv("PROMPT_QA_OUTPUT") or "").strip()
    output_path = Path(output_override).expanduser().resolve() if output_override else default_report
    _write_report(output_path, csv_path, endpoint, pairs)
    print(f"[prompt-quality] report written: {output_path}")

    if not strict:
        return

    errors: List[str] = []
    for item, generated in pairs:
        source = item["question"]
        first = _first_sentence(generated)
        if source not in first:
            errors.append(
                f"CSV行{item['index']} 首句未完整包含题干。first={first} source={source}"
            )
        nums = _extract_numbers(source)
        missing_nums = [n for n in nums if n not in generated]
        if missing_nums:
            errors.append(
                f"CSV行{item['index']} 丢数字 {missing_nums}。source={source} generated={generated}"
            )
    if errors:
        raise AssertionError("\n".join(errors))
