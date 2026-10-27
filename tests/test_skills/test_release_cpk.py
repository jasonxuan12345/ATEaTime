import json

from deepyield.skills import get_skill, run_skill
from deepyield.compute.release_stdf import convert_stdf_to_wide_all


def test_release_cpk_skill_registered_and_roundtrips(tmp_path):
    from tests.test_compute.test_release_stdf import record, part, ptr, specification
    source = tmp_path / "sample.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + b"".join(
        part(name=f"P{i}", tests=ptr(value, optional=specification(low=1, high=9)))
        for i, value in enumerate([4, 5, 6])))
    output = convert_stdf_to_wide_all(source, tmp_path / "wide.parquet")
    assert get_skill("release_bin1_cpk").category == "capability"
    result = run_skill("release_bin1_cpk", {"file_paths": [str(output)],
                                          "params": {"part_indices": [1, 2, 3]}})
    assert result.success
    assert result.data["report"]["items"][0]["n"] == 3
    json.dumps(result.to_dict(serialize=True), allow_nan=False)


def test_release_cpk_skill_validates_inputs():
    for payload in ({}, {"file_paths": ["a", "b"]},
                    {"file_paths": ["a"], "params": {"threshold": 1.33}}):
        result = run_skill("release_bin1_cpk", payload)
        assert not result.success and result.errors
