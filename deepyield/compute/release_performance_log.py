"""Bounded, content-free Release diagnostics written to the existing stderr log."""
import ctypes
from datetime import datetime, timezone
import json
import logging
import os

LOGGER = logging.getLogger('deepyield.release.performance')
if not LOGGER.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter('release_perf %(message)s'))
    LOGGER.addHandler(handler)
LOGGER.setLevel(logging.INFO)
LOGGER.propagate = False


def enabled():
    return os.environ.get('DEEPYIELD_RELEASE_TIMING', '1') != '0'


def resources():
    if os.name != 'nt':
        return {}
    try:
        class Memory(ctypes.Structure):
            _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        memory = Memory()
        memory.cb = ctypes.sizeof(memory)
        if ctypes.windll.psapi.GetProcessMemoryInfo(
                ctypes.c_void_p(-1), ctypes.byref(memory), memory.cb):
            return {'rss_mb': round(memory.WorkingSetSize / 1024 ** 2, 2),
                    'peak_rss_mb': round(memory.PeakWorkingSetSize / 1024 ** 2, 2)}
    except (OSError, AttributeError):
        pass
    return {}


def emit(event, **fields):
    if enabled():
        LOGGER.info(json.dumps({'event': event, 'schema': 1,
            'utc': datetime.now(timezone.utc).isoformat(timespec='milliseconds'),
            'pid': os.getpid(), **fields}, ensure_ascii=True, allow_nan=False))
