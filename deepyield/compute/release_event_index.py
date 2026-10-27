"""Shared native event IDs, definition-based grouping and bounded gathers.

The directory owns only per-part/store metadata. A group is a read-only slice
of one uint32/uint64 CSR buffer; values and event views are never retained here.
Generic/legacy inputs continue through release_group_batch's compatibility path.
"""
import hashlib
import json
from operator import index as integer_index

import numpy as np

from deepyield.compute.release_control import checkpoint, count, measure
from deepyield.compute.release_event_store import EventView
from deepyield.compute.release_group_batch import GroupEvents, PartTable

FIELDS = ('column', 'test_num', 'name', 'record_type', 'result_index',
          'unit', 'low_limit', 'high_limit')
BUFFER_EVENTS = 131072


class UnsupportedLayout(ValueError):
    pass


class EventDirectory:
    def __init__(self, parts):
        self.parts = parts
        self.stores, self.bases, self.ends, self.store_ids = [], [], [], {}
        total = 0
        for part in parts.records:
            events = part['__test_records']
            if id(events.store) not in self.store_ids:
                self.store_ids[id(events.store)] = len(self.stores)
                self.stores.append(events.store)
                self.bases.append(total)
                total += len(events.store.definition_codes)
                self.ends.append(total)
        self.bases = np.asarray(self.bases, dtype=np.uint64)
        self.ends = np.asarray(self.ends, dtype=np.uint64)
        self.dtype = np.uint32 if total <= 2**32 else np.uint64
        starts, stops = [], []
        for part in parts.records:
            events = part['__test_records']
            base = int(self.bases[self.store_ids[id(events.store)]])
            starts.append(base + events.start)
            stops.append(base + events.stop)
        order = np.argsort(starts, kind='stable')
        nonempty = np.asarray(stops)[order] > np.asarray(starts)[order]
        self.part_rows = order[nonempty]
        self.part_starts = np.asarray(starts, dtype=np.uint64)[self.part_rows]
        self.part_ends = np.asarray(stops, dtype=np.uint64)[self.part_rows]
        if len(self.part_rows) > 1 and np.any(self.part_starts[1:] < self.part_ends[:-1]):
            raise UnsupportedLayout('Overlapping part views require explicit row references')
        sequence_dtype = np.result_type(*[s.columns['record_sequence'][1].dtype for s in self.stores])
        self.sequence_dtype = object if sequence_dtype.kind == 'f' else sequence_dtype

    def locate(self, event_ids):
        codes = np.searchsorted(self.ends, event_ids, side='right')
        local = (event_ids - self.bases[codes]).astype(np.int64)
        positions = np.searchsorted(self.part_ends, event_ids, side='right')
        rows = self.part_rows[positions]
        return rows, local, codes

    def gather(self, event_ids, parts):
        # One sort/split, not one full-array mask for every store.
        from deepyield.compute.release_sample_batch import _store_arrays
        rows, local, codes = self.locate(event_ids)
        values = np.empty(len(event_ids), dtype=np.float64)
        valid = np.empty(len(event_ids), dtype=bool)
        test = np.empty(len(event_ids), dtype=np.uint8)
        sequences = np.empty(len(event_ids), dtype=self.sequence_dtype)
        order = (np.arange(len(codes)) if len(codes) < 2 or np.all(codes[:-1] <= codes[1:])
                 else np.argsort(codes, kind='stable'))
        ordered = codes[order]
        bounds = np.r_[0, np.flatnonzero(ordered[1:] != ordered[:-1]) + 1, len(order)]
        for start, stop in zip(bounds[:-1], bounds[1:]):
            if start == stop:
                continue
            checkpoint()
            positions = order[start:stop]
            code = int(ordered[start])
            selected = local[positions]
            stored, accepted, flags = _store_arrays(parts, code)
            values[positions], valid[positions], test[positions] = stored[selected], accepted[selected], flags[selected]
            sequences[positions] = self.stores[code].columns['record_sequence'][1][selected]
        return dict(rows=rows, values=values, valid=valid, sequences=sequences, test=test)


class IndexedGroupEvents(GroupEvents):
    def __init__(self, directory, event_ids):
        self.parts, self.directory, self.event_ids = directory.parts, directory, event_ids
        self.prepared = None
        self.references = {}

    @property
    def rows(self):
        return self.directory.locate(self.event_ids)[0]

    @property
    def indices(self):
        return self.directory.locate(self.event_ids)[1]

    def __len__(self):
        return len(self.event_ids)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self[i] for i in range(*index.indices(len(self)))]
        index = integer_index(index)
        if index < 0:
            index += len(self)
        if not 0 <= index < len(self):
            raise IndexError(index)
        row, local, code = self.directory.locate(self.event_ids[index:index + 1])
        return self.parts.records[int(row[0])], EventView(self.directory.stores[int(code[0])], int(local[0]))


def build_definition_groups(records, source, chosen):
    if not records or any(not getattr(part['__test_records'].store, 'last_per_test', False)
                          for part in records):
        return None
    parts = PartTable(records)
    try:
        directory = EventDirectory(parts)
    except UnsupportedLayout:
        return None
    groups, identities, mappings, descriptors = [], {}, {}, []
    counts, firsts = np.zeros(0, dtype=np.int64), np.zeros(0, dtype=np.int64)
    traversed = 0
    for row, part in enumerate(records):
        checkpoint()
        events = part['__test_records']
        definitions = events.store.definitions
        encoded = part['__conditions_json']
        key = (id(definitions), encoded)
        mapping = mappings.get(key)
        if mapping is None:
            amount = len(next(iter(definitions.columns.values()))[1])
            # Unexecuted definitions may contain unrelated specification
            # variants. Decode/hash only identities actually selected here.
            mapping = np.full(amount, -2, dtype=np.int32)
            mappings[key] = mapping
        definitions_here = events.store.definition_codes[events.start:events.stop]
        unseen = definitions_here[mapping[definitions_here] == -2]
        if len(unseen):
            conditions = json.loads(encoded)
            unseen = np.unique(unseen)
            columns = [definitions.take(name, unseen) for name in FIELDS]
            for definition, values in zip(unseen, zip(*columns)):
                definition = int(definition)
                if definition % 4096 == 0:
                    checkpoint()
                column, number, name, kind, result_index, unit, low, high = values
                if chosen is not None and column not in chosen:
                    mapping[definition] = -1
                    continue
                identity = dict(column=column, test_num=number, test_name=name, record_type=kind,
                    result_index=result_index, spec=dict(unit=unit, low_limit=low, high_limit=high),
                    conditions=conditions, source=source)
                group_id = hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                    allow_nan=False).encode('utf-8')).hexdigest()[:24]
                code = identities.get(group_id)
                if code is None:
                    code = len(groups)
                    identities[group_id] = code
                    groups.append(dict(identity, group_id=group_id))
                mapping[definition] = code
            counts = np.pad(counts, (0, len(groups) - len(counts)))
            firsts = np.pad(firsts, (0, len(groups) - len(firsts)), constant_values=np.iinfo(np.int64).max)
        labels = mapping[definitions_here]
        accepted = labels >= 0
        selected = labels[accepted]
        counts += np.bincount(selected, minlength=len(groups))
        unique, first = np.unique(selected, return_index=True)
        firsts[unique] = np.minimum(firsts[unique], traversed + first)
        traversed += len(events)
        descriptors.append((events, mapping))
        count('groups.input_events', len(events))
        count('groups.compact_parts')
        count('groups.batch_parts')
    offsets = np.r_[0, np.cumsum(counts)]
    ids = np.empty(int(offsets[-1]), dtype=directory.dtype)
    cursor = offsets[:-1].copy()
    pending_ids, pending_codes, pending_size = [], [], 0

    def flush():
        nonlocal pending_size
        if not pending_size:
            return
        checkpoint()
        with measure('groups.index_flush'):
            codes = np.concatenate(pending_codes)
            values = np.concatenate(pending_ids)
            order = np.argsort(codes, kind='stable')
            ordered = codes[order]
            amounts = np.bincount(ordered, minlength=len(groups))
            starts = np.cumsum(amounts) - amounts
            destinations = cursor[ordered] + np.arange(len(order)) - starts[ordered]
            ids[destinations] = values[order]
            cursor[:] += amounts
        pending_ids.clear()
        pending_codes.clear()
        pending_size = 0
        count('groups.index_flushes')

    for events, mapping in descriptors:
        code = directory.store_ids[id(events.store)]
        base = int(directory.bases[code])
        for start in range(events.start, events.stop, BUFFER_EVENTS):
            stop = min(events.stop, start + BUFFER_EVENTS)
            labels = mapping[events.store.definition_codes[start:stop]]
            accepted = labels >= 0
            pending_codes.append(labels[accepted])
            pending_ids.append((np.arange(start, stop, dtype=np.uint64) + base)[accepted])
            pending_size += int(accepted.sum())
            if pending_size >= BUFFER_EVENTS:
                flush()
    flush()
    ids.flags.writeable = False
    output = {}
    for code in np.argsort(firsts, kind='stable'):
        if counts[code]:
            item = groups[code]
            item['events'] = IndexedGroupEvents(directory, ids[offsets[code]:offsets[code + 1]])
            output[item['group_id']] = item
    count('groups.definition_fast_path')
    count('groups.index_bytes', ids.nbytes)
    count('groups.built', len(output))
    return output
