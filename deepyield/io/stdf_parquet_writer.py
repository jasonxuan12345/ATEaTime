"""Frozen STDF PRR classification, extracted from DeepYield."""
import math
from typing import Any

def _optional_int(value: Any) -> int | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _classify_part_flag(value: Any, fallback: Any = None) -> tuple[str, set[str]]:
    """Classify one PRR using the frozen STDF PART_FLG priority."""
    flag = _optional_int(value)
    reasons: set[str] = set()
    if flag is None:
        return (str(fallback).upper() if str(fallback).upper() in {"PASS", "FAIL"} else "UNKNOWN", reasons)
    if flag & 0b11100000:
        reasons.add("PART_FLAG_RESERVED_BITS_SET")
    if flag & 0b00000100:
        return "ABORT", reasons
    if flag & 0b00010000:
        return "UNKNOWN", reasons
    if flag & 0b00001000:
        return "FAIL", reasons
    return "PASS", reasons
