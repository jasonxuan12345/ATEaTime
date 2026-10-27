"""Deterministic threshold comparisons. Thresholds come from YAML only."""

from __future__ import annotations

import math
import re
from typing import Any


_BAND_RE = re.compile(
    r"^\s*(>=|>|<=|<)\s*([+-]?(?:\d+\.?\d*|\.\d+))\s*(?:and\s*(>=|>|<=|<)\s*([+-]?(?:\d+\.?\d*|\.\d+)))?\s*$",
    re.IGNORECASE,
)


def as_float(value: Any) -> float | None:
    if value is None or value is False:
        return None
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def compare(operator: str, value: float, threshold: float, upper: float | None = None) -> bool:
    op = str(operator or "").upper()
    if op in {"GE", ">="}:
        return value >= threshold
    if op in {"GT", ">"}:
        return value > threshold
    if op in {"LE", "<="}:
        return value <= threshold
    if op in {"LT", "<"}:
        return value < threshold
    if op in {"GE_LT", "GEANDLT"}:
        if upper is None:
            raise ValueError("GE_LT requires upper bound")
        return value >= threshold and value < upper
    if op in {"GT_LT"}:
        if upper is None:
            raise ValueError("GT_LT requires upper bound")
        return value > threshold and value < upper
    raise ValueError(f"unsupported operator: {operator}")


def parse_band(condition: str) -> tuple[str, float, float | None]:
    match = _BAND_RE.match(str(condition or ""))
    if not match:
        raise ValueError(f"unparseable band condition: {condition!r}")
    left_op, left_n, right_op, right_n = match.groups()
    if right_op is None:
        return left_op, float(left_n), None
    return f"{left_op}and{right_op}", float(left_n), float(right_n)


def band_matches(condition: str, value: float) -> bool:
    op, threshold, upper = parse_band(condition)
    if "and" in op:
        left, right = op.split("and", 1)
        return compare(left, value, threshold) and compare(right, value, upper or 0.0)
    return compare(op, value, threshold, upper)
