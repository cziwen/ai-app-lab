import io

import pytest
from fastapi import HTTPException, UploadFile

from admin_api import parse_question_csv

HEADER = "问题,能力维度,评分分界线,最好标准,中等标准,最差标准,输出格式"


def _make_upload(content: bytes) -> UploadFile:
    return UploadFile(filename="questions.csv", file=io.BytesIO(content))


def test_parse_question_csv_requires_exact_header_and_parses_rows():
    upload = _make_upload(
        (
            f"{HEADER}\n"
            "题目A,沟通能力,是否能结构化表达,表达完整且有案例,表达基本清晰,表达混乱,评分0-5\n"
            "题目B,责任感,是否以结果为导向,主动负责推进,按要求完成,回避责任,评分0-5\n"
        ).encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "question": "题目A",
            "ability_dimension": "沟通能力",
            "scoring_boundary": "是否能结构化表达",
            "best_standard": "表达完整且有案例",
            "medium_standard": "表达基本清晰",
            "worst_standard": "表达混乱",
            "output_format": "评分0-5",
        },
        {
            "question": "题目B",
            "ability_dimension": "责任感",
            "scoring_boundary": "是否以结果为导向",
            "best_standard": "主动负责推进",
            "medium_standard": "按要求完成",
            "worst_standard": "回避责任",
            "output_format": "评分0-5",
        },
    ]


def test_parse_question_csv_skips_blank_rows():
    upload = _make_upload(
        (
            f"{HEADER}\n\n"
            "题目A,沟通能力,是否清晰,标准A,标准B,标准C,评分0-5\n"
            ",责任感,分界线,最好,中等,最差,评分0-5\n"
        ).encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "question": "题目A",
            "ability_dimension": "沟通能力",
            "scoring_boundary": "是否清晰",
            "best_standard": "标准A",
            "medium_standard": "标准B",
            "worst_standard": "标准C",
            "output_format": "评分0-5",
        }
    ]


def test_parse_question_csv_returns_400_if_no_valid_question():
    upload = _make_upload(f"{HEADER}\n,\n  ,  \n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 题库为空" in str(exc.value.detail)


def test_parse_question_csv_supports_gbk():
    upload = _make_upload(
        (f"{HEADER}\n题目甲,责任感,是否负责,主动,一般,不足,评分0-5\n").encode("gbk")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {
            "question": "题目甲",
            "ability_dimension": "责任感",
            "scoring_boundary": "是否负责",
            "best_standard": "主动",
            "medium_standard": "一般",
            "worst_standard": "不足",
            "output_format": "评分0-5",
        }
    ]


def test_parse_question_csv_returns_400_if_header_mismatch():
    upload = _make_upload("问题,能力维度\n题目A,沟通\n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 表头不匹配" in str(exc.value.detail)
