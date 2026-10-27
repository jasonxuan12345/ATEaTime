"""Optional cancellation and stage timings for Release computations."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from threading import Event, Thread
from time import perf_counter, process_time
from uuid import uuid4

from deepyield.compute.release_performance_log import emit, enabled, resources


class ReleaseCancelled(ValueError):
    pass


@dataclass
class Operation:
    cancel: Event = field(default_factory=Event)
    phase: str = '准备处理'
    progress: float | None = None
    timings: dict = field(default_factory=dict)
    started: float = field(default_factory=perf_counter)
    request_id: str = field(default_factory=lambda: uuid4().hex)
    job_id: str | None = None
    name: str = 'release'
    measurements: dict = field(default_factory=dict)
    counts: dict = field(default_factory=dict)
    active_spans: list = field(default_factory=list)
    last_progress: float = field(default_factory=perf_counter)
    cpu_started: float = field(default_factory=process_time)
    memory_start_mb: float | None = None
    sampled_peak_rss_mb: float | None = None

    def header(self):
        timings = {**self.timings, 'total': perf_counter() - self.started}
        return ', '.join(f'{name};dur={seconds * 1000:.3f}' for name, seconds in timings.items())


_operation = ContextVar('release_operation', default=None)
request_trace = ContextVar('release_request_trace', default=None)


def current_operation():
    return _operation.get()


@contextmanager
def operation(control):
    token = _operation.set(control)
    outcome = 'ok'
    trace = request_trace.get()
    if trace is not None:
        control.request_id = trace['id']
        trace['service_started'] = perf_counter()
    initial = resources()
    control.memory_start_mb = initial.get('rss_mb')
    control.sampled_peak_rss_mb = control.memory_start_mb
    stop_sampling = Event()

    def sample_memory():
        while not stop_sampling.wait(.2):
            value = resources().get('rss_mb')
            if value is not None:
                control.sampled_peak_rss_mb = max(control.sampled_peak_rss_mb or 0, value)

    sampler = Thread(target=sample_memory, daemon=True, name='release-memory-sampler') if enabled() else None
    if sampler is not None:
        sampler.start()
    event('operation_start', name=control.name, job_id=control.job_id, **initial)
    try:
        checkpoint()
        yield control
    except BaseException as exc:
        outcome = 'cancelled' if isinstance(exc, ReleaseCancelled) else 'error'
        event('operation_error', error_type=type(exc).__name__)
        raise
    finally:
        stop_sampling.set()
        if sampler is not None:
            sampler.join(timeout=.5)
        if trace is not None:
            trace['service_finished'] = perf_counter()
        final_resources = resources()
        if final_resources.get('rss_mb') is not None:
            control.sampled_peak_rss_mb = max(control.sampled_peak_rss_mb or 0, final_resources['rss_mb'])
        event('operation_end', name=control.name, outcome=outcome,
              memory_start_mb=control.memory_start_mb, sampled_peak_rss_mb=control.sampled_peak_rss_mb,
              elapsed_ms=round((perf_counter() - control.started) * 1000, 3),
              process_cpu_ms=round((process_time() - control.cpu_started) * 1000, 3),
              stages_ms={key: round(value * 1000, 3) for key, value in control.timings.items()},
              measurements=_measurements(control), counts=control.counts, **final_resources)
        _operation.reset(token)


def report_progress(phase, percent):
    """Publish measured export work, not a time-based estimated percentage."""
    checkpoint(phase)
    control = _operation.get()
    if control is not None:
        control.progress = max(0., min(100., float(percent)))


def checkpoint(phase=None):
    control = _operation.get()
    if control is not None:
        if control.cancel.is_set():
            raise ReleaseCancelled('操作已取消')
        if phase is not None:
            control.phase = phase
        now = perf_counter()
        if enabled() and now - control.last_progress >= 5:
            control.last_progress = now
            event('progress', elapsed_ms=round((now - control.started) * 1000, 3),
                  process_cpu_ms=round((process_time() - control.cpu_started) * 1000, 3),
                  active=[{'name': name, 'elapsed_ms': round((now - start) * 1000, 3)}
                          for name, start in control.active_spans],
                  measurements=_measurements(control), counts=control.counts, **resources())


@contextmanager
def stage(name, phase):
    checkpoint(phase)
    start = perf_counter()
    event('stage_start', name=name, **resources())
    outcome = 'ok'
    try:
        yield
        checkpoint()
    except BaseException as exc:
        outcome = 'cancelled' if isinstance(exc, ReleaseCancelled) else 'error'
        raise
    finally:
        control = _operation.get()
        if control is not None:
            seconds = perf_counter() - start
            control.timings[name] = control.timings.get(name, 0) + seconds
            event('stage_end', name=name, outcome=outcome,
                  elapsed_ms=round(seconds * 1000, 3), **resources())


def event(event_name, **fields):
    control = _operation.get()
    if control is not None:
        emit(event_name, request_id=control.request_id, phase=control.phase, **fields)


def _measurements(control):
    return {name: {'calls': metric['calls'], 'total_ms': round(metric['total_ms'], 3),
                   'max_ms': round(metric['max_ms'], 3)}
            for name, metric in control.measurements.items()}


def count(name, amount=1):
    control = _operation.get()
    if control is not None and enabled():
        control.counts[name] = control.counts.get(name, 0) + int(amount)


def peak_count(name, amount):
    control = _operation.get()
    if control is not None and enabled():
        control.counts[name] = max(control.counts.get(name, 0), int(amount))


def lock_wait(name, started):
    """Make blocking lock acquisition visible: the wait is contention, the hold is work."""
    waited = (perf_counter() - started) * 1000
    count('lock_wait.' + name)
    if waited >= 1.0:
        event('lock_wait', name=name, waited_ms=round(waited, 3))
    return waited


@contextmanager
def measure(name):
    """Aggregate inner-loop spans; no per-group or per-event log writes."""
    control = _operation.get()
    if control is None or not enabled():
        yield
        return
    start = perf_counter()
    active = (name, start)
    control.active_spans.append(active)
    try:
        yield
    finally:
        elapsed = (perf_counter() - start) * 1000
        control.active_spans.remove(active)
        metric = control.measurements.setdefault(name, {'calls': 0, 'total_ms': 0., 'max_ms': 0.})
        metric['calls'] += 1
        metric['total_ms'] += elapsed
        metric['max_ms'] = max(metric['max_ms'], elapsed)


def timed(name):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with measure(name):
                return function(*args, **kwargs)
        return call
    return decorate
