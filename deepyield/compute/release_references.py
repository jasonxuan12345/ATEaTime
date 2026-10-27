"""Private, run-scoped sample references; public reports still use ordinary lists."""
from collections.abc import Sequence
from contextlib import contextmanager
from contextvars import ContextVar
from operator import index

import numpy as np

from deepyield.compute.release_control import checkpoint

_compact = ContextVar('release_compact_references', default=False)


@contextmanager
def compact_sample_refs():
    token = _compact.set(True)
    try:
        yield
    finally:
        _compact.reset(token)


def compact_refs_enabled():
    return _compact.get()


class CompactReferences(Sequence):
    """Two arrays plus shared part metadata, decoded only for detail/export."""

    def __init__(self, rows, sequences, part_indices, sites):
        self.rows = np.asarray(rows, dtype=np.int64).copy()
        numbers = np.asarray(sequences)
        # Mixed signed/large unsigned integers must never pass through float64.
        self.sequences = (numbers.copy() if numbers.dtype.kind in 'iu'
                          else np.asarray(sequences, dtype=object))
        if len(self.rows) != len(self.sequences):
            raise ValueError('Sample reference arrays have different lengths')
        self.rows.flags.writeable = self.sequences.flags.writeable = False
        self.part_indices, self.sites = part_indices, sites

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, position):
        if isinstance(position, slice):
            return [self[i] for i in range(*position.indices(len(self)))]
        position = index(position)
        row = int(self.rows[position])
        sequence = self.sequences[position]
        if isinstance(sequence, np.generic):
            sequence = sequence.item()
        return dict(part_index=self.part_indices[row], record_sequence=sequence, site=self.sites[row])

    def __iter__(self):
        for position in range(len(self)):
            if position % 4096 == 0:
                checkpoint()
            yield self[position]

    def __eq__(self, other):
        if not isinstance(other, Sequence):
            return NotImplemented
        return len(self) == len(other) and all(left == right for left, right in zip(self, other))


class IndexedReferences(CompactReferences):
    """Shared group IDs plus an optional bit mask; no per-module ID copies."""
    def __init__(self, events, selected, *, raw_sites):
        self.directory, self.event_ids = events.directory, events.event_ids
        self.raw_sites = raw_sites
        self.length = len(selected)
        self.mask = None
        if len(selected) != len(events):
            mask = np.zeros(len(events), dtype=bool)
            mask[selected] = True
            self.mask = np.packbits(mask, bitorder='little')
            self.mask.flags.writeable = False

    def __len__(self):
        return self.length

    def selected_ids(self):
        if self.mask is None:
            return self.event_ids
        return self.event_ids[np.unpackbits(self.mask, count=len(self.event_ids), bitorder='little').astype(bool)]

    @property
    def rows(self):
        return self.directory.locate(self.selected_ids())[0]

    @property
    def sequences(self):
        ids = self.selected_ids()
        _, local, codes = self.directory.locate(ids)
        values = [self.directory.stores[int(code)].value('record_sequence', int(position))
                  for code, position in zip(codes, local)]
        return np.asarray(values, dtype=object)

    @property
    def storage_bytes(self):
        return 0 if self.mask is None else self.mask.nbytes

    def __iter__(self):
        ids = self.selected_ids()
        # Bound decoded arrays, including full JSON export of a large group.
        from deepyield.compute.release_cpk import _flag
        for start in range(0, len(ids), 4096):
            checkpoint()
            rows, local, codes = self.directory.locate(ids[start:start + 4096])
            for row, position, code in zip(rows, local, codes):
                part = self.directory.parts.records[int(row)]
                site = part.get('SITE')
                yield dict(part_index=int(part['__part_index']),
                    record_sequence=self.directory.stores[int(code)].value('record_sequence', int(position)),
                    site=site if self.raw_sites else _flag(site))

    def __getitem__(self, position):
        if isinstance(position, slice):
            return list(self)[position]
        position = index(position)
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError(position)
        # Random access is rare; charts/exports use the vectorized ID path.
        from deepyield.compute.release_cpk import _flag
        rows, local, codes = self.directory.locate(self.selected_ids()[position:position + 1])
        part = self.directory.parts.records[int(rows[0])]
        site = part.get('SITE')
        return dict(part_index=int(part['__part_index']),
            record_sequence=self.directory.stores[int(codes[0])].value('record_sequence', int(local[0])),
            site=site if self.raw_sites else _flag(site))


def reference_json(value):
    if isinstance(value, CompactReferences):
        return list(value)
    raise TypeError(f'Object of type {type(value).__name__} is not JSON serializable')
