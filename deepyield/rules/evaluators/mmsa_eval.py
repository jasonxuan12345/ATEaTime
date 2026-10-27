"""MMSA-001..008 findings for resolved Site Diff diagnosis outcomes."""

from __future__ import annotations

from typing import Any, Mapping

from deepyield.rules.conditions import as_float
from deepyield.rules.evaluators.common import emit_hit, not_evaluated, results_of
from deepyield.rules.loader import RuleCatalog


def evaluate_mmsa(result: Mapping[str, Any], catalog: RuleCatalog) -> list:
    rules = {rule_id: catalog.rule(rule_id) for rule_id in
             (f"MMSA-00{index}" for index in range(1, 9))}
    items = list((results_of(result).get("mmsa") or {}).get("test_items") or [])
    include = list((((catalog.document("MMSA-V1") or {}).get("evidence") or {}).get("include")) or [])
    if not items:
        return [not_evaluated(rule, result, "MISSING_INPUT") for rule in rules.values()]
    out = []
    for item in items:
        test_id = str(item.get("test_item_id") or "")
        subject = {"kind": "test_item", "test_item_id": test_id}
        rule = rules.get(str(item.get("rule_id") or ""))
        values = {"test_id": test_id, "test_name": item.get("test_name"),
                  "shift_sigma": as_float(item.get("shift_sigma")),
                  "shift_spec_pct": as_float(item.get("shift_spec_pct")),
                  "var_ratio": as_float(item.get("var_ratio")),
                  "overall_bin1_cpk": as_float(item.get("overall_bin1_cpk")),
                  "cgm_min_cg": as_float(item.get("cgm_min_cg")),
                  "rule_id": item.get("rule_id")}
        if rule is None or rule.status == "NOT_APPLICABLE":
            out.append(not_evaluated(rules["MMSA-005"], result, "GATE_NOT_TRIGGERED",
                                     subject=subject, id_parts=(test_id or "-",)))
            continue
        hit = emit_hit(rule, result, subject=subject,
                       evidence={key: values.get(key) for key in include if key in values},
                       id_parts=(test_id,))
        if hit:
            out.append(hit)
    return out
