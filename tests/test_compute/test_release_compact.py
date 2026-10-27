"""Calculation-only imports match full data, without retaining the evidence table."""
from contextlib import contextmanager
import json

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from deepyield.compute import release_stdf as release
from deepyield.compute.release_compact import DefinitionPool, calculation_frame, join_calculation_frames
from deepyield.compute.release_control import Operation, ReleaseCancelled, operation
from deepyield.compute.release_event_store import release_frame, retained_data_bytes
from deepyield.compute.release_chart_samples import report_samples
from deepyield.compute.release_outliers import build_release_outlier_report
from tests.test_compute.test_release_batch import dataset, exact, reports


def table(parts=64, tests=12, variants=True, ties=False):
    frame, metadata = dataset(parts=parts, tests=tests, variants=False)
    rows = []
    for part, row in enumerate(frame.to_dict('records')):
        events = []
        for number, view in enumerate(row['__test_records']):
            event = dict(view)
            event.update(test_flag=0, param_flag=0, raw_optional_json='evidence-' + str(part) * 200,
                         alarm_id='alarm', res_scal=-3, low_spec=0., high_spec=20.,
                         test_text='Original execution ' + str(part))
            if variants:
                events.append(dict(event, result=999., record_sequence=event['record_sequence'] - 1))
                if (part + number) % 11 == 0:
                    event.update(low_limit=None, unit=None)
                if (part + number) % 13 == 0:
                    event['test_flag'] = 1
                if (part + number) % 17 == 0:
                    event['param_flag'] = 2
                if (part + number) % 19 == 0:
                    event['result'] = None
                if (part + number) % 23 == 0:
                    event['result'] = float('nan')
                if ties and (part + number) % 5 == 0:
                    # Equal sequence: retain first, including its own validity.
                    events.append(dict(event))
                    event.update(result=100., high_limit=999.)
            events.append(event)
        row.update(Name='CHIP_' + str(part), HEAD_NUM=1, LOT_ID='LOT', WAFER_ID='WAFER',
                   STDF_PART_ID=str(part), DIE_X=part, DIE_Y=1,
                   __source_record_sequence=part * 1000 + 999,
                   __test_records=events, PART_FLAG=16 if variants and part % 29 == 0 else 0)
        row.update({test['column']: 5. for test in metadata['test_catalog']})
        rows.append(row)
    body = pa.Table.from_pylist(rows)
    return body.replace_schema_metadata({release.METADATA_KEY: json.dumps(metadata).encode()}), metadata


def compact(body, metadata, batch_rows=17):
    definitions = DefinitionPool()
    return join_calculation_frames([calculation_frame(batch, metadata, definitions)
        for batch in body.to_batches(max_chunksize=batch_rows)], definitions)


def test_all_reports_and_chart_samples_equal_full_data():
    body, metadata = table()
    full, reduced = release_frame(body), compact(body, metadata)
    expected, actual = reports(full, metadata), reports(reduced, metadata)
    exact(expected, actual)
    exact(build_release_outlier_report(full, metadata, minimum_samples_per_site=4),
          build_release_outlier_report(reduced, metadata, minimum_samples_per_site=4))
    for module, report in expected.items():
        for item in report['items']:
            exact(report_samples(full, item, metadata['source_sha256']),
                  report_samples(reduced, item, metadata['source_sha256']), module)
    assert all(len(events) == len(metadata['test_catalog']) for events in reduced['__test_records'])
    assert not set(test['column'] for test in metadata['test_catalog']) & set(reduced.columns)
    assert 'raw_optional_json' not in reduced.iloc[0]['__test_records'][0]
    assert 'test_flag' in reduced.iloc[0]['__test_records'][0]


def test_equal_sequences_keep_first_without_falling_back_from_invalid_last():
    body, metadata = table(ties=True)
    exact(reports(release_frame(body), metadata), reports(compact(body, metadata), metadata))


def test_definitions_are_shared_and_memory_budget_counts_them_once():
    body, metadata = table(parts=64, tests=12)
    reduced = compact(body, metadata, batch_rows=5)
    stores = {id(events.store): events.store for events in reduced['__test_records']}
    assert len(stores) > 1
    assert len({id(store.definitions) for store in stores.values()}) == 1
    seen = set()
    actual = sum(store.retained_bytes(seen) for store in stores.values())
    unshared = sum(store.retained_bytes() for store in stores.values())
    definitions = next(iter(stores.values())).definitions
    assert unshared - actual == (len(stores) - 1) * definitions.retained_bytes()


def test_compact_memory_is_measured_and_does_not_hold_wide_or_evidence():
    body, metadata = table(parts=128, tests=256, variants=False)
    full, reduced = release_frame(body), compact(body, metadata, batch_rows=128)
    old, new = retained_data_bytes(full), retained_data_bytes(reduced)
    assert new < old * .4, (old, new)
    store = reduced.iloc[0]['__test_records'].store
    assert set(store.columns) == {'column', 'test_num', 'name', 'record_type', 'result_index',
        'unit', 'low_limit', 'high_limit', 'result', 'record_sequence', 'test_flag',
        'param_flag', 'result_valid'}
    assert store.definition_codes.dtype == np.uint8
    assert store.columns['test_flag'][1].dtype == np.uint8
    assert store.columns['result'][1].dtype == np.float64
    assert new >= store.retained_bytes() > 0


def test_import_streams_calculation_batches_without_parquet(tmp_path, monkeypatch):
    body, metadata = table()
    metadata = {**metadata, 'schema_version': release.CALCULATION_SCHEMA_VERSION}
    body = body.replace_schema_metadata({release.METADATA_KEY: json.dumps(metadata).encode()})
    batches = body.to_batches(max_chunksize=9)
    class Reader:
        schema = body.schema
        def __iter__(self):
            yield from batches
        def read_all(self):
            pytest.fail('Import must not assemble the complete Arrow table')
    @contextmanager
    def native(source, *, calculation_only):
        assert calculation_only
        yield Reader()
    monkeypatch.setattr(release, '_native_batches', native)
    monkeypatch.setattr(release.pq, 'read_table', lambda *a, **k: pytest.fail('Parquet reload'))
    monkeypatch.setattr(release.pq, 'ParquetWriter', lambda *a, **k: pytest.fail('Parquet write'))
    source, output = tmp_path / 'synthetic.stdf', tmp_path / 'all.parquet'
    source.write_bytes(b'generated IPC instead of a source file')
    frame, actual_metadata = release.import_release_stdf(source)
    assert actual_metadata == metadata
    assert not output.exists()
    exact(reports(release_frame(body), metadata), reports(frame, actual_metadata))


def test_cancellation_does_not_publish_partial_parquet(tmp_path):
    body, metadata = table(parts=2)
    control = Operation()
    def cancel(batch):
        control.cancel.set()
        calculation_frame(batch, metadata)
    output = tmp_path / 'cancelled.parquet'
    with operation(control), pytest.raises(ReleaseCancelled):
        release._save(body, output, cancel)
    assert not output.exists()


def test_empty_executions_keep_chip_rows():
    body, metadata = table(parts=2, tests=2)
    rows = body.to_pylist()
    rows[0]['__test_records'] = []
    reduced = compact(pa.Table.from_pylist(rows, schema=body.schema), metadata, batch_rows=1)
    assert len(reduced) == 2
    assert len(reduced.iloc[0]['__test_records']) == 0
    assert len(reduced.iloc[1]['__test_records']) == 2
