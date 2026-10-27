"""Native indexed calculations preserve exact evidence with bounded buffers."""
import gc
import json
import weakref
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import numpy as np
import pytest

from deepyield.compute import release_cpk as cpk
from deepyield.compute import release_run_cache as cache
from deepyield.compute import release_sample_batch as samples
from deepyield.compute.release_chart_samples import ReportSampleResolver
from deepyield.compute.release_control import Operation, ReleaseCancelled, operation
from deepyield.compute.release_memory_cache import ByteCache
from deepyield.compute.release_outlier_stats import robust_outlier_statistics, robust_outlier_statistics_many
from deepyield.compute.release_references import IndexedReferences, compact_sample_refs, reference_json
from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.rules.loader import load_analysis_rules
from tests.test_compute.test_release_batch import exact, reports
from tests.test_compute.test_release_compact import table, compact
from deepyield.compute.release_event_store import release_frame


@pytest.fixture
def native():
    body, metadata = table(parts=64, tests=16, ties=True)
    return compact(body, metadata, batch_rows=7), metadata, body


def expanded(value):
    return json.loads(json.dumps(value, default=reference_json))


def test_native_reports_references_and_direct_gathers_match_scalar(native, monkeypatch):
    frame, metadata, body = native
    full = release_frame(body)
    with monkeypatch.context() as scalar:
        scalar.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = reports(full, metadata)
    with compact_sample_refs():
        actual = reports(frame, metadata)
    exact(expanded(expected), expanded(actual))
    cgm = {i['group_id']: i for i in actual['cgm']['items']}
    assert any(isinstance(i['sample_refs'], IndexedReferences) for i in actual['cpk']['items'])
    for item in actual['mmsa']['items']:
        refs = item['sample_refs']
        assert refs is cgm[item['group_id']]['sample_refs']
        assert refs.storage_bytes <= (len(refs.event_ids) + 7) // 8
        assert not refs.event_ids.flags.writeable
    resolver = ReportSampleResolver(frame, metadata['source_sha256'])
    def forbidden(*args):
        pytest.fail('Indexed report samples must not search each record')
    monkeypatch.setattr(resolver, 'find', forbidden)
    original = ReportSampleResolver(full, metadata['source_sha256'])
    for module in actual:
        for old, new in zip(expected[module]['items'], actual[module]['items']):
            exact(original(old), resolver(new))


def test_native_reverse_order_and_filtered_selection_match_scalar(native, monkeypatch):
    frame, metadata, body = native
    source = dict(source_file=metadata['source_file'], source_sha256=metadata['source_sha256'])
    chosen = {'T_1', 'T_7'}
    with monkeypatch.context() as scalar:
        scalar.setattr(cpk, 'build_compact_groups', lambda *args: None)
        expected = cpk._build_groups(release_frame(body).iloc[::-1], source, chosen)
    actual = cpk._build_groups(frame.iloc[::-1], source, chosen)
    assert list(actual) == list(expected)
    for key in expected:
        for (p, event), (old_part, old_event) in zip(actual[key]['events'], expected[key]['events']):
            assert p['__part_index'] == old_part['__part_index']
            exact({name: old_event[name] for name in event}, dict(event))


def test_preparations_are_batched_and_reset_after_cancellation(native, monkeypatch):
    frame, metadata, _ = native
    groups = cpk._group_events(frame, {}, None)
    monkeypatch.setattr(samples, 'SCRATCH_BYTES', 96 * 128)
    control = Operation()
    with operation(control), samples.prepared_groups(sorted(groups.items())) as entries:
        first = next(entries)[1]['events']
        assert samples._batch_data.get()
        control.cancel.set()
        with pytest.raises(ReleaseCancelled):
            list(entries)
    assert samples._batch_data.get() is None
    assert all(g['events'].prepared is None for g in groups.values())
    assert control.counts['samples.batch_peak_events'] <= 128
    assert 'sample_arrays' not in (first.parts.prepared or {})


def test_metrics_only_cgm_does_not_allocate_sample_references(native):
    frame, metadata, _ = native
    catalog = load_analysis_rules()
    with compact_sample_refs():
        metrics = build_release_cgm_report(frame, metadata, catalog=catalog, _metrics_only=True)
        complete = build_release_cgm_report(frame, metadata, catalog=catalog)
    assert all(i['sample_refs'] == [] for i in metrics['items'])
    left, right = expanded(metrics), expanded(complete)
    for item in right['items'] + right['risks']:
        item['sample_refs'] = []
    exact(left, right)


def test_session_index_reuse_selection_isolation_and_eviction(native, monkeypatch):
    frame, metadata, _ = native
    monkeypatch.setattr(cache, '_indexes', ByteCache(8 * 1024**2))
    source = dict(source_file=metadata['source_file'], source_sha256=metadata['source_sha256'])
    @cache.dataset_computation
    @cache.computation
    def groups(selected=frame, chosen=None):
        return cpk._group_events(selected, source, chosen)
    first = groups()
    assert groups() is first
    assert groups(chosen={'T_1'}) is not first
    assert groups(frame.iloc[1:]) is not first
    reference = weakref.ref(next(iter(first.values()))['events'].directory)
    del first
    cache.invalidate_indexes(source['source_sha256'])
    gc.collect()
    assert reference() is None
    assert cache._indexes.bytes == 0
    monkeypatch.setattr(cache, '_indexes', ByteCache(1))
    assert groups() is not groups()
    assert cache._indexes.bytes == 0


@pytest.mark.parametrize('minimum', [4, 30])
def test_batched_outlier_statistics_exactly_match_independent_populations(minimum):
    rng = np.random.default_rng(819)
    populations = [rng.normal(size=n) for n in (0, 0, 3, 3, 4, 4, 31, 31, 100, 100)]
    populations += [np.zeros(40), np.zeros(40), np.array([1e308, -1e308] * 20)]
    populations += [np.array([1e-300, 2e-300, 3e-300, 1e308]), np.ones(100) * 1e12]
    expected = [robust_outlier_statistics(values, 6., minimum) for values in populations]
    exact(expected, robust_outlier_statistics_many(populations, 6., minimum))


def test_byte_cache_budget_and_borrowed_values():
    cache = ByteCache(10)
    borrowed = np.arange(3)
    cache.put('a', borrowed, 6)
    cache.put('b', np.arange(2), 6)
    assert cache.get('a') is None and cache.bytes == 6
    assert borrowed.tolist() == [0, 1, 2]
    cache.put('too-large', object(), 11)
    assert cache.bytes == 6 and cache.get('too-large') is None


def test_validity_cache_does_not_own_the_source_store(native, monkeypatch):
    monkeypatch.setattr(samples, '_sample_arrays', ByteCache(1024**2))
    import pyarrow as pa
    from deepyield.compute.release_event_store import EventStore
    store = EventStore(pa.array([dict(result=1., test_flag=0, param_flag=0, result_valid=True)]))
    pointer = weakref.ref(store)
    samples._store_arrays({'stores': [store]}, 0)
    assert samples._sample_arrays.bytes
    del store
    gc.collect()
    assert pointer() is None and samples._sample_arrays.bytes == 0


def test_retained_indexes_expire_old_runs_and_dataset_removal_releases_them(native, monkeypatch):
    from app import release_check_service as service
    frame, metadata, _ = native
    owner = service.ReleaseDataset(None, Path('unused.parquet'), 'native.stdf', frame, metadata)
    monkeypatch.setattr(service, '_datasets', {'native': owner})
    monkeypatch.setattr(cache, '_indexes', ByteCache(0))
    monkeypatch.setattr(service, 'RETAINED_INDEX_BUDGET', 1)
    first = service.run_checks('native', {'cpk': {}}, ['cpk'])
    item = owner.runs[first['id']][1]['cpk']['items'][0]
    directory = weakref.ref(item['sample_refs'].directory)
    del item
    second = service.run_checks('native', {'cpk': {'part_indices': list(frame['__part_index'])[1:]}}, ['cpk'])
    assert list(owner.runs) == [second['id']]
    gc.collect()
    assert directory() is None
    assert service._retained_index_bytes() > 0
    service.remove_dataset('native')
    assert not service._datasets and service._retained_index_bytes() == 0


def test_compute_gate_wait_is_cancellable_and_does_not_leak_a_lock():
    entered, release, waiting = Event(), Event(), Event()
    calls = []
    @cache.dataset_computation
    def calculate(first=False):
        calls.append(first)
        if first:
            entered.set()
            assert release.wait(5)
        return 42
    control = Operation()
    def second():
        with operation(control):
            waiting.set()
            return calculate()
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(calculate, True)
        assert entered.wait(2)
        pending = pool.submit(second)
        assert waiting.wait(2)
        control.cancel.set()
        try:
            with pytest.raises(ReleaseCancelled):
                pending.result(timeout=2)
        finally:
            release.set()
        assert first.result(timeout=2) == 42
    assert calls == [True]
    assert calculate() == 42


def test_native_sequence_precision_and_invalid_negative_indexes(native):
    frame, metadata, _ = native
    for store in {id(e.store): e.store for e in frame['__test_records']}.values():
        values = store.columns['record_sequence'][1]
        store.columns['record_sequence'] = ('int', np.arange(len(values), dtype=np.uint64) + np.uint64(2**63 + 1), None)
    groups = cpk._group_events(frame, {}, None)
    events = next(iter(groups.values()))['events']
    with pytest.raises(IndexError):
        events[-len(events) - 2]
    assert events[-1][0] == events[len(events) - 1][0]
    with compact_sample_refs():
        output = reports(frame, metadata)
    item = next(i for i in output['cpk']['items'] if i['n'])
    points = ReportSampleResolver(frame, metadata['source_sha256'])(item)
    assert all(isinstance(p['record_sequence'], int) and p['record_sequence'] > 2**63 for p in points)


def test_unused_invalid_definition_cannot_break_a_valid_selection(native):
    import pyarrow as pa
    from deepyield.compute.release_event_store import EventStore
    frame, _, _ = native
    stores = {id(e.store): e.store for e in frame['__test_records']}
    original = next(iter(stores.values())).definitions
    amount = len(next(iter(original.columns.values()))[1])
    definitions = [{name: original.value(name, i) for name in original.columns} for i in range(amount)]
    definitions.append({**definitions[0], 'low_limit': float('nan')})
    shared = EventStore(pa.array(definitions))
    for store in stores.values():
        store.bind_definitions(shared)
    assert cpk._build_groups(frame, {}, None)
    first = next(iter(stores.values()))
    first.definition_codes[0] = amount
    with pytest.raises(ValueError):
        cpk._build_groups(frame, {}, None)
