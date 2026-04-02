import io

import pytest
from fastapi import HTTPException, UploadFile

from admin_api import parse_question_csv

HEADER = "场景,问题,评分标准,最大分数"


def _make_upload(content: bytes) -> UploadFile:
    return UploadFile(filename="questions.csv", file=io.BytesIO(content))


def test_parse_question_csv_requires_exact_header_and_parses_rows():
    upload = _make_upload(
        (
            f"{HEADER}\n"
            "项目复盘场景,题目A,是否能结构化表达,0-5分\n"
            "项目复盘场景,题目B,是否以结果为导向,5\n"
        ).encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "scenario": "项目复盘场景",
            "question": "题目A",
            "scoring_boundary": "是否能结构化表达",
            "score_format": "0-5分",
        },
        {
            "scenario": "项目复盘场景",
            "question": "题目B",
            "scoring_boundary": "是否以结果为导向",
            "score_format": "5",
        },
    ]


def test_parse_question_csv_skips_blank_rows():
    upload = _make_upload(
        (
            f"{HEADER}\n\n"
            "场景A,题目A,是否清晰,0-5分\n"
            ",,分界线,0-5分\n"
        ).encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "scenario": "场景A",
            "question": "题目A",
            "scoring_boundary": "是否清晰",
            "score_format": "0-5分",
        }
    ]


def test_parse_question_csv_returns_400_if_no_valid_question():
    upload = _make_upload(f"{HEADER}\n,\n  ,,\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 题库为空" in str(exc.value.detail)


def test_parse_question_csv_supports_gbk():
    upload = _make_upload(
        (f"{HEADER}\n场景甲,题目甲,是否负责,0-5分\n").encode("gbk")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "scenario": "场景甲",
            "question": "题目甲",
            "scoring_boundary": "是否负责",
            "score_format": "0-5分",
        }
    ]


def test_parse_question_csv_returns_400_if_header_mismatch():
    upload = _make_upload("问题,能力维度\n题目A,沟通\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 表头不匹配" in str(exc.value.detail)


def test_parse_question_csv_returns_400_for_legacy_output_format_header():
    legacy_header = "问题,能力维度,评分分界线,最好标准,中等标准,最差标准,分数,评语要求"
    upload = _make_upload(
        f"{legacy_header}\n题目A,沟通能力,是否清晰,标准A,标准B,标准C,评分0-5,给出建议\n".encode("utf-8")
    )
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 表头不匹配" in str(exc.value.detail)


def test_parse_question_csv_returns_400_for_invalid_score_format():
    upload = _make_upload(
        (
            f"{HEADER}\n"
            "场景A,题目A,是否清晰,高/中/低\n"
        ).encode("utf-8")
    )
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "最大分数”格式无效" in str(exc.value.detail)
