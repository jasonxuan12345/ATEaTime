"""Use existing memory sessions through a simulated HTTP API; no measurement IO."""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import release_check_service as sessions
from app.routers.release_check import router
from deepyield.compute.release_control import ReleaseCancelled
from deepyield.compute.release_performance_log import LOGGER
from tests.test_compute.outlier_fixtures import dataset


@pytest.fixture
def client(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('This endpoint must not import/read STDF or Parquet')
    monkeypatch.setattr(sessions, 'import_release_stdf', forbidden)
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    frame, metadata = dataset(test_count=2, compact=True)
    source = sessions.ReleaseDataset(None, Path('not-opened.parquet'), 'not-opened.stdf', frame, metadata)
    monkeypatch.setattr(sessions, '_datasets', {'memory': source})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as http:
        yield http, source


def test_computes_from_imported_memory_and_retains_existing_release_state(client, caplog):
    http, source = client
    LOGGER.addHandler(caplog.handler)
    try:
        response = http.post('/api/release-check/datasets/memory/outliers?job_id=memory-outliers', json={})
    finally:
        LOGGER.removeHandler(caplog.handler)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['dataset_id'] == 'memory'
    report = result['report']
    assert report['chips'][0]['part_index'] == 41
    assert report['chips'][0]['hit_count'] == 2
    assert report['algorithm']['sigma_multiplier'] == 6.
    assert report['algorithm']['minimum_samples_per_site'] == 30
    assert source.runs == {} and source.chart_samples == {}
    assert 'outlier_statistics;dur=' in response.headers['Server-Timing']
    assert response.headers['X-Release-Request-ID']
    events = [json.loads(record.getMessage()) for record in caplog.records if record.name == LOGGER.name]
    summary = next(event for event in events if event['event'] == 'operation_end')
    assert summary['counts']['outliers.candidate_records'] == 1
    assert summary['outcome'] == 'ok'
    assert 'generated.stdf' not in json.dumps(events) and 'TEST NAME' not in json.dumps(events)
    assert http.get('/api/release-check/jobs/memory-outliers').status_code == 404


def test_selection_and_threshold_options(client):
    http, _ = client
    response = http.post('/api/release-check/datasets/memory/outliers', json={
        'sites': [2], 'test_columns': ['TEST_1001'], 'sigma_multiplier': 9,
        'minimum_samples_per_site': 35})
    assert response.status_code == 200, response.text
    report = response.json()['report']
    assert report['chips'] == [] and len(report['baselines']) == 1
    assert report['selection']['selected_record_count'] == 40


def test_plot_view_reads_actual_selected_dies_and_test_values_from_memory(client):
    http, source = client
    response = http.post('/api/release-check/datasets/memory/outliers', json={'sites': [1]})
    result = response.json()
    group = result['report']['baselines'][0]['group_id']
    url = f"/api/release-check/datasets/memory/outliers/{result['report_id']}/view"
    response = http.get(url, params={'group_id': group})
    assert response.status_code == 200, response.text
    view = response.json()
    assert view['points'] and view['dies']
    assert all(point['site'] == 1 for point in view['points'])
    selected = set(result['report']['selection']['selected_part_indices'])
    assert all(point['part_index'] in selected for point in view['points'])
    actual = {(str(row['WAFER_ID']), int(row['DIE_X']), int(row['DIE_Y'])) for _, row in source.frame.iterrows()
              if row['__part_index'] in selected}
    assert {(die['wafer'], die['x'], die['y']) for die in view['dies']} == actual
    assert view['total_points'] == len(view['points']) and not view['sampled']
    assert http.get(url, params={'group_id': 'other-group'}).status_code == 404
    assert http.get(url.replace(result['report_id'], 'expired'), params={'group_id': group}).status_code == 404


@pytest.mark.parametrize('payload', [
    {'sigma_multiplier': True}, {'sigma_multiplier': '6'}, {'sigma_multiplier': 0},
    {'minimum_samples_per_site': 3}, {'minimum_samples_per_site': True},
    {'minimum_samples_per_site': 4.5}, {'part_indices': [True]},
    {'sites': [999]}, {'test_columns': ['missing']}, {'unknown_option': 1},
])
def test_invalid_options_return_422(client, payload):
    http, _ = client
    assert http.post('/api/release-check/datasets/memory/outliers', json=payload).status_code == 422


def test_expired_source_returns_404(client):
    http, _ = client
    assert http.post('/api/release-check/datasets/absent/outliers', json={}).status_code == 404


def test_cancelled_job_does_not_publish_a_report(client, monkeypatch):
    from app import release_outlier_service
    http, source = client
    def cancelled(*args, **kwargs):
        raise ReleaseCancelled('操作已取消')
    monkeypatch.setattr(release_outlier_service, 'build_release_outlier_report', cancelled)
    response = http.post('/api/release-check/datasets/memory/outliers', json={})
    assert response.status_code == 422 and source.runs == {}
