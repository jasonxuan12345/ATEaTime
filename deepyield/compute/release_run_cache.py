"""Run-local reports and byte-bounded native indexes for active dataset sessions."""
from contextvars import ContextVar
from functools import wraps
import os
from threading import Lock
from deepyield.compute.release_control import checkpoint, count, event, measure
from deepyield.compute.release_memory_cache import ByteCache

_current = ContextVar('release_computation_cache', default=None)
_persistent = ContextVar('release_dataset_indexes', default=False)
_compute_gate = Lock()
_indexes = ByteCache(int(os.environ.get('DEEPYIELD_RELEASE_INDEX_CACHE_MB', '512')) * 1024**2)


def dataset_computation(function):
    @wraps(function)
    def call(*args, **kwargs):
        if _persistent.get():
            return function(*args, **kwargs)
        checkpoint()
        with measure('compute.wait'):
            while not _compute_gate.acquire(timeout=.1):
                checkpoint('等待其他计算完成')
        token = _persistent.set(True)
        try:
            checkpoint()
            return function(*args, **kwargs)
        finally:
            _persistent.reset(token)
            _compute_gate.release()
    return call


def invalidate_indexes(source_hash):
    if source_hash:
        _indexes.discard(lambda key: source_hash == dict(key[1]).get('source_sha256'))


def index_bytes(groups):
    seen, total = set(), 0
    for group in groups.values():
        events = group['events']
        if hasattr(events, 'event_ids'):
            data = events.event_ids
            while getattr(data, 'base', None) is not None:
                data = data.base
            if id(data) not in seen:
                seen.add(id(data))
                total += data.nbytes
    return total


def computation(function):
    @wraps(function)
    def call(*args, **kwargs):
        if _current.get() is not None:
            return function(*args, **kwargs)
        token = _current.set({})
        try:
            return function(*args, **kwargs)
        finally:
            _current.reset(token)
    return call


def _freeze(value):
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(v) for v in value)
    if isinstance(value, dict):
        return tuple(sorted((k, _freeze(v)) for k, v in value.items()))
    return value


def cached_report(function):
    @computation
    @wraps(function)
    def call(frame, metadata, **kwargs):
        # Supporting reports are run-local immutable objects and must not be
        # confused across different selections, rule catalogs or source frames.
        with measure('cache.report_key'):
            options = tuple(sorted((k, id(v) if k in {'catalog', 'bin1_cpk_report', 'cgm_report'}
                                    else _freeze(v)) for k, v in kwargs.items()))
            key = (function.__name__, id(frame), id(metadata), options)
        cache = _current.get()
        with measure('cache.report_lookup'):
            hit = key in cache
        count('cache.report_hit' if hit else 'cache.report_miss')
        event('report_cache', report=function.__name__, hit=hit,
              selection_only=bool(kwargs.get('_selection_only')))
        if not hit:
            cache[key] = function(frame, metadata, **kwargs)
        with measure('cache.report_lookup'):
            return cache[key]
    return call


def grouped(selected, source, chosen, build):
    cache = _current.get()
    if cache is None:
        with measure('groups.build'):
            return build()
    with measure('cache.group_key'):
        columns = ['__part_index', 'SITE', 'SBIN', 'PART_FLAG', '__conditions_json', '__test_records']
        rows = tuple((*row[:-1], id(row[-1])) for row in selected[columns].itertuples(index=False, name=None))
        key = ('groups', tuple(sorted(source.items())), rows,
               None if chosen is None else tuple(sorted(chosen)))
    with measure('cache.group_lookup'):
        hit = key in cache
        if not hit and _persistent.get():
            shared = _indexes.get(key)
            if shared is not None:
                cache[key] = shared
                hit = True
                count('cache.dataset_index_hit')
    count('cache.group_hit' if hit else 'cache.group_miss')
    if not hit:
        with measure('groups.build'):
            groups = build()
        with measure('cache.group_store'):
            cache[key] = groups
            if _persistent.get() and groups and all(hasattr(g['events'], 'event_ids') for g in groups.values()):
                _indexes.put(key, groups, index_bytes(groups) + len(rows) * 256 + len(groups) * 2048)
                count('cache.dataset_index_bytes', _indexes.bytes)
    with measure('cache.group_lookup'):
        groups = cache[key]
    event('group_cache', hit=hit, records=len(selected), groups=len(groups))
    return groups
