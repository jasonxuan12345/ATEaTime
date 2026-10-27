"""Compact immutable event views; Python objects are created only on access."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import sys

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
from deepyield.compute.release_control import checkpoint


class EventStore:
    def __init__(self, events: pa.StructArray):
        self.columns = {}
        for field in events.type:
            checkpoint()
            column = events.field(field.name)
            if pa.types.is_dictionary(field.type):
                # Native identity dictionaries: the C++ parser already memoized
                # these strings once per file, so keep codes plus values only.
                self.columns[field.name] = ('text', column.indices.to_numpy(zero_copy_only=False),
                                            column.dictionary.to_pylist())
            elif pa.types.is_string(field.type):
                encoded = pc.dictionary_encode(column)
                self.columns[field.name] = ('text', encoded.indices.to_numpy(zero_copy_only=False),
                                            encoded.dictionary.to_pylist())
            elif pa.types.is_integer(field.type):
                self.columns[field.name] = ('int', column.to_numpy(zero_copy_only=False).copy(), None)
            elif pa.types.is_floating(field.type):
                # Preserve null separately from a written NaN result.
                self.columns[field.name] = ('float', column.to_numpy(zero_copy_only=False).copy(),
                                            column.is_valid().to_numpy(zero_copy_only=False))
            elif pa.types.is_boolean(field.type) and not column.null_count:
                self.columns[field.name] = ('bool', column.to_numpy(zero_copy_only=False), None)
            else:
                # Own the uncommon/list buffers as well, so they do not retain
                # the entire unencoded incoming IPC body after conversion.
                self.columns[field.name] = ('arrow', pc.take(column, pa.array(np.arange(len(column)))), None)

    def retained_bytes(self, seen=None):
        if seen is not None:
            if id(self) in seen:
                return 0
            seen.add(id(self))
        size = 0
        for kind, values, extra in self.columns.values():
            size += values.nbytes
            if kind == 'text':
                size += sys.getsizeof(extra) + sum(sys.getsizeof(value) for value in extra)
            elif kind == 'float':
                size += extra.nbytes
        return size

    def value(self, name: str, index: int):
        kind, values, extra = self.columns[name]
        value = values[index]
        if kind == 'arrow':
            return value.as_py()
        if kind == 'float':
            return float(value) if extra[index] else None
        if kind == 'bool':
            return bool(value)
        if np.isnan(value):
            return None
        return extra[int(value)] if kind == 'text' else int(value)

    def take(self, name: str, indices):
        """Decode a bounded selection with exactly the scalar view's semantics."""
        if name not in self.columns:
            return [None] * len(indices)
        kind, values, extra = self.columns[name]
        if kind == 'arrow':
            return pc.take(values, pa.array(indices)).to_pylist()
        selected = values[indices].tolist()
        if kind == 'float':
            return [value if valid else None for value, valid in zip(selected, extra[indices])]
        if kind == 'bool':
            return selected
        if kind == 'int' and values.dtype.kind in 'iu':
            return selected
        if kind == 'text':
            if values.dtype.kind in 'iu':
                return [extra[value] for value in selected]
            missing = np.isnan(values[indices])
            codes = np.where(missing, 0, values[indices]).astype(np.int64).tolist()
            return [None if absent else extra[code] for code, absent in zip(codes, missing)]
        return [None if np.isnan(value) else int(value) for value in selected]


class EventView(Mapping):
    __slots__ = ('store', 'index')

    def __init__(self, store: EventStore, index: int):
        self.store, self.index = store, index

    def __getitem__(self, key):
        return self.store.value(key, self.index)

    def __iter__(self):
        return iter(self.store.columns)

    def __len__(self):
        return len(self.store.columns)


class PartEvents(Sequence):
    __slots__ = ('store', 'start', 'stop')

    def __init__(self, store: EventStore, start: int, stop: int):
        self.store, self.start, self.stop = store, start, stop

    def __len__(self):
        return self.stop - self.start

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        return EventView(self.store, self.start + index)

    def latest(self, chosen: set[str] | None):
        """Resolve last executions without constructing a mapping for every event."""
        latest = {}
        for index in range(self.start, self.stop):
            if index % 4096 == 0:
                checkpoint()
            column = self.store.value('column', index)
            if chosen is not None and column not in chosen:
                continue
            previous = latest.get(column)
            if previous is None or self.store.value('record_sequence', index) > self.store.value('record_sequence', previous):
                latest[column] = index
        return {column: EventView(self.store, index) for column, index in latest.items()}

    def find(self, sequence, column, record_type, result_index):
        columns = self.store.columns
        indices = np.flatnonzero(columns['record_sequence'][1][self.start:self.stop] == sequence) + self.start
        return [EventView(self.store, int(index)) for index in indices
                if self.store.value('column', index) == column
                and self.store.value('record_type', index) == record_type
                and self.store.value('result_index', index) == result_index]


def release_frame(table: pa.Table) -> pd.DataFrame:
    """Keep the public frame interface and standalone Parquet schema unchanged."""
    if '__test_records' not in table.column_names:
        return table.to_pandas(integer_object_nulls=True)
    frame = table.drop(['__test_records']).to_pandas(integer_object_nulls=True)
    rows = np.empty(len(table), dtype=object)
    position = 0
    for chunk in table.column('__test_records').chunks:
        store = EventStore(chunk.values)
        offsets = chunk.offsets.to_numpy(zero_copy_only=False)
        for start, stop in zip(offsets[:-1], offsets[1:]):
            rows[position] = PartEvents(store, int(start), int(stop))
            position += 1
    frame.insert(table.column_names.index('__test_records'), '__test_records', rows)
    return frame


def retained_data_bytes(frame: pd.DataFrame) -> int:
    """Account retained columns, not transient parser/report/process peaks."""
    size = int(frame.memory_usage(deep=True).drop(labels=['__test_records'], errors='ignore').sum())
    stores = {}
    size += len(frame) * np.dtype(object).itemsize
    for events in frame.get('__test_records', []):
        if isinstance(events, PartEvents):
            stores[id(events.store)] = events.store
            size += sys.getsizeof(events)
        else:
            size += sys.getsizeof(events) + sum(sys.getsizeof(event) for event in events)
    seen = set()
    for store in stores.values():
        size += store.retained_bytes(seen)
    return size
