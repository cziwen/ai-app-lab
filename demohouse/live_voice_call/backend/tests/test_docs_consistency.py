from __future__ import annotations

import re
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

# 本轮“关键文档 + 关键模块文档”一致性检查范围
TARGET_DOCS = [
    REPO_ROOT / "README.md",
    REPO_ROOT / "docs/ARCHITECTURE.md",
    REPO_ROOT / "docs/DATA_FLOW.md",
    REPO_ROOT / "docs/TROUBLESHOOTING.md",
    REPO_ROOT / "docs/modules/backend/event.md",
    REPO_ROOT / "docs/modules/backend/interview_flow.md",
    REPO_ROOT / "docs/modules/backend/interview_judge.md",
    REPO_ROOT / "docs/modules/backend/service.md",
    REPO_ROOT / "docs/modules/backend/handler.md",
    REPO_ROOT / "docs/modules/backend/prompt.md",
]

DEPRECATED_QUEUE_EVENTS = [
    "QueueEntered",
    "QueueUpdate",
    "QueueAdmitted",
    "QueueTimeout",
    "QueueCancelled",
]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_docs_referenced_backend_tests_exist() -> None:
    missing: list[str] = []
    pattern = re.compile(r"backend/tests/[A-Za-z0-9_./-]+\\.py")

    for doc in TARGET_DOCS:
        content = _read_text(doc)
        refs = sorted(set(pattern.findall(content)))
        for ref in refs:
            if not (REPO_ROOT / ref).exists():
                missing.append(f"{doc}: {ref}")

    if missing:
        raise AssertionError(
            "文档引用了不存在的测试文件：\n- " + "\n- ".join(missing)
        )


def test_docs_do_not_contain_deprecated_queue_event_names() -> None:
    hits: list[str] = []

    for doc in TARGET_DOCS:
        content = _read_text(doc)
        for name in DEPRECATED_QUEUE_EVENTS:
            if name in content:
                hits.append(f"{doc}: {name}")

    if hits:
        raise AssertionError(
            "文档仍包含已废弃排队事件名：\n- " + "\n- ".join(hits)
        )


def test_interview_judge_doc_uses_new_decision_contract() -> None:
    doc = REPO_ROOT / "docs/modules/backend/interview_judge.md"
    content = _read_text(doc)

    required = [
        "next_action",
        "next_prompt",
        "reason",
        "coverage_score",
    ]
    for field in required:
        assert field in content, f"缺少 Decision 字段说明: {field}"
