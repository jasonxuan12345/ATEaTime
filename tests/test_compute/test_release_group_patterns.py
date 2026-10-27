"""Group reuse and bounded index buffering must match scalar order exactly."""
import json

import pandas as pd
import pyarrow as pa
import pytest

from deepyield.compute import release_cpk as cpk, release_group_batch as groups
from deepyield.compute.release_control import Operation, operation
from deepyield.compute.release_event_store import EventStore, PartEvents
from tests.test_compute.test_release_batch import dataset, exact


@pytest.fixture(autouse=True)
def no_measurement_files(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Generated memory inputs only')
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)


def compare(frame, metadata, monkeypatch, chosen=None):
    source = dict(source_file=metadata['source_file'], source_sha256=metadata['source_sha256'])
    with monkeypatch.context() as old:
        old.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = cpk._build_groups(frame, source, chosen)
    control = Operation()
    with operation(control):
        actual = cpk._build_groups(frame, source, chosen)
    assert list(actual) == list(expected)
    for key in actual:
        for name in actual[key]:
            if name != 'events':
                exact(expected[key][name], actual[key][name])
        assert len(actual[key]['events']) == len(expected[key]['events'])
        for (old_part, old_event), (part, event) in zip(expected[key]['events'], actual[key]['events']):
            assert old_part['__part_index'] == part['__part_index']
            exact(dict(old_event), dict(event))
    return actual, control


def test_repeated_metadata_reuses_labels_and_flushes_exact_part_order(monkeypatch):
    monkeypatch.setenv('DEEPYIELD_RELEASE_TIMING', '1')
    frame, metadata = dataset(parts=25, tests=12, variants=False)
    monkeypatch.setattr(groups, '_BUFFER_LIMIT', 25)
    _, control = compare(frame, metadata, monkeypatch)
    assert control.counts['groups.pattern_hit'] >= 24
    assert control.counts['groups.index_flushes'] > 1


@pytest.mark.parametrize('chosen', [None, set(), {'T_1', 'T_7'}])
def test_reverse_selection_multiple_stores_and_pattern_eviction(monkeypatch, chosen):
    frame, metadata = dataset(parts=20, tests=12, variants=True, chunks=3)
    monkeypatch.setattr(groups, '_PATTERN_LIMIT', 1)
    monkeypatch.setattr(groups, '_BUFFER_LIMIT', 13)
    compare(frame.iloc[::-1], metadata, monkeypatch, chosen)


def test_same_conditions_different_json_order_still_has_original_groups(monkeypatch):
    frame, metadata = dataset(parts=8, tests=4, variants=False)
    original = json.loads(frame.iloc[0]['__conditions_json'])
    frame.loc[1::2, '__conditions_json'] = json.dumps(dict(reversed(list(original.items()))))
    compare(frame, metadata, monkeypatch)


@pytest.mark.parametrize('field', ['low_limit', 'high_limit'])
def test_null_limit_pattern_cannot_hide_later_written_nan(monkeypatch, field):
    frame, metadata = dataset(parts=3, tests=1, variants=False)
    rows = [{**dict(frame.iloc[i]['__test_records'][0]), field: None if i < 2 else float('nan')}
            for i in range(3)]
    store = EventStore(pa.array(rows))
    frame['__test_records'] = pd.Series([PartEvents(store, i, i + 1) for i in range(3)], dtype=object)
    source = dict(source_file=metadata['source_file'], source_sha256=metadata['source_sha256'])
    with pytest.raises(ValueError):
        cpk._build_groups(frame, source, None)
    with monkeypatch.context() as old:
        old.setattr(cpk, 'build_compact_groups', lambda *args: None)
        with pytest.raises(ValueError):
            cpk._build_groups(frame, source, None)


def test_nullable_nonstandard_metadata_falls_back_without_merging_values(monkeypatch):
    frame, metadata = dataset(parts=10, tests=2, variants=False)
    events = [{**dict(event), 'result_index': [None, 0, 1][part % 3]} for part, values in enumerate(frame['__test_records']) for event in values]
    store = EventStore(pa.array(events))
    frame['__test_records'] = [PartEvents(store, part * 2, part * 2 + 2) for part in range(10)]
    compare(frame, metadata, monkeypatch)
