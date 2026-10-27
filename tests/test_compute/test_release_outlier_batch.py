"""Strict Outlier batch/legacy parity on generated memory data only."""
from copy import deepcopy

import numpy as np
import pandas as pd
import pyarrow as pa
import pytest

from deepyield.compute import release_outliers as outliers
from deepyield.compute.release_control import Operation, ReleaseCancelled, operation
from deepyield.compute.release_event_store import EventStore, PartEvents
from deepyield.compute.release_group_batch import GroupEvents
from tests.test_compute.outlier_fixtures import dataset, population
from tests.test_compute.test_release_batch import exact


@pytest.fixture(autouse=True)
def no_measurement_files(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Outlier batch tests must not process STDF/Parquet')
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    for name in ('read_release_wide_all', 'import_release_stdf'):
        monkeypatch.setattr('deepyield.compute.release_stdf.' + name, forbidden)


def compact(frame, chunks=1, float32=False):
    result = frame.copy(deep=True)
    records = result['__test_records'].tolist()
    converted = []
    for chunk in np.array_split(np.arange(len(records)), chunks):
        if not len(chunk):
            continue
        events, bounds = [], []
        for index in chunk:
            start = len(events)
            events.extend(records[int(index)])
            bounds.append((start, len(events)))
        arrow = pa.array(events)
        if float32:
            fields = [pa.field(field.name, pa.float32()) if field.name == 'result' else field
                      for field in arrow.type]
            arrow = arrow.cast(pa.struct(fields))
        store = EventStore(arrow)
        converted.extend(PartEvents(store, start, stop) for start, stop in bounds)
    result['__test_records'] = pd.Series(converted, index=result.index, dtype=object)
    return result


def compare(frame, metadata, monkeypatch, **options):
    before = frame['__test_records'].tolist()
    batch = outliers.batch_buckets
    with monkeypatch.context() as legacy:
        legacy.setattr(outliers, 'batch_buckets', lambda *args: None)
        expected = outliers.build_release_outlier_report(frame, metadata, **options)
    calls = []
    def checked(group, *args):
        result = batch(group, *args)
        calls.append(isinstance(group['events'], GroupEvents) and result is not None)
        return result
    monkeypatch.setattr(outliers, 'batch_buckets', checked)
    actual = outliers.build_release_outlier_report(frame, metadata, **options)
    exact(expected, actual)
    assert all(calls)
    assert frame['__test_records'].tolist() == before
    return actual


@pytest.mark.parametrize('chunks', [1, 3])
@pytest.mark.parametrize('options', [{}, {'sites': [2]}, {'part_indices': [1, 2, 41]},
    {'test_columns': ['TEST_1002']}, {'sigma_multiplier': 3., 'minimum_samples_per_site': 4},
    {'part_indices': []}, {'test_columns': []}])
def test_complete_reports_match_across_selections(chunks, options, monkeypatch):
    frame, metadata = dataset(test_count=3)
    compare(compact(frame, chunks), metadata, monkeypatch, **options)


def test_all_exclusion_priorities_variants_and_last_events(monkeypatch):
    frame, metadata = dataset({(1, 1, 'W1'): population() + [12.],
        (2, 1, 'W1'): population(20.), (1, 2, 'W2'): population(30.),
        (None, 3, 'W1'): population(), (1, None, 'W1'): population()}, test_count=6)
    for index, events in enumerate(frame['__test_records']):
        for event in events:
            test = event['test_num'] - 1001
            if test == 1:
                event.update(record_type='FTR')
            elif test == 2:
                event.update(high_limit=0.)
            elif test == 3:
                event.update(record_type='MPR', result_index=1, low_limit=None, high_limit=None)
            elif test == 4:
                event.update(result_valid=None)
            elif test == 5:
                event.update(unit=None, high_limit=11.)
            if index % 13 == 0:
                event['test_flag'] = 0x80
            if index % 17 == 0:
                event['param_flag'] = 0x20
        if index % 19 == 0:
            frame.at[index, 'SBIN'] = 2
        if index % 23 == 0:
            frame.at[index, 'PART_FLAG'] = 8
        if index % 11 == 0:
            old = deepcopy(events[0])
            old.update(record_sequence=old['record_sequence'] - 1, result=80.)
            tie = deepcopy(events[0])
            tie.update(result=99., high_limit=200.)
            events.insert(0, old)
            events.append(tie)
    compare(compact(frame, 4), metadata, monkeypatch)


@pytest.mark.parametrize('field,values', [
    ('test_flag', [0., None, 1.5, -1., 256., float('nan'), 0x80, 0xC0]),
    ('test_flag', ['0', '', '1.5', '-1', '256', 'nan', '128', '192']),
    ('test_flag', [False, True]),
    ('param_flag', [None, 0, 0x20, 0x40]),
    ('result', [None, 0., 100., -1., 101., float('nan'), float('inf'), -float('inf')]),
    ('result', ['10.0', '12.0', '', 'nan', 'inf']),
    ('result', [True, False]),
    ('result_valid', [True, False, None]),
    ('result_valid', [1, 0, None]),
    ('result_valid', ['True', 'False', None]),
])
def test_typed_nullable_fields_preserve_scalar_semantics(field, values, monkeypatch):
    frame, metadata = dataset(test_count=2)
    for index, events in enumerate(frame['__test_records']):
        for event in events:
            event[field] = values[index % len(values)]
    compare(compact(frame, 3), metadata, monkeypatch)


def test_float32_and_missing_physical_identity(monkeypatch):
    frame, metadata = dataset(test_count=2)
    frame = frame.drop(columns=['LOT_ID', 'WAFER_ID', 'STDF_PART_ID', 'DIE_X', 'DIE_Y'])
    report = compare(compact(frame, float32=True), metadata, monkeypatch)
    assert report['chips'][0]['part_id'] is None


def test_generic_events_keep_legacy_path(monkeypatch):
    frame, metadata = dataset()
    generic = outliers.build_release_outlier_report(frame, metadata)
    exact(generic, outliers.build_release_outlier_report(compact(frame), metadata))


@pytest.mark.parametrize('values', [[10.] * 40, [10.] * 3,
    [-1.e308] * 20 + [1.e308] * 20,
    [-.675] * 12 + [0.] * 16 + [.675] * 12 + [6.]])
def test_degenerate_extreme_and_strict_thresholds(values, monkeypatch):
    frame, metadata = dataset({(1, 1, 'W1'): values})
    for events in frame['__test_records']:
        events[0].update(low_limit=None, high_limit=None)
    compare(compact(frame), metadata, monkeypatch)


@pytest.mark.parametrize('field', ['SBIN', 'PART_FLAG', 'HEAD_NUM', 'SITE'])
def test_part_metadata_preserves_flag_conversion(field, monkeypatch):
    frame, metadata = dataset(test_count=3)
    variants = [None, True, False, '0', '1', '', float('nan'), 1.5, -1, 256]
    frame[field] = pd.Series([variants[index % len(variants)] for index in range(len(frame))],
                            dtype=object)
    compare(compact(frame, 3), metadata, monkeypatch)


def test_measurements_are_not_decoded_through_scalar_views(monkeypatch):
    frame, metadata = dataset(test_count=4, compact=True)
    original = EventStore.value
    def checked(store, name, index):
        assert name not in {'result', 'test_flag', 'param_flag', 'result_valid'}
        return original(store, name, index)
    monkeypatch.setattr(EventStore, 'value', checked)
    report = outliers.build_release_outlier_report(frame, metadata)
    assert report['summary']['candidate_hit_count'] == 4


def test_batch_cancellation_inside_partition(monkeypatch):
    frame, metadata = dataset(compact=True)
    from deepyield.compute import release_outlier_batch as batch
    original = batch._event_arrays
    control = Operation()
    def cancel(*args):
        result = original(*args)
        control.cancel.set()
        return result
    monkeypatch.setattr(batch, '_event_arrays', cancel)
    with operation(control), pytest.raises(ReleaseCancelled):
        outliers.build_release_outlier_report(frame, metadata)
