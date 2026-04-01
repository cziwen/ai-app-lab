import re
from typing import Optional, Tuple


_RANGE_PATTERN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*[-~～]\s*([+-]?\d+(?:\.\d+)?)\s*(?:分)?\s*$")
_SINGLE_PATTERN = re.compile(r"^\s*([+-]?\d+(?:\.\d+)?)\s*(?:分)?\s*$")


def parse_score_scale(raw: str) -> Tuple[Optional[float], Optional[str]]:
    value = str(raw or "").strip()
    if value.startswith("评分"):
        value = value[2:].strip()
    if not value:
        return None, "分数字段不能为空"

    matched_range = _RANGE_PATTERN.match(value)
    if matched_range:
        try:
            min_score = float(matched_range.group(1))
            max_score = float(matched_range.group(2))
        except ValueError:
            return None, "分数格式无法解析"
        if min_score != 0:
            return None, "分数区间下限必须为0"
        if max_score <= 0:
            return None, "分数上限必须大于0"
        return max_score, None

    matched_single = _SINGLE_PATTERN.match(value)
    if matched_single:
        try:
            max_score = float(matched_single.group(1))
        except ValueError:
            return None, "分数格式无法解析"
        if max_score <= 0:
            return None, "分数上限必须大于0"
        return max_score, None

    return None, "仅支持 5、5分、0-5、0~5、0～5分 等格式"
