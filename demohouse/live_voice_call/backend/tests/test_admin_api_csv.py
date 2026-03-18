import io

import pytest
from fastapi import HTTPException, UploadFile

from admin_api import parse_question_csv


def _make_upload(content: bytes) -> UploadFile:
    return UploadFile(filename="questions.csv", file=io.BytesIO(content))


def test_parse_question_csv_reads_from_second_row_with_any_header():
    upload = _make_upload("foo,bar\n题目A,答案A\n题目B,答案B\n".encode("utf-8"))
    rows = parse_question_csv(upload)
    assert rows == [
        {"question": "题目A", "reference_answer": "答案A"},
        {"question": "题目B", "reference_answer": "答案B"},
    ]


def test_parse_question_csv_skips_blank_rows_and_allows_single_column():
    upload = _make_upload(
        "h1,h2\n\n题目A,答案A\n仅题目一列\n,空题目应忽略\n".encode("utf-8")
    )
    rows = parse_question_csv(upload)
    assert rows == [
        {"question": "题目A", "reference_answer": "答案A"},
        {"question": "仅题目一列", "reference_answer": ""},
    ]


def test_parse_question_csv_returns_400_if_no_valid_question():
    upload = _make_upload("h1,h2\n,\n  ,  \n".encode("utf-8"))
    with pytest.raises(HTTPException) as exc:
        parse_question_csv(upload)
    assert exc.value.status_code == 400
    assert "CSV 题库为空" in str(exc.value.detail)


def test_parse_question_csv_supports_gbk():
    upload = _make_upload("列1,列2\n题目甲,答案甲\n".encode("gbk"))
    rows = parse_question_csv(upload)
    assert rows == [{"question": "题目甲", "reference_answer": "答案甲"}]
