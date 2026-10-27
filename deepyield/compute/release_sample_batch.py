"""Batch sample validity with ordered exclusions and unchanged public references."""
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import os
import weakref

import numpy as np

from deepyield.compute.release_control import checkpoint, count, measure, peak_count
from deepyield.compute.release_group_batch import GroupEvents
from deepyield.compute.release_references import CompactReferences, IndexedReferences, compact_refs_enabled
from deepyield.compute.release_memory_cache import ByteCache
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.rules.conditions import as_float

_batch_data = ContextVar('release_prepared_batch', default=None)
SCRATCH_BYTES = int(os.environ.get('DEEPYIELD_RELEASE_SCRATCH_MB', '128')) * 1024**2
_sample_arrays = ByteCache(int(os.environ.get('DEEPYIELD_RELEASE_VALIDITY_CACHE_MB', '64')) * 1024**2)


def invalidate_store_arrays(stores):
    identities = {id(store) for store in stores}
    _sample_arrays.discard(lambda reference: id(reference()) in identities)


@contextmanager
def prepared_groups(entries):
    """Read a bounded batch once per store, then release it before the next.

    The 96-byte allowance includes gather positions, sort order, masks and
    outputs. A single unusually large group remains exact, not sampled.
    """
    pending = []
    size = 0

    def batches():
        nonlocal pending, size
        for key, group in entries:
            events = group['events']
            if not hasattr(events, 'event_ids'):
                if pending:
                    yield pending
                    pending, size = [], 0
                yield [(key, group)]
                continue
            if pending and (size + len(events) > max(1, SCRATCH_BYTES // 96) or
                            pending[0][1]['events'].directory is not events.directory):
                yield pending
                pending, size = [], 0
            pending.append((key, group))
            size += len(events)
        if pending:
            yield pending

    def iterate():
        from deepyield.compute.release_cpk import _flag
        for batch in batches():
            events = batch[0][1]['events']
            if not hasattr(events, 'event_ids'):
                yield from batch
                continue
            checkpoint()
            with measure('samples.prepare_batch'):
                ids = np.concatenate([g['events'].event_ids for _, g in batch])
                data = events.directory.gather(ids, _parts(events.parts, _flag))
                prepared, start = {}, 0
                for _, group in batch:
                    current = group['events']
                    stop = start + len(current)
                    prepared[id(current)] = {key: value[start:stop] for key, value in data.items()}
                    start = stop
                peak_count('samples.batch_peak_events', len(ids))
                peak_count('samples.batch_output_bytes', ids.nbytes + sum(v.nbytes for v in data.values()))
            token = _batch_data.set(prepared)
            try:
                yield from batch
            finally:
                _batch_data.reset(token)
                # Clear before the next batch allocates its working buffers.
                prepared.clear()
                del data, ids

    iterator = iterate()
    try:
        yield iterator
    finally:
        iterator.close()


@dataclass
class Samples:
    values: list
    refs: Sequence[dict]
    excluded: Counter
    by_site: dict


def _store_length(store):
    return len(next(iter(store.columns.values()))[1])


def _numbers(store, name, indices):
    column = store.columns.get(name)
    if column is not None and column[0] in {'int', 'float'}:
        kind, data, extra = column
        values = data if indices is None else data[indices]
        values = values.astype(np.float64, copy=False)
        valid = np.isfinite(values)
        if kind == 'float':
            valid &= extra if indices is None else extra[indices]
        return values, valid
    selected = np.arange(_store_length(store)) if indices is None else indices
    items = [as_float(value) for value in store.take(name, selected)]
    values = np.array([np.nan if value is None else value for value in items], dtype=np.float64)
    return values, np.isfinite(values)


def _flags(store, name, indices):
    values, valid = _numbers(store, name, indices)
    valid &= (values >= 0) & (values <= 255) & (values == np.floor(values))
    return np.where(valid, values, 0).astype(np.uint8), valid


def _result_valid(store, indices):
    """Vectorized result_valid; keeps the scalar view's `is True` semantics."""
    column = store.columns.get('result_valid')
    if column is not None and column[0] == 'bool':
        values = column[1]
        return values if indices is None else values[indices]
    selected = np.arange(_store_length(store)) if indices is None else indices
    return np.fromiter((value is True for value in store.take('result_valid', selected)),
                       dtype=bool, count=len(selected))


def _event_values(store, indices=None):
    """Shared scalar-equivalent validity; callers add their own population policy."""
    result, finite = _numbers(store, 'result', indices)
    test, test_valid = _flags(store, 'test_flag', indices)
    param, param_valid = _flags(store, 'param_flag', indices)
    valid = (finite & test_valid & param_valid & ((test & 0x3F) == 0)
             & ((param & 0x27) == 0) & _result_valid(store, indices))
    return result, valid, test


def _store_arrays(parts, code):
    """Whole-store (values, valid, test) computed once, shared by every group."""
    store = parts['stores'][code]
    arrays = _sample_arrays.get(weakref.ref(store))
    if arrays is None:
        with measure('samples.store_arrays'):
            arrays = _event_values(store, None)
            column = store.columns.get('result')
            borrowed = column is not None and column[0] in {'float', 'int'} and np.shares_memory(arrays[0], column[1])
            size = sum(array.nbytes for array in arrays[1:]) + (0 if borrowed else arrays[0].nbytes)
            cache = _sample_arrays
            reference = weakref.ref(store, lambda expired: cache.discard(lambda key: key is expired))
            _sample_arrays.put(reference, arrays, size)
            peak_count('samples.validity_cache_bytes', _sample_arrays.bytes)
    return arrays


def _parts(table, flag):
    if table.prepared is None:
        with measure('samples.part_flags'):
            records = table.records
            flags = [flag(part.get('PART_FLAG')) for part in records]
            states = [None if value is None else _classify_part_flag(value)[0] for value in flags]
            stores = []
            store_ids = {}
            codes = []
            for part in records:
                store = part['__test_records'].store
                if id(store) not in store_ids:
                    store_ids[id(store)] = len(stores)
                    stores.append(store)
                codes.append(store_ids[id(store)])
            sites = tuple(flag(part.get('SITE')) for part in records)
            table.prepared = dict(
                bin1=np.array([as_float(part.get('SBIN')) == 1 for part in records]),
                passed=np.array([state == 'PASS' for state in states]),
                usable=np.array([state in {'PASS', 'FAIL'} for state in states]),
                sites=sites,
                # Float64 with NaN keeps site masking vectorized; None sites
                # resurface as dict keys exactly where reports consume them.
                site_array=np.array([np.nan if value is None else value for value in sites],
                                    dtype=np.float64),
                raw_sites=tuple(part.get('SITE') for part in records),
                part_indices=tuple(int(part['__part_index']) for part in records),
                stores=stores, store_codes=np.array(codes, dtype=np.int64))
    return table.prepared


def _prepare(events, parts):
    batch = _batch_data.get()
    if batch is not None and id(events) in batch:
        count('samples.batch_hit')
        return batch[id(events)]
    if hasattr(events, 'event_ids'):
        with measure('samples.prepare_arrays'):
            return events.directory.gather(events.event_ids, parts)
    if events.prepared is not None:
        count('samples.prepared_hit')
        return events.prepared
    with measure('samples.prepare_arrays'):
        rows = np.frombuffer(events.rows, dtype=np.int64)
        indices = np.frombuffer(events.indices, dtype=np.int64)
        store_codes = parts['store_codes'][rows]
        values = np.empty(len(events), dtype=np.float64)
        valid = np.zeros(len(events), dtype=bool)
        sequences = np.empty(len(events), dtype=np.int64)
        for code in np.unique(store_codes):
            checkpoint()
            positions = np.flatnonzero(store_codes == code)
            selected = indices[positions]
            store_values, store_valid, _ = _store_arrays(parts, int(code))
            values[positions] = store_values[selected]
            valid[positions] = store_valid[selected]
            sequences[positions] = parts['stores'][code].columns['record_sequence'][1][selected]
        # No whole-run array retention. Legacy groups are prepared on demand too.
        prepared = dict(rows=rows, values=values, valid=valid, sequences=sequences)
    count('samples.prepared_miss')
    return prepared


def collect_samples(group, mode, flag, *, keep_refs=True):
    events = group['events']
    if not isinstance(events, GroupEvents):
        return None
    checkpoint()
    parts = _parts(events.parts, flag)
    data = _prepare(events, parts)
    rows = data['rows']
    reasons = np.zeros(len(events), dtype=np.uint8)
    functional = group['record_type'] == 'FTR'
    with measure(mode + '.batch_mask'):
        if mode == 'cpk':
            labels = ('NOT_BIN1', 'INVALID_OR_CONFLICTING_PART_RESULT',
                      'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS')
            reasons[~parts['bin1'][rows]] = 1
            reasons[(reasons == 0) & ~parts['passed'][rows]] = 2
            sites = None
        else:
            labels = ('INVALID_PART_RESULT', 'MISSING_OR_INVALID_SITE',
                      'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS')
            sites = parts['site_array'][rows]
            reasons[~parts['usable'][rows]] = 1
            reasons[(reasons == 0) & np.isnan(sites)] = 2
        if not functional:
            reasons[(reasons == 0) & ~data['valid']] = 3
        excluded = Counter()
        # Preserve the original Counter's first-encounter order as well as its counts.
        codes, first, amounts = np.unique(reasons, return_index=True, return_counts=True)
        for position in np.argsort(first):
            code = int(codes[position])
            if code:
                excluded[labels[code - 1]] = int(amounts[position])
        selected = np.flatnonzero(reasons == 0) if not functional else np.array([], dtype=np.int64)
    with measure(mode + '.batch_refs'):
        array_values = data['values'][selected]
        values = array_values if compact_refs_enabled() else array_values.tolist()
        refs, by_site = [], {}
        if compact_refs_enabled():
            checkpoint()
            if not keep_refs:
                refs = []
            elif hasattr(events, 'event_ids'):
                policy = 'bin1' if mode == 'cpk' else 'valid_site'
                refs = events.references.get(policy)
                if refs is None:
                    refs = events.references[policy] = IndexedReferences(events, selected, raw_sites=mode == 'cpk')
            else:
                refs = CompactReferences(rows[selected], data['sequences'][selected],
                    parts['part_indices'], parts['raw_sites'] if mode == 'cpk' else parts['sites'])
            if mode != 'cpk':
                selected_sites = sites[selected]
                unique, first = np.unique(selected_sites, return_index=True)
                for site in unique[np.argsort(first)]:
                    checkpoint()
                    missing = np.isnan(site)
                    members = np.isnan(selected_sites) if missing else selected_sites == site
                    by_site[None if missing else float(site)] = \
                        array_values[members]
            count(mode + '.compact_refs', len(refs))
            count(mode + '.reference_array_bytes', refs.storage_bytes if isinstance(refs, IndexedReferences)
                  else refs.rows.nbytes + refs.sequences.nbytes if refs else 0)
            checkpoint()
        else:
            for number, (position, value) in enumerate(zip(selected, values)):
                if number % 4096 == 0:
                    checkpoint()
                row = int(rows[position])
                part = events.parts.records[row]
                site = part.get('SITE') if mode == 'cpk' else parts['sites'][row]
                refs.append(dict(part_index=int(part['__part_index']),
                                 record_sequence=int(data['sequences'][int(position)]), site=site))
                if mode != 'cpk':
                    by_site.setdefault(site, []).append(value)
    count(mode + '.batch_groups')
    return Samples(values, refs, excluded, by_site)
