"""Release MMSA (Site Diff) decision tree, dual-parameter bands and supports."""
from copy import deepcopy
from dataclasses import replace
import json

import pandas as pd
import pytest

from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_mmsa import analyze_release_mmsa, build_release_mmsa_report
from deepyield.compute.release_stdf import SCHEMA_VERSION, convert_stdf_to_wide_all
from deepyield.rules.loader import load_analysis_rules
from tests.test_compute.test_release_cpk import dataset


@pytest.fixture
def catalog():
    return load_analysis_rules()


def sites_frame(sites_values, low, high, unit="V"):
    """Build a Release frame with the given per-site measurement lists."""
    conditions = {"part_type": "TEST_PRODUCT", "job_name": "TEST_PROGRAM",
                  "job_revision": "A", "test_code": "FT", "test_temperature": "25"}
    test = {"column": "1001_VDD", "test_num": 1001, "name": "VDD", "record_type": "PTR",
            "unit": unit, "low_limit": low, "high_limit": high, "result_index": None}
    rows, index = [], 0
    for site, values in sites_values.items():
        for value in values:
            index += 1
            rows.append({"__part_index": index, "Name": "SAME_DISPLAY_NAME", "SBIN": 1,
                "PART_FLAG": 0, "SITE": site, "__conditions_json": json.dumps(conditions),
                "__test_records": [{**test, "test_flag": 0, "param_flag": 0, "result_valid": True,
                    "result": value, "record_sequence": index * 10}]})
    metadata = {"schema_version": SCHEMA_VERSION, "source_file": "fixture.stdf",
                "source_sha256": "a" * 64, "test_catalog": [test]}
    return pd.DataFrame(rows), metadata


def build(catalog, sites_values, low, high, **kwargs):
    frame, metadata = sites_frame(sites_values, low, high, kwargs.pop("unit", "V"))
    if kwargs.pop('isolate_good_cg', False):
        # Isolate later decision branches from the repeatability gate. Real-data
        # integration tests below use the actual 20%-tolerance Cg calculations.
        support = build_release_cgm_report(frame, metadata, catalog=catalog)
        for item in support['items']:
            item['cg'] = 1.5
            for site in item['sites']:
                site['cg'] = 1.5
        kwargs['cgm_report'] = support
    return build_release_mmsa_report(frame, metadata, catalog=catalog, **kwargs)


def one(report):
    assert len(report["items"]) == 1
    return report["items"][0]


def test_band_boundaries_and_gate(catalog):
    # cpk >= 2 and every site Cg ok keeps the item out of the tree.
    item = one(build(catalog, {0: [5.0] * 10, 1: [8.0, 8.0]}, -40, 55))
    assert item["status"] == "NOT_APPLICABLE" and item["reason"] == "GATE_NOT_TRIGGERED"
    # Same population, tighter limits: shift 2.57 sigma is RED and gated in.
    item = one(build(catalog, {0: [5.0] * 10, 1: [8.0, 8.0]}, 1, 12))
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-003"
    assert item["reason"] == "SYSTEMATIC_SITE_SHIFT"
    assert item["shift_severity"] == "RED" and item["shift_spec_severity"] == "RED"
    assert item["var_severity"] == "GREEN" and item["secondary_rule_id"] is None


def test_variance_rule_with_all_sites_repeatability_ok(catalog):
    # stds [0.866, 0.1414, 0.1414]: var RED, shift GREEN, every site Cg >= 1.33, cpk < 2.
    item = one(build(catalog, {0: [4, 5, 6] * 3, 1: [4.9, 5.1], 2: [4.9, 5.1]}, 0.9, 49.1))
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-004"
    assert item["reason"] == "SITE_VARIANCE_ANOMALY"
    assert item["cgm_min_cg"] > 1.33 and item["var_ratio"] > 2.5 and item["shift_sigma"] == 0


def test_repeatability_branch_counts_failing_sites(catalog):
    # Both sites have Cg = 0.20 (<1.33): branch 1 with 2 failing sites.
    item = one(build(catalog, {0: [4.9, 5.0, 5.1], 1: [5.9, 6.0, 6.1]}, 4.7, 5.3))
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-001"
    assert item["reason"] == "LOCAL_SITE_HARDWARE_FAULT"
    assert item["cgm_failed_site_count"] == 2 and item["cgm_min_cg"] == pytest.approx(.20)
    assert item["shift_severity"] == "YELLOW" and item["var_ratio"] == pytest.approx(1.0)


def test_wide_distribution_sub_judgment(catalog):
    # Identical wide sites, centered: Cp = 1.33 < 1.67 -> MMSA-008.
    item = one(build(catalog, {0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9, isolate_good_cg=True))
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-008"
    assert item["reason"] == "DISTRIBUTION_WIDE_OR_BIMODAL"
    assert item["cp"] == pytest.approx(8 / (6 * item["overall_std"])) and item["shift_sigma"] == 0


def test_rule5_unclassified_fallback_stays_review(catalog):
    # Cp 3.33 with margin 25%: neither close-to-limit nor wide -> MMSA-005.
    item = one(build(catalog, {0: [4.9, 5.0, 5.1], 1: [4.9, 5.0, 5.1]}, 4.5, 6.5, isolate_good_cg=True))
    assert item["status"] == "REVIEW_REQUIRED" and item["rule_id"] == "MMSA-005"
    assert item["reason"] == "DISTRIBUTION_OR_LIMIT_REVIEW"
    assert item["cp"] == pytest.approx(2 / (6 * item["overall_std"]))
    assert item["limit_margin_lower"] == pytest.approx(.25)


def test_rule5_close_to_limit_sub_judgment(catalog):
    # Tight sites with the mean at 5% of the band from the high limit:
    # every site Cg >= 1.33, cpk < 2, Cp >= 2 -> MMSA-007 HIGH.
    item = one(build(catalog, {0: [7.96, 8.0, 8.04], 1: [7.96, 8.0, 8.04]}, 6.1, 8.1))
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-007"
    assert item["reason"] == "CLOSE_TO_LIMIT_MARGIN"
    assert item["close_to"] == "HIGH" and item["limit_margin_upper"] == pytest.approx(.05)
    assert item["cgm_min_cg"] > 1.33 and item["cp"] > 2 and item["overall_bin1_cpk"] < 2


def test_spec_severity_is_reported_but_never_triggers(catalog):
    # shift 1.6 sigma YELLOW with 22.4% of the band: the expert tree triggers
    # rule 3 on sigma only, so this is boundary evidence, not site shift.
    item = one(build(catalog, {0: [4.0, 5.0, 6.0], 1: [7.0, 8.0, 9.0]}, -0.2, 13.2, isolate_good_cg=True))
    assert item["status"] == "REVIEW_REQUIRED" and item["rule_id"] == "MMSA-006"
    assert item["reason"] == "S2S_EVIDENCE_INCONCLUSIVE"
    assert item["shift_severity"] == "YELLOW" and item["shift_spec_severity"] == "RED"


def test_yellow_evidence_band_needs_manual_review(catalog):
    # One-sided spec, shift 1.8 sigma YELLOW, var GREEN -> MMSA-006.
    item = one(build(catalog, {0: [4.9, 5.0, 5.1], 1: [5.9, 6.0, 6.1]}, 3.9, None))
    assert item["status"] == "REVIEW_REQUIRED" and item["rule_id"] == "MMSA-006"
    assert item["reason"] == "S2S_EVIDENCE_INCONCLUSIVE"
    assert item["shift_spec_pct"] is None and item["shift_spec_severity"] is None


def test_zero_median_std_paths(catalog):
    # All site stds zero: perfectly homogeneous -> GREEN 0.0.
    item = one(build(catalog, {0: [5.0] * 10, 1: [8.0, 8.0]}, 1, 12))
    assert item["var_ratio"] == 0.0 and item["var_severity"] == "GREEN"
    # Median zero but a worst site with real spread: RED without a ratio value.
    item = one(build(catalog, {0: [5.0, 5.0, 5.0], 1: [5.0, 5.0, 5.0], 2: [1.7, 5.0, 8.3]}, 0.9, 9.1))
    assert item["var_ratio"] is None and item["var_severity"] == "RED"
    report = build(catalog, {0: [5.0, 5.0, 5.0], 1: [5.0, 5.0, 5.0], 2: [1.7, 5.0, 8.3]}, 0.9, 9.1)
    assert any("标准差的中位数为零" in warning for warning in report["warnings"])


def test_missing_supports_fail_closed(catalog):
    frame, metadata = sites_frame({0: [4.9, 5.0, 5.1], 1: [4.9, 5.0, 5.1]}, 1, 9)
    frame["SBIN"] = 2  # no BIN1 rows -> no evaluable CPK support
    report = build_release_mmsa_report(frame, metadata, catalog=catalog)
    item = one(report)
    # Gate can no longer fire on CPK, but good Cg means nothing is anomalous.
    assert item["status"] == "NOT_APPLICABLE" and item["reason"] == "GATE_NOT_TRIGGERED"
    assert any("触发判定仅能按 Cg 进行" in warning for warning in report["warnings"])
    # Without any CPK support and without Cg values the item cannot be judged.
    empty_cgm = {"report_type": "release_cgm", "source": report["source"],
                 "policy": {"cg_tolerance_fraction": .20},
                 "rules": {"catalog_hash": catalog.catalog_hash}, "items": []}
    item = one(build_release_mmsa_report(frame, metadata, catalog=catalog, cgm_report=empty_cgm))
    assert item["status"] == "NOT_EVALUATED" and item["reason"] == "NO_MATCHED_BIN1_CPK"


@pytest.mark.parametrize('fraction', [None, 1.])
def test_old_external_cgm_requires_recalculation(catalog, fraction):
    frame, metadata = sites_frame({0: [4.9, 5., 5.1], 1: [4.9, 5., 5.1]}, 1., 9.)
    support = build_release_cgm_report(frame, metadata, catalog=catalog)
    support['policy'] = {} if fraction is None else {'cg_tolerance_fraction': fraction}
    with pytest.raises(ValueError, match='20%.*重新计算'):
        build_release_mmsa_report(frame, metadata, catalog=catalog, cgm_report=support)


def test_gate_fires_on_repeatability_alone(catalog):
    # CPK support missing entirely, but both sites fail Cg: branch 1 still runs.
    frame, metadata = sites_frame({0: [4.9, 5.0, 5.1], 1: [5.9, 6.0, 6.1]}, 4.7, 5.3)
    frame["SBIN"] = 2
    report = build_release_mmsa_report(frame, metadata, catalog=catalog)
    item = one(report)
    assert item["status"] == "RISK" and item["rule_id"] == "MMSA-001"
    assert item["overall_bin1_cpk"] is None and item["cgm_failed_site_count"] == 2


def test_single_usable_site_is_insufficient(catalog):
    item = one(build(catalog, {0: [4.0, 5.0, 6.0], 1: [7.0]}, 1, 9))
    assert item["status"] == "INSUFFICIENT_DATA" and item["site_count"] == 1
    assert item["reason"] == "TOO_FEW_USABLE_SITES"


def test_functional_tests_and_missing_unit_inherit_sibling_semantics(catalog):
    item = one(build(catalog, {0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9, unit=None))
    expected = one(build(catalog, {0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9))
    assert item["status"] == expected["status"] and item["rule_id"] == expected["rule_id"]
    frame, metadata = sites_frame({0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9)
    for events in frame["__test_records"]:
        events[0]["record_type"] = "FTR"
    item = one(build_release_mmsa_report(frame, metadata, catalog=catalog))
    assert item["status"] == "NOT_APPLICABLE" and item["reason"] == "FUNCTIONAL_TEST"


@pytest.mark.parametrize("unit", [None, ""])
@pytest.mark.parametrize("values,low,high", [
    ({0: [4.9, 5, 5.1], 1: [5.9, 6, 6.1]}, 4.7, 5.3),
    ({0: [5, 5], 1: [8, 8]}, 1, 12),
    ({0: [4, 5, 6], 1: [4, 5, 6]}, -10, 20),
])
def test_missing_unit_preserves_site_diff_diagnosis_and_gate(catalog, unit, values, low, high):
    expected = one(build(catalog, values, low, high))
    report = build(catalog, values, low, high, unit=unit)
    actual = one(report)
    for key in ("status", "reason", "rule_id", "shift_sigma", "shift_spec_pct", "var_ratio",
                "overall_bin1_cpk", "cgm_min_cg", "cgm_failed_site_count"):
        assert actual[key] == expected[key]
    assert "单位缺失" in report["warnings"]


def test_site_stats_cover_expected_sites_with_std_none(catalog):
    frame, metadata = sites_frame({0: [4.0, 5.0, 6.0], 1: [7.0]}, 1, 9)
    item = one(build_release_mmsa_report(frame, metadata, catalog=catalog))
    stats = {site["site"]: site for site in item["sites"]}
    assert stats[1]["n"] == 1 and stats[1]["std"] is None and stats[1]["mean"] == pytest.approx(7.0)


def test_external_supports_match_by_identity_and_reject_mismatch(catalog):
    frame, metadata = sites_frame({0: [4.9, 5.0, 5.1], 1: [5.9, 6.0, 6.1]}, 4.7, 5.3)
    cpk = build_release_bin1_cpk_report(frame, metadata, catalog=catalog)
    report = build_release_mmsa_report(frame, metadata, catalog=catalog,
        bin1_cpk_report=cpk, allow_cross_source_cpk=True)
    item = one(report)
    assert item["overall_bin1_cpk"] == cpk["items"][0]["cpk"]
    for bad in ({"report_type": "wrong", "source": cpk["source"], "rules": cpk["rules"]},
                {**cpk, "rules": {"catalog_hash": "other"}}):
        with pytest.raises(ValueError):
            build_release_mmsa_report(frame, metadata, catalog=catalog,
                bin1_cpk_report=bad, allow_cross_source_cpk=True)


def test_yaml_bands_and_gate_drive_the_tree(catalog):
    bands = {"shift_sigma": {"green_lt": 5.0, "red_ge": 6.0}, "shift_spec_pct": {"green_lt": 99, "red_ge": 100},
             "var_ratio": {"green_lt": 1.5, "red_ge": 2.5}}
    document = {**catalog.document("MMSA-V1"), "parameters": bands}
    modified = replace(catalog, documents={**catalog.documents, "MMSA-V1": document})
    # shift 1.8 sigma would be RED under 5.0/6.0 bands; one-sided spec has no
    # Cp, so rule 5 falls back to the unclassified review outcome.
    item = one(build(modified, {0: [4.9, 5.0, 5.1], 1: [5.9, 6.0, 6.1]}, 3.9, None))
    assert item["rule_id"] == "MMSA-005" and item["shift_severity"] == "GREEN"
    assert item["cp"] is None


def test_invalid_yaml_configuration_fails_closed(catalog):
    for override in ({"gate": {"condition": {"operator": "GE", "threshold": 2.0}}},
                     {"parameters": {"shift_sigma": {"green_lt": 3.0, "red_ge": 2.0}}},
                     {"cgm_reference": {"cg_pass_threshold_source": "CGM-002",
                                        "local_fault_max_sites": 4}}):
        document = {**catalog.document("MMSA-V1"), **override}
        with pytest.raises(ValueError):
            build_release_mmsa_report(*sites_frame({0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9),
                catalog=replace(catalog, documents={**catalog.documents, "MMSA-V1": document}))


def test_findings_and_report_shapes(catalog):
    report = build(catalog, {0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9)
    item = one(report)
    assert report["report_type"] == "release_mmsa" and report["release_decision"] == "ENGINEER_CONFIRMATION_REQUIRED"
    assert [rule["rule_id"] for rule in report["rules"]["rules"]] == [f"MMSA-00{i}" for i in range(1, 9)]
    assert report["findings"] and report["findings"][0]["rule_id"] == "MMSA-001"
    assert item["sample_refs"] and item["sites"][0]["cg"] is None
    assert set(item["s2s"]) >= {"gate", "bands", "shift_sigma", "var_ratio", "overall_bin1_cpk", "cp"}
    assert item["s2s"]["gate"]["trigger"] == "ANY" and item["s2s"]["gate"]["cg_lt"] == 1.33
    json.dumps(report, allow_nan=False)


def test_invalid_sample_requirement(catalog):
    with pytest.raises(ValueError):
        build(catalog, {0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 6.0]}, 1, 9, minimum_samples_per_site=1)


def test_parquet_roundtrip(tmp_path):
    from tests.test_compute.test_release_stdf import part, ptr, record, specification
    source = tmp_path / "source.stdf"
    values = [7.0] * 6 + [10.0, 10.2]
    source.write_bytes(record(0, 10, b"\2\4") + b"".join(
        part(name=f"P{i}", tests=ptr(value, optional=specification(low=1, high=15))) for i, value in enumerate(values)))
    output = convert_stdf_to_wide_all(source, tmp_path / "wide.parquet")
    report = analyze_release_mmsa(output)
    assert report["items"] and json.dumps(report, allow_nan=False)


def test_last_invalid_value_does_not_fall_back(catalog):
    frame, metadata = sites_frame({0: [4.0, 5.0, 6.0], 1: [4.0, 5.0, 100.0]}, 1, 9)
    event = deepcopy(frame["__test_records"][5][0])
    event.update(result=100, result_valid=False, record_sequence=1000)
    frame["__test_records"][5].insert(0, event)
    item = one(build_release_mmsa_report(frame, metadata, catalog=catalog))
    assert item["n"] == 5 and item["overall_bin1_cpk"] is not None


@pytest.mark.parametrize("values,count", [({1: [1, 2, 3] * 20}, 1),
                                         ({1: [1], 2: [2]}, 0),
                                         ({1: [1, 2], 2: [3]}, 1)])
def test_insufficient_sites_distinguish_site_count_from_overall_samples(catalog, values, count):
    item = one(build(catalog, values, 0, 10))
    assert item["status"] == "INSUFFICIENT_DATA"
    assert item["reason"] == "TOO_FEW_USABLE_SITES"
    assert item["site_count"] == count and item["n"] >= 2


def test_single_measurement_keeps_the_sample_shortage_reason(catalog):
    item = one(build(catalog, {1: [1]}, 0, 10))
    assert item["status"] == "INSUFFICIENT_DATA"
    assert item["reason"] == "TOO_FEW_VALID_SAMPLES"
