"""Release MMSA (Site Diff) diagnosis adapter over the shared grouping machinery.

Dual-parameter complementary method, thresholds and the decision tree live in
the MMSA-V1 YAML document; this module only measures and applies them.
Diagnosis statuses never escalate the merged release verdict (RISK at most).
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from deepyield.compute.release_numeric import require_current_cg_policy
from deepyield.compute.release_cgm import build_release_cgm_report, cross_source_identity
from deepyield.compute.release_numeric import cg_number
from deepyield.compute.release_cpk import _flag, _group_events, build_release_bin1_cpk_report
from deepyield.compute.release_stdf import read_release_wide_all
from deepyield.compute.release_run_cache import cached_report
from deepyield.compute.release_control import checkpoint, count, measure, timed
from deepyield.compute.release_sample_batch import collect_samples, prepared_groups
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.rules.conditions import as_float, compare
from deepyield.rules.evaluators.mmsa_eval import evaluate_mmsa
from deepyield.rules.loader import RuleCatalog, load_analysis_rules

_LABELS = {"PASS": "通过", "RISK": "风险", "FAIL": "未达标",
           "INSUFFICIENT_DATA": "数据不足", "NOT_CALCULABLE": "无法计算",
           "NOT_EVALUATED": "尚未评估", "REVIEW_REQUIRED": "需复核",
           "NOT_APPLICABLE": "不适用"}
_RULE_IDS = tuple(f"MMSA-00{index}" for index in range(1, 9))
_ALLOWED_STATUSES = {"RISK", "FAIL", "REVIEW_REQUIRED"}
_GATE_METRICS = ("overall_bin1_cpk", "cgm_min_cg")


def _band(value: float | None, band: dict[str, float]) -> str | None:
    if value is None:
        return None
    if value < band["green_lt"]:
        return "GREEN"
    if value >= band["red_ge"]:
        return "RED"
    return "YELLOW"


def _gate_condition(catalog: RuleCatalog, entry: dict[str, Any]) -> dict[str, Any]:
    metric, operator = entry.get("metric"), entry.get("operator")
    if metric not in _GATE_METRICS or operator != "LT":
        raise ValueError(f"MMSA gate 条件不支持：{metric} {operator}")
    threshold = as_float(entry.get("threshold"))
    if threshold is None and entry.get("threshold_source"):
        source = catalog.rule(str(entry["threshold_source"])).spec.get("condition") or {}
        if source.get("operator") != "GE":
            raise ValueError("MMSA gate 阈值来源规则需要 GE 阈值")
        threshold = as_float(source.get("threshold"))
    if threshold is None:
        raise ValueError(f"MMSA gate 条件缺少阈值：{metric}")
    return {"metric": metric, "operator": operator, "threshold": threshold}


def _mmsa_config(catalog: RuleCatalog) -> dict[str, Any]:
    document = catalog.document("MMSA-V1")
    gate = document.get("gate") or {}
    if gate.get("trigger") != "ANY" or not isinstance(gate.get("conditions"), list) or not gate["conditions"]:
        raise ValueError("MMSA gate 需要 ANY 触发和至少一个条件")
    conditions = [_gate_condition(catalog=catalog, entry=entry) for entry in gate["conditions"]]
    bands: dict[str, dict[str, float]] = {}
    for name in ("shift_sigma", "shift_spec_pct", "var_ratio"):
        band = (document.get("parameters") or {}).get(name) or {}
        green, red = as_float(band.get("green_lt")), as_float(band.get("red_ge"))
        if green is None or red is None or not green < red:
            raise ValueError(f"MMSA 参数 {name} 的绿/红分界缺失或顺序有误")
        bands[name] = {"green_lt": green, "red_ge": red}
    split = document.get("distribution_split") or {}
    cp_close, margin_close, cp_wide = (as_float(split.get("cp_close_ge")),
        as_float(split.get("margin_close_lt")), as_float(split.get("cp_wide_lt")))
    if cp_close is None or margin_close is None or cp_wide is None or not 0 < margin_close < 1:
        raise ValueError("MMSA 规则 5 细分参数缺失或顺序有误")
    reference = document.get("cgm_reference") or {}
    max_local = as_float(reference.get("local_fault_max_sites"))
    cg_rule = catalog.rule(str(reference.get("cg_pass_threshold_source") or "CGM-004"))
    cg_condition = cg_rule.spec.get("condition") or {}
    cg_pass = as_float(cg_condition.get("threshold"))
    if cg_condition.get("operator") != "GE" or cg_pass is None:
        raise ValueError("MMSA 引用的 CG 通过规则需要 GE 阈值")
    if max_local is None or max_local < 1:
        raise ValueError("MMSA 局部故障 site 数上限必须是至少 1 的数")
    rules = {}
    for rule_id in _RULE_IDS:
        rule = catalog.rule(rule_id)
        if rule.status not in _ALLOWED_STATUSES:
            raise ValueError(f"不支持的 MMSA 规则状态：{rule_id} {rule.status}")
        rules[rule_id] = rule
    return {"gate": {"trigger": "ANY", "conditions": conditions}, "bands": bands,
            "distribution_split": {"cp_close_ge": cp_close, "margin_close_lt": margin_close,
                                   "cp_wide_lt": cp_wide},
            "cg_pass": cg_pass, "local_fault_max_sites": int(max_local), "rules": rules}


def _validate_support(report: dict, local_source: dict, report_type: str,
                      allow_cross: bool, catalog_hash: str) -> bool:
    same_source = report.get("source") == local_source
    if report.get("report_type") != report_type or (not same_source and not allow_cross):
        raise ValueError(f"{report_type} 支持报告必须来自同一份 Release Parquet")
    if (report.get("rules") or {}).get("catalog_hash") != catalog_hash:
        raise ValueError(f"{report_type} 支持报告的规则版本不匹配，请重新计算")
    return same_source


def _matcher(items: list[dict], same_source: bool, groups: list[dict]) -> Callable[[str, dict], dict | None]:
    by_id = {item["group_id"]: item for item in items}
    if len(by_id) != len(items):
        raise ValueError("支持报告包含重复测项分组")
    group_counts = Counter(cross_source_identity(group) for group in groups)
    cross: dict[str, list[dict]] = {}
    if not same_source:
        for item in items:
            identity = cross_source_identity(item)
            if identity:
                cross.setdefault(identity, []).append(item)
    def match(key: str, group: dict) -> dict | None:
        if same_source:
            return by_id.get(key)
        identity = cross_source_identity(group)
        if group_counts[identity] != 1:
            return None
        matches = cross.get(identity, [])
        return matches[0] if len(matches) == 1 else None
    return match


def _hit(item: dict, rule_id: str, config: dict[str, Any]) -> None:
    rule = config["rules"][rule_id]
    item.update(status=rule.status, rule_status=rule.status, rule_id=rule.rule_id,
                reason=rule.spec.get("reason"))


def _gate_triggered(item: dict, config: dict[str, Any]) -> bool:
    return any(compare(condition["operator"], value, condition["threshold"])
               for condition in config["gate"]["conditions"]
               if (value := cg_number(item.get(condition["metric"]))) is not None)


def _classify_distribution(item: dict, config: dict[str, Any]) -> None:
    """Rule 5 sub-judgment per the expert rule book: close-to-limit vs wide."""
    split = config["distribution_split"]
    cp, upper, lower = item["cp"], item["limit_margin_upper"], item["limit_margin_lower"]
    if cp is None or upper is None or lower is None:
        _hit(item, "MMSA-005", config)
        return
    if cp >= split["cp_close_ge"] and min(upper, lower) < split["margin_close_lt"]:
        item["close_to"] = "HIGH" if upper <= lower else "LOW"
        _hit(item, "MMSA-007", config)
    elif cp < split["cp_wide_lt"]:
        _hit(item, "MMSA-008", config)
    else:
        _hit(item, "MMSA-005", config)


def _diagnose(item: dict, config: dict[str, Any]) -> None:
    if not _gate_triggered(item, config):
        if all(item.get(metric) is None for metric in _GATE_METRICS):
            item.update(status="NOT_EVALUATED", reason="NO_MATCHED_BIN1_CPK")
        else:
            item.update(status="NOT_APPLICABLE", reason="GATE_NOT_TRIGGERED")
        return
    if item["cgm_min_cg"] is not None and compare("LT", cg_number(item["cgm_min_cg"]), config["cg_pass"]):
        branch = "MMSA-001" if item["cgm_failed_site_count"] <= config["local_fault_max_sites"] else "MMSA-002"
        _hit(item, branch, config)
        return
    shift_red = item["shift_severity"] == "RED"
    no_red = not shift_red and item["var_severity"] != "RED"
    # The expert tree's rule 5 trigger only consults shift and var; the spec%
    # parameter is reported, never a trigger.
    repeat_ok = item["shift_severity"] == "GREEN" and item["var_severity"] == "GREEN"
    if not no_red:
        primary = "MMSA-003" if shift_red else "MMSA-004"
        item["secondary_rule_id"] = "MMSA-004" if primary == "MMSA-003" and item["var_severity"] == "RED" else None
        _hit(item, primary, config)
    elif repeat_ok:
        _classify_distribution(item, config)
    else:
        _hit(item, "MMSA-006", config)


@timed('mmsa.calculate')
def _calculate(group: dict, expected_sites: set[int | None], cpk_item: dict | None,
               cgm_item: dict | None, config: dict[str, Any]) -> dict:
    checkpoint()
    spec = group["spec"]
    # Release limit policy: unset limits arrive as None from the parser; a
    # written 0.0 is a real limit (the engine opt-out mirrors this).
    low, high = as_float(spec["low_limit"]), as_float(spec["high_limit"])
    two_sided = low is not None and high is not None and low < high
    excluded: Counter = Counter()
    by_site: dict[int | None, list[float]] = {}
    refs: list[dict] = []
    with measure('mmsa.samples'):
        batch = collect_samples(group, 'mmsa', _flag)
        if batch is not None:
            by_site, refs, excluded = batch.by_site, batch.refs, batch.excluded
        else:
            for part, event in group["events"]:
                flag = _flag(part.get("PART_FLAG"))
                if flag is None or _classify_part_flag(flag)[0] not in {"PASS", "FAIL"}:
                    excluded["INVALID_PART_RESULT"] += 1
                    continue
                site = _flag(part.get("SITE"))
                if site is None:
                    excluded["MISSING_OR_INVALID_SITE"] += 1
                    continue
                if group["record_type"] == "FTR":
                    continue
                tf, pf = _flag(event.get("test_flag")), _flag(event.get("param_flag"))
                value = as_float(event.get("result"))
                if tf is None or pf is None or tf & 0x3F or pf & 0x27 or value is None or event.get("result_valid") is not True:
                    excluded["INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS"] += 1
                    continue
                by_site.setdefault(site, []).append(value)
                refs.append({"part_index": int(part["__part_index"]),
                             "record_sequence": event["record_sequence"], "site": site})
    count('mmsa.examined_samples', len(group['events']))
    count('mmsa.valid_samples', len(refs))
    with measure('mmsa.statistics'):
        site_stats = []
        shared = getattr(group['events'], 'site_statistics', {}) if batch is not None else {}
        for site in sorted(expected_sites, key=lambda value: -1 if value is None else value):
            values = by_site.get(site, [])
            cached = shared.get(site)
            if cached is not None and cached[0] == len(values):
                n, mean, std = cached
                count('mmsa.shared_site_statistics')
            else:
                n = len(values)
                mean = float(np.mean(values)) if n else None
                std = as_float(np.std(values, ddof=1)) if n >= 2 else None
            site_stats.append({"site": site, "n": n, "mean": mean, "std": std, "cg": None})
        usable = [entry for entry in site_stats if entry["std"] is not None]
        overall = np.concatenate(list(by_site.values())) if by_site else np.empty(0, dtype=np.float64)
        n = len(overall)
        overall_mean = float(np.mean(overall)) if len(overall) else None
        overall_std = as_float(np.std(overall, ddof=1)) if len(overall) >= 2 else None
        means = [entry["mean"] for entry in usable]
        stds = [entry["std"] for entry in usable]
        mean_shift = max(means) - min(means) if len(means) >= 2 else None
        median_std = float(np.median(stds)) if stds else None
        max_std = max(stds) if stds else None
        if median_std == 0:
            # All-zero stds are perfectly homogeneous; a lone worst site is a clear anomaly.
            var_ratio, var_severity = (0.0, "GREEN") if max_std == 0 else (None, "RED")
        else:
            var_ratio = as_float(max_std / median_std) if median_std else None
            var_severity = _band(var_ratio, config["bands"]["var_ratio"])
        shift_sigma = as_float(mean_shift / overall_std) if mean_shift is not None and overall_std else None
        shift_spec_pct = as_float(mean_shift / (high - low) * 100) if two_sided and mean_shift is not None else None
        bandwidth = high - low if two_sided else None
        cp = as_float(bandwidth / (6 * overall_std)) if bandwidth is not None and overall_std else None
        upper_margin = as_float((high - overall_mean) / bandwidth) if bandwidth is not None and overall_mean is not None else None
        lower_margin = as_float((overall_mean - low) / bandwidth) if bandwidth is not None and overall_mean is not None else None
    item = {key: group[key] for key in ("group_id", "column", "test_num", "test_name",
                                        "record_type", "result_index", "conditions")}
    item.update(unit=spec["unit"], lsl=as_float(spec["low_limit"]), usl=as_float(spec["high_limit"]),
        effective_lsl=low, effective_usl=high, n=len(overall), site_count=len(usable),
        sites=site_stats, overall_mean=overall_mean, overall_std=overall_std,
        mean_shift=mean_shift, min_site_mean=min(means) if means else None,
        max_site_mean=max(means) if means else None,
        shift_sigma=shift_sigma, shift_severity=_band(shift_sigma, config["bands"]["shift_sigma"]),
        shift_spec_pct=shift_spec_pct,
        shift_spec_severity=_band(shift_spec_pct, config["bands"]["shift_spec_pct"]) if two_sided else None,
        var_ratio=var_ratio, var_severity=var_severity,
        median_site_std=median_std, max_site_std=max_std,
        cgm_min_cg=cgm_item.get("cg") if cgm_item and cg_number(cgm_item.get("cg")) is not None else None,
        cgm_failed_site_count=sum(1 for entry in (cgm_item or {}).get("sites") or []
                                  if cg_number(entry.get("cg")) is not None
                                  and cg_number(entry["cg"]) < config["cg_pass"]),
        overall_bin1_cpk=as_float(cpk_item.get("cpk"))
            if cpk_item and cpk_item.get("status") in {"PASS", "RISK", "FAIL"} else None,
        selected_execution_count=len(group["events"]), sample_refs=refs,
        mean=overall_mean, std=overall_std,
        cp=cp, limit_margin_upper=upper_margin, limit_margin_lower=lower_margin, close_to=None,
        excluded_counts=dict(excluded), status=None, rule_id=None, rule_status=None,
        secondary_rule_id=None, reason=None)
    if group["record_type"] == "FTR":
        item.update(status="NOT_APPLICABLE", reason="FUNCTIONAL_TEST")
    elif low is None and high is None:
        item.update(status="NOT_CALCULABLE", reason="NO_EFFECTIVE_LIMIT")
    elif n < 2:
        item.update(status="INSUFFICIENT_DATA", reason="TOO_FEW_VALID_SAMPLES")
    elif len(usable) < 2:
        item.update(status="INSUFFICIENT_DATA", reason="TOO_FEW_USABLE_SITES")
    elif overall_std is None or overall_std == 0:
        item.update(status="NOT_CALCULABLE", reason="ZERO_OR_UNAVAILABLE_STD")
    else:
        with measure('mmsa.diagnose'):
            _diagnose(item, config)
    if item["status"] is None:
        raise ValueError("MMSA 判定未完成，请检查规则配置")
    item["status_label"] = _LABELS[item["status"]]
    cpk_gate = next((condition["threshold"] for condition in config["gate"]["conditions"]
                     if condition["metric"] == "overall_bin1_cpk"), None)
    item["s2s"] = {"gate": {"trigger": "ANY", "cpk_lt": cpk_gate, "cg_lt": config["cg_pass"]},
        "bands": {k: dict(v) for k, v in config["bands"].items()},
        "cg_pass_threshold": config["cg_pass"], "local_fault_max_sites": config["local_fault_max_sites"],
        "site_count": len(usable), "mean_shift": item["mean_shift"], "overall_std": overall_std,
        "median_site_std": median_std, "max_site_std": max_std,
        "min_site_mean": item["min_site_mean"], "max_site_mean": item["max_site_mean"],
        "shift_sigma": shift_sigma, "shift_severity": item["shift_severity"],
        "shift_spec_pct": shift_spec_pct, "shift_spec_severity": item["shift_spec_severity"],
        "var_ratio": var_ratio, "var_severity": var_severity,
        "cp": item["cp"], "limit_margin_upper": item["limit_margin_upper"],
        "limit_margin_lower": item["limit_margin_lower"], "close_to": item["close_to"],
        "cgm_min_cg": item["cgm_min_cg"], "cgm_failed_site_count": item["cgm_failed_site_count"],
        "overall_bin1_cpk": item["overall_bin1_cpk"], "secondary_rule_id": item["secondary_rule_id"]}
    count('mmsa.status.' + item['status'])
    return item


@cached_report
@timed('mmsa.report')
def build_release_mmsa_report(frame: pd.DataFrame, metadata: dict[str, Any], *, catalog: RuleCatalog,
                              part_indices: list[int] | None = None, sites: list[int] | None = None,
                              test_columns: list[str] | None = None,
                              minimum_samples_per_site: int | None = None,
                              bin1_cpk_report: dict | None = None, cgm_report: dict | None = None,
                              allow_cross_source_cpk: bool = False,
                              allow_cross_source_cgm: bool = False) -> dict[str, Any]:
    """Diagnose site diff for gate-triggered items using matched CPK/CGM support."""
    if minimum_samples_per_site is not None and (isinstance(minimum_samples_per_site, bool) or
        not isinstance(minimum_samples_per_site, int) or minimum_samples_per_site < 2):
        raise ValueError("minimum_samples_per_site 必须是至少 2 的整数")
    config = _mmsa_config(catalog)
    with measure('mmsa.support'):
        local_cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog,
            part_indices=part_indices, sites=sites, test_columns=test_columns,
            _selection_only=bin1_cpk_report is not None)
        cpk_support = bin1_cpk_report if bin1_cpk_report is not None else local_cpk
        same_cpk = _validate_support(cpk_support, local_cpk["source"], "release_bin1_cpk",
                                     allow_cross_source_cpk, catalog.catalog_hash)
        if cgm_report is not None:
            require_current_cg_policy(cgm_report)
            cgm_support = cgm_report
        else:
            cgm_support = build_release_cgm_report(frame, metadata, catalog=catalog,
                part_indices=part_indices, sites=sites, test_columns=test_columns,
                minimum_samples_per_site=minimum_samples_per_site,
                bin1_cpk_report=cpk_support, allow_cross_source_cpk=True, _metrics_only=True)
        same_cgm = _validate_support(cgm_support, local_cpk["source"], "release_cgm",
                                     allow_cross_source_cgm, catalog.catalog_hash)
    with measure('mmsa.selection'):
        selected = frame.loc[frame["__part_index"].isin(local_cpk["selection"]["selected_part_indices"])]
        chosen = None if test_columns is None else set(test_columns)
    groups = _group_events(selected, local_cpk["source"], chosen)
    with measure('mmsa.match_and_sites'):
        cpk_match = _matcher(cpk_support.get("items") or [], same_cpk, list(groups.values()))
        cgm_match = _matcher(cgm_support.get("items") or [], same_cgm, list(groups.values()))
        condition_sites: dict[str, set[int | None]] = {}
        for part in selected[["__conditions_json", "SITE"]].to_dict("records"):
            key = json.dumps(json.loads(part["__conditions_json"]), sort_keys=True, ensure_ascii=False)
            condition_sites.setdefault(key, set()).add(_flag(part.get("SITE")))
    with measure('mmsa.items'):
        with prepared_groups(sorted(groups.items())) as entries:
            items = [_calculate(group, condition_sites[json.dumps(group["conditions"], sort_keys=True,
                     ensure_ascii=False)], cpk_match(group["group_id"], group),
                 cgm_match(group["group_id"], group), config)
                for key, group in entries]
    count('mmsa.selected_records', len(selected))
    count('mmsa.item_groups', len(items))
    with measure('mmsa.rules'):
        evaluable = {item["group_id"] for item in items if item["rule_status"] is not None}
        facts = {"context": {"analysis_type": "release_mmsa"}, "results": {"mmsa": {"test_items": [
            {"test_item_id": item["group_id"], "test_name": item["test_name"], "rule_id": item["rule_id"],
             "status": item["status"],
             "shift_sigma": item["shift_sigma"] if item["group_id"] in evaluable else None,
             "shift_spec_pct": item["shift_spec_pct"] if item["group_id"] in evaluable else None,
             "var_ratio": item["var_ratio"] if item["group_id"] in evaluable else None,
             "overall_bin1_cpk": item["overall_bin1_cpk"], "cgm_min_cg": item["cgm_min_cg"]}
            for item in items]}}}
        findings = [finding.to_dict() for finding in evaluate_mmsa(facts, catalog)] if items else []
    warnings = [w for w in local_cpk["warnings"] if "最小样本" not in w]
    if minimum_samples_per_site is None:
        warnings.append("未配置各 site 的最小样本要求，样本充分性尚未评估。")
    if any(item["overall_bin1_cpk"] is None for item in items):
        warnings.append("部分测项无匹配且可评估的 bin1 CPK，触发判定仅能按 Cg 进行。")
    if any(item["cgm_min_cg"] is None for item in items):
        warnings.append("部分测项无匹配且可计算的 CGM 结果，跳过重复性分流，直接进行 Site Diff 判定。")
    if any(item["var_severity"] == "RED" and item["var_ratio"] is None for item in items):
        warnings.append("部分测项各 Site 标准差的中位数为零，但最大标准差非零，按 Site 标准差异常处理。")
    return {"report_type": "release_mmsa", "source": local_cpk["source"],
        "bin1_cpk_source": cpk_support.get("source"), "cgm_source": cgm_support.get("source"),
        "selection": local_cpk["selection"], "bin1_cpk_selection": cpk_support.get("selection"),
        "cgm_selection": cgm_support.get("selection"),
        "policy": {"population": "all_selected_valid_results", "ddof": 1,
            "minimum_samples_per_site": minimum_samples_per_site,
            "computational_minimum_per_site": 2,
            "limit_policy": local_cpk["policy"]["limit_policy"],
            "shift_spec_requires": "two_sided_effective_limits",
            "cpk_gate": config["gate"], "cg_pass_threshold": config["cg_pass"],
            "local_fault_max_sites": config["local_fault_max_sites"]},
        "parameters": {name: dict(band) for name, band in config["bands"].items()},
        "rules": {"catalog_hash": catalog.catalog_hash, "rules": [
            {"rule_id": rule.rule_id, "version": rule.version, "status": rule.status,
             "reason": rule.spec.get("reason")} for rule_id, rule in sorted(config["rules"].items())]},
        "status": "COMPLETE" if items else "EMPTY", "items": items,
        "unexecuted_tests": local_cpk["unexecuted_tests"],
        "summary": {"item_group_count": len(items), "status_counts": dict(Counter(i["status"] for i in items)),
                    "unexecuted_test_count": len(local_cpk["unexecuted_tests"])},
        "risks": [item for item in items if item["status"] in {"FAIL", "RISK"}],
        "findings": findings, "warnings": warnings,
        "release_decision": "ENGINEER_CONFIRMATION_REQUIRED"}


def analyze_release_mmsa(path: str | Path, *, rule_dir: str | Path | None = None,
                         bin1_cpk_report_path: str | Path | None = None,
                         cgm_report_path: str | Path | None = None, **selection) -> dict:
    frame, metadata = read_release_wide_all(path)
    for report_path, key, name in ((bin1_cpk_report_path, "bin1_cpk_report", "bin1 CPK"),
                                   (cgm_report_path, "cgm_report", "CGM")):
        if report_path is not None:
            if key in selection:
                raise ValueError(f"只能提供一个 {name} 报告来源")
            selection[key] = json.loads(Path(report_path).read_text(encoding="utf-8"))
    return build_release_mmsa_report(frame, metadata, catalog=load_analysis_rules(rule_dir), **selection)
