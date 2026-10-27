"""Exact report-sample lookup shared by Release charts; no UI dependencies."""
from __future__ import annotations

import hashlib
import json
from collections import Counter

import pandas as pd
import numpy as np

from deepyield.rules.conditions import as_float
from deepyield.compute.release_control import checkpoint, timed
from deepyield.compute.release_event_store import PartEvents


class ReportSampleResolver:
    """Export-scoped lookup; part metadata and event indices are reused across tests."""

    def __init__(self, frame: pd.DataFrame, source_hash: str, *, identifiers=True, part_indices=None, minimal=False):
        self.frame, self.source_hash, self.identifiers = frame, source_hash, identifiers
        self.part_indices, self.minimal = part_indices, minimal
        self.parts = None
        self.indices = {}
        self._compact = None
        self._entries = {}

    def _parts(self):
        if self.parts is None:
            columns = [c for c in ['__part_index', '__test_records', 'HEAD_NUM', 'LOT_ID',
                'WAFER_ID', 'DIE_X', 'DIE_Y', 'STDF_PART_ID'] if c in self.frame.columns]
            if self.minimal:
                columns = ['__part_index', '__test_records']
            selected = (self.frame if self.part_indices is None else
                        self.frame.loc[self.frame['__part_index'].isin(self.part_indices)])
            self.parts = {}
            for index, values in enumerate(selected[columns].itertuples(index=False, name=None)):
                if index % 4096 == 0:
                    checkpoint()
                row = dict(zip(columns, values))
                self.parts[int(row['__part_index'])] = row
        return self.parts

    def find(self, part_index, events, ref, item):
        key = (ref['record_sequence'], item['column'], item['record_type'], item.get('result_index'))
        if part_index not in self.indices:
            if hasattr(events, 'store'):
                sequences = events.store.columns['record_sequence'][1][events.start:events.stop]
                if len(sequences) < 2 or np.all(sequences[:-1] <= sequences[1:]):
                    self.indices[part_index] = (sequences, None)
                else:
                    order = np.argsort(sequences, kind='stable')
                    self.indices[part_index] = (sequences[order], order)
            else:
                lookup = {}
                for index, event in enumerate(events):
                    if index % 4096 == 0:
                        checkpoint()
                    identity = (event['record_sequence'], event['column'],
                                event.get('record_type'), event.get('result_index'))
                    lookup.setdefault(identity, []).append(event)
                self.indices[part_index] = lookup
        lookup = self.indices[part_index]
        if isinstance(lookup, dict):
            return lookup.get(key, [])
        sequences, order = lookup
        left = np.searchsorted(sequences, key[0], side='left')
        right = np.searchsorted(sequences, key[0], side='right')
        matches = [events[int(index)] for index in (range(left, right) if order is None else order[left:right])]
        return [event for event in matches if event['column'] == key[1]
                and event.get('record_type') == key[2] and event.get('result_index') == key[3]]

    def _store_entry(self, store):
        """Array handles for store-wide resolution; None keeps the per-ref path."""
        entry = {'store': store}
        for name in ('record_sequence', 'column', 'record_type', 'result_index', 'result'):
            column = store.columns.get(name)
            if column is None:
                return None
            kind, values, extra = column
            if name == 'record_sequence':
                if kind != 'int' or values.dtype.kind not in 'iu':
                    return None
                # Retained events keep parse order, so sequences ascend per store.
                entry['sequences'] = values.astype(np.int64, copy=False)
            elif name in ('column', 'record_type'):
                if kind != 'text':
                    return None
                entry[name] = (values, {text: code for code, text in enumerate(extra)})
            elif name == 'result_index':
                if kind != 'int':
                    return None
                entry[name] = values
            elif kind not in ('float', 'int'):
                return None
            else:
                entry[name] = column
        return entry

    def _layout(self):
        if self._compact is not None:
            return
        self._compact = {}
        for part_index, row in self.parts.items():
            events = row['__test_records']
            if not isinstance(events, PartEvents):
                continue
            entry = self._entries.get(id(events.store), False)
            if entry is False:
                entry = self._store_entry(events.store)
                self._entries[id(events.store)] = entry
            if entry is not None:
                self._compact[part_index] = (entry, events.start, events.stop)

    def _identity_matches(self, entry, positions, item):
        values, codes = entry['column']
        target = codes.get(item['column'])
        if target is None or not (values[positions] == target).all():
            return False
        values, codes = entry['record_type']
        target = codes.get(item['record_type'])
        if target is None or not (values[positions] == target).all():
            return False
        selected = entry['result_index'][positions]
        target = item.get('result_index')
        if target is None:
            # Null result indices surface as float NaN; absent means int dtype.
            return selected.dtype.kind == 'f' and bool(np.isnan(selected).all())
        return bool((selected == target).all())

    def _compact_samples(self, item, parts):
        """Resolve compact native refs with store-wide vectorized lookups.

        Returns None for anything unexpected; the per-ref generic path then
        raises the exact historical error for the first offending reference.
        """
        refs = item.get('sample_refs')
        if not refs:
            return [] if item.get('n') == 0 else None
        self._layout()
        groups = {}
        for number, ref in enumerate(refs):
            located = self._compact.get(int(ref['part_index']))
            if located is None:
                return None
            entry, start, stop = located
            group = groups.get(id(entry))
            if group is None:
                group = groups[id(entry)] = [entry, [], [], [], []]
            group[1].append(number)
            group[2].append(int(ref['record_sequence']))
            group[3].append(start)
            group[4].append(stop)
        positions = np.full(len(refs), -1, dtype=np.int64)
        for entry, numbers, sequences, starts, stops in groups.values():
            numbers = np.asarray(numbers)
            sequences = np.asarray(sequences, dtype=np.int64)
            found = np.searchsorted(entry['sequences'], sequences)
            inside = ((found >= np.asarray(starts)) & (found < np.asarray(stops))
                      & (found < len(entry['sequences'])))
            inside &= entry['sequences'][np.where(inside, found, 0)] == sequences
            positions[numbers[inside]] = found[inside]
        if (positions < 0).any():
            return None
        for entry, numbers, *_ in groups.values():
            if not self._identity_matches(entry, positions[np.asarray(numbers)], item):
                return None
        values = np.empty(len(refs), dtype=np.float64)
        for entry, numbers, *_ in groups.values():
            local = positions[np.asarray(numbers)]
            kind, stored, valid = entry['result']
            picked = stored[local]
            if kind == 'float':
                accepted = valid[local] & np.isfinite(picked)
            else:
                picked = picked.astype(np.float64)
                accepted = np.isfinite(picked)
            if not accepted.all():
                return None
            values[numbers] = picked
        seen = set()
        for ref in refs:
            key = (int(ref['part_index']), ref['record_sequence'])
            if key in seen:
                return None
            seen.add(key)
        samples = []
        for number, ref in enumerate(refs):
            if number % 4096 == 0:
                checkpoint()
            samples.append(_sample_entry(item, ref, parts[int(ref['part_index'])],
                float(values[number]), self.source_hash,
                identifiers=self.identifiers, minimal=self.minimal))
        if item.get('n') is None or len(samples) != item['n']:
            return None
        return sorted(samples, key=lambda sample: (sample['record_sequence'],
                                                   sample['part_index'], sample.get('id', '')))

    @timed('export.samples')
    def __call__(self, item):
        parts = self._parts()
        resolved = self._indexed_samples(item, parts)
        if resolved is not None:
            return resolved
        resolved = self._compact_samples(item, parts)
        if resolved is not None:
            return resolved
        return _report_samples(item, self.source_hash, parts, self)

    def _indexed_samples(self, item, parts):
        from deepyield.compute.release_references import IndexedReferences
        refs = item.get('sample_refs')
        if not isinstance(refs, IndexedReferences):
            return None
        ids = refs.selected_ids()
        rows, local, codes = refs.directory.locate(ids)
        values = np.empty(len(ids), dtype=np.float64)
        for code in np.unique(codes):
            checkpoint()
            positions = np.flatnonzero(codes == code)
            store = refs.directory.stores[int(code)]
            selected = local[positions]
            # Provenance and identity are verified even for private references.
            for definition in np.unique(store.definition_codes[selected]):
                if (store.definitions.value('column', int(definition)) != item['column'] or
                    store.definitions.value('record_type', int(definition)) != item['record_type'] or
                    store.definitions.value('result_index', int(definition)) != item.get('result_index')):
                    return None
            kind, stored, valid = store.columns['result']
            picked = stored[selected].astype(np.float64, copy=False)
            if not np.isfinite(picked).all() or (kind == 'float' and not valid[selected].all()):
                return None
            values[positions] = picked
        samples, seen = [], set()
        for number, ref in enumerate(refs):
            if number % 4096 == 0:
                checkpoint()
            part = parts.get(int(ref['part_index']))
            if part is None:
                return None
            events = part['__test_records']
            if (not isinstance(events, PartEvents) or events.store is not refs.directory.stores[int(codes[number])]
                    or not events.start <= local[number] < events.stop):
                return None
            key = (ref['part_index'], ref['record_sequence'])
            if key in seen:
                return None
            seen.add(key)
            samples.append(_sample_entry(item, ref, part, float(values[number]), self.source_hash,
                identifiers=self.identifiers, minimal=self.minimal))
        if len(samples) != item.get('n'):
            return None
        return sorted(samples, key=lambda sample: (sample['record_sequence'], sample['part_index'], sample.get('id', '')))


def report_samples(frame: pd.DataFrame, item: dict, source_hash: str) -> list[dict]:
    return ReportSampleResolver(frame, source_hash)(item)


def _sample_entry(item, ref, part, value, source_hash, *, identifiers, minimal):
    """Public sample dict shared by the compact and generic resolution paths."""
    if minimal:
        return {'part_index': int(ref['part_index']), 'record_sequence': int(ref['record_sequence']),
                'site': as_float(ref.get('site')), 'value': value}
    identity = [source_hash, int(ref['part_index']), ref['record_sequence'],
                item['column'], item['record_type'], item.get('result_index')]
    sample_id = (hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:32]
                 if identifiers else f'{identity[1]}:{identity[2]}')
    return {'id': sample_id, 'part_index': int(ref['part_index']),
        'record_sequence': int(ref['record_sequence']), 'column': item['column'],
        'result_index': item.get('result_index'), 'record_type': item['record_type'],
        'head': as_float(part.get('HEAD_NUM')), 'site': as_float(ref.get('site')),
        'value': value, 'lot': _text(part.get('LOT_ID')), 'wafer': _text(part.get('WAFER_ID')),
        'x': as_float(part.get('DIE_X')), 'y': as_float(part.get('DIE_Y')),
        'part_id': _text(part.get('STDF_PART_ID'))}


def _report_samples(item, source_hash, parts, resolver=None):
    samples = []
    seen = set()
    for index, ref in enumerate(item['sample_refs']):
        if index % 2048 == 0:
            checkpoint()
        part = parts.get(int(ref['part_index']))
        if part is None:
            raise ValueError('报告样本记录已不存在，请重新计算')
        events = part['__test_records']
        matches = (resolver.find(int(ref['part_index']), events, ref, item) if resolver is not None else
                   events.find(ref['record_sequence'], item['column'], item['record_type'], item.get('result_index'))
                   if hasattr(events, 'find') else [e for e in events if e['record_sequence'] == ref['record_sequence']
                   and e['column'] == item['column']
                   and e.get('record_type') == item['record_type']
                   and e.get('result_index') == item.get('result_index')])
        if len(matches) != 1:
            raise ValueError('无法唯一定位报告测项子结果，请重新计算')
        event = matches[0]
        value = as_float(event.get('result'))
        if value is None:
            raise ValueError('报告包含非有限测量值，请重新计算')
        identity = [source_hash, int(ref['part_index']), ref['record_sequence'],
                    item['column'], item['record_type'], item.get('result_index')]
        identity_key = tuple(identity)
        if identity_key in seen:
            raise ValueError('报告中参与计算的测试记录重复，请重新计算')
        seen.add(identity_key)
        samples.append(_sample_entry(item, ref, part, value, source_hash,
            identifiers=resolver.identifiers if resolver is not None else True,
            minimal=bool(resolver is not None and resolver.minimal)))
    if len(samples) != item['n']:
        raise ValueError('图形样本数与报告不一致，请重新计算')
    return sorted(samples, key=lambda s: (s['record_sequence'], s['part_index'], s.get('id', '')))


def _text(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    return str(value).strip() or None


def sample_quality(samples: list[dict]) -> dict:
    heads: dict[float | None, set] = {}
    for point in samples:
        heads.setdefault(point['site'], set()).add(point['head'])
    mixed = [site for site, values in heads.items() if len(values) > 1]
    return {'mixed_head_sites': mixed, 'heads': dict(Counter(str(s['head']) for s in samples)),
            'warnings': ['相同 site 含多个 head，统计沿用报告的 SITE 汇总范围。'] if mixed else []}
