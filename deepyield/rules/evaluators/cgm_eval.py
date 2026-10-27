"""CGM-001 override + CGM-002/003/004 CG bands."""

from __future__ import annotations

from typing import Any, Mapping

from deepyield.rules.conditions import as_float, compare
from deepyield.compute.release_numeric import cg_number
from deepyield.rules.findings import RuleFinding
from deepyield.rules.loader import RuleCatalog
from deepyield.rules.evaluators.common import REASON_MISSING_INPUT, emit_hit, not_evaluated, results_of


def evaluate_cgm(result: Mapping[str, Any], catalog: RuleCatalog) -> list[RuleFinding]:
    override = catalog.rule("CGM-001")
    fail = catalog.rule("CGM-002")
    risk = catalog.rule("CGM-003")
    pass_rule = catalog.rule("CGM-004")
    sections = results_of(result)
    items = list((sections.get("cgm") or {}).get("test_items") or [])
    cpk_by_id = {
        str(item.get("test_item_id") or ""): as_float((item.get("bin1") or {}).get("cpk"))
        for item in list((sections.get("cpk") or {}).get("test_items") or [])
    }
    if not items:
        return [
            not_evaluated(rule, result, REASON_MISSING_INPUT, subject={"kind": "cg"})
            for rule in (override, fail, risk, pass_rule)
        ]
    out: list[RuleFinding] = []
    for item in items:
        test_id = str(item.get("test_item_id") or item.get("stable_test_item_id") or "")
        cg = cg_number(item.get("cg"))
        bin1_cpk = as_float(item.get("supported_by_bin1_cpk"))
        if bin1_cpk is None:
            bin1_cpk = cpk_by_id.get(test_id)
        subject = {"kind": "test_item", "test_item_id": test_id}
        if cg is None:
            out.append(
                not_evaluated(
                    fail, result, REASON_MISSING_INPUT, subject=subject, id_parts=(test_id or "-",)
                )
            )
            out.append(
                not_evaluated(
                    risk, result, REASON_MISSING_INPUT, subject=subject, id_parts=(test_id or "-",)
                )
            )
            out.append(
                not_evaluated(
                    pass_rule, result, REASON_MISSING_INPUT, subject=subject, id_parts=(test_id or "-",)
                )
            )
            continue
        evidence = {
            "test_id": test_id,
            "test_name": item.get("test_name"),
            "cg": item.get("cg"),
            "bin1_cpk": bin1_cpk,
            "cpk_override_applied": False,
        }
        override_th = as_float((override.spec.get("condition") or {}).get("threshold"))
        fail_th = as_float((fail.spec.get("condition") or {}).get("threshold"))
        risk_lo = as_float((risk.spec.get("condition") or {}).get("lower"))
        risk_hi = as_float((risk.spec.get("condition") or {}).get("upper"))
        pass_th = as_float((pass_rule.spec.get("condition") or {}).get("threshold"))
        if None in (override_th, fail_th, risk_lo, risk_hi, pass_th):
            out.append(not_evaluated(fail, result, REASON_MISSING_INPUT, subject=subject, id_parts=(test_id,)))
            continue
        if bin1_cpk is not None and compare("GE", bin1_cpk, override_th):
            evidence["cpk_override_applied"] = True
            evidence["cg_threshold_band"] = "CPK_MARGIN_ACCEPTANCE"
            hit = emit_hit(override, result, subject=subject, evidence=evidence, id_parts=(test_id,))
            if hit:
                out.append(hit)
            continue
        if compare("LT", cg, fail_th):
            evidence["cg_threshold_band"] = f"<{fail_th}"
            hit = emit_hit(fail, result, subject=subject, evidence=evidence, id_parts=(test_id,))
            if hit:
                out.append(hit)
        elif compare("GE", cg, risk_lo) and cg < risk_hi:
            evidence["cg_threshold_band"] = f"[{risk_lo}, {risk_hi})"
            hit = emit_hit(risk, result, subject=subject, evidence=evidence, id_parts=(test_id,))
            if hit:
                out.append(hit)
        elif compare("GE", cg, pass_th):
            evidence["cg_threshold_band"] = f">={pass_th}"
            hit = emit_hit(pass_rule, result, subject=subject, evidence=evidence, id_parts=(test_id,))
            if hit:
                out.append(hit)
        else:
            out.append(not_evaluated(fail, result, REASON_MISSING_INPUT, subject=subject, id_parts=(test_id,)))
    return out
