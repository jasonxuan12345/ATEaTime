"""Data models for the device import configuration.

All models are immutable dataclasses.  The :class:`DeviceRegistry`
holds the validated configuration; callers must not mutate it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class DeviceConfigError(Exception):
    """Raised when device configuration is invalid or an import gate fails.

    ``error_code`` is a stable string that the import pipeline uses to
    distinguish rejection reasons.  Configuration-validation errors use
    ``DEVICE_CONFIG_INVALID``.
    """

    def __init__(self, message: str, *, error_code: str = "DEVICE_CONFIG_INVALID") -> None:
        super().__init__(message)
        self.error_code = error_code


# ---------------------------------------------------------------------------
# Fail view
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FailViewConfig:
    """One fail-view preset for a device.

    ``mode`` is either ``"all_fail"`` (every failing test item) or
    ``"first_fail"`` (only the first failing test item per unit).

    ``test_num_min`` / ``test_num_max`` are inclusive integer bounds.
    ``None`` means unbounded on that side.
    """

    view_key: str
    display_name: str
    mode: str
    test_num_min: int | None
    test_num_max: int | None
    is_default: bool = False

    def in_range(self, test_num: int) -> bool:
        """Return True when *test_num* falls within this view's range."""
        if self.test_num_min is not None and test_num < self.test_num_min:
            return False
        if self.test_num_max is not None and test_num > self.test_num_max:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        return {
            "view_key": self.view_key,
            "display_name": self.display_name,
            "mode": self.mode,
            "test_num_min": self.test_num_min,
            "test_num_max": self.test_num_max,
            "is_default": self.is_default,
        }


# ---------------------------------------------------------------------------
# Wafer map coordinate configuration
# ---------------------------------------------------------------------------

_WAFER_MAP_COORD_PIR_DIE = "pir_die"
_WAFER_MAP_COORD_TEST_ITEM = "test_item"
_VALID_COORDINATE_SOURCES = frozenset({_WAFER_MAP_COORD_PIR_DIE, _WAFER_MAP_COORD_TEST_ITEM})
_WAFER_MAP_TEST_ITEM_KEYS = ("x", "y", "wafer", "lot")

_WAFER_MAP_ORIGIN_CORNER = "corner"
_WAFER_MAP_ORIGIN_CENTER = "center"
_VALID_WAFER_MAP_ORIGINS = frozenset({_WAFER_MAP_ORIGIN_CORNER, _WAFER_MAP_ORIGIN_CENTER})
_VALID_WAFER_MAP_CORNERS = frozenset({
    "top_left",
    "top_right",
    "bottom_left",
    "bottom_right",
})


@dataclass(frozen=True)
class WaferMapDisplayConfig:
    """Per-device wafer-map display-frame geometry.

    Declares where product ``(0, 0)`` sits and optional axis flips relative to
    the default paint convention (data bbox min → screen top-left; X right;
    Y down). Notch orientation is intentionally not modeled.

    ``origin`` is ``"corner"`` or ``"center"``. When ``origin == "corner"``,
    ``corner`` names which die-array corner holds ``(0, 0)``. When
    ``origin == "center"``, ``corner`` must be ``None``.
    """

    origin: str
    corner: str | None = None
    flip_x: bool = False
    flip_y: bool = False

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "origin": self.origin,
            "flip_x": self.flip_x,
            "flip_y": self.flip_y,
        }
        if self.corner is not None:
            result["corner"] = self.corner
        return result


@dataclass(frozen=True)
class WaferMapConfig:
    """Per-device wafer-map coordinate resolution configuration.

    ``coordinate_source`` is either ``"pir_die"`` (read coordinates from the
    PRR/PIR ``DIE_X``/``DIE_Y`` fields — the EWS prober coordinate) or
    ``"test_item"`` (read coordinates from specific test items whose IDs are
    declared in ``test_item_ids``).

    When ``coordinate_source == "test_item"``, ``test_item_ids`` must contain
    four positive integers keyed by ``"x"``, ``"y"``, ``"wafer"`` and ``"lot"``.

    ``display`` is optional display-frame geometry (origin / corner / flips).
    When omitted, consumers keep the historical raw-bbox paint behaviour.
    """

    coordinate_source: str
    test_item_ids: dict[str, int] | None = None
    display: WaferMapDisplayConfig | None = None

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"coordinate_source": self.coordinate_source}
        if self.test_item_ids is not None:
            result["test_item_ids"] = dict(self.test_item_ids)
        if self.display is not None:
            result["display"] = self.display.to_dict()
        return result


# ---------------------------------------------------------------------------
# Device
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceConfig:
    """One device entry in the import configuration."""

    device_key: str
    display_name: str
    enabled: bool
    config_version: int
    part_type_values: list[str]
    allowed_data_types: list[str]
    fail_views: list[FailViewConfig]
    wafer_map: WaferMapConfig | None = None

    @property
    def default_view(self) -> FailViewConfig | None:
        """Return the default fail view, or None if none is marked default."""
        for v in self.fail_views:
            if v.is_default:
                return v
        return None

    def allows_data_type(self, data_type: str | None) -> bool:
        if not data_type:
            return False
        return data_type.strip().upper() in {
            dt.strip().upper() for dt in self.allowed_data_types
        }

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "device_key": self.device_key,
            "display_name": self.display_name,
            "enabled": self.enabled,
            "config_version": self.config_version,
            "part_type_values": list(self.part_type_values),
            "allowed_data_types": list(self.allowed_data_types),
            "fail_views": [v.to_dict() for v in self.fail_views],
        }
        if self.wafer_map is not None:
            result["wafer_map"] = self.wafer_map.to_dict()
        return result


# ---------------------------------------------------------------------------
# Match result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceMatchResult:
    """Result of resolving a device for an import.

    Returned by :func:`resolve_device` when the import gate passes.
    """

    device_key: str
    part_type: str
    data_type: str
    tester_kind: str
    config_id: str
    config_version: int
    config_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "device_key": self.device_key,
            "matched_part_type": self.part_type,
            "data_type": self.data_type,
            "tester_kind": self.tester_kind,
            "config_id": self.config_id,
            "config_version": self.config_version,
            "config_fingerprint": self.config_fingerprint,
        }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeviceRegistry:
    """Validated, read-only collection of device configurations.

    Constructed by :func:`load_device_registry`.  Callers should not
    instantiate this directly — use the loader so validation always runs.
    """

    config_id: str
    schema_version: int
    config_fingerprint: str
    devices: list[DeviceConfig] = field(default_factory=list)

    # internal lookup tables (rebuilt on construction)
    _by_key: dict[str, DeviceConfig] = field(default_factory=dict, repr=False)
    _by_part_type: dict[str, DeviceConfig] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_by_key", {d.device_key: d for d in self.devices})
        pt_map: dict[str, DeviceConfig] = {}
        for d in self.devices:
            for pt in d.part_type_values:
                pt_map[pt] = d
        object.__setattr__(self, "_by_part_type", pt_map)

    def get_device(self, device_key: str) -> DeviceConfig | None:
        return self._by_key.get(device_key)

    def find_by_part_type(self, part_type: str) -> DeviceConfig | None:
        return self._by_part_type.get(part_type)

    def list_enabled_devices(self) -> list[DeviceConfig]:
        return [d for d in self.devices if d.enabled]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_id": self.config_id,
            "schema_version": self.schema_version,
            "config_fingerprint": self.config_fingerprint,
            "devices": [d.to_dict() for d in self.devices],
        }
