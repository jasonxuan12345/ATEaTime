"""Timing diagnostics on generated memory objects only; no STDF/Parquet IO."""
import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pandas as pd
import pyarrow as pa
import pytest

from app import release_check_service as service
from app.routers.release_check import router
from app.release_operations import track
from deepyield.compute.release_control import (
    Operation, ReleaseCancelled, checkpoint, measure, operation, request_trace, stage,
)
from deepyield.compute.release_event_store import EventStore, PartEvents
from deepyield.compute.release_performance_log import LOGGER
from deepyield.compute.release_stdf import SCHEMA_VERSION


@pytest.fixture(autouse=True)
def no_measurement_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('This test must not read or write STDF/Parquet')
    monkeypatch.setattr(service, 'import_release_stdf', forbidden)
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    monkeypatch.setattr('deepyield.compute.release_stdf.read_release_wide_all', forbidden)


@pytest.fixture
def logs(caplog):
    LOGGER.addHandler(caplog.handler)
    try:
        yield lambda: [json.loads(record.getMessage()) for record in caplog.records
                       if record.name == LOGGER.name]
    finally:
        LOGGER.removeHandler(caplog.handler)


def dataset(compact=False):
    conditions = json.dumps(dict(part_type='PRIVATE_PRODUCT', job_name='PRIVATE_PROGRAM',
        job_revision='A', test_code='FT', test_temperature='25'))
    tests = [dict(column=f'COLUMN_{i}', test_num=i, name=f'PRIVATE_TEST_{i}',
        record_type='PTR', result_index=None, unit='V', low_limit=0., high_limit=20.)
        for i in (1001, 1002)]
    events = [[dict(test, test_flag=0, param_flag=0, result_valid=True,
                   result=5. + part + index / 10, record_sequence=part * 10 + index)
               for index, test in enumerate(tests)] for part in range(8)]
    if compact:
        store = EventStore(pa.array([event for part in events for event in part]))
        events = [PartEvents(store, i * 2, i * 2 + 2) for i in range(8)]
    rows = [dict(__part_index=i, Name='PRIVATE_PART', SBIN=1 if i < 6 else 2,
                 PART_FLAG=0, SITE=i % 2, __conditions_json=conditions, __test_records=events[i])
            for i in range(8)]
    metadata = dict(schema_version=SCHEMA_VERSION, source_file='PRIVATE_FILE.stdf',
                    source_sha256='private_hash', test_catalog=tests)
    return service.ReleaseDataset(None, Path('never-opened.parquet'), 'PRIVATE_FILE.stdf',
                                  pd.DataFrame(rows), metadata)


def selections():
    return dict(cpk=dict(part_indices=None, sites=None, test_columns=None, minimum_samples=2),
        cgm=dict(part_indices=None, sites=None, test_columns=None, minimum_samples_per_site=2),
        mmsa=dict(part_indices=None, sites=None, test_columns=None, minimum_samples_per_site=2))


@pytest.mark.parametrize('compact', [False, True])
def test_reports_unchanged_and_diagnostics_are_bounded(monkeypatch, logs, compact):
    source = dataset(compact)
    monkeypatch.setattr(service, '_datasets', {'memory': source})
    monkeypatch.setenv('DEEPYIELD_RELEASE_TIMING', '0')
    original = service.run_checks('memory', selections(), ['cpk', 'cgm', 'mmsa'])
    expected = source.runs[original['id']][1]
    monkeypatch.setenv('DEEPYIELD_RELEASE_TIMING', '1')
    with track('generated-job', 'run_checks'):
        actual = service.run_checks('memory', selections(), ['cpk', 'cgm', 'mmsa'])
    assert source.runs[actual['id']][1] == expected
    records = logs()
    summary = next(record for record in records if record['event'] == 'operation_end')
    assert summary['outcome'] == 'ok'
    assert summary['counts']['run.result_rows'] == 2
    assert summary['counts']['cache.group_miss'] == 1
    assert summary['counts']['cache.group_hit'] >= 2
    assert summary['counts']['groups.' + ('compact_parts' if compact else 'dict_parts')] == 8
    assert summary['measurements']['cpk.numeric']['calls'] == 2
    assert summary['measurements']['cgm.statistics']['calls'] == 2
    assert summary['measurements']['mmsa.statistics']['calls'] == 2
    assert set(summary['stages_ms']) == {'cpk', 'cgm', 'mmsa'}
    assert all(record['request_id'] == summary['request_id'] for record in records)
    assert len(records) < 40
    text = json.dumps(records)
    for secret in ('PRIVATE_', 'private_hash', 'never-opened', 'sample_refs', 'COLUMN_'):
        assert secret not in text


def test_http_includes_encoding_boundary_and_skips_job_polling(monkeypatch, logs):
    monkeypatch.setattr(service, '_datasets', {'memory': dataset(True)})
    app = FastAPI()
    app.include_router(router)
    payload = {module: dict(dataset_id='memory', minimum_samples=2) for module in selections()}
    with TestClient(app) as client:
        response = client.post('/api/release-check/runs?job_id=generated-job', json=payload)
        assert response.status_code == 200, response.text
        request_id = response.headers['X-Release-Request-ID']
        assert 'total;dur=' in response.headers['Server-Timing']
        result = response.json()
        detail = client.get(f"/api/release-check/datasets/memory/runs/{result['id']}/items/{result['rows'][0]['key']}")
        assert detail.status_code == 200
        exported = client.get(f"/api/release-check/datasets/memory/runs/{result['id']}/report")
        assert exported.status_code == 200
        assert int(exported.headers['Content-Length']) == len(exported.content)
        assert 'attachment' in exported.headers['Content-Disposition']
        assert exported.json()['reports']['cpk']['items']
        before = len(logs())
        client.get('/api/release-check/jobs/absent')
        assert len(logs()) == before
        invalid = client.post('/api/release-check/runs', json={'PRIVATE_PAYLOAD': True})
        assert invalid.status_code == 422
        assert logs()[-1]['event'] == 'http_end' and logs()[-1]['status'] == 422
    end = next(record for record in logs() if record['event'] == 'http_end'
               and record['request_id'] == request_id)
    operation_end = next(record for record in logs() if record['event'] == 'operation_end'
                         and record['request_id'] == request_id)
    assert end['handler_ms'] >= operation_end['elapsed_ms']
    assert end['before_service_ms'] >= 0 and end['post_service_ms'] >= 0
    assert end['response_bytes'] == len(response.content)
    assert 'PRIVATE_' not in json.dumps(logs())
    assert request_trace.get() is None


def test_cancel_error_progress_and_context_cleanup(logs):
    control = Operation()
    with pytest.raises(ReleaseCancelled):
        with operation(control):
            with stage('generated', 'generated phase'), measure('generated.work'):
                control.last_progress -= 6
                checkpoint()
                control.cancel.set()
                checkpoint()
    assert control.active_spans == []
    records = logs()
    progress = next(record for record in records if record['event'] == 'progress')
    assert progress['active'][0]['name'] == 'generated.work'
    summary = next(record for record in records if record['event'] == 'operation_end')
    assert summary['outcome'] == 'cancelled'
    assert summary['measurements']['generated.work']['calls'] == 1
    with pytest.raises(ValueError):
        with operation(Operation()):
            raise ValueError('PRIVATE_ERROR_MESSAGE')
    assert logs()[-1]['outcome'] == 'error'
    assert 'PRIVATE_ERROR_MESSAGE' not in json.dumps(logs())


def test_disabled_mode_emits_no_records(monkeypatch, logs):
    monkeypatch.setenv('DEEPYIELD_RELEASE_TIMING', '0')
    with operation(Operation()), measure('generated'):
        checkpoint()
    assert logs() == []


def test_cancelled_http_keeps_outcome_and_existing_status(monkeypatch, logs):
    def cancelled(*args):
        raise ReleaseCancelled('操作已取消')
    monkeypatch.setattr(service, 'run_checks', cancelled)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post('/api/release-check/runs', json={
            'cpk': {'dataset_id': 'memory'}, 'cgm': {'dataset_id': 'memory'}})
    assert response.status_code == 422
    summary = next(record for record in logs() if record['event'] == 'operation_end')
    assert summary['outcome'] == 'cancelled'
    assert next(record for record in logs() if record['event'] == 'operation_error')['error_type'] == 'ReleaseCancelled'
