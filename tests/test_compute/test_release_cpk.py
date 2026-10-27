"""Release adapter must agree with the formal report and configured rule bands."""
from copy import deepcopy
from dataclasses import replace
import json

import pandas as pd
import pytest

from deepyield.compute.release_cpk import build_release_bin1_cpk_report, analyze_release_bin1_cpk
from deepyield.compute.release_stdf import SCHEMA_VERSION, convert_stdf_to_wide_all
from deepyield.compute.report_engine import generate_bin1_cpk_report
from deepyield.rules.loader import load_analysis_rules


@pytest.fixture
def catalog():
    return load_analysis_rules()


def dataset(values=(4.0, 5.0, 6.0), low=1.0, high=9.0, unit="V"):
    conditions = {"part_type": "TEST_PRODUCT", "job_name": "TEST_PROGRAM",
                  "job_revision": "A", "test_code": "FT", "test_temperature": "25"}
    test = {"column": "1001_VDD", "test_num": 1001, "name": "VDD", "record_type": "PTR",
            "unit": unit, "low_limit": low, "high_limit": high, "result_index": None}
    rows = []
    for i, value in enumerate(values, 1):
        rows.append({"__part_index": i, "Name": "SAME_DISPLAY_NAME", "SBIN": 1,
            "PART_FLAG": 0, "SITE": i % 2, "__conditions_json": json.dumps(conditions),
            "__test_records": [{**test, "test_flag": 0, "param_flag": 0,
                "result_valid": True, "result": value, "record_sequence": i * 10}]})
    metadata = {"schema_version": SCHEMA_VERSION, "source_file": "fixture.stdf",
                "source_sha256": "a" * 64, "test_catalog": [test]}
    return pd.DataFrame(rows), metadata


def build(catalog, *args, **kwargs):
    frame, metadata = dataset(*args)
    return build_release_bin1_cpk_report(frame, metadata, catalog=catalog, **kwargs)


def test_result_matches_existing_formal_report(catalog):
    frame, metadata = dataset()
    report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog, minimum_samples=3)
    item = report["items"][0]
    expected = generate_bin1_cpk_report(pd.DataFrame({"SBIN": [1, 1, 1], "VDD": [4, 5, 6]}),
        pd.DataFrame([{"Test ID": 1001, "Test Name": "VDD", "Unit": "V", "Lowlim": 1, "Uplim": 9}])).iloc[0]
    for name, key in [("cpk", "cpk"), ("cpk/cp", "cpk_cp"), ("Mean", "mean"),
                      ("Std", "std"), ("ExecCount", "n"), ("FailCount", "fail_count")]:
        assert item[key] == pytest.approx(expected[name])
    assert item["status"] == "FAIL"
    assert item["rule_id"] == "CPK-001"
    assert report["findings"][0]["status"] == "FAIL"
    assert report["release_decision"] == "ENGINEER_CONFIRMATION_REQUIRED"
    assert report["policy"]["cpk_high_check"] == "NOT_EVALUATED"


@pytest.mark.parametrize("cpk,status", [(1.66, "FAIL"), (1.67, "RISK"),
                                        (1.999, "RISK"), (2.0, "PASS"), (100, "PASS")])
def test_exact_rule_boundaries_and_no_high_cpk_check(catalog, cpk, status):
    report = build(catalog, (4, 5, 6), 5 - cpk * 3, 5 + cpk * 3)
    assert report["items"][0]["status"] == status
    assert len(report["findings"]) == (0 if status == "PASS" else 1)
    assert report["rules"]["catalog_hash"] == catalog.catalog_hash


def test_custom_rules_are_used_not_remembered_threshold(catalog):
    rules = []
    for rule in catalog.rules:
        condition = {"CPK-001": "< 1.0", "CPK-002": ">= 1.0 and < 1.5", "CPK-003": ">= 1.5"}.get(rule.rule_id)
        rules.append(replace(rule, spec={**rule.spec, "condition": condition}) if condition else rule)
    modified = replace(catalog, rules=tuple(rules), catalog_hash="custom-rule-hash")
    report = build(modified)
    assert report["items"][0]["status"] == "RISK"
    assert report["findings"][0]["rule_id"] == "CPK-002"


def test_selection_preserves_explicit_retest_records(catalog):
    frame, metadata = dataset((4, 5, 6, 8))
    frame["SITE"] = [0, 0, 0, 1]
    report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog,
                                         part_indices=[1, 2, 3], sites=[0])
    assert report["items"][0]["n"] == 3
    assert report["selection"]["selected_part_indices"] == [1, 2, 3]
    assert report["selection"]["attempt_policy"] == "selected_execution_records"
    assert report["items"][0]["sample_refs"][0]["part_index"] == 1
    assert frame["Name"].nunique() == 1  # Display name never drives deduplication.


def test_bins_and_flags_do_not_enter_pass_population(catalog):
    frame, metadata = dataset((4, 5, 6, 100, 101, 102))
    frame.loc[3, "SBIN"] = 2
    frame.loc[4, "PART_FLAG"] = 0x10
    frame.loc[5, "PART_FLAG"] = 8
    report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    item = report["items"][0]
    assert item["n"] == 3 and item["mean"] == 5
    assert item["excluded_counts"] == {"NOT_BIN1": 1, "INVALID_OR_CONFLICTING_PART_RESULT": 2}


@pytest.mark.parametrize("change", [{"result_valid": False}, {"test_flag": 2},
                                    {"param_flag": 1}, {"param_flag": 0x20},
                                    {"result": float("inf")}, {"result": float("nan")}])
def test_invalid_results_are_excluded(catalog, change):
    frame, metadata = dataset((4, 5, 6, 100))
    frame["__test_records"][3][0].update(change)
    item = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["n"] == 3 and item["mean"] == 5


def test_last_invalid_execution_does_not_fall_back(catalog):
    frame, metadata = dataset()
    event = deepcopy(frame["__test_records"][0][0])
    event.update(result=99, result_valid=False, record_sequence=100)
    frame["__test_records"][0].insert(0, event)  # Intentionally out of list order.
    item = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["n"] == 2 and item["mean"] == 5.5
    assert {ref["part_index"] for ref in item["sample_refs"]} == {2, 3}


@pytest.mark.parametrize("field,value", [("unit", "A"), ("low_limit", 2), ("high_limit", 8)])
def test_spec_variants_never_mix(catalog, field, value):
    frame, metadata = dataset((4, 5, 6, 7))
    frame["__test_records"][3][0][field] = value
    items = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)["items"]
    assert len(items) == 2 and sorted(item["n"] for item in items) == [1, 3]
    assert any(item["status"] == "INSUFFICIENT_DATA" for item in items)


def test_conditions_never_mix(catalog):
    frame, metadata = dataset((4, 5, 6, 7))
    conditions = json.loads(frame["__conditions_json"][3])
    conditions["job_revision"] = "B"
    frame.loc[3, "__conditions_json"] = json.dumps(conditions)
    items = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)["items"]
    assert len(items) == 2 and sorted(item["n"] for item in items) == [1, 3]


@pytest.mark.parametrize("values,low,high,status,reason", [
    ((4,), 1, 9, "INSUFFICIENT_DATA", "TOO_FEW_VALID_SAMPLES"),
    ((5, 5, 5), 1, 9, "NOT_CALCULABLE", "ZERO_OR_UNAVAILABLE_STD"),
    ((4, 5, 6), None, None, "NOT_CALCULABLE", "NO_EFFECTIVE_LIMIT"),
    ((4, 5, 6), 9, 1, "NOT_CALCULABLE", "INVALID_LIMIT_ORDER"),
    ((4, 5, 6), 0, 0, "NOT_CALCULABLE", "INVALID_LIMIT_ORDER"),
])
def test_unevaluable_is_never_pass(catalog, values, low, high, status, reason):
    item = build(catalog, values, low, high)["items"][0]
    assert item["status"] == status and item["reason"] == reason
    assert item["rule_status"] is None


def test_zero_bound_is_a_real_limit_and_matches_formal_engine(catalog):
    # Release limit policy: unset arrives as None; a written 0.0 is a real limit.
    item = build(catalog, (4, 5, 6), 0, 11)["items"][0]
    assert item["effective_lsl"] == 0
    expected = generate_bin1_cpk_report(
        pd.DataFrame({"SBIN": [1, 1, 1], "value": [4, 5, 6]}),
        pd.DataFrame([{"Test ID": 1001, "Test Name": "value", "Unit": "V",
                       "Lowlim": 0, "Uplim": 11}]), zero_limit_is_unset=False).iloc[0]
    assert item["cpk"] == pytest.approx(expected["cpk"]) == pytest.approx(5 / 3)
    assert item["cpk_cp"] == pytest.approx(expected["cpk/cp"])


def test_sample_requirement_is_explicit_and_not_guessed(catalog):
    report = build(catalog)
    assert report["items"][0]["sample_adequacy_status"] == "NOT_EVALUATED"
    assert report["warnings"]
    item = build(catalog, minimum_samples=10)["items"][0]
    assert item["status"] == "INSUFFICIENT_DATA" and item["cpk"] is not None


def test_missing_unit_keeps_classification_and_missing_conditions_reported(catalog):
    frame, metadata = dataset(unit=None)
    frame["__conditions_json"] = "{}"
    report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    assert report["items"][0]["status"] == "FAIL"
    assert report["items"][0]["rule_id"] == "CPK-001"
    assert "单位缺失" in report["warnings"]
    assert any("温度" in warning for warning in report["warnings"])


@pytest.mark.parametrize("unit", [None, ""])
@pytest.mark.parametrize("cpk", [1.0, 1.8, 3.0])
def test_missing_unit_does_not_change_cpk_values_or_rules(catalog, unit, cpk):
    expected = build(catalog, (4, 5, 6), 5 - cpk * 3, 5 + cpk * 3, "V")["items"][0]
    report = build(catalog, (4, 5, 6), 5 - cpk * 3, 5 + cpk * 3, unit)
    actual = report["items"][0]
    for key in ("cpk", "mean", "std", "status", "rule_id", "reason"):
        assert actual[key] == expected[key]
    assert "单位缺失" in report["warnings"]


@pytest.mark.parametrize("kwargs", [{"part_indices": [99]}, {"sites": [99]},
                                   {"test_columns": ["unknown"]}, {"minimum_samples": 1},
                                   {"part_indices": [True]}, {"minimum_samples": True}])
def test_invalid_selection_is_rejected(catalog, kwargs):
    with pytest.raises(ValueError):
        build(catalog, **kwargs)


def test_empty_selection_has_no_old_result_or_findings(catalog):
    report = build(catalog, part_indices=[])
    assert report["status"] == "EMPTY" and report["items"] == [] and report["findings"] == []


def test_missing_or_overlapping_rules_do_not_mark_pass(catalog):
    altered = [replace(r, spec={**r.spec, "condition": ">= 0"})
               if r.rule_id == "CPK-003" else r for r in catalog.rules]
    report = build(replace(catalog, rules=tuple(altered)))
    assert report["items"][0]["status"] == "NOT_EVALUATED"


def test_parquet_roundtrip_uses_events_not_untrusted_wide_values(tmp_path):
    from tests.test_compute.test_release_stdf import record, part, ptr, specification
    source = tmp_path / "sample.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + b"".join(
        part(name=f"P{i}", tests=ptr(value, optional=specification(low=1, high=9)))
        for i, value in enumerate([4, 5, 6])))
    parquet = convert_stdf_to_wide_all(source, tmp_path / "wide.parquet")
    report = analyze_release_bin1_cpk(parquet)
    assert report["items"][0]["n"] == 3
    assert report["items"][0]["cpk"] == pytest.approx(4 / 3)
    json.dumps(report, allow_nan=False)


def test_functional_tests_are_not_cpk(catalog):
    frame, metadata = dataset()
    for events in frame["__test_records"]:
        events[0].update(record_type="FTR", result=1, param_flag=None)
    assert build_release_bin1_cpk_report(frame, metadata, catalog=catalog)["items"][0]["status"] == "NOT_APPLICABLE"


def test_selected_unexecuted_test_remains_visible(catalog):
    frame, metadata = dataset()
    frame.at[0, "__test_records"] = []
    report = build_release_bin1_cpk_report(frame, metadata, catalog=catalog,
                                         part_indices=[1], test_columns=["1001_VDD"])
    assert report["items"] == [] and report["findings"] == []
    assert report["unexecuted_tests"] == [{"column": "1001_VDD",
        "status": "INSUFFICIENT_DATA", "status_label": "数据不足",
        "reason": "NO_SELECTED_EXECUTION", "n": 0, "cpk": None, "rule_status": None}]
    assert report["summary"]["unexecuted_test_count"] == 1


@pytest.mark.parametrize("value", [1.5, -1, True, float("inf")])
def test_corrupt_part_index_is_rejected(catalog, value):
    frame, metadata = dataset()
    frame["__part_index"] = frame["__part_index"].astype(object)
    frame.at[0, "__part_index"] = value
    with pytest.raises(ValueError, match="非负整数"):
        build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
