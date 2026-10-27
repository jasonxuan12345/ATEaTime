"""Release CGM adapter using the existing site report and YAML evaluator."""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
from typing import Any

import pandas as pd
import numpy as np

from deepyield.compute.release_cpk import build_release_bin1_cpk_report, _flag, _group_events
from deepyield.compute.release_stdf import read_release_wide_all
from deepyield.compute.release_numeric import site_metrics, cg_number, INFINITE_CG, CG_TOLERANCE_FRACTION
from deepyield.compute.release_run_cache import cached_report
from deepyield.compute.release_control import checkpoint, count, measure, timed
from deepyield.compute.release_sample_batch import collect_samples, prepared_groups
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.rules.conditions import as_float, compare
from deepyield.rules.evaluators.cgm_eval import evaluate_cgm
from deepyield.rules.loader import RuleCatalog, load_analysis_rules

_LABELS = {"PASS": "通过", "RISK": "风险", "FAIL": "未达标",
           "INSUFFICIENT_DATA": "数据不足", "NOT_CALCULABLE": "无法计算",
           "NOT_EVALUATED": "尚未评估", "REVIEW_REQUIRED": "需复核",
           "NOT_APPLICABLE": "不适用"}


def cross_source_identity(item: dict) -> str | None:
    """Match cross-file tests by exact Test ID and Test Name only."""
    if item.get("test_num") is None or not item.get("test_name"):
        return None
    return json.dumps({"test_num": item["test_num"], "test_name": item["test_name"]},
                      sort_keys=True, ensure_ascii=False)


def _site(value: Any) -> int | None:
    return _flag(value)


def _classify(item: dict, catalog: RuleCatalog) -> None:
    rules = [catalog.rule(f"CGM-00{i}") for i in range(1, 5)]
    conditions = [r.spec.get("condition") or {} for r in rules]
    expected = [("bin1_cpk", "GE"), ("cg", "LT"), ("cg", "GE_LT"), ("cg", "GE")]
    if any(c.get("metric") != metric or c.get("operator") != operator
           for c, (metric, operator) in zip(conditions, expected)):
        item.update(status="NOT_EVALUATED", reason="UNSUPPORTED_RULE_CONDITION")
        return
    thresholds = [as_float(c.get("lower" if c.get("operator") == "GE_LT" else "threshold"))
                  for c in conditions]
    upper = as_float(conditions[2].get("upper"))
    if any(value is None for value in thresholds) or upper is None:
        item.update(status="NOT_EVALUATED", reason="MISSING_RULE_THRESHOLD")
        return
    cpk = item["supported_by_bin1_cpk"]
    if cpk is not None and compare("GE", cpk, thresholds[0]):
        hits = [rules[0]]
        item["cpk_override_applied"] = True
    else:
        hits = [rule for rule, threshold, condition in zip(rules[1:], thresholds[1:], conditions[1:])
                if compare(condition["operator"], cg_number(item["cg"]), threshold, upper)]
    if len(hits) != 1 or hits[0].status not in {"PASS", "RISK", "FAIL"}:
        item.update(status="NOT_EVALUATED", reason="RULE_BANDS_AMBIGUOUS_OR_MISSING")
        return
    rule = hits[0]
    item.update(status=rule.status, rule_status=rule.status, rule_id=rule.rule_id,
                reason=rule.spec.get("reason"), threshold_condition=rule.spec["condition"])


@timed('cgm.calculate')
def _calculate(group: dict, expected_sites: set[int | None], minimum: int | None,
               cpk_item: dict | None, catalog: RuleCatalog, metrics_only=False) -> dict:
    checkpoint()
    spec = group["spec"]
    # Release limit policy: unset limits arrive as None from the parser; a
    # written 0.0 is a real limit (the engine opt-out mirrors this).
    low, high = as_float(spec["low_limit"]), as_float(spec["high_limit"])
    rows, refs, excluded = [], [], Counter()
    with measure('cgm.samples'):
        batch = collect_samples(group, 'cgm', _flag, keep_refs=not metrics_only)
        if batch is not None:
            refs, excluded = batch.refs, batch.excluded
        else:
            for part, event in group["events"]:
                flag = _flag(part.get("PART_FLAG"))
                if flag is None or _classify_part_flag(flag)[0] not in {"PASS", "FAIL"}:
                    excluded["INVALID_PART_RESULT"] += 1
                    continue
                site = _site(part.get("SITE"))
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
                rows.append({"SITE": site, "value": value})
                refs.append({"part_index": int(part["__part_index"]),
                             "record_sequence": event["record_sequence"], "site": site})
    count('cgm.examined_samples', len(group['events']))
    sample_count = len(batch.values) if batch is not None else len(refs)
    count('cgm.valid_samples', sample_count)
    with measure('cgm.statistics'):
        by_site = {} if batch is None else batch.by_site
        for entry in rows:
            by_site.setdefault(entry['SITE'], []).append(entry['value'])
        site_values = {site: site_metrics(values, low, high) for site, values in by_site.items()}
        site_items = []
        for site in sorted(expected_sites, key=lambda value: -1 if value is None else value):
            values = by_site.get(site, []) if site is not None else []
            raw_std, raw_cg = site_values.get(site, (None, None))
            cg = INFINITE_CG if raw_cg == INFINITE_CG else as_float(raw_cg)
            n, std = len(values), as_float(raw_std)
            data = np.asarray(values, dtype=np.float64)
            outside = np.zeros(n, dtype=bool)
            if low is not None:
                outside |= data < low
            if high is not None:
                outside |= data > high
            if site is None:
                status, reason = "NOT_EVALUATED", "MISSING_OR_INVALID_SITE"
            elif n < max(2, minimum or 2):
                status, reason = "INSUFFICIENT_DATA", "TOO_FEW_VALID_SAMPLES"
            elif std is None:
                status, reason = "NOT_CALCULABLE", "ZERO_OR_UNAVAILABLE_STD"
            elif cg is None:
                status, reason = "NOT_CALCULABLE", "NO_EFFECTIVE_TWO_SIDED_LIMIT"
            else:
                status, reason = "CALCULATED", None
            site_items.append({"site": site, "n": n, "std": std, "cg": cg,
                               "mean": as_float(data.mean()) if n else None,
                               "min": as_float(data.min()) if n else None,
                               "max": as_float(data.max()) if n else None,
                               "fail_count": int(outside.sum()) if n else None,
                               "status": status, "reason": reason,
                               "sample_adequacy_status": "NOT_EVALUATED" if minimum is None else (
                                   "PASS" if n >= minimum else "INSUFFICIENT_DATA")})
        if batch is not None:
            # Same validity policy as Site Diff. Keep only scalar moments;
            # module-specific Cg/status/adequacy decisions remain independent.
            group['events'].site_statistics = {s['site']: (s['n'], s['mean'], s['std']) for s in site_items}
    # Classify the available sites; retain unavailable sites for explicit display.
    usable_cg = [site["cg"] for site in site_items if site["status"] == "CALCULATED"]
    item = {key: group[key] for key in ("group_id", "column", "test_num", "test_name",
                                       "record_type", "result_index", "conditions")}
    support = as_float(cpk_item.get("cpk")) if cpk_item and cpk_item.get("status") in {"PASS", "RISK", "FAIL"} else None
    item.update(unit=spec["unit"], lsl=as_float(spec["low_limit"]), usl=as_float(spec["high_limit"]),
                effective_lsl=low, effective_usl=high, n=sample_count, cg=min(usable_cg, key=cg_number) if usable_cg else None,
                sites=site_items, selected_execution_count=len(group["events"]),
                sample_refs=[] if metrics_only else refs, excluded_counts=dict(excluded), supported_by_bin1_cpk=support,
                bin1_cpk_status=cpk_item.get("status") if cpk_item else "NOT_EVALUATED",
                cpk_override_applied=False, rule_id=None, rule_status=None, reason=None)
    if group["record_type"] == "FTR":
        item.update(status="NOT_APPLICABLE", reason="FUNCTIONAL_TEST", cg=None)
    elif low is None or high is None:
        item.update(status="NOT_CALCULABLE", reason="NO_EFFECTIVE_TWO_SIDED_LIMIT", cg=None)
    elif low >= high:
        item.update(status="NOT_CALCULABLE", reason="INVALID_LIMIT_ORDER", cg=None)
        for site in site_items:
            site.update(cg=None, status="NOT_CALCULABLE", reason="INVALID_LIMIT_ORDER")
    elif not usable_cg:
        item.update(status="NOT_EVALUATED", reason="INCOMPLETE_SITE_COVERAGE")
    elif item["cg"] is None:
        item.update(status="NOT_CALCULABLE", reason="MISSING_CG")
    else:
        _classify(item, catalog)
    item["status_label"] = _LABELS[item["status"]]
    count('cgm.status.' + item['status'])
    return item


@cached_report
@timed('cgm.report')
def build_release_cgm_report(frame: pd.DataFrame, metadata: dict, *, catalog: RuleCatalog,
                             part_indices: list[int] | None = None, sites: list[int] | None = None,
                             test_columns: list[str] | None = None,
                             minimum_samples_per_site: int | None = None,
                             bin1_cpk_report: dict | None = None,
                             allow_cross_source_cpk: bool = False,
                             _metrics_only: bool = False) -> dict:
    """Use all valid selected measurements and provenance-matched BIN1 support."""
    if minimum_samples_per_site is not None and (isinstance(minimum_samples_per_site, bool) or
        not isinstance(minimum_samples_per_site, int) or minimum_samples_per_site < 2):
        raise ValueError("minimum_samples_per_site 必须是至少 2 的整数")
    with measure('cgm.support'):
        local_cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog,
            part_indices=part_indices, sites=sites, test_columns=test_columns,
            _selection_only=bin1_cpk_report is not None)
        support_report = local_cpk if bin1_cpk_report is None else bin1_cpk_report
        same_source = support_report.get("source") == local_cpk["source"]
        if support_report.get("report_type") != "release_bin1_cpk" or (not same_source and not allow_cross_source_cpk):
            raise ValueError("bin1 CPK 报告必须来自同一份 Release Parquet")
        if (support_report.get("rules") or {}).get("catalog_hash") != catalog.catalog_hash:
            raise ValueError("bin1 CPK 报告的规则版本不匹配，请重新计算")
        cpk_items = support_report.get("items") or []
        cpk_by_id = {item["group_id"]: item for item in cpk_items}
        if len(cpk_by_id) != len(cpk_items):
            raise ValueError("bin1 CPK 报告包含重复测项分组")
        cross_support: dict[str, list[dict]] = {}
        if not same_source:
            for item in cpk_items:
                identity = cross_source_identity(item)
                if identity:
                    cross_support.setdefault(identity, []).append(item)
        def support_for(key, group):
            if same_source:
                return cpk_by_id.get(key)
            identity = cross_source_identity(group)
            if group_counts[identity] != 1:
                return None
            matches = cross_support.get(identity, [])
            return matches[0] if len(matches) == 1 else None
    with measure('cgm.selection'):
        selected = frame.loc[frame["__part_index"].isin(local_cpk["selection"]["selected_part_indices"])]
        chosen = None if test_columns is None else set(test_columns)
    groups = _group_events(selected, local_cpk["source"], chosen)
    with measure('cgm.match_and_sites'):
        group_counts = Counter(cross_source_identity(group) for group in groups.values())
        requested_sites = set(sites) if sites is not None else None
        condition_sites = {}
        for part in selected[["__conditions_json", "SITE"]].to_dict("records"):
            key = json.dumps(json.loads(part["__conditions_json"]), sort_keys=True, ensure_ascii=False)
            condition_sites.setdefault(key, set()).add(_site(part.get("SITE")))
    with measure('cgm.items'):
        with prepared_groups(sorted(groups.items())) as entries:
            items = [_calculate(group, requested_sites if requested_sites is not None else condition_sites[json.dumps(group["conditions"], sort_keys=True,
                     ensure_ascii=False)], minimum_samples_per_site, support_for(key, group), catalog, _metrics_only)
                 for key, group in entries]
    count('cgm.selected_records', len(selected))
    count('cgm.item_groups', len(items))
    with measure('cgm.rules'):
        evaluable = {item["group_id"] for item in items if item["rule_status"] is not None}
        facts = {"context": {"analysis_type": "release_cgm"}, "results": {"cgm": {"test_items": [
            {"test_item_id": item["group_id"], "test_name": item["test_name"],
             "cg": item["cg"] if item["group_id"] in evaluable else None,
             "supported_by_bin1_cpk": item["supported_by_bin1_cpk"]} for item in items]}}}
        findings = [f.to_dict() for f in evaluate_cgm(facts, catalog)] if items else []
    warnings = [w for w in local_cpk["warnings"] if "最小样本" not in w]
    if minimum_samples_per_site is None:
        warnings.append("未配置各 site 的最小样本要求，样本充分性尚未评估。")
    if any(item["supported_by_bin1_cpk"] is None for item in items):
        warnings.append("部分测项无匹配且可评估的 bin1 CPK，按 CG 单独判断，不启用 CPK 接受规则。")
    report_sites = sorted(requested_sites if requested_sites is not None else
                          {_site(site) for site in selected["SITE"]} - {None})
    unexecuted = [{**item, "sites": [{"site": site, "n": 0, "cg": None,
                    "status": "INSUFFICIENT_DATA"} for site in report_sites]}
                  for item in local_cpk["unexecuted_tests"]]
    return {"report_type": "release_cgm", "source": local_cpk["source"],
            "test_catalog": [test for test in metadata["test_catalog"]
                             if chosen is None or test["column"] in chosen],
            "sites": report_sites,
            "bin1_cpk_source": support_report.get("source"),
            "selection": local_cpk["selection"], "bin1_cpk_selection": support_report.get("selection"),
            "policy": {"population": "all_selected_valid_results", "ddof": 1,
                       "cg_tolerance_fraction": CG_TOLERANCE_FRACTION,
                       "minimum_samples_per_site": minimum_samples_per_site,
                       "computational_minimum_per_site": 2,
                       "limit_policy": local_cpk["policy"]["limit_policy"],
                       "cpk_high_check": "NOT_EVALUATED"},
            "rules": {"catalog_hash": catalog.catalog_hash, "rules": [
                {"rule_id": rule.rule_id, "version": rule.version, "condition": rule.spec.get("condition"),
                 "status": rule.status} for rule in (catalog.rule(f"CGM-00{i}") for i in range(1, 5))]},
            "status": "COMPLETE" if items else "EMPTY", "items": items,
            "unexecuted_tests": unexecuted,
            "summary": {"item_group_count": len(items), "status_counts": dict(Counter(i["status"] for i in items)),
                        "unexecuted_test_count": len(local_cpk["unexecuted_tests"])},
            "risks": [i for i in items if i["status"] in {"FAIL", "RISK"}],
            "findings": findings, "warnings": warnings,
            "release_decision": "ENGINEER_CONFIRMATION_REQUIRED"}


def analyze_release_cgm(path: str | Path, *, rule_dir: str | Path | None = None,
                        bin1_cpk_report_path: str | Path | None = None, **selection) -> dict:
    frame, metadata = read_release_wide_all(path)
    if bin1_cpk_report_path is not None:
        if "bin1_cpk_report" in selection:
            raise ValueError("只能提供一个 bin1 CPK 报告来源")
        selection["bin1_cpk_report"] = json.loads(Path(bin1_cpk_report_path).read_text(encoding="utf-8"))
    return build_release_cgm_report(frame, metadata, catalog=load_analysis_rules(rule_dir), **selection)
