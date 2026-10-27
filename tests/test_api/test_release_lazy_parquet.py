"""Import never writes Parquet; explicit export preserves the original executions."""
import bz2
import gzip
import gc
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from app import release_check_service as service
from deepyield.compute.release_control import Operation, ReleaseCancelled, checkpoint, operation
from deepyield.compute.release_stdf import read_release_wide_all
from tests.test_api.test_release_check import client
from tests.test_api.test_release_compact import generated_stdf


@pytest.mark.parametrize('suffix,compress', [('.stdf', lambda b: b), ('.stdf.gz', gzip.compress),
                                            ('.stdf.bz2', bz2.compress)])
def test_import_retains_source_and_only_explicit_download_writes_full_parquet(client, monkeypatch, suffix, compress):
    raw = compress(generated_stdf())
    import pyarrow.parquet as pq
    with monkeypatch.context() as patch:
        patch.setattr(pq, 'ParquetWriter', lambda *a, **k: pytest.fail('Import wrote Parquet'))
        response = client.post('/api/release-check/datasets', files={'file': ('source' + suffix, raw)})
    assert response.status_code == 200, response.text
    identifier = response.json()['id']
    dataset = service.get_dataset(identifier)
    original = dataset.frame
    assert dataset.source.read_bytes() == raw
    assert not dataset.parquet.exists()
    assert all(len(events) == 1 for events in dataset.frame['__test_records'])
    response = client.get(f'/api/release-check/datasets/{identifier}/parquet?job_id=full-export')
    assert response.status_code == 200, response.text[:200]
    assert response.content.startswith(b'PAR1')
    assert 'Server-Timing' in response.headers
    full, metadata = read_release_wide_all(dataset.parquet)
    assert metadata['all_attempts_preserved'] is True
    assert all(len(events) == 2 for events in full['__test_records'])
    assert dataset.frame is original
    assert dataset.source.read_bytes() == raw
    monkeypatch.setattr(service, 'convert_stdf_to_wide_all', lambda *a: pytest.fail('Repeated export reparsed STDF'))
    assert client.get(f'/api/release-check/datasets/{identifier}/parquet').content == response.content
    directory = dataset.parquet.parent
    client.delete(f'/api/release-check/datasets/{identifier}')
    del dataset
    gc.collect()
    assert not directory.exists()


@pytest.mark.parametrize('cancelled', [False, True])
def test_failed_or_cancelled_export_leaves_source_and_can_retry(client, monkeypatch, cancelled):
    response = client.post('/api/release-check/datasets', files={'file': ('source.stdf', generated_stdf())})
    identifier = response.json()['id']
    dataset = service.get_dataset(identifier)
    control = Operation()
    convert = service.convert_stdf_to_wide_all
    def interrupt(source, destination):
        destination.write_bytes(b'partial')
        if cancelled:
            control.cancel.set()
            checkpoint()
        raise ValueError('synthetic conversion failure')
    monkeypatch.setattr(service, 'convert_stdf_to_wide_all', interrupt)
    with operation(control), pytest.raises(ReleaseCancelled if cancelled else ValueError):
        service.prepare_parquet(identifier)
    assert dataset.source.is_file()
    assert not list(dataset.parquet.parent.glob('*.parquet'))
    monkeypatch.setattr(service, 'convert_stdf_to_wide_all', convert)
    assert service.prepare_parquet(identifier).parquet.is_file()


def test_concurrent_download_preparation_runs_converter_once(client, monkeypatch):
    response = client.post('/api/release-check/datasets', files={'file': ('source.stdf', generated_stdf())})
    identifier = response.json()['id']
    started, release = Event(), Event()
    calls = []
    def convert(source, destination):
        calls.append(source)
        started.set()
        assert release.wait(10)
        destination.write_bytes(b'complete')
    monkeypatch.setattr(service, 'convert_stdf_to_wide_all', convert)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(service.prepare_parquet, identifier)
        assert started.wait(10)
        second = pool.submit(service.prepare_parquet, identifier)
        release.set()
        assert first.result(10) is second.result(10)
    assert len(calls) == 1
