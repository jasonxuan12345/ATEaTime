"""Calculation-only import views; original STDF is retained for full export.

Keep the last execution per test in each PRR, exactly as the report engines do.
Share test identities/specifications instead of expanding them for every value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc

from deepyield.compute.release_control import checkpoint, count, measure
from deepyield.compute.release_event_store import EventStore, PartEvents


IDENTITY_FIELDS = ('column', 'test_num', 'name', 'record_type', 'result_index',
                   'unit', 'low_limit', 'high_limit')
VALUE_FIELDS = ('result', 'record_sequence', 'test_flag', 'param_flag', 'result_valid')


class _LookupValues:
    """Expose existing batch-array indexing without expanding shared definitions."""
    def __init__(self, codes, values):
        self.codes, self.values = codes, values
        self.dtype = values.dtype

    def __len__(self):
        return len(self.codes)

    def __getitem__(self, key):
        return self.values[self.codes[key]]

    def __array__(self, dtype=None, copy=None):
        result = self.values[self.codes]
        return result.astype(dtype, copy=False) if dtype is not None else result


def _unsigned(values):
    """Narrow exact nonnegative integers; never reduce measurement precision."""
    if not len(values):
        return values.astype(np.uint8)
    if values.dtype.kind not in 'iu' or values.min() < 0:
        return values
    maximum = values.max()
    dtype = next(dtype for dtype in (np.uint8, np.uint16, np.uint32, np.uint64)
                 if maximum <= np.iinfo(dtype).max)
    return values.astype(dtype, copy=False)


def _indices(column):
    """Per-row integer codes for a field; native dictionaries skip re-encoding."""
    encoded = column if pa.types.is_dictionary(column.type) else pc.dictionary_encode(column)
    values = encoded.indices.to_numpy(zero_copy_only=False)
    # Use the decoded validity here. Some Arrow versions mishandle the null
    # bitmap offset in fill_null after taking a sliced struct.
    if values.dtype.kind == 'f':
        values = np.nan_to_num(values, nan=-1).astype(np.int32)
    return values


class CalculationStore(EventStore):
    def __init__(self, events):
        fields = [name for name in VALUE_FIELDS if name in events.type.names]
        super().__init__(pa.StructArray.from_arrays([events.field(name) for name in fields], names=fields))
        for name, (kind, values, extra) in list(self.columns.items()):
            if kind == 'int':
                self.columns[name] = kind, _unsigned(values), extra

        fields = [name for name in IDENTITY_FIELDS if name in events.type.names]
        # Deduplicate identity tuples on per-row codes only: strings stay in the
        # field dictionaries, so the full event body is never materialized.
        if not fields:
            self.definitions, self.definition_codes = None, np.empty(0, dtype=np.uint8)
            return
        with measure('prepare.encode'):
            encoded = [_indices(events.field(name)) for name in fields]
        with measure('prepare.identities'):
            # Run-length grouping first: events are written per part in test
            # order, so unique tuples repeat consecutively and the sorted
            # np.unique pass only sees a few thousand representative rows.
            stacked = np.column_stack(encoded)
            starts = np.concatenate(([0], np.flatnonzero(
                np.any(stacked[1:] != stacked[:-1], axis=1)) + 1)) if len(stacked) > 1 \
                else np.array([0] if len(stacked) else [], dtype=np.int64)
            if len(starts):
                _, first, key_codes = np.unique(stacked[starts], axis=0,
                                                return_index=True, return_inverse=True)
                codes = key_codes[np.searchsorted(starts, np.arange(len(stacked)), side='right') - 1]
            else:
                first = np.empty(0, dtype=np.int64)
                codes = np.empty(0, dtype=np.int64)
        self.definition_codes = _unsigned(codes)
        definitions = pc.take(events, pa.array(starts[first]))
        self.definitions = EventStore(pa.StructArray.from_arrays(
            [definitions.field(name) for name in fields], names=fields))
        self.bind_definitions(self.definitions)

    def bind_definitions(self, definitions):
        self.definitions = definitions
        for name, (kind, values, extra) in definitions.columns.items():
            if kind == 'arrow':
                # Native identity fields have explicit scalar types, including
                # all-null result indices. Do not silently retain nested data.
                raise ValueError(f'计算字段类型不受支持：{name}')
            self.columns[name] = (kind, _LookupValues(self.definition_codes, values),
                _LookupValues(self.definition_codes, extra) if kind == 'float' else extra)

    def retained_bytes(self, seen=None):
        if seen is None:
            seen = set()
        if id(self) in seen:
            return 0
        seen.add(id(self))
        size = self.definition_codes.nbytes
        for kind, values, extra in self.columns.values():
            if isinstance(values, _LookupValues):
                continue
            size += values.nbytes
            if kind == 'float':
                size += extra.nbytes
        if self.definitions is not None:
            size += self.definitions.retained_bytes(seen)
        return size


class DefinitionPool:
    """Share definitions across batches of one file, including specification variants."""
    def __init__(self):
        self.rows, self.identities, self.stores = [], {}, []
        self.schema = None

    def add(self, store, event_type):
        fields = list(store.definitions.columns)
        if self.schema is None:
            self.schema = pa.struct([event_type.field(name) for name in fields])
        amount = len(next(iter(store.definitions.columns.values()))[1])
        count('prepare.definition_rows', amount)
        codes = np.empty(amount, dtype=np.int64)
        # One vectorized decode per field, then tuple pooling; scalar
        # store.value() calls dominated this loop before.
        rows = zip(*(store.definitions.take(name, np.arange(amount)) for name in fields))
        for index, key in enumerate(rows):
            if index % 4096 == 0:
                checkpoint()
            code = self.identities.get(key)
            if code is None:
                code = len(self.rows)
                self.identities[key] = code
                self.rows.append(dict(zip(fields, key)))
            codes[index] = code
        store.definition_codes = _unsigned(codes[store.definition_codes])
        # Only calculation scalars need to survive until final definitions exist.
        store.columns = {name: column for name, column in store.columns.items()
                         if name not in fields}
        store.definitions = None
        self.stores.append(store)

    def finish(self):
        if self.schema is None:
            return
        definitions = EventStore(pa.array(self.rows, type=self.schema))
        for store in self.stores:
            store.bind_definitions(definitions)


def _last_indices(records):
    events = records.values
    columns = _indices(events.field('column'))
    sequences = events.field('record_sequence').to_numpy(zero_copy_only=False)
    selected, offsets = [], [0]
    for start, stop in zip(records.offsets.to_numpy()[:-1], records.offsets.to_numpy()[1:]):
        checkpoint()
        codes = columns[start:stop]
        _, first, inverse = np.unique(codes, return_index=True, return_inverse=True)
        if len(first) == len(codes):
            indices = np.arange(start, stop, dtype=np.int64)
        else:
            sequence = sequences[start:stop]
            maxima = sequence[first].copy()
            np.maximum.at(maxima, inverse, sequence)
            candidates = np.where(sequence == maxima[inverse], np.arange(len(codes)), len(codes))
            latest = np.full(len(first), len(codes), dtype=np.int64)
            np.minimum.at(latest, inverse, candidates)
            indices = latest[np.argsort(first, kind='stable')] + start
        selected.append(indices)
        offsets.append(offsets[-1] + len(indices))
    return (np.concatenate(selected) if selected else np.empty(0, dtype=np.int64)), offsets


def calculation_frame(batch: pa.RecordBatch, metadata: dict, definitions=None) -> pd.DataFrame:
    """Consume one bounded native batch; keep no full Arrow body or wide duplicate."""
    count('prepare.batches')
    count('prepare.rows', batch.num_rows)
    wide = {test['column'] for test in metadata['test_catalog']}
    excluded = wide | {'__test_records', 'SOURCE_FILE', 'PART_TXT', '__missing_fields', 'PASS_FAIL'}
    with measure('prepare.part_frame'):
        frame = batch.select([name for name in batch.schema.names if name not in excluded]) \
                     .to_pandas(integer_object_nulls=True)
    records = batch.column(batch.schema.get_field_index('__test_records'))
    with measure('prepare.last_indices'):
        indices, offsets = _last_indices(records)
    count('prepare.events', len(indices))
    fields = [name for name in (*IDENTITY_FIELDS, *VALUE_FIELDS) if name in records.values.type.names]
    # Project before take: alarm text/raw optional payloads never enter retained data.
    projected = pa.StructArray.from_arrays([records.values.field(name) for name in fields], names=fields)
    with measure('prepare.store'):
        store = CalculationStore(pc.take(projected, pa.array(indices)))
        # Explicit guarantee for grouping; arbitrary CalculationStore instances
        # must still use the duplicate-execution compatibility path.
        store.last_per_test = True
    if definitions is not None:
        with measure('prepare.definitions'):
            definitions.add(store, projected.type)
    rows = np.empty(len(frame), dtype=object)
    with measure('prepare.part_events'):
        for row, (start, stop) in enumerate(zip(offsets[:-1], offsets[1:])):
            rows[row] = PartEvents(store, start, stop)
    frame['__test_records'] = rows
    return frame


def join_calculation_frames(frames, definitions=None):
    if definitions is not None:
        with measure('events.definitions'):
            definitions.finish()
    with measure('events.concat'):
        frame = pd.concat(frames, ignore_index=True, copy=False)
    # These strings repeat per chip; category preserves their public scalar values.
    with measure('events.categories'):
        for name in ('__conditions_json', 'LOT_ID', 'WAFER_ID'):
            if name in frame:
                frame[name] = frame[name].astype('category')
    return frame
