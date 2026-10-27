from copy import deepcopy
import pytest
from deepyield.compute.release_knowledge import match_reference
from tests.test_compute.remark_fixtures import example_catalog
from deepyield.compute.release_remarks import diagnose_item, diagnose_outlier


@pytest.fixture(autouse=True)
def synthetic_references(monkeypatch):
    catalog = example_catalog()
    monkeypatch.setattr("deepyield.compute.release_remarks.load_knowledge", lambda: catalog)
    return catalog


def item(**overrides):
    return dict(test_num=1001, test_name="ExampleVoltage", conditions={}, status="RISK",
                cpk=1.8, std=.01, effective_lsl=0.0, effective_usl=10.0, **overrides)


def test_exact_project_identity_preserves_history_separately():
    data = item()
    before = deepcopy(data)
    diagnosis = diagnose_item("cpk", data)
    assert data == before
    assert "Bin1 CPK=1.8" in diagnosis["current_remark"]
    assert diagnosis["knowledge"]["status"] == "reference"
    assert "Synthetic reference: review the example limits." in diagnosis["auto_remark"]
    assert diagnosis["auto_remark"] == "1. " + diagnosis["knowledge"]["historical_remark"]
    assert "Bin1 CPK=" not in diagnosis["remark"] and "Cp=" not in diagnosis["remark"]
    assert diagnosis["knowledge_hash"] and diagnosis["knowledge"]["source"]["sha256"]


def test_same_number_with_different_name_does_not_import_historical_cause():
    data = item()
    data["test_name"] = "DifferentParameter"
    diagnosis = diagnose_item("cpk", data)
    assert diagnosis["knowledge"]["status"] == "none"
    assert "Synthetic reference" not in diagnosis["remark"]
    assert "example limits" not in diagnosis["remark"]


def test_reference_scope_conflict_and_duplicate_are_not_applied(synthetic_references):
    catalog = deepcopy(synthetic_references)
    key = (1001, "ExampleVoltage")
    catalog["entries"][key][0].update(conditions={"part_type": "DEVICE_A"}, scope_confirmed=True)
    data = item()
    data["conditions"] = {"part_type": "DEVICE_B"}
    assert match_reference(data, catalog)["status"] == "not_applicable"
    assert "example limits" not in diagnose_item("cpk", data, catalog=catalog)["remark"]
    data["conditions"] = {}
    assert match_reference(data, catalog)["status"] == "reference"
    data["conditions"] = {"part_type": "DEVICE_A"}
    assert match_reference(data, catalog)["status"] == "applicable"
    catalog["entries"][key].append(deepcopy(catalog["entries"][key][0]))
    assert match_reference(data, catalog)["status"] == "ambiguous"


def test_cgm_uses_current_bad_sites_and_retains_acceptance():
    data = dict(test_num=1002, test_name="ExampleRepeatability", conditions={}, status="PASS",
                cg=.8, sites=[{"site": 4, "cg": .8}, {"site": 11, "cg": 2.1}],
                _cg_pass_threshold=1.33, cpk_override_applied=True, supported_by_bin1_cpk=2.4)
    diagnosis = diagnose_item("cgm", data)
    assert "S4 (Cg=0.8)" in diagnosis["current_remark"]
    assert "S11 (Cg=" not in diagnosis["current_remark"]
    assert "Accepted by matching bin1 CPK=2.4" in diagnosis["current_remark"]
    assert "Site 8" in diagnosis["knowledge"]["historical_remark"]
    assert "Site 8" not in diagnosis["current_remark"]
    assert data["status"] == "PASS"


@pytest.mark.parametrize("reason", ["MISSING_UNIT", "TOO_FEW_VALID_SAMPLES", "ZERO_OR_UNAVAILABLE_STD"])
def test_unevaluable_results_never_receive_fake_capability_or_digital_labels(reason):
    data = item()
    data.update(status="NOT_CALCULABLE", reason=reason, cpk=None)
    text = diagnose_item("cpk", data)["current_remark"]
    assert "CPK=0" not in text and "CPK=inf" not in text
    if reason == "MISSING_UNIT":
        assert "does not identify a digital test" in text


def test_associated_site_diff_keeps_its_own_selection_and_source():
    support = {"status": "RISK", "rule_id": "MMSA-008", "reason": "DISTRIBUTION_WIDE_OR_BIMODAL",
               "shift_sigma": .1, "var_ratio": 1.1, "sites": [{"site": 7, "std": .2}]}
    diagnosis = diagnose_item("cpk", item(), related=(support, {"filename": "other.stdf"}, {"sites": [7, 8]}))
    assert diagnosis["remark"] == "1. " + diagnosis["knowledge"]["historical_remark"]
    assert diagnosis["related_evidence"]["source"]["filename"] == "other.stdf"
    assert "Site Diff" not in diagnosis["remark"]


@pytest.mark.parametrize('module', ['cpk', 'cgm'])
def test_site_diff_lists_observed_mean_extremes_and_spread_sites_only(module):
    support = {"status": "RISK", "rule_id": "MMSA-003", "secondary_rule_id": "MMSA-004",
               "sites": [{"site": 1, "mean": 2, "std": .1},
                         {"site": 4, "mean": 5, "std": 2},
                         {"site": 7, "mean": 8, "std": .1},
                         {"site": 9, "mean": 5, "std": .1}]}
    diagnosis = diagnose_item(module, item(), related=(support, {"filename": "other.stdf"}, {}))
    assert diagnosis["remark"].splitlines()[0] == "1. Site Diff: S1, S4, S7."
    assert "S9" not in diagnosis["remark"]
    assert "Associated" not in diagnosis["remark"] and "sigma" not in diagnosis["remark"]


@pytest.mark.parametrize('rule', ['MMSA-005', 'MMSA-006', 'MMSA-007', 'MMSA-008'])
def test_distribution_and_inconclusive_results_do_not_claim_site_diff(rule):
    support = {"status": "RISK", "rule_id": rule,
               "sites": [{"site": 1, "mean": 4, "std": .1}, {"site": 2, "mean": 6, "std": .2}]}
    diagnosis = diagnose_item('cpk', item(), related=(support, {}, {}))
    assert 'Site Diff' not in diagnosis['remark']


def test_spread_difference_lists_every_site_above_the_existing_threshold_including_site_zero():
    support = dict(status='RISK', rule_id='MMSA-004', median_site_std=1,
                   s2s={'bands': {'var_ratio': {'red_ge': 2.5}}},
                   sites=[{'site': 0, 'std': 3}, {'site': 2, 'std': 4},
                          {'site': 3, 'std': 1}, {'site': 4, 'std': 1}, {'site': 5, 'std': 1}])
    diagnosis = diagnose_item('cpk', item(), related=(support, {}, {}))
    assert diagnosis['remark'].splitlines()[0] == '1. Site Diff: S0, S2.'


def test_cgm_starts_with_one_advice_sentence_without_metrics_or_history_prefix():
    data = dict(test_num=1003, test_name="ExampleResolution", conditions={}, status="PASS",
                cg=.5, sites=[{"site": 0, "cg": .5}, {"site": 2, "cg": 2.1}],
                _cg_pass_threshold=1.33, cpk_override_applied=True, supported_by_bin1_cpk=2.4)
    before = deepcopy(data)
    diagnosis = diagnose_item('cgm', data)
    lines = diagnosis['remark'].splitlines()
    assert lines[0] == '1. Check site repeatability, contact and distribution.'
    assert lines[1] == '2. ' + diagnosis['knowledge']['historical_remark']
    assert all(token not in diagnosis['remark'] for token in
               ['Min Cg=', 'Accepted by', 'Matching bin1 CPK=', 'Historical project reference'])
    assert data == before


def test_cpk_without_current_site_diff_or_history_does_not_invent_a_recommendation():
    data = item()
    data.update(test_name='UnknownTrimParameter', test_num=999)
    assert diagnose_item('cpk', data)['remark'] == ''


def test_manual_additions_follow_automatic_parts_and_can_be_cleared():
    from app.release_remark_service import compose_remark
    diagnosis = diagnose_item('cpk', item())
    row = {'cpk': {'diagnosis': diagnosis}}
    note = dict(tags=['drift', 'outliers'], suggestion='Check contact.', remark='Recheck S4.')
    lines = compose_remark(row, 'cpk', note).splitlines()
    assert lines == ['1. ' + diagnosis['knowledge']['historical_remark'],
                     '2. sequence drift observed.', '3. outlying samples observed.',
                     '4. Check contact.', '5. Recheck S4.']
    row['cpk']['diagnosis'] = diagnose_item('cpk', {**item(), 'test_name': 'Unknown'})
    assert compose_remark(row, 'cpk', dict(tags=[], remark='')) == ''
    assert compose_remark(row, 'cpk', {**note, 'final_remark': 'Engineer final'}) == 'Engineer final'


def test_outlier_only_describes_its_own_evidence():
    data = dict(test_num=999, test_name="Unknown", conditions={}, status="OUTLIER_CANDIDATES",
                candidate_count=3, head=1, site=4, n=38)
    text = diagnose_outlier(data)["remark"]
    assert "3 statistical outlier" in text and "H1/S4" in text
    assert "CPK" not in text and "do not establish a physical defect" in text
