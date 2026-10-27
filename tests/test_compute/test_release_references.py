"""Private reference storage must preserve complete reports and drill-down data."""
import json
from contextvars import copy_context
from pathlib import Path

import numpy as np
import pytest

from app import release_check_service as service
from deepyield.compute.release_chart_samples import report_samples
from deepyield.compute.release_references import (
    CompactReferences, compact_refs_enabled, compact_sample_refs, reference_json,
)
from deepyield.compute.release_control import Operation, ReleaseCancelled, operation
from tests.test_compute.test_release_batch import dataset, exact, reports


@pytest.fixture(autouse=True)
def memory_only(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Reference tests must not process STDF/Parquet')
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    monkeypatch.setattr(service, 'import_release_stdf', forbidden)
    monkeypatch.setattr('deepyield.compute.release_stdf.read_release_wide_all', forbidden)


def expanded(value):
    return json.loads(json.dumps(value, default=reference_json, allow_nan=False))


@pytest.mark.parametrize('variants,chunks,different', [
    (False, 1, False), (True, 1, False), (True, 3, False), (True, 3, True),
])
def test_complete_reports_and_charts_unchanged(variants, chunks, different):
    frame, metadata = dataset(variants=variants, chunks=chunks)
    plain = reports(frame, metadata, different)
    assert all(type(i['sample_refs']) is list for r in plain.values() for i in r['items'])
    with compact_sample_refs():
        compact = reports(frame, metadata, different)
    exact(expanded(plain), expanded(compact))
    for module in plain:
        for original, changed in zip(plain[module]['items'], compact[module]['items']):
            if original['n']:
                assert isinstance(changed['sample_refs'], CompactReferences)
                exact(original['sample_refs'], list(changed['sample_refs']))
                try:
                    samples = report_samples(frame, original, 'memory')
                except ValueError as error:
                    # The variant fixture deliberately includes duplicate sequence IDs.
                    with pytest.raises(ValueError) as compact_error:
                        report_samples(frame, changed, 'memory')
                    assert str(error) == str(compact_error.value)
                else:
                    exact(samples, report_samples(frame, changed, 'memory'))


@pytest.mark.parametrize('sequences', [[], [0, 8, -1], [2**63 + 1, 2**64 - 1],
    [-1, 2**63 + 1, 2**64 - 1], [2**80, 0, -2**80]])
def test_index_slice_integer_precision_and_metadata_types(sequences):
    rows = np.arange(len(sequences), dtype=np.int64)
    sites = (True, 1.5, None)[:len(rows)]
    refs = CompactReferences(rows, sequences, (2, 4, 8), sites)
    expected = [dict(part_index=(2, 4, 8)[i], record_sequence=s, site=sites[i])
                for i, s in enumerate(sequences)]
    exact(expected, list(refs))
    exact(expected[:2], refs[:2])
    exact(expected[::-1], refs[::-1])
    if expected:
        exact(expected[-1], refs[-1])
    with pytest.raises(IndexError):
        refs[len(refs)]
    rows[:] = 99
    exact(expected, list(refs))
    assert not refs.rows.flags.writeable and not refs.sequences.flags.writeable
    exact(expected, expanded(refs))


def test_opt_in_context_reset_and_strict_serializer():
    outside = copy_context()
    assert not compact_refs_enabled()
    with pytest.raises(ValueError), compact_sample_refs():
        assert compact_refs_enabled() and not outside.run(compact_refs_enabled)
        with compact_sample_refs():
            assert compact_refs_enabled()
        assert compact_refs_enabled()
        raise ValueError('reset on exception')
    assert not compact_refs_enabled()
    with pytest.raises(TypeError):
        json.dumps(object(), default=reference_json)


def test_legacy_event_reports_remain_lists_in_private_context():
    frame, metadata = dataset(parts=12, variants=False)
    frame['__test_records'] = [[dict(e) for e in events] for events in frame['__test_records']]
    before = reports(frame, metadata)
    with compact_sample_refs():
        after = reports(frame, metadata)
    exact(before, after)


def test_compact_construction_cancellation_is_checked(monkeypatch):
    from deepyield.compute import release_sample_batch as batch
    frame, metadata = dataset(parts=12, variants=False)
    original = batch.CompactReferences
    control = Operation(name='memory-cancel-build')

    def cancel_after_construction(*args):
        result = original(*args)
        control.cancel.set()
        return result

    monkeypatch.setattr(batch, 'CompactReferences', cancel_after_construction)
    with operation(control), compact_sample_refs(), pytest.raises(ReleaseCancelled):
        reports(frame, metadata)
    assert not compact_refs_enabled()


def test_reference_iteration_cancellable():
    refs = CompactReferences([0], [1], (1,), (1,))
    control = Operation(name='memory-reference')
    with operation(control):
        control.cancel.set()
        with pytest.raises(ReleaseCancelled):
            list(refs)


def test_service_result_detail_and_full_export_unchanged(monkeypatch):
    frame, metadata = dataset(parts=24, tests=12, variants=False)
    source = service.ReleaseDataset(None, Path('never-opened.parquet'), 'memory.stdf', frame, metadata)
    monkeypatch.setattr(service, '_datasets', {'memory': source})
    modules = ['cpk', 'cgm', 'mmsa']
    selections = {m: {} for m in modules}
    with monkeypatch.context() as legacy:
        legacy.setattr('deepyield.compute.release_sample_batch.compact_refs_enabled', lambda: False)
        before = service.run_checks('memory', selections, modules)
    before_details = [service.item_detail('memory', before['id'], row['key']) for row in before['rows']]
    # Hold the artifact: its spool directory is removed as soon as it is dropped.
    before_artifact = service.export_report('memory', before['id'])
    before_export = json.loads(before_artifact.path.read_text(encoding='utf-8'))
    after = service.run_checks('memory', selections, modules)
    _, retained = service.get_run(source, after['id'])
    assert any(isinstance(i['sample_refs'], CompactReferences)
               for r in retained.values() for i in r['items'])
    after_details = [service.item_detail('memory', after['id'], row['key']) for row in after['rows']]
    after_artifact = service.export_report('memory', after['id'])
    after_export = json.loads(after_artifact.path.read_text(encoding='utf-8'))
    exact(before_details, after_details)
    before_export['result']['id'] = after['id']
    exact(before_export, after_export)
    assert not compact_refs_enabled()
