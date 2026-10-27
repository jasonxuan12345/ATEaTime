"""Load `config/analysis_rule/` YAML into a versioned catalog.

This is the only analysis-rule reader. Identity chip rules stay in
`deepyield.identity.rule_loader` and must not be mixed with these files.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RULE_DIR = _REPO_ROOT / "config" / "analysis_rule"
DEFAULT_BASELINE_PATH = _REPO_ROOT / "config" / "device_yield_monitor.yaml"
_MARGIN_FACTOR = re.compile(r"\*\s*([0-9]*\.?[0-9]+)")
_BIN_MIN_COUNT = re.compile(
    r"max\(\s*([0-9]*\.?[0-9]+)\s*,\s*total_tested\s*\*\s*([0-9]*\.?[0-9]+)\s*\)",
    re.IGNORECASE,
)

KNOWN_RULE_IDS: tuple[str, ...] = (
    "YIELD-001",
    "YIELD-002",
    "YIELD-ITEM-001",
    "CPK-001",
    "CPK-002",
    "CPK-003",
    "CGM-001",
    "CGM-002",
    "CGM-003",
    "CGM-004",
    "MMSA-001",
    "MMSA-002",
    "MMSA-003",
    "MMSA-004",
    "MMSA-005",
    "MMSA-006",
    "MMSA-007",
    "MMSA-008",
    "SPEC-001",
    "SITE-001",
    "SITE-002",
    "SITE-003",
    "WAFER-001",
    "WAFER-002",
    "BIN-001",
)


class RuleLoadError(ValueError):
    """YAML missing, malformed, or duplicate rule_id."""


@dataclass(frozen=True)
class LoadedRule:
    rule_id: str
    rule_set_id: str
    version: str
    finding_type: str
    severity: str
    status: str
    emit_finding: bool
    group: str
    spec: Mapping[str, Any]


@dataclass(frozen=True)
class RuleCatalog:
    rules: tuple[LoadedRule, ...]
    documents: Mapping[str, Any]
    baselines: Mapping[str, Any]
    margin_ratio: float | None
    bin_min_count: float | None
    bin_min_share: float | None
    source_dir: str
    catalog_hash: str = ""

    def rule(self, rule_id: str) -> LoadedRule:
        for item in self.rules:
            if item.rule_id == rule_id:
                return item
        raise KeyError(rule_id)

    def by_id(self) -> dict[str, LoadedRule]:
        return {item.rule_id: item for item in self.rules}

    def document(self, rule_set_id: str) -> Mapping[str, Any]:
        return dict(self.documents.get(rule_set_id) or {})


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise RuleLoadError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise RuleLoadError(f"{path} must be a mapping")
    return raw


def parse_bin_minimum_count(document: Mapping[str, Any]) -> tuple[float | None, float | None]:
    formula = str(
        (((document.get("bin_abnormal") or {}).get("calculation") or {}).get("minimum_count") or {}).get("formula")
        or ""
    )
    match = _BIN_MIN_COUNT.search(formula)
    if not match:
        return None, None
    return float(match.group(1)), float(match.group(2))


def parse_margin_ratio(document: Mapping[str, Any]) -> float | None:
    calc = ((document.get("spec_margin") or {}).get("calculation") or {})
    formula = str((calc.get("margin_band") or {}).get("formula") or "")
    match = _MARGIN_FACTOR.search(formula)
    if not match:
        return None
    return float(match.group(1))


def _status_from_entry(entry: Mapping[str, Any]) -> str:
    status = str(entry.get("status") or "").upper()
    if status:
        return status
    severity = str(entry.get("severity") or "").upper()
    if severity == "FAIL":
        return "FAIL"
    if severity == "WARNING":
        return "RISK"
    if str(entry.get("emit_finding")).lower() == "false":
        return "PASS"
    return "RISK"


def _collect_entries(document: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    found: list[tuple[str, Mapping[str, Any]]] = []

    def walk(node: Any, group: str) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item, group)
            return
        if not isinstance(node, dict):
            return
        if "rule_id" in node:
            found.append((group, node))
            return
        if "rule" in node and isinstance(node["rule"], dict) and "rule_id" in node["rule"]:
            found.append((group, {**node["rule"], "_group_body": node}))
            return
        if "rules" in node and isinstance(node["rules"], list):
            for item in node["rules"]:
                if isinstance(item, dict):
                    found.append((group, {**item, "_group_body": node}))
            return
        if "bands" in node and isinstance(node["bands"], list):
            for item in node["bands"]:
                if isinstance(item, dict):
                    found.append((group, {**item, "_group_body": node}))
            return
        skip = {"evidence", "metric", "supporting_metric"}
        for key, value in node.items():
            if key in skip:
                continue
            walk(value, key if isinstance(value, dict) else group)

    walk(document, "root")
    return found


def _loaded_from_document(document: Mapping[str, Any], path: Path) -> list[LoadedRule]:
    header = document.get("rule_set") or {}
    if not isinstance(header, dict):
        raise RuleLoadError(f"{path} rule_set must be a mapping")
    rule_set_id = str(header.get("rule_set_id") or "")
    version = str(header.get("version") or "")
    if not rule_set_id:
        raise RuleLoadError(f"{path} missing rule_set.rule_set_id")
    loaded: list[LoadedRule] = []
    for group, entry in _collect_entries(document):
        rule_id = str(entry.get("rule_id") or "")
        if not rule_id:
            continue
        loaded.append(
            LoadedRule(
                rule_id=rule_id,
                rule_set_id=rule_set_id,
                version=version,
                finding_type=str(entry.get("finding_type") or ""),
                severity=str(entry.get("severity") or ""),
                status=_status_from_entry(entry),
                emit_finding=bool(entry.get("emit_finding", True)),
                group=group,
                spec=entry,
            )
        )
    return loaded


def load_analysis_rules(
    rule_dir: str | Path | None = None,
    baseline_path: str | Path | None = None,
) -> RuleCatalog:
    directory = Path(rule_dir) if rule_dir is not None else DEFAULT_RULE_DIR
    if not directory.is_dir():
        raise RuleLoadError(f"analysis rule directory not found: {directory}")
    documents: dict[str, Any] = {}
    rules: list[LoadedRule] = []
    seen: set[str] = set()
    for path in sorted(directory.glob("*.yaml")):
        document = _read_yaml(path)
        documents[str((document.get("rule_set") or {}).get("rule_set_id") or path.stem)] = document
        for item in _loaded_from_document(document, path):
            if item.rule_id in seen:
                raise RuleLoadError(f"duplicate rule_id {item.rule_id}")
            seen.add(item.rule_id)
            rules.append(item)
    missing = [rule_id for rule_id in KNOWN_RULE_IDS if rule_id not in seen]
    if missing:
        raise RuleLoadError(f"missing required rule_id(s): {missing}")
    baseline_file = Path(baseline_path) if baseline_path is not None else DEFAULT_BASELINE_PATH
    baselines = _read_yaml(baseline_file) if baseline_file.is_file() else {}
    spec_doc = next((doc for doc in documents.values() if (doc.get("rule_set") or {}).get("rule_set_id") == "SPEC-MARGIN-V1"), {})
    bin_doc = next((doc for doc in documents.values() if (doc.get("rule_set") or {}).get("rule_set_id") == "BIN-V1"), {})
    bin_floor, bin_share = parse_bin_minimum_count(bin_doc)
    catalog_hash = hashlib.sha256(
        json.dumps({"documents": documents, "baselines": baselines}, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()
    return RuleCatalog(
        rules=tuple(rules),
        documents=documents,
        baselines=baselines,
        margin_ratio=parse_margin_ratio(spec_doc),
        bin_min_count=bin_floor,
        bin_min_share=bin_share,
        source_dir=str(directory),
        catalog_hash=catalog_hash,
    )
