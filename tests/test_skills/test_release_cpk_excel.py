import json
from pathlib import Path

from deepyield.skills import get_skill, run_skill
from tests.test_compute.test_release_cpk_excel import report_fixture


def test_registered_skill_generates_actual_workbook(tmp_path):
    report = report_fixture()
    report["items"] = report["items"][:8]
    source = tmp_path / "cpk.json"
    source.write_text(json.dumps(report), encoding="utf-8")
    assert get_skill("release_cpk_excel").category == "reporting"
    output = run_skill("release_cpk_excel", {"file_paths": [str(source)],
                                           "params": {"output_dir": str(tmp_path)}})
    assert output.success
    assert Path(output.artifacts["xlsx"]).is_file()
    assert output.data["sheet_rows"]["CPK_too_Good"] == 1
    json.dumps(output.to_dict(serialize=True), allow_nan=False)


def test_registered_skill_validates_inputs():
    for payload in ({}, {"file_paths": ["a", "b"]},
                    {"file_paths": ["a"], "params": {"output_dir": "out", "threshold": 400}},
                    {"file_paths": ["a"]}):
        output = run_skill("release_cpk_excel", payload)
        assert not output.success and output.errors
