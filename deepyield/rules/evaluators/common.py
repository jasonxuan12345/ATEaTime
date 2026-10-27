"""Shared AnalysisResult accessors and fail-closed helpers."""

from __future__ import annotations

from typing import Any, Mapping

from deepyield.rules.conditions import as_float
from deepyield.rules.findings import NOT_EVALUATED, RuleFinding, make_finding
from deepyield.rules.loader import LoadedRule, RuleCatalog

REASON_MISSING_INPUT = "MISSING_INPUT"
REASON_POPULATION_MISMATCH = "POPULATION_MISMATCH"
REASON_TEMPERATURE_UNAVAILABLE = "TEMPERATURE_UNAVAILABLE"
REASON_ZERO_STD = "ZERO_STD"
REASON_ZERO_REFERENCE = "ZERO_REFERENCE"
REASON_INVALID_SPEC = "INVALID_SPEC"
REASON_ZERO_BIN1 = "ZERO_VALID_BIN1"
REASON_MARGIN_RATIO_REQUIRED = "MARGIN_RATIO_REQUIRED"
REASON_MISSING_REFERENCE = "MISSING_REFERENCE"
REASON_PASS_BIN_POLICY_UNAVAILABLE = "PASS_BIN_POLICY_UNAVAILABLE"


def results_of(result: Mapping[str, Any]) -> dict[str, Any]:
    if "results" in result and isinstance(result["results"], dict):
        return dict(result["results"])
    outputs = result.get("outputs") or {}
    sections = outputs.get("sections") if isinstance(outputs, dict) else None
    return dict(sections or {})


def warnings_of(result: Mapping[str, Any]) -> tuple[str, ...]:
    raw = result.get("warnings") or []
    return tuple(str(item) for item in raw)


def population_mismatch(result: Mapping[str, Any]) -> bool:
    return any(item.startswith("POPULATION_MISMATCH") for item in warnings_of(result))


def temperature_unavailable(value: Any, result: Mapping[str, Any] | None = None) -> bool:
    del result
    text = "" if value is None else str(value).strip()
    return (not text) or text.upper() in {"UNAVAILABLE", "TEMPERATURE_UNAVAILABLE", "NONE", "NULL"}


def context_dim(result: Mapping[str, Any], key: str) -> Any:
    context = result.get("context") or {}
    if context.get(key) not in (None, ""):
        return context.get(key)
    population = context.get("population") or {}
    return population.get(key)


def not_evaluated(
    rule: LoadedRule,
    result: Mapping[str, Any],
    reason: str,
    *,
    subject: Mapping[str, Any] | None = None,
    evidence: Mapping[str, Any] | None = None,
    id_parts: tuple[Any, ...] = (),
) -> RuleFinding:
    payload = dict(evidence or {})
    payload["reason"] = reason
    return make_finding(
        rule_id=rule.rule_id,
        finding_type=rule.finding_type or NOT_EVALUATED,
        status=NOT_EVALUATED,
        subject=subject or {"rule_id": rule.rule_id},
        evidence=payload,
        result=result,
        severity=NOT_EVALUATED,
        reason=reason,
        id_parts=id_parts or ("NOT_EVALUATED", reason),
    )


def mismatch_findings(catalog: RuleCatalog, result: Mapping[str, Any]) -> list[RuleFinding]:
    return [not_evaluated(rule, result, REASON_POPULATION_MISMATCH) for rule in catalog.rules]


def required_number(value: Any) -> float | None:
    return as_float(value)


def yaml_condition_threshold(rule: LoadedRule) -> float | None:
    """Read `spec.condition.threshold`. Missing YAML is None (fail-closed)."""

    return as_float((rule.spec.get("condition") or {}).get("threshold"))


def yaml_first_condition_threshold(rule: LoadedRule) -> float | None:
    conditions = list(rule.spec.get("conditions") or [])
    if not conditions or not isinstance(conditions[0], Mapping):
        return None
    return as_float(conditions[0].get("threshold"))


def pick_evidence(include: list[str], values: Mapping[str, Any]) -> dict[str, Any]:
    return {key: values.get(key) for key in include}


def emit_hit(rule: LoadedRule, result: Mapping[str, Any], *, subject: Mapping[str, Any], evidence: Mapping[str, Any], id_parts: tuple[Any, ...]) -> RuleFinding | None:
    if not rule.emit_finding:
        return None
    return make_finding(
        rule_id=rule.rule_id,
        finding_type=rule.finding_type,
        status=rule.status or "RISK",
        subject=subject,
        evidence=evidence,
        result=result,
        severity=rule.severity or None,
        id_parts=id_parts,
    )
