"""Site Diff through the Release HTTP surface: run, detail, export, multi-source."""
import struct

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import release_check_service as service
from app.routers.release_check import router


def record(kind, sub, data):
    return struct.pack("<HBB", len(data), kind, sub) + data


def cn(text):
    data = text.encode("ascii")
    return bytes([len(data)]) + data


def stdf():
    data = record(0, 10, bytes([2, 4]))
    for i, value in enumerate([7., 7., 8., 8., 9., 9., 1.1, 1.1, 14.9, 14.9]):
        site, bin_number = i % 2 + 1, 1 if i < 6 else 2
        optional = struct.pack("<Bbbbff", 2, 0, 0, 0, 1., 15.) + cn("V") if i == 0 else b""
        ptr = struct.pack("<IBBBBf", 1001, 1, site, 0, 0, value) + cn("VDD") + cn("") + optional
        prr = struct.pack("<BBBHHHhhI", 1, site, 0, 1, bin_number, bin_number, 0, 0, 0) + cn("SAME") + cn("") + b"\0"
        data += record(5, 10, bytes([1, site])) + record(15, 10, ptr) + record(5, 20, prr)
    return data


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(service, "_datasets", {})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client
    service._datasets.clear()


def upload(client):
    response = client.post("/api/release-check/datasets", files={"file": ("real.stdf", stdf())})
    assert response.status_code == 200, response.text
    return response.json()


def test_three_module_run_diagnoses_low_repeatability_even_with_good_cpk(client):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run",
        json={"modules": ["cpk", "cgm", "mmsa"], "mmsa": {"minimum_samples": 3}})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    # bin1 CPK is 2.6, but min Cg 0.47 triggers the gate via the OR condition:
    # branch 1 with 2 failing sites -> local hardware fault, merged verdict RISK.
    assert row["mmsa"]["status"] == "RISK" and row["mmsa"]["rule_id"] == "MMSA-001"
    assert row["mmsa"]["reason"] == "少数 Site 的 Cg 未达标，建议检查接触和局部测试硬件"
    assert row["status"] == "RISK"
    assert 'shift_spec_pct' in row['mmsa'] and 'var_ratio' in row['mmsa']
    detail = client.get(f"/api/release-check/datasets/{dataset['id']}/runs/{response.json()['id']}"
                        f"/items/{row['key']}").json()
    mmsa = detail["details"]["mmsa"]
    assert row['mmsa']['shift_spec_pct'] == mmsa['s2s']['shift_spec_pct']
    assert row['mmsa']['var_ratio'] == mmsa['s2s']['var_ratio']
    assert mmsa["s2s"]["gate"] == {"trigger": "ANY", "cpk_lt": 2.0, "cg_lt": 1.33}
    assert mmsa["s2s"]["overall_bin1_cpk"] == pytest.approx(2.6, abs=.05)
    assert mmsa["s2s"]["cgm_min_cg"] < 1.33
    assert len(mmsa["sites"]) == 2 and mmsa["sites"][0]["cg"] is None
    exported = client.get(f"/api/release-check/datasets/{dataset['id']}"
                          f"/runs/{response.json()['id']}/report").json()
    assert exported["reports"]["mmsa"]["report_type"] == "release_mmsa"
    assert exported["reports"]["mmsa"]["policy"]["cg_pass_threshold"] == 1.33


def test_mmsa_without_bin1_population_still_judges_by_cgm(client):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run",
        json={"modules": ["mmsa"], "mmsa": {"part_indices": [7, 8, 9, 10]}})
    assert response.status_code == 200, response.text
    result = response.json()
    row = result["rows"][0]
    assert row["mmsa"]["status"] == "RISK" and row["mmsa"]["rule_id"] == "MMSA-001"
    assert any("触发判定仅能按 Cg 进行" in warning for warning in result["warnings"])


def test_mmsa_only_run_builds_local_supports(client):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run",
        json={"modules": ["mmsa"]})
    assert response.status_code == 200, response.text
    result = response.json()
    row = result["rows"][0]
    # Local bin1 CPK support is computed; min Cg 0.47 triggers branch 1.
    assert row["mmsa"]["status"] == "RISK" and row["mmsa"]["rule_id"] == "MMSA-001"
    assert row["cpk"] is None and row["cgm"] is None


def test_multi_source_run_accepts_third_module_source(client):
    dataset = upload(client)
    selection = {"sites": [1, 2], "tests": ["1001_VDD"]}
    response = client.post("/api/release-check/runs", json={
        "cpk": {"dataset_id": dataset["id"], **selection},
        "cgm": {"dataset_id": dataset["id"], **selection},
        "mmsa": {"dataset_id": dataset["id"], **selection}})
    assert response.status_code == 200, response.text
    row = response.json()["rows"][0]
    assert row["mmsa"]["dataset_id"] == dataset["id"]
    assert row["mmsa"]["status"] == "RISK" and row["mmsa"]["rule_id"] == "MMSA-001"


def test_multi_source_run_without_mmsa_keeps_two_module_shape(client):
    dataset = upload(client)
    selection = {"sites": [1, 2], "tests": ["1001_VDD"]}
    response = client.post("/api/release-check/runs", json={
        "cpk": {"dataset_id": dataset["id"], **selection},
        "cgm": {"dataset_id": dataset["id"], **selection}})
    assert response.status_code == 200, response.text
    assert all(row["mmsa"] is None for row in response.json()["rows"])


def test_invalid_mmsa_selection_rejected(client):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run",
        json={"modules": ["mmsa"], "mmsa": {"minimum_samples": 1}})
    assert response.status_code == 422


def test_single_site_explanation_reaches_matrix_detail_and_export(client):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run",
        json={"modules": ["cpk", "cgm", "mmsa"], "mmsa": {"sites": [1]}})
    assert response.status_code == 200, response.text
    run = response.json()
    row = run["rows"][0]
    assert row["mmsa"]["status"] == "INSUFFICIENT_DATA"
    expected = "仅有 1 个有效 site，至少需要 2 个（每个 site 至少 2 条有效记录）"
    assert row["mmsa"]["reason"] == expected and row["mmsa"]["n"] == 5
    url = f"/api/release-check/datasets/{dataset['id']}/runs/{run['id']}"
    detail = client.get(f"{url}/items/{row['key']}").json()
    assert detail["details"]["mmsa"]["reason"] == expected
    exported = client.get(f"{url}/report").json()
    item = exported["reports"]["mmsa"]["items"][0]
    assert item["reason"] == "TOO_FEW_USABLE_SITES" and item["site_count"] == 1
