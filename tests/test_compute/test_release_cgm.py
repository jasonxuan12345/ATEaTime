"""Release CGM agrees with the formal site report and YAML acceptance rules."""
from copy import deepcopy
from dataclasses import replace
import json

import pandas as pd
import pytest

from deepyield.compute.release_cgm import build_release_cgm_report, analyze_release_cgm
from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_stdf import convert_stdf_to_wide_all
from deepyield.compute.report_engine import generate_cgm_report
from deepyield.rules.loader import load_analysis_rules
from tests.test_compute.test_release_cpk import dataset


@pytest.fixture
def catalog():
    return load_analysis_rules()


def fixture(values=(4, 5, 6), low=1, high=9, unit="V"):
    frame, metadata = dataset(values, low, high, unit)
    frame["SITE"] = 0
    return frame, metadata


def build(catalog, *args, **kwargs):
    frame, metadata = fixture(*args)
    return build_release_cgm_report(frame, metadata, catalog=catalog, **kwargs)


def test_site_metrics_match_formal_report(catalog):
    frame, metadata = fixture((4, 5, 6, 7, 9, 11))
    frame["SITE"] = [0, 0, 0, 1, 1, 1]
    report = build_release_cgm_report(frame, metadata, catalog=catalog)
    item = report["items"][0]
    expected = generate_cgm_report(pd.DataFrame({"SITE": frame.SITE, "VDD": [4, 5, 6, 7, 9, 11]}),
        pd.DataFrame([{"Test ID": 1001, "Test Name": "VDD", "Unit": "V", "Lowlim": 1, "Uplim": 9}])).iloc[0]
    assert item["cg"] == pytest.approx(expected["min_Cg"])
    for site in item["sites"]:
        assert site["cg"] == pytest.approx(expected[f"site{site['site']} cg"])
        assert site["n"] == 3
    assert report["policy"]["population"] == "all_selected_valid_results"
    assert report["policy"]["cg_tolerance_fraction"] == .20
    assert item["cg"] == pytest.approx(.20 * 8 / (6 * 2))
    assert item["status"] == "FAIL" and report["findings"][0]["rule_id"] == "CGM-002"


@pytest.mark.parametrize("cg,status", [(0.99, "FAIL"), (1.0, "RISK"), (1.329, "RISK"),
                                      (1.33, "PASS"), (1.9, "PASS")])
def test_rule_boundaries(catalog, cg, status):
    frame, metadata = fixture((4, 5, 6), 5 - cg * 15, 5 + cg * 15)
    frame['SBIN'] = 2  # Isolate Cg thresholds from the unchanged CPK override.
    report = build_release_cgm_report(frame, metadata, catalog=catalog)
    assert report["items"][0]["status"] == status
    assert not report["items"][0]["cpk_override_applied"]


def test_bin1_override_accepts_low_cg_using_all_bins(catalog):
    frame, metadata = fixture((4.9, 5, 5.1, 1.1, 8.9))
    frame.loc[3:, "SBIN"] = 2
    frame.loc[3:, "PART_FLAG"] = 8
    for events in frame["__test_records"][3:]:
        events[0].update(test_flag=0x80, param_flag=0x08)
    report = build_release_cgm_report(frame, metadata, catalog=catalog)
    item = report["items"][0]
    assert item["n"] == 5 and item["cg"] < 1
    assert item["supported_by_bin1_cpk"] > 2
    assert item["status"] == "PASS" and item["rule_id"] == "CGM-001"
    assert item["cpk_override_applied"] and report["risks"] == [] and report["findings"] == []


def test_exact_cpk_override_threshold(catalog):
    frame, metadata = fixture((4, 5, 6), -1, 11)
    report = build_release_cgm_report(frame, metadata, catalog=catalog)
    assert report["items"][0]["supported_by_bin1_cpk"] == 2
    assert report["items"][0]["cpk_override_applied"]


def test_missing_bin1_cpk_evaluates_cg_only(catalog):
    frame, metadata = fixture()
    frame["SBIN"] = 2
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["supported_by_bin1_cpk"] is None
    assert item["status"] == "FAIL" and item["rule_id"] == "CGM-002"


@pytest.mark.parametrize("values,sites", [((4, 5, 6), [0, 1, 2]),
                                          ((4, 5), [0, 1])])
def test_missing_or_partial_cg_never_passes_by_cpk(catalog, values, sites):
    frame, metadata = fixture(values, -100, 100)
    frame["SITE"] = sites
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["status"] == "NOT_EVALUATED" and not item["cpk_override_applied"]
    assert item["reason"] == "INCOMPLETE_SITE_COVERAGE"


def test_zero_std_is_infinite_cg_and_passes_with_valid_limits(catalog):
    report = build(catalog, (5, 5, 5))
    item = report['items'][0]
    assert item['cg'] == 'Infinity' and item['status'] == 'PASS'
    assert item['rule_id'] == 'CGM-004'
    assert item['sites'][0]['std'] == 0
    assert item['sites'][0]['cg'] == 'Infinity' and item['sites'][0]['status'] == 'CALCULATED'
    json.dumps(report, allow_nan=False)


def test_infinite_site_does_not_hide_worse_finite_cg(catalog):
    frame, metadata = fixture((5, 5, 5, 4, 5, 6))
    frame['SITE'] = [0, 0, 0, 1, 1, 1]
    item = build_release_cgm_report(frame, metadata, catalog=catalog)['items'][0]
    assert item['sites'][0]['cg'] == 'Infinity'
    assert item['cg'] == pytest.approx(.20 * 8 / 6)
    assert item['status'] == 'PASS' and item['cpk_override_applied']


def test_zero_std_still_requires_enough_samples_and_valid_limits(catalog):
    assert build(catalog, (5, 5, 5), minimum_samples_per_site=4)['items'][0]['status'] == 'NOT_EVALUATED'
    item = build(catalog, (5, 5, 5), None, 9)['items'][0]
    assert item['cg'] is None and item['status'] == 'NOT_CALCULABLE'


def test_unexecuted_selected_site_remains_visible(catalog):
    frame, metadata = fixture((4, 5, 6, 7))
    frame["SITE"] = [0, 0, 0, 1]
    frame.at[3, "__test_records"] = []
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["sites"][1]["n"] == 0
    assert item["cg"] == pytest.approx(.20 * 8 / 6)
    assert item["status"] == "FAIL"


@pytest.mark.parametrize("cg,status", [(0.8, "FAIL"), (1.1, "RISK"), (1.5, "PASS")])
def test_available_sites_are_classified_with_missing_site(catalog, cg, status):
    frame, metadata = fixture((4, 5, 6, 5), 5 - 15 * cg, 5 + 15 * cg)
    frame["SBIN"] = 2
    frame["SITE"] = [0, 0, 0, 1]
    frame.at[3, "__test_records"] = []
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["cg"] == pytest.approx(cg)
    assert item["status"] == status
    assert item["sites"][1]["n"] == 0 and item["sites"][1]["cg"] is None


def test_explicit_site_without_selected_records_is_retained(catalog):
    frame, metadata = fixture((4, 5, 6, 5))
    frame["SITE"] = [0, 0, 0, 1]
    item = build_release_cgm_report(frame, metadata, catalog=catalog,
        sites=[0, 1], part_indices=[1, 2, 3])["items"][0]
    assert [site["site"] for site in item["sites"]] == [0, 1]
    assert item["sites"][1]["n"] == 0
    assert item["status"] == "FAIL"


def test_insufficient_site_does_not_control_available_site_minimum(catalog):
    frame, metadata = fixture((4, 5, 6, 0, 10))
    frame["SITE"] = [0, 0, 0, 1, 1]
    item = build_release_cgm_report(frame, metadata, catalog=catalog,
        minimum_samples_per_site=3)["items"][0]
    assert item["sites"][1]["status"] == "INSUFFICIENT_DATA"
    assert item["cg"] == pytest.approx(.20 * 8 / 6)
    assert item["status"] == "FAIL"


def test_no_executions_still_lists_requested_sites(catalog):
    frame, metadata = fixture((4, 5, 6, 5))
    frame["SITE"] = [0, 0, 0, 1]
    for index in frame.index:
        frame.at[index, "__test_records"] = []
    report = build_release_cgm_report(frame, metadata, catalog=catalog, sites=[0, 1])
    assert not report["items"]
    assert report["unexecuted_tests"][0]["status"] == "INSUFFICIENT_DATA"
    assert report["unexecuted_tests"][0]["sites"] == [
        {"site": site, "n": 0, "cg": None, "status": "INSUFFICIENT_DATA"} for site in (0, 1)]


@pytest.mark.parametrize("low,high,reason", [(None, 9, "NO_EFFECTIVE_TWO_SIDED_LIMIT"),
    (1, None, "NO_EFFECTIVE_TWO_SIDED_LIMIT"), (9, 1, "INVALID_LIMIT_ORDER")])
def test_invalid_specs_not_overridden(catalog, low, high, reason):
    item = build(catalog, (4, 5, 6), low, high)["items"][0]
    assert item["cg"] is None and item["status"] == "NOT_CALCULABLE"
    assert item["reason"] == reason


def test_zero_low_limit_is_a_real_limit(catalog):
    # Release limit policy: a written 0.0 low limit is real, so the spec is
    # two-sided and Cg is computed against it.
    item = build(catalog, (0.9, 1.0, 1.1), 0, 2)["items"][0]
    assert item["effective_lsl"] == 0 and item["cg"] is not None
    assert item["status"] in {"PASS", "RISK", "FAIL"}


def test_sample_requirement_not_guessed(catalog):
    report = build(catalog)
    assert report["items"][0]["sites"][0]["sample_adequacy_status"] == "NOT_EVALUATED"
    item = build(catalog, minimum_samples_per_site=10)["items"][0]
    assert item["cg"] is None and item["status"] == "NOT_EVALUATED"


@pytest.mark.parametrize("minimum", [1, True, 2.5])
def test_invalid_sample_requirement(catalog, minimum):
    with pytest.raises(ValueError):
        build(catalog, minimum_samples_per_site=minimum)


def test_last_invalid_value_does_not_fall_back(catalog):
    frame, metadata = fixture((4, 5, 6, 7))
    event = deepcopy(frame["__test_records"][0][0])
    event.update(result=100, result_valid=False, record_sequence=1000)
    frame["__test_records"][0].insert(0, event)
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert item["n"] == 3
    assert {ref["part_index"] for ref in item["sample_refs"]} == {2, 3, 4}


@pytest.mark.parametrize("change", [{"result_valid": False}, {"test_flag": 2},
    {"param_flag": 1}, {"param_flag": 0x20}, {"result": float("inf")}])
def test_invalid_measurements_excluded(catalog, change):
    frame, metadata = fixture((4, 5, 6, 100))
    frame["__test_records"][3][0].update(change)
    assert build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]["n"] == 3


@pytest.mark.parametrize("field,value", [("unit", "A"), ("low_limit", 2), ("high_limit", 8)])
def test_specification_groups_do_not_mix(catalog, field, value):
    frame, metadata = fixture((4, 5, 6, 7, 8, 9))
    for events in frame["__test_records"][3:]:
        events[0][field] = value
    report = build_release_cgm_report(frame, metadata, catalog=catalog)
    assert len(report["items"]) == 2
    assert [item["n"] for item in report["items"]] == [3, 3]


def test_conditions_are_separate(catalog):
    frame, metadata = fixture((4, 5, 6, 7, 8, 9))
    conditions = json.loads(frame["__conditions_json"][0])
    conditions["job_revision"] = "B"
    frame.loc[3:, "__conditions_json"] = json.dumps(conditions)
    assert len(build_release_cgm_report(frame, metadata, catalog=catalog)["items"]) == 2


def test_selection_and_no_execution(catalog):
    frame, metadata = fixture((4, 5, 6, 7))
    report = build_release_cgm_report(frame, metadata, catalog=catalog, part_indices=[1, 2, 3], sites=[0])
    assert report["items"][0]["n"] == 3
    frame.at[0, "__test_records"] = []
    report = build_release_cgm_report(frame, metadata, catalog=catalog, part_indices=[1])
    assert report["items"] == [] and report["unexecuted_tests"][0]["n"] == 0


def test_external_cpk_requires_matching_provenance(catalog):
    frame, metadata = fixture()
    cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    report = build_release_cgm_report(frame, metadata, catalog=catalog, bin1_cpk_report=cpk)
    assert report["items"][0]["supported_by_bin1_cpk"] == cpk["items"][0]["cpk"]
    for key in ("source", "rules"):
        bad = deepcopy(cpk)
        bad[key] = {}
        with pytest.raises(ValueError):
            build_release_cgm_report(frame, metadata, catalog=catalog, bin1_cpk_report=bad)


def test_cpk_for_different_specification_not_borrowed(catalog):
    frame, metadata = fixture()
    cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    for events in frame["__test_records"]:
        events[0]["high_limit"] = 8
    item = build_release_cgm_report(frame, metadata, catalog=catalog, bin1_cpk_report=cpk)["items"][0]
    assert item["supported_by_bin1_cpk"] is None and not item["cpk_override_applied"]


def test_custom_rule_threshold_is_used(catalog):
    rules = [replace(r, spec={**r.spec, "condition": {**r.spec["condition"], "threshold": 1.5}})
             if r.rule_id == "CGM-001" else r for r in catalog.rules]
    item = build(replace(catalog, rules=tuple(rules)), (4, 5, 6), 0.2, 9.8)["items"][0]
    assert item["cpk_override_applied"]


def test_missing_rule_threshold_never_passes(catalog):
    rules = [replace(r, spec={**r.spec, "condition": {"metric": "cg", "operator": "LT"}})
             if r.rule_id == "CGM-002" else r for r in catalog.rules]
    assert build(replace(catalog, rules=tuple(rules)))["items"][0]["status"] == "NOT_EVALUATED"


def test_missing_unit_and_functional_tests(catalog):
    assert build(catalog, (4, 5, 6), 1, 9, None)["items"][0]["status"] == "FAIL"
    frame, metadata = fixture()
    for events in frame["__test_records"]:
        events[0]["record_type"] = "FTR"
    assert build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize("unit", [None, ""])
@pytest.mark.parametrize("low,high", [(3, 7), (2, 8), (1, 9), (-2, 12)])
def test_missing_unit_preserves_cgm_rules_and_cpk_acceptance(catalog, unit, low, high):
    expected = build(catalog, (4, 5, 6), low, high, "V")["items"][0]
    report = build(catalog, (4, 5, 6), low, high, unit)
    actual = report["items"][0]
    for key in ("cg", "sites", "status", "rule_id", "reason", "supported_by_bin1_cpk", "cpk_override_applied"):
        assert actual[key] == expected[key]
    assert "单位缺失" in report["warnings"]


def test_missing_site_does_not_disappear(catalog):
    frame, metadata = fixture((4, 5, 6, 100))
    frame.loc[3, "SITE"] = None
    item = build_release_cgm_report(frame, metadata, catalog=catalog)["items"][0]
    assert any(site["site"] is None for site in item["sites"])
    assert item["status"] == "FAIL"
    assert item["cg"] == pytest.approx(.20 * 8 / 6)


def test_parquet_roundtrip_and_cpk_json(tmp_path):
    from tests.test_compute.test_release_stdf import record, part, ptr, specification
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + b"".join(part(name=f"P{i}",
        tests=ptr(value, optional=specification(low=1, high=9))) for i, value in enumerate([4, 5, 6])))
    output = convert_stdf_to_wide_all(source, tmp_path / "wide.parquet")
    report = analyze_release_cgm(output)
    assert report["items"][0]["cg"] == pytest.approx(.20 * 4 / 3)
    json.dumps(report, allow_nan=False)
