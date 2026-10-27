"""Fast and compatibility paths must return identical reports on memory-only inputs."""
import json
import struct

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from deepyield.compute import release_cpk as cpk
from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.compute.release_mmsa import build_release_mmsa_report
from deepyield.compute.release_event_store import EventStore, PartEvents
from deepyield.compute.release_group_batch import GroupEvents
from deepyield.compute.release_run_cache import computation
from deepyield.compute.release_control import Operation, ReleaseCancelled, operation
from deepyield.compute.release_stdf import SCHEMA_VERSION
from deepyield.rules.loader import load_analysis_rules


@pytest.fixture(autouse=True)
def no_measurement_file_io(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Memory-only regression must not process STDF/Parquet')
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    monkeypatch.setattr('deepyield.compute.release_stdf.read_release_wide_all', forbidden)


def exact(expected, actual, path='report'):
    assert type(expected) is type(actual), path
    if isinstance(expected, dict):
        assert list(expected) == list(actual), path
        for key in expected:
            exact(expected[key], actual[key], path + '.' + str(key))
    elif isinstance(expected, (list, tuple)):
        assert len(expected) == len(actual), path
        for index, (left, right) in enumerate(zip(expected, actual)):
            exact(left, right, path + '.' + str(index))
    elif isinstance(expected, float):
        assert struct.pack('>d', expected) == struct.pack('>d', actual), path
    else:
        assert expected == actual, path


def dataset(parts=20, tests=12, variants=True, chunks=1):
    definitions = [dict(column=f'T_{index}', test_num=1000 + index, name=f'ITEM_{index}',
        record_type='FTR' if index % 9 == 0 else 'MPR' if index % 7 == 0 else 'PTR',
        result_index=index % 2 if index % 7 == 0 else None,
        unit='V', low_limit=0., high_limit=20.) for index in range(tests)]
    rows, flat, bounds = [], [], []
    for part in range(parts):
        start = len(flat)
        for index, definition in enumerate(definitions):
            event = dict(definition, result=5. + (part % 9) / 5 + index / 100,
                test_flag=0., param_flag=0., result_valid=True,
                record_sequence=part * tests * 10 + index * 3)
            if variants:
                if (part + index) % 11 == 0:
                    event.update(low_limit=1., unit=None)
                if (part + index) % 17 == 0:
                    event['result'] = float('nan')
                if (part + index) % 13 == 0:
                    event['test_flag'] = None
                if (part + index) % 19 == 0:
                    event['param_flag'] = 1.5
                if (part + index) % 7 == 0:
                    flat.append(dict(event, result=99., record_sequence=event['record_sequence'] - 2))
                if (part + index) % 5 == 0:
                    # Equal latest sequences must keep the first, despite different values/specs.
                    flat.append(dict(event))
                    event.update(result=100., high_limit=999.)
            flat.append(event)
        bounds.append((start, len(flat)))
        rows.append(dict(__part_index=part, SITE=part % 4, SBIN=1 if part % 7 else 2,
            PART_FLAG=0, __conditions_json=json.dumps(dict(part_type='P', job_name='J',
                job_revision='A', test_code='FT', test_temperature='25', variant=part % 2 if variants else 0)),
            __test_records=None))
    if variants:
        for part, row in enumerate(rows):
            row['PART_FLAG'] = [0, 8, 4, 16, 32, None, 256, -1, 1.5, True][part % 10]
            row['SITE'] = [0, 1, 2, 3, None, 256, -1, 1.5, True, 0][part % 10]
    size = max(1, (parts + chunks - 1) // chunks)
    for first in range(0, parts, size):
        last = min(parts, first + size)
        offset, stop = bounds[first][0], bounds[last - 1][1]
        store = EventStore(pa.array(flat[offset:stop]))
        for part in range(first, last):
            rows[part]['__test_records'] = PartEvents(store, bounds[part][0] - offset, bounds[part][1] - offset)
    frame = pd.DataFrame(rows)
    # Keep bool/non-integral/null part metadata visible to the exact existing validators.
    if variants:
        for name in ('SITE', 'PART_FLAG'):
            frame[name] = pd.Series([row[name] for row in rows], dtype=object)
    metadata = dict(schema_version=SCHEMA_VERSION, source_file='generated', source_sha256='generated',
                    test_catalog=definitions)
    return frame, metadata


@computation
def reports(frame, metadata, different=False):
    catalog = load_analysis_rules()
    a = cpk.build_release_bin1_cpk_report(frame, metadata, catalog=catalog, minimum_samples=2)
    selection = dict(part_indices=list(range(3, len(frame)))) if different else {}
    b = build_release_cgm_report(frame, metadata, catalog=catalog,
        bin1_cpk_report=a, minimum_samples_per_site=2, **selection)
    c = build_release_mmsa_report(frame, metadata, catalog=catalog,
        bin1_cpk_report=a, cgm_report=b, minimum_samples_per_site=2,
        **(dict(part_indices=list(range(1, len(frame) - 1))) if different else {}))
    return dict(cpk=a, cgm=b, mmsa=c)


@pytest.mark.parametrize('variants,chunks,different', [(False, 1, False), (True, 1, False),
    (True, 3, False), (True, 3, True)])
def test_complete_reports_bitwise_equal_to_original_scalar_path(monkeypatch, variants, chunks, different):
    frame, metadata = dataset(variants=variants, chunks=chunks)
    with monkeypatch.context() as legacy:
        legacy.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = reports(frame, metadata, different)
    actual = reports(frame, metadata, different)
    exact(expected, actual)


@pytest.mark.parametrize('chosen', [None, set(), {'T_1', 'T_7'}])
def test_group_order_latest_flags_and_reference_indices(monkeypatch, chosen):
    frame, metadata = dataset(chunks=2)
    source = {'source_file': metadata['source_file'], 'source_sha256': metadata['source_sha256']}
    with monkeypatch.context() as legacy:
        legacy.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = cpk._build_groups(frame, source, chosen)
    actual = cpk._build_groups(frame, source, chosen)
    assert list(expected) == list(actual)
    for key in expected:
        assert isinstance(actual[key]['events'], GroupEvents)
        assert len(expected[key]['events']) == len(actual[key]['events'])
        for (part_a, event_a), (part_b, event_b) in zip(expected[key]['events'], actual[key]['events']):
            assert part_a['__part_index'] == part_b['__part_index']
            exact(dict(event_a), dict(event_b))


def test_null_sequence_and_mixed_inputs_keep_compatibility_path():
    frame, metadata = dataset(parts=4, variants=False)
    events = [dict(event) for event in frame.iloc[0]['__test_records']]
    frame.at[0, '__test_records'] = events
    groups = cpk._build_groups(frame, {}, None)
    assert all(isinstance(group['events'], list) for group in groups.values())


def test_batch_take_equals_scalar_for_nulls_nan_bool_and_float32():
    table = pa.StructArray.from_arrays([
        pa.array(['a', None, 'b', 'a']), pa.array([1, None, -2, 3], type=pa.int64()),
        pa.array([0., None, float('nan'), float('inf')], type=pa.float32()),
        pa.array([True, None, False, True]), pa.nulls(4)],
        names=['text', 'integer', 'float', 'boolean', 'unset'])
    store = EventStore(table)
    indices = np.array([3, 1, 0, 2], dtype=np.int64)
    for name in store.columns:
        expected = [store.value(name, int(index)) for index in indices]
        exact(expected, store.take(name, indices), name)


@pytest.mark.parametrize('field,values', [
    ('test_flag', ['0', '64', None, '1.5']),
    ('param_flag', [True, False, None, True]),
    ('result_valid', [True, False, None, True]),
    ('result', [0., float('nan'), float('inf'), None]),
    ('low_limit', [0., None, 9., 21.]),
    ('name', ['name', None, '', 'other']),
])
def test_unusual_field_types_keep_scalar_semantics(monkeypatch, field, values):
    frame, metadata = dataset(parts=12, tests=4, variants=False)
    flat = []
    for part, events in enumerate(frame['__test_records']):
        for index, event in enumerate(events):
            flat.append({**dict(event), field: values[(part + index) % len(values)]})
    store = EventStore(pa.array(flat))
    frame['__test_records'] = [PartEvents(store, i * 4, i * 4 + 4) for i in range(12)]
    with monkeypatch.context() as legacy:
        legacy.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = reports(frame, metadata)
    exact(expected, reports(frame, metadata))


def test_null_sequences_fall_back_without_changing_results(monkeypatch):
    frame, metadata = dataset(parts=4, tests=3, variants=False)
    flat = [dict(event) for events in frame['__test_records'] for event in events]
    flat[0]['record_sequence'] = None
    store = EventStore(pa.array(flat))
    frame['__test_records'] = [PartEvents(store, i * 3, i * 3 + 3) for i in range(4)]
    assert all(isinstance(group['events'], list) for group in cpk._build_groups(frame, {}, None).values())
    with monkeypatch.context() as legacy:
        legacy.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = reports(frame, metadata)
    exact(expected, reports(frame, metadata))


def test_batch_grouping_honours_cancellation(monkeypatch):
    frame, metadata = dataset(parts=4, tests=3, variants=False)
    store = frame.iloc[0]['__test_records'].store
    original_take = store.take
    control = Operation()
    def cancelled_take(name, indices):
        result = original_take(name, indices)
        control.cancel.set()
        return result
    monkeypatch.setattr(store, 'take', cancelled_take)
    with pytest.raises(ReleaseCancelled), operation(control):
        cpk.build_release_bin1_cpk_report(frame, metadata, catalog=load_analysis_rules())
