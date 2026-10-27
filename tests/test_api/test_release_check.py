"""Real single-file upload, selection, matching and evidence through HTTP."""
import gzip
import struct

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import release_check_service as service
from app.routers.release_check import router
from deepyield.compute.release_stdf import read_release_wide_all


def record(kind, sub, data):
    return struct.pack("<HBB", len(data), kind, sub) + data


def cn(text):
    data = text.encode("ascii")
    return bytes([len(data)]) + data


def stdf(values=None, unit="V"):
    data = record(0, 10, bytes([2, 4]))
    for i, value in enumerate(values or [7., 7., 8., 8., 9., 9., 1.1, 1.1, 14.9, 14.9]):
        site, bin_number = i % 2 + 1, 1 if i < 6 else 2
        optional = struct.pack("<Bbbbff", 2, 0, 0, 0, 1., 15.) + cn(unit) if i == 0 else b""
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


def test_missing_unit_only_warns_without_changing_module_results(client):
    rows = []
    for unit in ("V", ""):
        response = client.post("/api/release-check/datasets", files={"file": ("unit-check.stdf", stdf(unit=unit))})
        assert response.status_code == 200, response.text
        dataset = response.json()
        response = client.post(f"/api/release-check/datasets/{dataset['id']}/run", json={
            "modules": ["cpk", "cgm", "mmsa"],
            **{module: {"minimum_samples": 2} for module in ("cpk", "cgm", "mmsa")}})
        assert response.status_code == 200, response.text
        report = response.json()
        rows.append(report["rows"][0])
        if not unit:
            assert any("单位缺失" in warning for warning in report["warnings"])
    for module in ("cpk", "cgm", "mmsa"):
        for key in ("value", "status", "rule_id", "override"):
            assert rows[1][module][key] == rows[0][module][key]
        assert rows[1][module]["value"] is not None


def test_unexecuted_test_uses_catalog_identity_without_guessing_specs():
    reports = {"cpk": {"items": [], "unexecuted_tests": [{"column": "TEST_1007",
        "status": "INSUFFICIENT_DATA", "reason": "NO_SELECTED_EXECUTION", "n": 0}]}}
    rows = service.merge_reports(reports, [{"column": "TEST_1007", "test_num": 1007,
        "name": "P1007_EXAMPLE_MEASUREMENT", "unit": "mAMPS", "low_limit": 70}])
    assert rows[0]["number"] == 1007
    assert rows[0]["name"] == "P1007_EXAMPLE_MEASUREMENT"
    assert rows[0]["lsl"] is None and rows[0]["unit"] is None


def test_missing_name_is_explicit_not_a_generated_test_id():
    reports = {"cpk": {"items": [], "unexecuted_tests": [{"column": "TEST_1007",
        "status": "INSUFFICIENT_DATA", "reason": "NO_SELECTED_EXECUTION", "n": 0}]}}
    row = service.merge_reports(reports, [{"column": "TEST_1007", "test_num": 1007}])[0]
    assert row["name"] == "名称未记录" and row["number"] == 1007


def run(client, dataset, **payload):
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run", json={"modules": ["cpk", "cgm"], **payload})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_inherits_first_specs_and_download_is_standalone(client, tmp_path):
    dataset = upload(client)
    assert dataset["record_count"] == 10 and dataset["sites"] == [1, 2]
    assert len(dataset["parts"]) == 10  # identical names never deduplicated
    downloaded = client.get(f"/api/release-check/datasets/{dataset['id']}/parquet")
    path = tmp_path / "standalone.parquet"
    path.write_bytes(downloaded.content)
    frame, meta = read_release_wide_all(path)
    assert len(frame) == 10 and meta["all_attempts_preserved"]
    for events in frame["__test_records"]:
        assert (events[0]["unit"], events[0]["low_limit"], events[0]["high_limit"]) == ("V", 1, 15)


def test_real_cgm_override_and_histogram_exact_sample_population(client):
    dataset = upload(client)
    result = run(client, dataset, cpk={"part_indices": [1, 2, 3, 4, 5, 6]}, cgm={"sites": [1, 2]})
    row = result["rows"][0]
    assert row["cpk"]["n"] == 6 and row["cgm"]["n"] == 10
    assert row["cpk"]["mean"] == pytest.approx(8)
    assert row["cpk"]["std"] == pytest.approx((4 / 5) ** .5)
    assert row["cpk"]["cp"] == pytest.approx(14 / (6 * (4 / 5) ** .5))
    assert row["cgm"]["value"] < 1 and row["cgm"]["override"]
    assert row["cgm"]["rule_id"] == "CGM-001" and row["status"] == "PASS"
    detail = client.get(f"/api/release-check/datasets/{dataset['id']}/runs/{result['id']}/items/{row['key']}").json()
    assert sum(sum(s["histogram"]) for s in detail["details"]["cgm"]["sites"]) == 10
    assert sum(sum(s["histogram"]) for s in detail["details"]["cpk"]["sites"]) == 6
    assert [s["mean"] for s in detail["details"]["cgm"]["sites"]] == pytest.approx([8, 8])
    exported = client.get(f"/api/release-check/datasets/{dataset['id']}/runs/{result['id']}/report").json()
    assert exported["mode"] == "real" and exported["reports"]["cpk"]["selection"]["part_indices"] == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize('low,high,std,expected_cp', [
    (0., 6., 1., 1.), (None, 6., 1., None), (0., None, 1., None),
    (6., 0., 1., None), (6., 6., 1., None), (0., 6., 0., None),
    (0., 6., None, None),
])
def test_cpk_table_statistics_keep_zero_and_unavailable_values(low, high, std, expected_cp):
    cell = service._cell({'status': 'NOT_CALCULABLE', 'n': 2, 'mean': 0., 'std': std,
                          'effective_lsl': low, 'effective_usl': high}, 'cpk')
    assert cell['mean'] == 0. and cell['std'] == std
    assert cell['cp'] == expected_cp


def test_cgm_only_never_implicitly_uses_unrequested_cpk(client):
    dataset = upload(client)
    result = run(client, dataset, modules=["cgm"])
    assert not result["rows"][0]["cgm"]["override"]
    assert result["rows"][0]["status"] == "FAIL"


def test_cgm_run_contains_all_site_statistics_without_opening_items(client):
    data = record(0, 10, bytes([2, 4]))
    for repeat in range(3):
        for site in range(1, 5):
            data += record(5, 10, bytes([1, site]))
            for number in (1001, 1002):
                if number == 1002 and site == 4:
                    continue
                value = 5 + (site - 1) * (repeat - 1)
                optional = struct.pack('<Bbbbff', 2, 0, 0, 0, 1., 9.) + cn('V') if repeat == 0 and site == 1 else b''
                ptr = struct.pack('<IBBBBf', number, 1, site, 0, 0, value) + cn(f'TEST{number}') + cn('') + optional
                data += record(15, 10, ptr)
            prr = struct.pack('<BBBHHHhhI', 1, site, 0, 2, 1, 1, 0, 0, 0) + cn('PART') + cn('') + b'\0'
            data += record(5, 20, prr)
    response = client.post('/api/release-check/datasets', files={'file': ('missing-site.stdf', data)})
    assert response.status_code == 200, response.text
    dataset = response.json()
    result = run(client, dataset, modules=['cgm'], cgm={'sites': [1, 2, 3, 4]})
    assert len(result['rows']) == 2
    for row in result['rows']:
        assert [site['site'] for site in row['cgm']['sites']] == [1, 2, 3, 4]
        assert row['cgm']['sites'][0]['cg'] == 'Infinity'
    missing = next(row for row in result['rows'] if row['number'] == 1002)
    assert missing['cgm']['value'] == pytest.approx(.20 * 2 / 3)
    assert missing['cgm']['status'] == missing['status'] == 'FAIL'
    assert missing['cgm']['sites'][-1] == {'site': 4, 'n': 0, 'cg': None, 'status': 'INSUFFICIENT_DATA'}


def test_infinite_cg_survives_json_charts_matrix_and_report(client):
    response = client.post('/api/release-check/datasets', files={'file': ('stable.stdf', stdf([7., 8.] * 5))})
    assert response.status_code == 200, response.text
    dataset = response.json()
    result = run(client, dataset, modules=['cpk', 'cgm', 'mmsa'])
    row = result['rows'][0]
    assert row['cgm']['value'] == 'Infinity' and row['cgm']['status'] == 'PASS'
    assert row['mmsa']['status'] == 'NOT_APPLICABLE'
    root = f"/api/release-check/datasets/{dataset['id']}/runs/{result['id']}"
    for path in (f"/items/{row['key']}/charts/cgm", '/chart-matrix?sort=cg', '/report'):
        response = client.get(root + path)
        assert response.status_code == 200, response.text
        response.json()
    chart = client.get(root + f"/items/{row['key']}/charts/cgm").json()
    assert all(site['cg'] == 'Infinity' and site['cg_status'] == 'PASS' for site in chart['sites'])
    assert '∞' in row['cgm']['diagnosis']['current_remark']


def test_per_site_insufficient_population_cannot_be_accepted(client):
    dataset = upload(client)
    result = run(client, dataset, cgm={"minimum_samples": 6})
    row = result["rows"][0]
    assert row["cgm"]["status"] == "NOT_EVALUATED" and not row["cgm"]["override"]
    assert row["status"] == "REVIEW_REQUIRED"


@pytest.mark.parametrize("selection", [{"minimum_samples": 1}, {"minimum_samples": True}, {"sites": [999]}, {"part_indices": [999]}, {"test_columns": ["missing"]}])
def test_invalid_selections_rejected(client, selection):
    dataset = upload(client)
    response = client.post(f"/api/release-check/datasets/{dataset['id']}/run", json={"modules": ["cpk"], "cpk": selection})
    assert response.status_code == 422


@pytest.mark.parametrize("name,data", [("bad.csv", b"x"), ("bad.stdf", b"broken"), ("bad.stdf.gz", b"badgzip"),
    ("corrupt.stdf.gz", b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\x00" + b"\xff" * 30)])
def test_invalid_import_keeps_no_partial_session(client, name, data):
    response = client.post("/api/release-check/datasets", files={"file": (name, data)})
    assert response.status_code == 422
    assert not service._datasets


def test_compressed_stdf_and_expired_session(client):
    response = client.post("/api/release-check/datasets", files={"file": ("valid.stdf.gz", gzip.compress(stdf()))})
    assert response.status_code == 200
    dataset = response.json()
    assert client.delete(f"/api/release-check/datasets/{dataset['id']}").status_code == 204
    assert client.get(f"/api/release-check/datasets/{dataset['id']}/parquet").status_code == 404


def test_spec_variants_are_not_combined_and_missing_check_does_not_pass():
    def item(key, unit):
        return {"group_id": key, "column": "1001_VDD", "test_name": "VDD", "test_num": 1001,
                "unit": unit, "effective_lsl": 1., "effective_usl": 15., "conditions": {}, "status": "PASS",
                "n": 3, "cpk": 2.5, "cg": 2.0, "reason": None, "threshold_band": ">= 2.0"}
    rows = service.merge_reports({"cpk": {"items": [item("a", "V")]}, "cgm": {"items": [item("b", "A")]}})
    assert len(rows) == 2 and all(row["status"] == "REVIEW_REQUIRED" for row in rows)


def test_unexecuted_measurement_stays_visible():
    report = {"items": [], "unexecuted_tests": [{"column": "never", "status": "INSUFFICIENT_DATA", "n": 0, "reason": "NO_SELECTED_EXECUTION"}]}
    rows = service.merge_reports({"cpk": report, "cgm": report})
    assert rows[0]["status"] == "REVIEW_REQUIRED" and "没有执行" in rows[0]["reason"]


def test_idle_sessions_expire_without_leaving_unreachable_slots(client, monkeypatch):
    dataset = upload(client)
    stored = service.get_dataset(dataset["id"])
    monkeypatch.setattr(service.time, "monotonic", lambda: stored.last_access + service.SESSION_IDLE_SECONDS + 1)
    assert client.get(f"/api/release-check/datasets/{dataset['id']}/parquet").status_code == 404
    assert not service._datasets
