import json

from deepyield.skills import get_skill, run_skill
from deepyield.compute.release_stdf import convert_stdf_to_wide_all


def test_release_cgm_skill_roundtrip(tmp_path):
    from tests.test_compute.test_release_stdf import record, part, ptr, specification
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + b"".join(part(name=f"P{i}",
        tests=ptr(value, optional=specification(low=1, high=9))) for i, value in enumerate([4, 5, 6])))
    output = convert_stdf_to_wide_all(source, tmp_path / "wide.parquet")
    assert get_skill("release_cgm").category == "capability"
    result = run_skill("release_cgm", {"file_paths": [str(output)]})
    assert result.success and result.data["report"]["items"][0]["n"] == 3
    json.dumps(result.to_dict(serialize=True), allow_nan=False)


def test_release_cgm_skill_rejects_invalid_input():
    for payload in ({}, {"file_paths": ["a", "b"]},
                    {"file_paths": ["a"], "params": {"threshold": 1.33}}):
        assert not run_skill("release_cgm", payload).success
