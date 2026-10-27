"""Active operations only: no background queue, persistence or extra workers."""
from contextlib import contextmanager
from threading import RLock
from time import perf_counter

from deepyield.compute.release_control import Operation, operation

_active = {}
_cancelled = {}
_lock = RLock()


@contextmanager
def track(job_id=None, name='release'):
    control = Operation(job_id=job_id, name=name)
    if job_id is not None:
        with _lock:
            now = perf_counter()
            for key, expires in list(_cancelled.items()):
                if expires < now:
                    _cancelled.pop(key)
            if job_id in _active:
                raise ValueError('操作编号已在使用')
            if job_id in _cancelled:
                _cancelled.pop(job_id)
                control.cancel.set()
            _active[job_id] = control
    try:
        with operation(control):
            yield control
    finally:
        if job_id is not None:
            with _lock:
                _active.pop(job_id, None)


def status(job_id):
    with _lock:
        if job_id not in _active:
            raise KeyError('操作已结束')
        control = _active[job_id]
        return {'phase': control.phase, 'progress': control.progress, 'cancel_requested': control.cancel.is_set(),
                'elapsed_seconds': perf_counter() - control.started,
                'timings': dict(control.timings)}


def cancel(job_id):
    with _lock:
        if job_id in _active:
            _active[job_id].cancel.set()
        else:
            # Cancellation may arrive before multipart upload has finished and
            # before the synchronous route registers its operation.
            _cancelled[job_id] = perf_counter() + 1800
            while len(_cancelled) > 256:
                _cancelled.pop(next(iter(_cancelled)))
