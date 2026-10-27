"""Compact group indices for immutable native events; generic inputs keep their path."""
from array import array
from collections.abc import Sequence
import hashlib
import json

import numpy as np

from deepyield.compute.release_control import checkpoint, count, measure
from deepyield.compute.release_event_store import EventView, PartEvents

_IDENTITY_FIELDS = ('column', 'test_num', 'name', 'record_type', 'result_index',
                    'unit', 'low_limit', 'high_limit')
_PATTERN_LIMIT = 8
_BUFFER_LIMIT = 131072


class PartTable:
    def __init__(self, records):
        self.records = records
        self.prepared = None


class GroupEvents(Sequence):
    """Keep two indices per event, rather than an EventView and a Python tuple."""
    def __init__(self, parts):
        self.parts = parts
        self.rows = array('q')
        self.indices = array('q')
        self.prepared = None

    def append_index(self, row, index):
        self.rows.append(row)
        self.indices.append(index)

    def extend_indices(self, rows, indices):
        self.rows.frombytes(np.asarray(rows, dtype=np.int64).tobytes())
        self.indices.frombytes(np.asarray(indices, dtype=np.int64).tobytes())

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        part = self.parts.records[self.rows[index]]
        return part, EventView(part['__test_records'].store, self.indices[index])


def _eligible(records):
    stores = {}
    for part in records:
        events = part['__test_records']
        if not isinstance(events, PartEvents):
            return False
        stores[id(events.store)] = events.store
    for store in stores.values():
        if not {'column', 'record_sequence', 'test_num', 'record_type'}.issubset(store.columns):
            return False
        column_kind, columns, _ = store.columns['column']
        sequence_kind, sequences, _ = store.columns['record_sequence']
        if column_kind != 'text' or sequence_kind != 'int':
            return False
        if not np.isfinite(columns).all() or not np.isfinite(sequences).all():
            return False
    return True


def _latest(events, chosen, allowed_cache):
    store = events.store
    codes = store.columns['column'][1][events.start:events.stop]
    positions = np.arange(len(codes), dtype=np.int64)
    if chosen is not None:
        key = id(store)
        if key not in allowed_cache:
            allowed_cache[key] = np.array([name in chosen for name in store.columns['column'][2]], dtype=bool)
        allowed = allowed_cache[key]
        positions = positions[allowed[codes.astype(np.int64)]]
        codes = codes[positions]
    if not len(positions):
        return positions
    _, first, inverse = np.unique(codes, return_index=True, return_inverse=True)
    if len(first) == len(codes):
        return positions + events.start
    sequence = store.columns['record_sequence'][1][events.start:events.stop][positions]
    maxima = sequence[first].copy()
    np.maximum.at(maxima, inverse, sequence)
    candidates = np.where(sequence == maxima[inverse], np.arange(len(codes)), len(codes))
    latest = np.full(len(first), len(codes), dtype=np.int64)
    np.minimum.at(latest, inverse, candidates)
    # Dictionary insertion order is the first appearance; equal maxima keep the first.
    return positions[latest[np.argsort(first, kind='stable')]] + events.start


def _signature(store, indices):
    """Compare immutable metadata arrays, including float null-vs-NaN validity."""
    signature = []
    for name in _IDENTITY_FIELDS:
        if name not in store.columns:
            signature.append(None)
            continue
        kind, values, extra = store.columns[name]
        if kind == 'arrow':
            if values.null_count != len(values):
                return None
            signature.append(None)
        else:
            signature.append(values[indices])
            if kind == 'float':
                signature.append(extra[indices])
    return signature


def _same_signature(left, right):
    if len(left) != len(right):
        return False
    for a, b in zip(left, right):
        if a is None or b is None:
            if a is not b:
                return False
        elif not np.array_equal(a, b, equal_nan=a.dtype.kind in 'fc'):
            return False
    return True


def _flush_indices(pending, targets):
    if not pending:
        return
    with measure('groups.index_flush'):
        rows = np.concatenate([item[0] for item in pending])
        indices = np.concatenate([item[1] for item in pending])
        codes = np.concatenate([item[2] for item in pending])
        order = np.argsort(codes, kind='stable')
        ordered = codes[order]
        bounds = np.r_[0, np.flatnonzero(ordered[1:] != ordered[:-1]) + 1, len(order)]
        for start, stop in zip(bounds[:-1], bounds[1:]):
            checkpoint()
            positions = order[start:stop]
            targets[int(ordered[start])].extend_indices(rows[positions], indices[positions])
        pending.clear()
    count('groups.index_flushes')


def build_compact_groups(records, source, chosen):
    # Native calculation imports already share definitions and resolve retests.
    if records and all(hasattr(p['__test_records'], 'store') for p in records):
        from deepyield.compute.release_event_index import build_definition_groups
        indexed = build_definition_groups(records, source, chosen)
        if indexed is not None:
            return indexed
    if not _eligible(records):
        return None
    parts = PartTable(records)
    groups, identities, conditions_cache, allowed = {}, {}, {}, {}
    patterns, targets, target_codes, pending = [], [], {}, []
    buffered = 0
    for row, part in enumerate(records):
        checkpoint()
        events = part['__test_records']
        encoded = part['__conditions_json']
        if encoded not in conditions_cache:
            conditions_cache[encoded] = json.loads(encoded)
        with measure('groups.latest'):
            indices = _latest(events, chosen, allowed)
        with measure('groups.identities'):
            signature = _signature(events.store, indices)
            labels = None
            if signature is not None:
                for number, (store, condition, previous, codes) in enumerate(patterns):
                    if store is events.store and condition == encoded and _same_signature(signature, previous):
                        labels = codes
                        patterns.insert(0, patterns.pop(number))
                        count('groups.pattern_hit')
                        break
            if labels is None:
                count('groups.pattern_miss')
                labels = np.empty(len(indices), dtype=np.int64)
                columns = [events.store.take(name, indices) for name in _IDENTITY_FIELDS]
                for position, values in enumerate(zip(*columns)):
                    if position % 4096 == 0:
                        checkpoint()
                    lookup = (*values, encoded)
                    code = identities.get(lookup)
                    if code is None:
                        column, number, name, kind, result_index, unit, low, high = values
                        spec = dict(unit=unit, low_limit=low, high_limit=high)
                        identity = dict(column=column, test_num=number, test_name=name,
                            record_type=kind, result_index=result_index, spec=spec,
                            conditions=conditions_cache[encoded], source=source)
                        group_id = hashlib.sha256(json.dumps(identity, sort_keys=True,
                            ensure_ascii=False, allow_nan=False).encode('utf-8')).hexdigest()[:24]
                        group = groups.setdefault(group_id, {**identity, 'group_id': group_id,
                                                              'events': GroupEvents(parts)})
                        if group_id not in target_codes:
                            target_codes[group_id] = len(targets)
                            targets.append(group['events'])
                        code = target_codes[group_id]
                        identities[lookup] = code
                    labels[position] = code
                if signature is not None:
                    patterns.insert(0, (events.store, encoded, signature, labels))
                    del patterns[_PATTERN_LIMIT:]
            if len(indices):
                pending.append((np.full(len(indices), row, dtype=np.int64), indices, labels))
                buffered += len(indices)
        if buffered >= _BUFFER_LIMIT:
            _flush_indices(pending, targets)
            buffered = 0
        count('groups.input_events', len(events))
        count('groups.compact_parts')
        count('groups.batch_parts')
    _flush_indices(pending, targets)
    count('groups.built', len(groups))
    return groups
