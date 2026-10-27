"""Device import configuration — formal registry, models, and import gate.

This package is the single source of truth for which devices may be
imported, what ``data_type`` values each device allows, and which fail
views are defined.  Backend import paths, existing-asset checks, and
future Layer3 code all read configuration through this module.

Public API::

    from deepyield.device_config import (
        DeviceConfig,
        DeviceConfigError,
        DeviceMatchResult,
        DeviceRegistry,
        FailViewConfig,
        get_device,
        list_enabled_devices,
        load_device_registry,
        resolve_device,
    )
"""

from __future__ import annotations

from deepyield.device_config.models import (
    DeviceConfig,
    DeviceConfigError,
    DeviceMatchResult,
    DeviceRegistry,
    FailViewConfig,
)
from deepyield.device_config.registry import (
    get_device,
    list_enabled_devices,
    load_device_registry,
    resolve_device,
)

__all__ = [
    "DeviceConfig",
    "DeviceConfigError",
    "DeviceMatchResult",
    "DeviceRegistry",
    "FailViewConfig",
    "get_device",
    "list_enabled_devices",
    "load_device_registry",
    "resolve_device",
]
