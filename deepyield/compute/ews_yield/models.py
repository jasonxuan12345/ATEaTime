"""Immutable data types and policy configuration for EWS Yield v2.

Contract: ews-yield-policy-v1
All types are frozen dataclasses or enums. No mutation after construction.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any


class AttemptState(str, enum.Enum):
    """Canonical result state for a single test attempt."""
    PASS = "PASS"
    FAIL = "FAIL"
    ABORT = "ABORT"
    UNKNOWN = "UNKNOWN"
    CONFLICT = "CONFLICT"
    # Sentinel bin 65535: the attempt ran but produced no pass/fail verdict.
    NO_RESULT = "NO_RESULT"

    @property
    def is_classified(self) -> bool:
        return self in (AttemptState.PASS, AttemptState.FAIL)


class CoordinateStatus(str, enum.Enum):
    USABLE = "USABLE"
    SENTINEL = "SENTINEL"
    MISSING = "MISSING"
    UNUSABLE = "UNUSABLE"


class IdentityStatus(str, enum.Enum):
    USABLE = "USABLE"
    IDENTITY_UNUSABLE = "IDENTITY_UNUSABLE"


class OrderingStatus(str, enum.Enum):
    EXACT = "EXACT"
    FILE_TIME = "FILE_TIME"
    SAME_OUTCOME_TIE = "SAME_OUTCOME_TIE"
    AMBIGUOUS = "AMBIGUOUS"
    MISSING_TIME = "MISSING_TIME"


class TimeStatus(str, enum.Enum):
    EXACT = "EXACT"
    FILE_TIME = "FILE_TIME"
    MISSING = "MISSING"


class CoverageStatus(str, enum.Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
    IDENTITY_UNUSABLE = "IDENTITY_UNUSABLE"


class ResultStatus(str, enum.Enum):
    CLASSIFIED = "CLASSIFIED"
    PARTIAL = "PARTIAL"
    UNCLASSIFIED = "UNCLASSIFIED"


class TransitionType(str, enum.Enum):
    NONE = "NONE"
    REPEAT_PASS = "REPEAT_PASS"
    PERSISTENT_FAIL = "PERSISTENT_FAIL"
    RECOVERY = "RECOVERY"
    REGRESSION = "REGRESSION"


@dataclass(frozen=True)
class PolicyConfig:
    """Immutable policy configuration.

    Attributes:
        policy_version: Policy identifier string.
        bin_pass_values: Set of HBIN values considered pass (empty = PRR-only).
        bin_fail_values: Set of HBIN values considered fail.
        enable_bin_consistency_check: If True, check PRR vs bin agreement.
        enable_bin_fallback: If True, use bin when PRR result unavailable.
    """
    policy_version: str = "ews-yield-policy-v1"
    bin_pass_values: frozenset[int] = frozenset()
    bin_fail_values: frozenset[int] = frozenset()
    enable_bin_consistency_check: bool = False
    enable_bin_fallback: bool = False


@dataclass(frozen=True)
class DieCounts:
    """Counts for a single physical Die."""
    attempt_count: int = 0
    classified_attempt_count: int = 0
    pass_attempt_count: int = 0
    fail_attempt_count: int = 0
    abort_count: int = 0
    unknown_count: int = 0
    conflict_count: int = 0


@dataclass(frozen=True)
class DieState:
    """Derived state for a single physical Die."""
    die_key: tuple[Any, ...]
    counts: DieCounts
    first_attempt_state: AttemptState | None = None
    first_classified_outcome: AttemptState | None = None
    latest_attempt_state_as_of: AttemptState | None = None
    final_classified_outcome_as_of: AttemptState | None = None
    is_retested: bool = False
    is_recovered: bool = False
    is_regressed: bool = False
    transition: TransitionType = TransitionType.NONE
    ordering_status: OrderingStatus = OrderingStatus.EXACT
    coordinate_status: CoordinateStatus = CoordinateStatus.USABLE
    identity_status: IdentityStatus = IdentityStatus.USABLE


@dataclass(frozen=True)
class AggregateCounts:
    """Numerator/denominator counts for an aggregate scope."""
    attempt: int = 0
    physical_die: int = 0
    classified_attempt: int = 0
    pass_attempt: int = 0
    fail_attempt: int = 0
    abort: int = 0
    unknown: int = 0
    conflict: int = 0
    identity_unusable: int = 0
    first_classified_die: int = 0
    first_pass_die: int = 0
    first_fail_die: int = 0
    first_fail_final_classified_die: int = 0
    first_pass_final_classified_die: int = 0
    final_classified_die: int = 0
    final_pass_die: int = 0
    final_fail_die: int = 0
    retested_die: int = 0
    recovered_die: int = 0
    regressed_die: int = 0

    def add(self, other: AggregateCounts) -> AggregateCounts:
        """Sum two count sets (never average percentages)."""
        return AggregateCounts(**{
            f.name: getattr(self, f.name) + getattr(other, f.name)
            for f in _AC_FIELDS
        })

    def to_dict(self) -> dict[str, int]:
        return {f.name: getattr(self, f.name) for f in _AC_FIELDS}


_AC_FIELDS = tuple(AggregateCounts.__dataclass_fields__.values())
