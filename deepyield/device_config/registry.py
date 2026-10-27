"""Registry loader, validator, and import-gate resolver for device config.

This module reads the selected ``device_import.yaml`` configuration, fully
validates it, computes a content fingerprint, and provides the import
gate that all STDF import paths must call before committing a parser
result.
"""

from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path
from typing import Any

import yaml
from deepyield.configuration import configuration_file

from deepyield.device_config.models import (
    DeviceConfig,
    DeviceConfigError,
    DeviceMatchResult,
    DeviceRegistry,
    FailViewConfig,
    WaferMapConfig,
    WaferMapDisplayConfig,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUPPORTED_SCHEMA_VERSIONS = {1}
_VALID_MODES = {"all_fail", "first_fail"}
_VALID_DATA_TYPES = {"EWS", "FT"}
_VALID_COORDINATE_SOURCES = {"pir_die", "test_item"}
_WAFER_MAP_TEST_ITEM_KEYS = ("x", "y", "wafer", "lot")
_VALID_WAFER_MAP_ORIGINS = {"corner", "center"}
_VALID_WAFER_MAP_CORNERS = {"top_left", "top_right", "bottom_left", "bottom_right"}
_WAFER_MAP_DISPLAY_KEYS = frozenset({"origin", "corner", "flip_x", "flip_y"})
_VALID_TESTER_KINDS = {"Advantest_93K", "ETS800"}

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_registry: DeviceRegistry | None = None
_registry_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def config_path() -> Path:
    """Return the path to the formal device import config YAML."""
    return configuration_file("device_import.yaml")


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def _normalize_part_type(part_type: str | None) -> str:
    """Trim and uppercase a part type value for exact matching."""
    return "".join(str(part_type or "").split()).upper()


def _normalize_data_type(data_type: str | None) -> str:
    return str(data_type or "").strip().upper()


# ---------------------------------------------------------------------------
# Fingerprint
# ---------------------------------------------------------------------------

def _canonical_config_text(raw: dict[str, Any]) -> str:
    """Return a deterministic JSON string of the raw config for fingerprinting.

    The ``wafer_map`` block is stripped before fingerprinting so that adding
    or changing wafer-map coordinate configuration does not invalidate the
    config fingerprint of existing assets.
    """
    import copy
    stripped = copy.deepcopy(raw)
    devices = stripped.get("devices")
    if isinstance(devices, list):
        for device in devices:
            if isinstance(device, dict):
                device.pop("wafer_map", None)
    return json.dumps(stripped, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_fingerprint(raw: dict[str, Any]) -> str:
    """Return the SHA-256 fingerprint of the canonical config content."""
    return hashlib.sha256(_canonical_config_text(raw).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_wafer_map(raw_wafer_map: Any, device_key: str) -> WaferMapConfig | None:
    """Validate the optional ``wafer_map`` block for one device.

    Returns ``None`` when the block is absent (backward-compatible default).
    """
    if raw_wafer_map is None:
        return None
    if not isinstance(raw_wafer_map, dict):
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map must be a mapping, "
            f"got {type(raw_wafer_map).__name__}."
        )

    coordinate_source = str(raw_wafer_map.get("coordinate_source") or "").strip()
    if coordinate_source not in _VALID_COORDINATE_SOURCES:
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.coordinate_source must be one of "
            f"{sorted(_VALID_COORDINATE_SOURCES)}, got {coordinate_source!r}."
        )

    raw_ids = raw_wafer_map.get("test_item_ids")
    test_item_ids: dict[str, int] | None = None
    if coordinate_source == "test_item":
        if not isinstance(raw_ids, dict) or not raw_ids:
            raise DeviceConfigError(
                f"Device {device_key!r}: wafer_map.test_item_ids is required "
                f"when coordinate_source is 'test_item'."
            )
        test_item_ids = {}
        for key in _WAFER_MAP_TEST_ITEM_KEYS:
            value = raw_ids.get(key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise DeviceConfigError(
                    f"Device {device_key!r}: wafer_map.test_item_ids.{key} "
                    f"must be a positive integer, got {value!r}."
                )
            test_item_ids[key] = value
        extra = set(raw_ids) - set(_WAFER_MAP_TEST_ITEM_KEYS)
        if extra:
            raise DeviceConfigError(
                f"Device {device_key!r}: wafer_map.test_item_ids has unknown "
                f"keys: {sorted(extra)}."
            )
    elif raw_ids is not None:
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.test_item_ids must be omitted "
            f"when coordinate_source is 'pir_die'."
        )

    display = _validate_wafer_map_display(raw_wafer_map.get("display"), device_key)

    return WaferMapConfig(
        coordinate_source=coordinate_source,
        test_item_ids=test_item_ids,
        display=display,
    )


def _validate_wafer_map_display(
    raw_display: Any,
    device_key: str,
) -> WaferMapDisplayConfig | None:
    """Validate optional ``wafer_map.display`` geometry."""
    if raw_display is None:
        return None
    if not isinstance(raw_display, dict):
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.display must be a mapping, "
            f"got {type(raw_display).__name__}."
        )

    unknown = set(raw_display) - _WAFER_MAP_DISPLAY_KEYS
    if unknown:
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.display has unknown keys: "
            f"{sorted(unknown)}."
        )

    origin = str(raw_display.get("origin") or "").strip()
    if origin not in _VALID_WAFER_MAP_ORIGINS:
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.display.origin must be one of "
            f"{sorted(_VALID_WAFER_MAP_ORIGINS)}, got {origin!r}."
        )

    raw_corner = raw_display.get("corner")
    corner: str | None
    if origin == "corner":
        if raw_corner is None or (isinstance(raw_corner, str) and not raw_corner.strip()):
            raise DeviceConfigError(
                f"Device {device_key!r}: wafer_map.display.corner is required "
                f"when origin is 'corner'."
            )
        corner = str(raw_corner).strip()
        if corner not in _VALID_WAFER_MAP_CORNERS:
            raise DeviceConfigError(
                f"Device {device_key!r}: wafer_map.display.corner must be one of "
                f"{sorted(_VALID_WAFER_MAP_CORNERS)}, got {corner!r}."
            )
    else:
        if raw_corner is not None:
            raise DeviceConfigError(
                f"Device {device_key!r}: wafer_map.display.corner must be omitted "
                f"when origin is 'center'."
            )
        corner = None

    flip_x = _validate_wafer_map_flip(raw_display.get("flip_x", False), device_key, "flip_x")
    flip_y = _validate_wafer_map_flip(raw_display.get("flip_y", False), device_key, "flip_y")

    return WaferMapDisplayConfig(
        origin=origin,
        corner=corner,
        flip_x=flip_x,
        flip_y=flip_y,
    )


def _validate_wafer_map_flip(value: Any, device_key: str, field_name: str) -> bool:
    if type(value) is not bool:
        raise DeviceConfigError(
            f"Device {device_key!r}: wafer_map.display.{field_name} must be a "
            f"boolean, got {value!r}."
        )
    return value


def _validate(raw: dict[str, Any]) -> DeviceRegistry:
    """Validate the raw YAML dict and build a :class:`DeviceRegistry`.

    Raises :class:`DeviceConfigError` (code ``DEVICE_CONFIG_INVALID``)
    for any structural or semantic problem.
    """
    if not isinstance(raw, dict):
        raise DeviceConfigError("Device config root must be a mapping.")

    schema_version = raw.get("schema_version")
    if schema_version not in _SUPPORTED_SCHEMA_VERSIONS:
        raise DeviceConfigError(
            f"Unsupported schema_version: {schema_version!r}. "
            f"Supported: {sorted(_SUPPORTED_SCHEMA_VERSIONS)}."
        )

    status = str(raw.get("status") or "").strip()
    if status != "active":
        raise DeviceConfigError(
            f"Device config status must be 'active', got {status!r}."
        )

    config_id = str(raw.get("config_id") or "").strip()
    if not config_id:
        raise DeviceConfigError("config_id is required and must be non-empty.")

    raw_devices = raw.get("devices")
    if not isinstance(raw_devices, list) or not raw_devices:
        raise DeviceConfigError("devices must be a non-empty list.")

    devices: list[DeviceConfig] = []
    seen_keys: set[str] = set()
    seen_part_types: dict[str, str] = {}  # part_type -> device_key (for cross-device check)

    for idx, raw_dev in enumerate(raw_devices):
        if not isinstance(raw_dev, dict):
            raise DeviceConfigError(f"devices[{idx}] must be a mapping.")

        device_key = str(raw_dev.get("device_key") or "").strip()
        if not device_key:
            raise DeviceConfigError(f"devices[{idx}].device_key is required.")
        if device_key in seen_keys:
            raise DeviceConfigError(f"Duplicate device_key: {device_key!r}.")
        seen_keys.add(device_key)

        display_name = str(raw_dev.get("display_name") or device_key).strip()

        enabled = bool(raw_dev.get("enabled", False))

        config_version = raw_dev.get("config_version")
        if not isinstance(config_version, int) or config_version < 1:
            raise DeviceConfigError(
                f"Device {device_key!r}: config_version must be a positive integer, "
                f"got {config_version!r}."
            )

        # part_type_values
        raw_pt = raw_dev.get("part_type_values")
        if not isinstance(raw_pt, list) or not raw_pt:
            raise DeviceConfigError(
                f"Device {device_key!r}: part_type_values must be a non-empty list."
            )
        part_types: list[str] = []
        local_pt_seen: set[str] = set()
        for pt in raw_pt:
            norm = _normalize_part_type(pt)
            if not norm:
                raise DeviceConfigError(
                    f"Device {device_key!r}: part_type_values contains an empty value."
                )
            if norm in local_pt_seen:
                raise DeviceConfigError(
                    f"Device {device_key!r}: duplicate part_type_value {norm!r}."
                )
            local_pt_seen.add(norm)
            if norm in seen_part_types:
                raise DeviceConfigError(
                    f"part_type_value {norm!r} appears in both "
                    f"{seen_part_types[norm]!r} and {device_key!r}."
                )
            seen_part_types[norm] = device_key
            part_types.append(norm)

        # allowed_data_types
        raw_dt = raw_dev.get("allowed_data_types")
        if not isinstance(raw_dt, list) or not raw_dt:
            raise DeviceConfigError(
                f"Device {device_key!r}: allowed_data_types must be a non-empty list."
            )
        allowed: list[str] = []
        for dt in raw_dt:
            norm_dt = _normalize_data_type(dt)
            if norm_dt not in _VALID_DATA_TYPES:
                raise DeviceConfigError(
                    f"Device {device_key!r}: allowed_data_types contains "
                    f"invalid value {dt!r}. Allowed: {sorted(_VALID_DATA_TYPES)}."
                )
            allowed.append(norm_dt)

        # fail_views
        raw_views = raw_dev.get("fail_views")
        if enabled and (not isinstance(raw_views, list) or not raw_views):
            raise DeviceConfigError(
                f"Device {device_key!r} (enabled) must have at least one fail_view."
            )
        if not isinstance(raw_views, list):
            raw_views = []

        views: list[FailViewConfig] = []
        seen_view_keys: set[str] = set()
        default_count = 0
        for vidx, raw_v in enumerate(raw_views):
            if not isinstance(raw_v, dict):
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_views[{vidx}] must be a mapping."
                )
            view_key = str(raw_v.get("view_key") or "").strip()
            if not view_key:
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_views[{vidx}].view_key is required."
                )
            if view_key in seen_view_keys:
                raise DeviceConfigError(
                    f"Device {device_key!r}: duplicate view_key {view_key!r}."
                )
            seen_view_keys.add(view_key)

            v_display = str(raw_v.get("display_name") or view_key).strip()
            mode = str(raw_v.get("mode") or "").strip()
            if mode not in _VALID_MODES:
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_view {view_key!r} has invalid "
                    f"mode {mode!r}. Must be one of {sorted(_VALID_MODES)}."
                )

            tmin = raw_v.get("test_num_min")
            tmax = raw_v.get("test_num_max")
            if tmin is not None and (not isinstance(tmin, int) or isinstance(tmin, bool)):
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_view {view_key!r} test_num_min "
                    f"must be an integer or null, got {tmin!r}."
                )
            if tmax is not None and (not isinstance(tmax, int) or isinstance(tmax, bool)):
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_view {view_key!r} test_num_max "
                    f"must be an integer or null, got {tmax!r}."
                )
            if tmin is not None and tmax is not None and tmin > tmax:
                raise DeviceConfigError(
                    f"Device {device_key!r}: fail_view {view_key!r} has "
                    f"test_num_min ({tmin}) > test_num_max ({tmax})."
                )

            is_default = bool(raw_v.get("is_default", False))
            if is_default:
                default_count += 1

            views.append(FailViewConfig(
                view_key=view_key,
                display_name=v_display,
                mode=mode,
                test_num_min=tmin,
                test_num_max=tmax,
                is_default=is_default,
            ))

        if enabled and default_count == 0:
            raise DeviceConfigError(
                f"Device {device_key!r} (enabled) must have exactly one default "
                f"fail_view (is_default: true), found none."
            )
        if default_count > 1:
            raise DeviceConfigError(
                f"Device {device_key!r} has {default_count} default fail_views; "
                f"exactly one is required."
            )

        # wafer_map (optional)
        wafer_map = _validate_wafer_map(raw_dev.get("wafer_map"), device_key)

        devices.append(DeviceConfig(
            device_key=device_key,
            display_name=display_name,
            enabled=enabled,
            config_version=config_version,
            part_type_values=part_types,
            allowed_data_types=allowed,
            fail_views=views,
            wafer_map=wafer_map,
        ))

    fingerprint = compute_fingerprint(raw)
    return DeviceRegistry(
        config_id=config_id,
        schema_version=int(schema_version),
        config_fingerprint=fingerprint,
        devices=devices,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_device_registry(*, force_reload: bool = False) -> DeviceRegistry:
    """Load and fully validate the device import config.

    The result is cached for the process; pass ``force_reload=True`` to
    re-read from disk (used by tests).
    """
    global _registry
    with _registry_lock:
        if _registry is not None and not force_reload:
            return _registry
        path = config_path()
        if not path.is_file():
            raise DeviceConfigError(
                f"Device config file not found: {path}",
            )
        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise DeviceConfigError(f"Invalid YAML in {path}: {exc}") from exc
        _registry = _validate(raw)
        return _registry


def get_device(device_key: str) -> DeviceConfig:
    """Return the device config for *device_key*.

    Raises :class:`DeviceConfigError` if the device does not exist.
    """
    reg = load_device_registry()
    dev = reg.get_device(device_key)
    if dev is None:
        raise DeviceConfigError(
            f"Unknown device_key: {device_key!r}.",
            error_code="DEVICE_UNKNOWN",
        )
    return dev


def list_enabled_devices() -> list[DeviceConfig]:
    """Return all enabled devices from the current config."""
    return load_device_registry().list_enabled_devices()


def _normalize_tester_kind(tester_kind: str | None) -> str:
    """Strip whitespace from a tester kind value."""
    return str(tester_kind or "").strip()


def _canonical_tester_kind(value: str) -> str | None:
    """Case-insensitive match against the canonical valid set.

    Returns the canonical form (e.g. ``"ETS800"``) or ``None``.
    """
    lowered = value.lower()
    for valid in _VALID_TESTER_KINDS:
        if lowered == valid.lower():
            return valid
    return None


def resolve_device(
    part_type: str | None,
    data_type: str | None,
    tester_kind: str | None,
) -> DeviceMatchResult:
    """Execute the import gate and return a match result.

    This is the single entry point all STDF import paths must call
    *before* committing a parser result as a formal asset.

    All three parameters are required — there are no defaults.  The
    caller must explicitly provide ``data_type`` and ``tester_kind``
    from user input; ``part_type`` comes from the parser's MIR record.

    Raises :class:`DeviceConfigError` with a specific ``error_code``
    when the gate rejects the import.
    """
    norm_dt = _normalize_data_type(data_type)
    if not norm_dt:
        raise DeviceConfigError(
            "data_type is required; the user must explicitly select EWS or FT.",
            error_code="DATA_TYPE_REQUIRED",
        )
    if norm_dt not in _VALID_DATA_TYPES:
        raise DeviceConfigError(
            f"Unsupported data_type: {data_type!r}. Must be EWS or FT.",
            error_code="DATA_TYPE_UNSUPPORTED",
        )

    norm_tk = _normalize_tester_kind(tester_kind)
    if not norm_tk:
        raise DeviceConfigError(
            "tester_kind is required; the user must explicitly select "
            "Advantest_93K or ETS800.",
            error_code="TESTER_KIND_REQUIRED",
        )
    canonical_tk = _canonical_tester_kind(norm_tk)
    if canonical_tk is None:
        raise DeviceConfigError(
            f"Unsupported tester_kind: {norm_tk!r}. "
            f"Must be one of {sorted(_VALID_TESTER_KINDS)}.",
            error_code="TESTER_KIND_UNSUPPORTED",
        )

    norm_pt = _normalize_part_type(part_type)
    if not norm_pt:
        raise DeviceConfigError(
            "STDF MIR.PART_TYP is missing; device cannot be determined.",
            error_code="PART_TYPE_REQUIRED",
        )

    reg = load_device_registry()
    device = reg.find_by_part_type(norm_pt)
    if device is None:
        raise DeviceConfigError(
            f"No device matches MIR.PART_TYP={norm_pt!r}.",
            error_code="DEVICE_UNKNOWN",
        )
    if not device.enabled:
        raise DeviceConfigError(
            f"Device {device.device_key!r} is disabled.",
            error_code="DEVICE_DISABLED",
        )
    if not device.allows_data_type(norm_dt):
        raise DeviceConfigError(
            f"Device {device.device_key!r} does not allow data_type {norm_dt!r}. "
            f"Allowed: {device.allowed_data_types}.",
            error_code="DATA_TYPE_NOT_ALLOWED",
        )

    return DeviceMatchResult(
        device_key=device.device_key,
        part_type=norm_pt,
        data_type=norm_dt,
        tester_kind=canonical_tk,
        config_id=reg.config_id,
        config_version=device.config_version,
        config_fingerprint=reg.config_fingerprint,
    )


def reset_cache() -> None:
    """Clear the cached registry (test helper)."""
    global _registry
    with _registry_lock:
        _registry = None
