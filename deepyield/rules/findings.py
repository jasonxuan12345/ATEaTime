"""Atomic finding record for Rule Engine v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


NOT_EVALUATED = "NOT_EVALUATED"
PASS = "PASS"
RISK = "RISK"
FAIL = "FAIL"

_SEVERITY_FOR_STATUS = {
    FAIL: "FAIL",
    RISK: "WARNING",
    PASS: "INFO",
    NOT_EVALUATED: NOT_EVALUATED,
}


def scope_from_result(result: Mapping[str, Any]) -> dict[str, Any]:
    context = dict(result.get("context") or {})
    population = dict(context.get("population") or {})
    return {
        "dataset_version_id": context.get("dataset_version_id") or population.get("dataset_version_id"),
        "scope_id": context.get("scope_id") or population.get("scope_id"),
        "scope_hash": context.get("scope_hash") or population.get("scope_hash"),
        "analysis_type": context.get("analysis_type") or population.get("analysis_type"),
    }


def finding_id(rule_id: str, *parts: Any) -> str:
    tokens = [str(rule_id)]
    for part in parts:
        text = "" if part is None else str(part).strip()
        tokens.append(text.replace(" ", "_") or "-")
    return ":".join(tokens)


@dataclass(frozen=True)
class RuleFinding:
    finding_id: str
    type: str
    severity: str
    scope: Mapping[str, Any]
    subject: Mapping[str, Any]
    evidence: Mapping[str, Any]
    rule_id: str
    status: str
    reason: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "finding_id": self.finding_id,
            "type": self.type,
            "severity": self.severity,
            "scope": dict(self.scope),
            "subject": dict(self.subject),
            "evidence": dict(self.evidence),
            "rule_id": self.rule_id,
            "status": self.status,
        }
        if self.reason:
            payload["reason"] = self.reason
        payload.update(dict(self.extra))
        return payload


def make_finding(
    *,
    rule_id: str,
    finding_type: str,
    status: str,
    subject: Mapping[str, Any],
    evidence: Mapping[str, Any],
    result: Mapping[str, Any],
    severity: str | None = None,
    reason: str | None = None,
    id_parts: tuple[Any, ...] = (),
) -> RuleFinding:
    return RuleFinding(
        finding_id=finding_id(rule_id, *id_parts),
        type=finding_type or NOT_EVALUATED,
        severity=severity or _SEVERITY_FOR_STATUS.get(status, status),
        scope=scope_from_result(result),
        subject=dict(subject),
        evidence=dict(evidence),
        rule_id=rule_id,
        status=status,
        reason=reason,
    )
