"""CPK-001 / CPK-002 / CPK-003 bands on BIN1 CPK."""

from __future__ import annotations

from typing import Any, Mapping

from deepyield.rules.conditions import as_float, band_matches
from deepyield.rules.findings import RuleFinding
from deepyield.rules.loader import RuleCatalog
from deepyield.rules.evaluators.common import (
    REASON_MISSING_INPUT,
    emit_hit,
    not_evaluated,
    results_of,
)


def evaluate_cpk(result: Mapping[str, Any], catalog: RuleCatalog) -> list[RuleFinding]:
    bands = [catalog.rule("CPK-001"), catalog.rule("CPK-002"), catalog.rule("CPK-003")]
    items = list((results_of(result).get("cpk") or {}).get("test_items") or [])
    if not items:
        return [
            not_evaluated(rule, result, REASON_MISSING_INPUT, subject={"kind": "bin1_cpk"})
            for rule in bands
        ]
    out: list[RuleFinding] = []
    for item in items:
        test_id = str(item.get("test_item_id") or item.get("stable_test_item_id") or "")
        bin1 = item.get("bin1") or {}
        cpk = as_float((bin1 or {}).get("cpk"))
        subject = {"kind": "test_item", "test_item_id": test_id}
        if cpk is None:
            for rule in bands:
                out.append(
                    not_evaluated(
                        rule,
                        result,
                        REASON_MISSING_INPUT,
                        subject=subject,
                        evidence={"test_id": test_id, "bin1_cpk": None},
                        id_parts=(test_id or "-",),
                    )
                )
            continue
        matched = False
        for rule in bands:
            condition = str((rule.spec.get("condition") or ""))
            if not condition.strip():
                continue
            try:
                hit_band = band_matches(condition, cpk)
            except ValueError:
                continue
            if not hit_band:
                continue
            matched = True
            evidence = {
                "test_id": test_id,
                "test_name": item.get("test_name"),
                "bin1_cpk": cpk,
                "bin1_count": (bin1 or {}).get("n"),
                "lsl": item.get("lsl"),
                "usl": item.get("usl"),
                "threshold_band": condition,
            }
            hit = emit_hit(rule, result, subject=subject, evidence=evidence, id_parts=(test_id,))
            if hit:
                out.append(hit)
            break
        if not matched:
            for rule in bands:
                out.append(
                    not_evaluated(
                        rule,
                        result,
                        REASON_MISSING_INPUT,
                        subject=subject,
                        evidence={"test_id": test_id, "bin1_cpk": cpk},
                        id_parts=(test_id,),
                    )
                )
    return out
