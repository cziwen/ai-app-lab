import io

import pytest
from fastapi import HTTPException, UploadFile

from admin_api import parse_question_csv

HEADER = "场景,问题,评分标准,最大分数"


def _make_upload(content: bytes) -> UploadFile:
    return UploadFile(filename="questions.csv", file=io.BytesIO(content))


def test_parse_question_csv_accepts_scene_head_with_followup_rows():
    upload = _make_upload(
        (
            f"{HEADER}\n"
            "项目复盘场景,题目A,是否能结构化表达,12\n"
            ",题目B,,\n"
            ",题目C,,\n"
            "线上故障场景,题目D,关注排障路径完整性,10\n"
            ",题目E,,\n"
        ).encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "scenario": "项目复盘场景",
            "scene_segment": "scene-1",
            "question": "题目A",
            "scoring_boundary": "是否能结构化表达",
            "score_format": "12",
        },
        {
            "scenario": "项目复盘场景",
            "scene_segment": "scene-1",
            "question": "题目B",
            "scoring_boundary": "",
            "score_format": "",
        },
        {
            "scenario": "项目复盘场景",
            "scene_segment": "scene-1",
            "question": "题目C",
            "scoring_boundary": "",
            "score_format": "",
        },
        {
            "scenario": "线上故障场景",
            "scene_segment": "scene-2",
            "question": "题目D",
            "scoring_boundary": "关注排障路径完整性",
            "score_format": "10",
        },
        {
            "scenario": "线上故障场景",
            "scene_segment": "scene-2",
            "question": "题目E",
            "scoring_boundary": "",
            "score_format": "",
        },
    ]


def test_parse_question_csv_returns_400_if_header_mismatch():
    upload = _make_upload("问题,能力维度\n题目A,沟通\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 表头不匹配" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_question_empty():
    upload = _make_upload(f"{HEADER}\n场景A,,评分标准,5\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“问题”不能为空" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_first_scene_missing():
    upload = _make_upload(f"{HEADER}\n,题目A,评分标准,5\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“场景”为空" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_scene_head_scoring_boundary_missing():
    upload = _make_upload(f"{HEADER}\n场景A,题目A,,5\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“评分标准”不能为空" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_scene_head_score_missing():
    upload = _make_upload(f"{HEADER}\n场景A,题目A,评分标准,\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“最大分数”不能为空" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_scene_head_score_invalid():
    upload = _make_upload(f"{HEADER}\n场景A,题目A,评分标准,高/中/低\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“最大分数”格式无效" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_followup_has_scoring_boundary():
    upload = _make_upload(
        f"{HEADER}\n场景A,题目A,评分标准,5\n,题目B,不该填写,\n".encode("utf-8")
    )
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“评分标准”必须留空" in str(exc.value.detail)


def test_parse_question_csv_returns_400_if_followup_has_score():
    upload = _make_upload(f"{HEADER}\n场景A,题目A,评分标准,5\n,题目B,,5\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "“最大分数”必须留空" in str(exc.value.detail)


def test_parse_question_csv_supports_gbk():
    upload = _make_upload((f"{HEADER}\n场景甲,题目甲,是否负责,5\n,题目乙,,\n").encode("gbk"))
    rows = parse_question_csv(upload)
    assert rows[0]["scenario"] == "场景甲"
    assert rows[1]["scenario"] == "场景甲"

