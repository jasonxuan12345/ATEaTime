"""Release single-file adapter around the existing BIN1 report and rule engine."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from deepyield.compute.release_stdf import COMPUTE_SCHEMAS, read_release_wide_all
from deepyield.compute.release_numeric import cpk_metrics
from deepyield.compute.release_run_cache import cached_report, grouped
from deepyield.compute.release_control import checkpoint, count, measure, timed
from deepyield.compute.release_sample_batch import collect_samples, prepared_groups
from deepyield.compute.release_group_batch import build_compact_groups
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.rules.conditions import as_float, band_matches, parse_band
from deepyield.rules.evaluators.cpk_eval import evaluate_cpk
from deepyield.rules.loader import RuleCatalog, load_analysis_rules

_LABELS = {"PASS": "通过", "RISK": "风险", "FAIL": "未达标",
           "INSUFFICIENT_DATA": "数据不足", "NOT_CALCULABLE": "无法计算",
           "REVIEW_REQUIRED": "需复核", "NOT_EVALUATED": "尚未评估",
           "NOT_APPLICABLE": "不适用"}


def _flag(value: Any) -> int | None:
    number = as_float(value)
    return int(number) if number is not None and number.is_integer() and 0 <= number <= 255 else None


def _ids(values, field: str) -> set[int] | None:
    if values is None:
        return None
    result = set()
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError(f"{field} 必须是非负整数列表")
        result.add(value)
    return result


def _cpk_rules(catalog: RuleCatalog):
    rules = [catalog.rule(f"CPK-00{i}") for i in range(1, 4)]
    for rule in rules:
        parse_band(str(rule.spec.get("condition") or ""))
        if rule.status not in {"PASS", "RISK", "FAIL"}:
            raise ValueError(f"不支持的 CPK 规则状态：{rule.status}")
    return rules


def _classify(item: dict, rules) -> None:
    hits = [rule for rule in rules if band_matches(str(rule.spec["condition"]), item["cpk"])]
    if len(hits) != 1:
        item.update(status="NOT_EVALUATED", reason="RULE_BANDS_AMBIGUOUS_OR_MISSING")
        return
    rule = hits[0]
    item.update(status=rule.status, rule_id=rule.rule_id,
                threshold_band=str(rule.spec["condition"]), rule_status=rule.status)


@timed('cpk.calculate')
def _calculate(group: dict, minimum_samples: int | None, rules) -> dict:
    checkpoint()
    events = group["events"]
    spec = group["spec"]
    low, high = as_float(spec["low_limit"]), as_float(spec["high_limit"])
    # Release limit policy: unset limits arrive as None from the parser; a
    # written 0.0 is a real limit (the engine opt-out mirrors this).
    effective_low, effective_high = low, high
    excluded = Counter()
    values, sample_refs = [], []
    with measure('cpk.samples'):
        batch = collect_samples(group, 'cpk', _flag)
        if batch is not None:
            values, sample_refs, excluded = batch.values, batch.refs, batch.excluded
        else:
            for part, event in events:
                if as_float(part.get("SBIN")) != 1:
                    excluded["NOT_BIN1"] += 1
                    continue
                part_flag = _flag(part.get("PART_FLAG"))
                if part_flag is None or _classify_part_flag(part_flag)[0] != "PASS":
                    excluded["INVALID_OR_CONFLICTING_PART_RESULT"] += 1
                    continue
                tf, pf = _flag(event.get("test_flag")), _flag(event.get("param_flag"))
                value = as_float(event.get("result"))
                if group["record_type"] != "FTR" and (
                    tf is None or pf is None or tf & 0x3F or pf & 0x27 or
                    event.get("result_valid") is not True or value is None
                ):
                    excluded["INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS"] += 1
                    continue
                if group["record_type"] == "FTR":
                    continue
                values.append(value)
                sample_refs.append({"part_index": int(part["__part_index"]),
                                    "record_sequence": event["record_sequence"], "site": part.get("SITE")})
    count('cpk.examined_samples', len(events))
    count('cpk.valid_samples', len(values))
    n = len(values)
    # Population and validity have already been resolved above.
    with measure('cpk.numeric'):
        metrics = cpk_metrics(values, low, high)
    item = {key: group[key] for key in ("group_id", "column", "test_num", "test_name",
                                       "record_type", "result_index", "conditions")}
    item.update(unit=spec["unit"], lsl=low, usl=high,
                effective_lsl=effective_low, effective_usl=effective_high,
                n=n, mean=as_float(metrics["Mean"]), std=as_float(metrics["Std"]),
                fail_count=as_float(metrics["FailCount"]), cpk=as_float(metrics["cpk"]),
                cpk_cp=as_float(metrics["cpk/cp"]), sample_refs=sample_refs,
                selected_execution_count=len(events), excluded_counts=dict(excluded),
                rule_id=None, rule_status=None, reason=None,
                sample_adequacy_status="NOT_EVALUATED" if minimum_samples is None else (
                    "PASS" if n >= minimum_samples else "INSUFFICIENT_DATA"))
    if group["record_type"] == "FTR":
        item.update(status="NOT_APPLICABLE", reason="FUNCTIONAL_TEST")
    elif effective_low is None and effective_high is None:
        item.update(status="NOT_CALCULABLE", reason="NO_EFFECTIVE_LIMIT")
    elif effective_low is not None and effective_high is not None and effective_low >= effective_high:
        item.update(status="NOT_CALCULABLE", reason="INVALID_LIMIT_ORDER", cpk=None, cpk_cp=None)
    elif n < max(2, minimum_samples or 2):
        item.update(status="INSUFFICIENT_DATA", reason="TOO_FEW_VALID_SAMPLES")
    elif item["std"] is None or item["std"] == 0 or item["cpk"] is None:
        item.update(status="NOT_CALCULABLE", reason="ZERO_OR_UNAVAILABLE_STD")
    else:
        _classify(item, rules)
    item["status_label"] = _LABELS[item["status"]]
    count('cpk.status.' + item['status'])
    return item


def _group_events(selected: pd.DataFrame, source: dict, chosen_tests: set[str] | None) -> dict:
    return grouped(selected, source, chosen_tests, lambda: _build_groups(selected, source, chosen_tests))


def _build_groups(selected: pd.DataFrame, source: dict, chosen_tests: set[str] | None) -> dict:
    groups: dict[str, dict] = {}
    identities = {}
    conditions_cache = {}
    columns = ["__part_index", "SITE", "SBIN", "PART_FLAG", "__conditions_json", "__test_records"]
    with measure('groups.part_records'):
        part_records = selected[columns].to_dict("records")
    compact = build_compact_groups(part_records, source, chosen_tests)
    if compact is not None:
        return compact
    for part in part_records:
        checkpoint()
        encoded_conditions = part["__conditions_json"]
        if encoded_conditions not in conditions_cache:
            conditions_cache[encoded_conditions] = json.loads(encoded_conditions)
        conditions = conditions_cache[encoded_conditions]
        with measure('groups.latest'):
            latest = {}
            events = part["__test_records"]
            if hasattr(events, 'latest'):
                latest = events.latest(chosen_tests)
            for index, event in enumerate(() if hasattr(events, 'latest') else events):
                if index % 4096 == 0:
                    checkpoint()
                column = event["column"]
                if chosen_tests is None or column in chosen_tests:
                    # Never rely on the Arrow list's ordering to select the last event.
                    if column not in latest or event["record_sequence"] > latest[column]["record_sequence"]:
                        latest[column] = event
        with measure('groups.identities'):
            for column, event in latest.items():
                spec = {key: event.get(key) for key in ("unit", "low_limit", "high_limit")}
                lookup = (column, event['test_num'], event.get('name'), event['record_type'],
                          event.get('result_index'), *spec.values(), encoded_conditions)
                if lookup in identities:
                    identities[lookup]['events'].append((part, event))
                    continue
                identity = {"column": column, "test_num": event["test_num"],
                            "test_name": event.get("name"), "record_type": event["record_type"],
                            "result_index": event.get("result_index"), "spec": spec,
                            "conditions": conditions, "source": source}
                group_id = hashlib.sha256(json.dumps(identity, sort_keys=True,
                    ensure_ascii=False, allow_nan=False).encode("utf-8")).hexdigest()[:24]
                group = groups.setdefault(group_id, {**identity, "group_id": group_id, "events": []})
                identities[lookup] = group
                group["events"].append((part, event))
        count('groups.input_events', len(events))
        count('groups.compact_parts' if hasattr(events, 'latest') else 'groups.dict_parts')
    count('groups.built', len(groups))
    return groups


@cached_report
@timed('cpk.report')
def build_release_bin1_cpk_report(
    frame: pd.DataFrame, metadata: dict[str, Any], *, catalog: RuleCatalog,
    part_indices: list[int] | None = None, sites: list[int] | None = None,
    test_columns: list[str] | None = None, minimum_samples: int | None = None,
    _selection_only: bool = False,
) -> dict[str, Any]:
    """Compute from explicitly selected execution rows; never infer retest identity.

    The last execution of each test within a selected PRR is used, matching wide
    semantics. Select it before rejecting invalid values: no fallback to an older
    result. Source/conditions/specification variants never share a sample group.
    """
    with measure('cpk.selection'):
        if metadata.get("schema_version") not in COMPUTE_SCHEMAS:
            raise ValueError("需要 Release 单文件 Parquet 的内置元数据")
        required = {"__part_index", "SBIN", "PART_FLAG", "SITE", "__test_records", "__conditions_json"}
        if not required.issubset(frame.columns):
            raise ValueError(f"Release 数据缺少字段：{sorted(required - set(frame.columns))}")
        if frame["__part_index"].isna().any() or frame["__part_index"].duplicated().any():
            raise ValueError("测试记录编号缺失或重复，无法确定所选记录")
        if any(isinstance(value, bool) or as_float(value) is None or
               not as_float(value).is_integer() or as_float(value) < 0
               for value in frame["__part_index"]):
            raise ValueError("测试记录编号必须是非负整数")
        if minimum_samples is not None and (isinstance(minimum_samples, bool) or
            not isinstance(minimum_samples, int) or minimum_samples < 2):
            raise ValueError("minimum_samples 必须是至少 2 的整数")
        rules = _cpk_rules(catalog)
        parts, site_set = _ids(part_indices, "part_indices"), _ids(sites, "sites")
        known_parts = set(frame["__part_index"].astype(int))
        if parts is not None and not parts.issubset(known_parts):
            raise ValueError(f"所选测试记录不存在：{sorted(parts - known_parts)}")
        known_sites = set(pd.to_numeric(frame.get("SITE"), errors="coerce").dropna().astype(int))
        if site_set is not None and not site_set.issubset(known_sites):
            raise ValueError(f"所选 site 不存在：{sorted(site_set - known_sites)}")
        known_tests = {str(test["column"]) for test in metadata["test_catalog"]}
        chosen_tests = None if test_columns is None else set(test_columns)
        if chosen_tests is not None and not chosen_tests.issubset(known_tests):
            raise ValueError(f"所选测项不存在：{sorted(chosen_tests - known_tests)}")
        selected = frame
        if parts is not None:
            selected = selected.loc[selected["__part_index"].isin(parts)]
        if site_set is not None:
            selected = selected.loc[selected["SITE"].isin(site_set)]
    source = {"source_file": metadata.get("source_file"),
              "source_sha256": metadata.get("source_sha256")}
    groups = _group_events(selected, source, chosen_tests)
    with measure('cpk.items'):
        if _selection_only:
            items = list(groups.values())
        else:
            with prepared_groups(sorted(groups.items())) as entries:
                items = [_calculate(group, minimum_samples, rules) for key, group in entries]
    count('cpk.selection_only' if _selection_only else 'cpk.full_reports')
    count('cpk.selected_records', len(selected))
    count('cpk.item_groups', len(items))
    observed_tests = {item["column"] for item in items}
    requested_tests = known_tests if chosen_tests is None else chosen_tests
    unexecuted_tests = [{"column": column, "status": "INSUFFICIENT_DATA",
                         "status_label": "数据不足", "reason": "NO_SELECTED_EXECUTION",
                         "n": 0, "cpk": None, "rule_status": None}
                        for column in sorted(requested_tests - observed_tests)]
    if _selection_only:
        warnings = list((metadata.get("stdf_metadata") or {}).get("warnings") or [])
        if any(not group["spec"]["unit"] and group["record_type"] != "FTR" for group in groups.values()):
            warnings.append("单位缺失")
        if any(not item["conditions"].get(key) for item in items for key in (
            "part_type", "job_name", "job_revision", "test_code", "test_temperature")):
            warnings.append("部分产品、程序版本、阶段或温度信息缺失，需核对测试条件。")
        return {"source": source, "selection": {
            "part_indices": sorted(parts) if parts is not None else None,
            "sites": sorted(site_set) if site_set is not None else None,
            "test_columns": sorted(chosen_tests) if chosen_tests is not None else None,
            "selected_part_indices": [int(v) for v in selected["__part_index"]],
            "selected_record_count": len(selected), "attempt_policy": "selected_execution_records",
            "test_execution_policy": "last_per_test_in_selected_record"},
            "policy": {"limit_policy": "release-explicit-limits-null-is-unset"},
            "unexecuted_tests": unexecuted_tests, "warnings": warnings}
    result = {"context": {"analysis_type": "release_bin1_cpk"},
              "results": {"cpk": {"test_items": [
                  {"test_item_id": item["group_id"], "test_name": item["test_name"],
                   "lsl": item["effective_lsl"], "usl": item["effective_usl"],
                   "bin1": {"n": item["n"], "cpk": item["cpk"]}}
                  for item in items if item["rule_status"] is not None]}}}
    with measure('cpk.rules'):
        findings = [finding.to_dict() for finding in evaluate_cpk(result, catalog)] if result["results"]["cpk"]["test_items"] else []
    warnings = list((metadata.get("stdf_metadata") or {}).get("warnings") or [])
    if any(not group["spec"]["unit"] and group["record_type"] != "FTR" for group in groups.values()):
        warnings.append("单位缺失")
    if minimum_samples is None:
        warnings.append("未配置最小样本要求，样本充分性尚未评估；分类仅反映 CPK 规则。")
    if any(not item["conditions"].get(key) for item in items for key in (
        "part_type", "job_name", "job_revision", "test_code", "test_temperature")):
        warnings.append("部分产品、程序版本、阶段或温度信息缺失，需核对测试条件。")
    counts = dict(Counter(item["status"] for item in items))
    return {"report_type": "release_bin1_cpk", "source": source,
            "selection": {"part_indices": sorted(parts) if parts is not None else None,
                          "sites": sorted(site_set) if site_set is not None else None,
                          "test_columns": sorted(chosen_tests) if chosen_tests is not None else None,
                          "selected_part_indices": [int(v) for v in selected["__part_index"]],
                          "selected_record_count": len(selected),
                          "attempt_policy": "selected_execution_records",
                          "test_execution_policy": "last_per_test_in_selected_record"},
            "policy": {"bin_column": "SBIN", "bin_value": 1, "ddof": 1,
                       "limit_policy": "release-explicit-limits-null-is-unset",
                       "minimum_samples": minimum_samples, "computational_minimum": 2,
                       "cpk_high_check": "NOT_EVALUATED"},
            "rules": {"catalog_hash": catalog.catalog_hash,
                      "bands": [{"rule_id": r.rule_id, "version": r.version,
                                 "condition": r.spec["condition"], "status": r.status} for r in rules]},
            "status": "COMPLETE" if items else "EMPTY", "items": items,
            "unexecuted_tests": unexecuted_tests,
            "summary": {"item_group_count": len(items), "status_counts": counts,
                        "unexecuted_test_count": len(unexecuted_tests)},
            "risks": [item for item in items if item["status"] in {"FAIL", "RISK"}],
            "findings": findings, "warnings": warnings,
            "release_decision": "ENGINEER_CONFIRMATION_REQUIRED"}


def analyze_release_bin1_cpk(path: str | Path, *, rule_dir: str | Path | None = None,
                             **selection) -> dict[str, Any]:
    frame, metadata = read_release_wide_all(path)
    return build_release_bin1_cpk_report(frame, metadata,
                                       catalog=load_analysis_rules(rule_dir), **selection)
