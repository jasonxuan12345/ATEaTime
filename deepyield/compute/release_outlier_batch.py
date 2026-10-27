"""Batch Outlier validity; materialize event views only for candidate hits."""
from collections import Counter
from collections.abc import Sequence

import numpy as np

from deepyield.compute.release_control import checkpoint, count, measure
from deepyield.compute.release_group_batch import GroupEvents
from deepyield.compute.release_sample_batch import _parts, _store_arrays, _prepare
from deepyield.rules.conditions import as_float

_REASONS = ('MISSING_OR_INVALID_CHANNEL', 'NON_PARAMETRIC_TEST', 'NOT_BIN1',
    'INVALID_OR_CONFLICTING_PART_RESULT', 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS',
    'EXPLICIT_FAILED_TEST', 'INVALID_LIMIT_ORDER', 'OUTSIDE_SPECIFICATION')


class CandidateRows(Sequence):
    """Match legacy (trace, event, value) rows without per-sample objects."""
    def __init__(self, events, traces, positions, values):
        self.events, self.traces = events, traces
        self.positions, self.values = positions, values

    def __len__(self):
        return len(self.positions)

    def __getitem__(self, index):
        position = int(self.positions[index])
        part, event = self.events[position]
        return self.traces[int(part['__part_index'])], event, float(self.values[index])


def _channels(events, traces, flag, cache):
    table = events.parts
    key = id(table)
    if key not in cache:
        with measure('outliers.part_channels'):
            parts = _parts(table, flag)
            keys, codes, valid, identities = [], [], [], {}
            for number, part in enumerate(table.records):
                if number % 4096 == 0:
                    checkpoint()
                trace = traces[int(part['__part_index'])]
                channel = (trace['lot'], trace['wafer'], trace['head'], trace['site'])
                if channel not in identities:
                    identities[channel] = len(keys)
                    keys.append(channel)
                codes.append(identities[channel])
                valid.append(trace['head'] is not None and trace['site'] is not None)
            cache[key] = (parts, keys, np.array(codes, dtype=np.int64), np.array(valid, dtype=bool))
    return cache[key]


def _event_arrays(events, parts, rows, prepared=None):
    if hasattr(events, 'event_ids'):
        data = _prepare(events, parts) if prepared is None else prepared
        flags = data['test']
        return data['values'], data['valid'], ((flags & 0x40) == 0) & ((flags & 0x80) != 0)
    indices = np.frombuffer(events.indices, dtype=np.int64)
    values = np.empty(len(events), dtype=np.float64)
    valid, failed = np.zeros(len(events), dtype=bool), np.zeros(len(events), dtype=bool)
    store_codes = parts['store_codes'][rows]
    for code in np.unique(store_codes):
        checkpoint()
        positions = np.flatnonzero(store_codes == code)
        selected = indices[positions]
        store_values, store_valid, store_test = _store_arrays(parts, int(code))
        values[positions] = store_values[selected]
        valid[positions] = store_valid[selected]
        flags = store_test[selected]
        failed[positions] = ((flags & 0x40) == 0) & ((flags & 0x80) != 0)
    return values, valid, failed


def batch_buckets(group, traces, flag, cache):
    events = group['events']
    if not isinstance(events, GroupEvents):
        return None
    checkpoint()
    parts, keys, channel_codes, channel_valid = _channels(events, traces, flag, cache)
    prepared = _prepare(events, parts) if hasattr(events, 'event_ids') else None
    rows = prepared['rows'] if prepared is not None else np.frombuffer(events.rows, dtype=np.int64)
    with measure('outliers.batch_mask'):
        reasons = np.zeros(len(events), dtype=np.uint8)
        reasons[~channel_valid[rows]] = 1
        if group['record_type'] not in {'PTR', 'MPR'}:
            reasons[reasons == 0] = 2
            values = np.empty(len(events), dtype=np.float64)
        else:
            reasons[(reasons == 0) & ~parts['bin1'][rows]] = 3
            reasons[(reasons == 0) & ~parts['passed'][rows]] = 4
            values, valid, failed = _event_arrays(events, parts, rows, prepared)
            reasons[(reasons == 0) & ~valid] = 5
            reasons[(reasons == 0) & failed] = 6
            low, high = as_float(group['spec']['low_limit']), as_float(group['spec']['high_limit'])
            if low is not None and high is not None and low >= high:
                reasons[reasons == 0] = 7
            else:
                outside = np.zeros(len(events), dtype=bool)
                if low is not None:
                    outside |= values < low
                if high is not None:
                    outside |= values > high
                reasons[(reasons == 0) & outside] = 8
    with measure('outliers.batch_buckets'):
        codes = channel_codes[rows]
        order = np.argsort(codes, kind='stable')
        unique, starts, amounts = np.unique(codes[order], return_index=True, return_counts=True)
        buckets = {}
        for offset in np.argsort(order[starts], kind='stable'):
            checkpoint()
            start, amount = int(starts[offset]), int(amounts[offset])
            positions = order[start:start + amount]
            excluded = Counter()
            labels, first, counts = np.unique(reasons[positions], return_index=True, return_counts=True)
            for index in np.argsort(first):
                reason = int(labels[index])
                if reason:
                    excluded[_REASONS[reason - 1]] = int(counts[index])
            selected = positions[reasons[positions] == 0]
            buckets[keys[int(unique[offset])]] = dict(
                rows=CandidateRows(events, traces, selected, values[selected]),
                excluded=excluded, executions=amount)
    count('outliers.batch_groups')
    count('outliers.examined_samples', len(events))
    return buckets
