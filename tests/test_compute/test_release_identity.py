"""Cross-file identity intentionally uses only Test ID and Test Name."""
import pytest

from deepyield.compute.release_cgm import cross_source_identity
from deepyield.compute.release_mmsa import _matcher


def test_only_id_and_name_define_cross_file_identity():
    base = {"test_num": 0, "test_name": "VDD"}
    assert cross_source_identity(base) == cross_source_identity({**base,
        "record_type": "MPR", "result_index": 7, "unit": "mV", "lsl": 0, "usl": 3,
        "conditions": {"job_revision": "other"}, "spec": {"unit": "A"}})


@pytest.mark.parametrize("item", [{"test_name": "VDD"}, {"test_num": 1},
    {"test_num": None, "test_name": "VDD"}, {"test_num": 1, "test_name": ""}])
def test_missing_identity_fields_do_not_match(item):
    assert cross_source_identity(item) is None


def test_pair_does_not_have_concatenation_collisions():
    assert cross_source_identity({"test_num": 1, "test_name": "23"}) != cross_source_identity(
        {"test_num": 12, "test_name": "3"})


@pytest.mark.parametrize("duplicate_side", ["support", "target"])
def test_mmsa_cross_file_match_rejects_ambiguity_on_either_side(duplicate_side):
    identity = {"test_num": 1001, "test_name": "VDD"}
    support = [{**identity, "group_id": "support-1"}]
    targets = [{**identity, "group_id": "target-1"}]
    if duplicate_side == "support":
        support.append({**identity, "group_id": "support-2"})
    else:
        targets.append({**identity, "group_id": "target-2"})
    match = _matcher(support, False, targets)
    assert all(match(group["group_id"], group) is None for group in targets)


def test_mmsa_unique_cross_file_match_ignores_unrelated_definition_fields():
    identity = {"test_num": 1001, "test_name": "VDD"}
    support = {**identity, "group_id": "support", "unit": "V", "conditions": {}}
    target = {**identity, "group_id": "target", "unit": "mV", "result_index": 3}
    assert _matcher([support], False, [target])("target", target) is support


def test_mmsa_same_file_match_preserves_group_id_with_repeated_identity():
    identity = {"test_num": 1001, "test_name": "VDD"}
    items = [{**identity, "group_id": "first"}, {**identity, "group_id": "second"}]
    match = _matcher(items, True, items)
    assert all(match(item["group_id"], item) is item for item in items)
