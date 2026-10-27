"""Transient single-file Release sessions; calculations stay in compute/YAML."""
from __future__ import annotations

from dataclasses import dataclass, field
from collections import OrderedDict
import json
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from threading import RLock
from typing import BinaryIO
from uuid import uuid4

import numpy as np
import pandas as pd

from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_cgm import build_release_cgm_report, cross_source_identity
from deepyield.compute.release_mmsa import build_release_mmsa_report
from deepyield.compute.release_stdf import import_release_stdf, convert_stdf_to_wide_all
from deepyield.compute.release_run_cache import computation, dataset_computation, invalidate_indexes
from deepyield.compute.release_references import compact_sample_refs, reference_json
from deepyield.compute.release_sample_batch import invalidate_store_arrays
from deepyield.compute.release_chart_samples import ReportSampleResolver
from deepyield.compute.release_control import checkpoint, stage, measure, count, event, timed
from deepyield.compute.release_event_store import retained_data_bytes
from deepyield.rules.conditions import as_float
from deepyield.rules.loader import load_analysis_rules
from app.release_remark_service import prepare_remarks
from app.release_export_cache import ReportFile, ReportFiles
from deepyield.i18n import language

REASONS = {
    "MISSING_UNIT": "单位缺失，需要核对", "NO_EFFECTIVE_LIMIT": "没有有效上下限",
    "NO_EFFECTIVE_TWO_SIDED_LIMIT": "没有有效双侧上下限", "INVALID_LIMIT_ORDER": "上下限顺序有误",
    "ZERO_OR_UNAVAILABLE_STD": "标准差为零或无法取得", "TOO_FEW_VALID_SAMPLES": "参与计算的记录数不足",
    "TOO_FEW_USABLE_SITES": "有效 site 不足，至少需要 2 个（每个 site 至少 2 条有效记录）",
    "INCOMPLETE_SITE_COVERAGE": "部分 site 样本不足或无法计算",
    "NO_SELECTED_EXECUTION": "所选数据中没有执行记录", "FUNCTIONAL_TEST": "功能测试不适用此指标",
    "CPK_MARGIN_ACCEPTANCE": "匹配的 bin1 CPK 满足接受条件", "MISSING_CG": "没有可计算的 Cg",
    "RULE_BANDS_AMBIGUOUS_OR_MISSING": "规则范围缺失或重叠，需要核对",
    "GATE_NOT_TRIGGERED": "CPK 与 Cg 均达标，无需 Site Diff 诊断",
    "LOCAL_SITE_HARDWARE_FAULT": "少数 Site 的 Cg 未达标，建议检查接触和局部测试硬件",
    "TEST_RESOLUTION_OR_METHOD": "多数 Site 的 Cg 未达标，建议检查测试分辨率和测试方法",
    "SYSTEMATIC_SITE_SHIFT": "Site 均值差达到所设异常阈值", "SITE_VARIANCE_ANOMALY": "个别 Site 的标准差明显高于其他 Site",
    "DISTRIBUTION_OR_LIMIT_REVIEW": "当前指标未检出明显 Site 差异，需查看分布图，检查是否偏宽、多峰或接近规格限值",
    "CLOSE_TO_LIMIT_MARGIN": "当前指标未检出明显 Site 差异，均值接近规格限值且 CPK 偏低，建议检查规格限值设置和 trim 目标",
    "DISTRIBUTION_WIDE_OR_BIMODAL": "当前指标未检出明显 Site 差异，数据分布偏宽且 CPK 偏低，建议查看分布图并检查 trim 设置",
    "S2S_EVIDENCE_INCONCLUSIVE": "Site Diff 指标处于警告区间，需人工复核",
    "NO_MATCHED_BIN1_CPK": "无匹配且可评估的 bin1 CPK，无法触发诊断",
}
LABELS = {"PASS": "通过", "FAIL": "未达标", "RISK": "风险", "REVIEW_REQUIRED": "需复核",
          "NOT_CALCULABLE": "无法计算", "INSUFFICIENT_DATA": "数据不足",
          "NOT_EVALUATED": "尚未评估", "NOT_APPLICABLE": "不适用"}
_RANK = {"FAIL": 0, "RISK": 1, "REVIEW_REQUIRED": 2, "PASS": 3, "NOT_APPLICABLE": 4}


@dataclass
class ReleaseDataset:
    temporary: tempfile.TemporaryDirectory
    parquet: Path
    filename: str
    frame: pd.DataFrame
    metadata: dict
    runs: dict = field(default_factory=dict)
    run_sources: dict = field(default_factory=dict, repr=False)
    outlier_runs: OrderedDict = field(default_factory=OrderedDict)
    outlier_report_exports: dict = field(default_factory=dict, repr=False)
    chart_samples: OrderedDict = field(default_factory=OrderedDict)
    chart_lock: RLock = field(default_factory=RLock, repr=False)
    excel_cache: OrderedDict = field(default_factory=OrderedDict, repr=False)
    report_files: ReportFiles = field(default_factory=ReportFiles, repr=False)
    last_access: float = field(default_factory=time.monotonic)
    memory_bytes: int = 0
    source: Path | None = None
    parquet_lock: RLock = field(default_factory=RLock, repr=False)


_datasets: dict[str, ReleaseDataset] = {}
_lock = RLock()
SESSION_IDLE_SECONDS = 30 * 60
RETAINED_DATA_BUDGET = int(os.environ.get('DEEPYIELD_RELEASE_MEMORY_MB', '1536')) * 1024 ** 2
RETAINED_INDEX_BUDGET = int(os.environ.get('DEEPYIELD_RELEASE_RETAINED_INDEX_MB', '768')) * 1024**2
# Each import is an independent C++ subprocess plus its own Arrow buffers, so a
# few concurrent files only multiply the transient working set, not the logic.
MAX_CONCURRENT_IMPORTS = int(os.environ.get('DEEPYIELD_RELEASE_PARALLEL_IMPORTS', '4'))
_pending_imports = 0


def _expire_sessions() -> None:
    now = time.monotonic()
    for key, dataset in list(_datasets.items()):
        if now - dataset.last_access > SESSION_IDLE_SECONDS:
            remove_dataset(key)


def get_dataset(dataset_id: str) -> ReleaseDataset:
    with _lock:
        _expire_sessions()
        if dataset_id not in _datasets:
            raise KeyError("文件已不在当前会话，请重新导入")
        _datasets[dataset_id].last_access = time.monotonic()
        return _datasets[dataset_id]


def remove_dataset(dataset_id: str) -> None:
    # Active readers retain the TemporaryDirectory until their response finishes.
    with _lock:
        removed = _datasets.pop(dataset_id, None)
        if removed is not None:
            invalidate_indexes(removed.metadata.get('source_sha256', ''))
            invalidate_store_arrays([e.store for e in removed.frame.get('__test_records', []) if hasattr(e, 'store')])
            from app.release_chart_service import invalidate_chart_cache
            invalidate_chart_cache(dataset_id)
        for owner in _datasets.values():
            for run_id, (result, _) in list(owner.runs.items()):
                if any(dataset_id in source.get('dataset_ids', [source.get('dataset_id')])
                       for source in result.get('sources', {}).values()):
                    _discard_run(owner, run_id)
            for run_id, saved in list(owner.outlier_report_exports.items()):
                if any(source.get('dataset_id') == dataset_id for source in saved.get('sources', [])):
                    owner.outlier_report_exports.pop(run_id, None)


def _source_pool(ids: list[str]) -> ReleaseDataset:
    """Share immutable event stores; only reindex PRRs across files.

    Offsets retain an exact mapping back to every file's original part index.
    Pools belong to a run, never occupy an upload slot or create another Parquet.
    """
    sources = [get_dataset(key) for key in ids]
    frames, members, tests, warnings = [], [], {}, []
    offset = 0
    for key, source in zip(ids, sources):
        checkpoint()
        # Calculations and evidence resolve values through immutable events.
        # Avoid duplicating all wide measurement columns for each module pool.
        wide = {test['column'] for test in source.metadata['test_catalog']}
        frame = source.frame.loc[:, [column for column in source.frame if column not in wide]].copy(deep=False)
        frame['__part_index'] = source.frame['__part_index'] + offset
        frames.append(frame)
        members.append(dict(dataset_id=key, filename=source.filename,
            source_sha256=source.metadata.get('source_sha256'), part_index_offset=offset))
        offset += int(source.frame['__part_index'].max()) + 1 if len(frame) else 0
        for test in source.metadata['test_catalog']:
            tests.setdefault(test['column'], test)
        warnings.extend(source.metadata.get('stdf_metadata', {}).get('warnings', []))
    digest = hashlib.sha256(json.dumps(members, sort_keys=True).encode()).hexdigest()
    filename = ' + '.join(source.filename for source in sources)
    frame = pd.concat(frames, ignore_index=True, copy=False)
    metadata = {**sources[0].metadata, 'source_file': filename, 'source_sha256': digest,
        'source_files': members, 'row_count': len(frame), 'test_catalog': list(tests.values()),
        'stdf_metadata': {**sources[0].metadata.get('stdf_metadata', {}),
                          'warnings': list(dict.fromkeys(warnings))}}
    return ReleaseDataset(sources[0].temporary, sources[0].parquet, filename, frame, metadata)


def run_source(owner: ReleaseDataset, result: dict, module: str) -> ReleaseDataset:
    info = result['sources'][module]
    # Keep the same expiration/removal behavior for original files and pools.
    for key in info.get('dataset_ids', [info['dataset_id']]):
        get_dataset(key)
    return owner.run_sources.get(result['id'], {}).get(module) or get_dataset(info['dataset_id'])


def import_file(filename: str, stream: BinaryIO) -> dict:
    global _pending_imports
    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
    if not re.fullmatch(r"[^/]+\.(stdf|std)(\.(gz|bz2))?", filename, re.I):
        raise ValueError("请选择 STDF、STDF.gz 或 STDF.bz2 文件")
    with _lock:
        _expire_sessions()
        if len(_datasets) >= 8:
            raise ValueError("当前已有 8 份文件，请先清空不用的会话")
        if _pending_imports >= MAX_CONCURRENT_IMPORTS:
            raise ValueError(f"已有 {MAX_CONCURRENT_IMPORTS} 个文件在并行导入，请等待其中一个完成后再导入")
        if sum(d.memory_bytes for d in _datasets.values()) >= RETAINED_DATA_BUDGET:
            raise ValueError("当前文件数据已达到内存预算，请先移除不用的文件")
        _pending_imports += 1
    temporary = None
    try:
        temporary = tempfile.TemporaryDirectory(prefix="deepyield-release-")
        checkpoint('读取上传文件')
        source = Path(temporary.name) / filename
        size = 0
        with stage('upload', '读取上传文件'), source.open("wb") as output:
            while chunk := stream.read(1024 * 1024):
                checkpoint()
                size += len(chunk)
                if size > 1024 ** 3:
                    raise ValueError("当前单文件导入上限为 1 GB")
                output.write(chunk)
        parquet = Path(temporary.name) / "wide_all.parquet"
        frame, metadata = import_release_stdf(source)
        dataset = ReleaseDataset(temporary, parquet, filename, frame, metadata, source=source)
        dataset.memory_bytes = retained_data_bytes(frame)
        dataset_id = uuid4().hex
        with stage('summary', '准备文件摘要'):
            summary = describe(dataset_id, dataset)
        with _lock:
            _expire_sessions()
            checkpoint()
            if sum(d.memory_bytes for d in _datasets.values()) + dataset.memory_bytes > RETAINED_DATA_BUDGET:
                raise ValueError("新文件超出当前文件数据内存预算，请先移除不用的文件或提高 DEEPYIELD_RELEASE_MEMORY_MB")
            _datasets[dataset_id] = dataset
        return summary
    except Exception:
        if temporary is not None:
            temporary.cleanup()
        raise
    finally:
        with _lock:
            _pending_imports -= 1


def prepare_parquet(dataset_id: str) -> ReleaseDataset:
    """Generate full data only on download; publish only a complete file."""
    dataset = get_dataset(dataset_id)
    checkpoint('准备完整 Parquet')
    # A waiting request can be cancelled without interrupting the active export.
    while not dataset.parquet_lock.acquire(timeout=.1):
        checkpoint()
    pending = dataset.parquet.with_name('wide_all.pending.parquet')
    try:
        checkpoint()
        if dataset.parquet.is_file():
            return dataset
        if dataset.source is None or not dataset.source.is_file():
            raise ValueError('原始 STDF 已不存在，请重新导入后再保存 Parquet')
        with stage('parquet_export', '从原始 STDF 生成完整 Parquet'):
            convert_stdf_to_wide_all(dataset.source, pending)
            checkpoint()
            pending.rename(dataset.parquet)
        return dataset
    finally:
        try:
            pending.unlink(missing_ok=True)
        finally:
            dataset.parquet_lock.release()


def describe(dataset_id: str, dataset: ReleaseDataset) -> dict:
    frame, meta = dataset.frame, dataset.metadata
    return {"id": dataset_id, "filename": dataset.filename, "record_count": len(frame),
            "sites": sorted(int(v) for v in pd.to_numeric(frame.SITE, errors="coerce").dropna().unique()),
            "tests": [{"column": t["column"], "name": t.get("name") or "名称未记录",
                       "number": t.get("test_num"), "unit": t.get("unit")}
                      for t in meta["test_catalog"]],
            "conditions": meta.get("stdf_metadata", {}).get("mir", {}),
            "parts": [{"id": int(row["__part_index"]), "site": as_float(row.get("SITE")),
                       "bin": as_float(row.get("SBIN")), "name": str(row.get("Name") or "")}
                      for row in frame[["__part_index", "SITE", "SBIN", "Name"]].to_dict("records")],
            "source_sha256": meta.get("source_sha256")}


def reason_text(item: dict) -> str:
    if item.get("reason") == "TOO_FEW_USABLE_SITES":
        return f"仅有 {item['site_count']} 个有效 site，至少需要 2 个（每个 site 至少 2 条有效记录）"
    if item.get("reason"):
        return REASONS.get(item["reason"], item["reason"])
    if item.get("threshold_band"):
        return f"CPK {item['threshold_band']}"
    condition = item.get("threshold_condition") or {}
    if condition.get("metric") == "cg":
        if condition.get("operator") == "GE_LT":
            return f"{condition.get('lower')} ≤ Cg < {condition.get('upper')}"
        return f"Cg {'<' if condition.get('operator') == 'LT' else '≥'} {condition.get('threshold')}"
    return LABELS.get(item.get("status"), "需要核对")


def _cell(item: dict | None, metric: str) -> dict | None:
    if item is None:
        return None
    cell = {"status": item["status"], "value": item.get(metric), "n": item["n"], "unit": item.get("unit"),
            "reason": reason_text(item), "rule_id": item.get("rule_id"),
            "override": item.get("cpk_override_applied", False),
            "supported_by_bin1_cpk": item.get("supported_by_bin1_cpk")}
    if metric == "cpk":
        low, high, std = (as_float(item.get(key)) for key in ("effective_lsl", "effective_usl", "std"))
        cp = (high - low) / (6 * std) if low is not None and high is not None and low < high and std else None
        cell.update(mean=item.get("mean"), std=std, cp=as_float(cp))
    elif metric == "cg":
        cell["sites"] = [{key: site.get(key) for key in ("site", "n", "cg", "status")}
                         for site in item.get("sites", [])]
    elif metric == "shift_sigma":
        cell.update(shift_spec_pct=item.get("shift_spec_pct"), var_ratio=item.get("var_ratio"))
    return cell


def merge_reports(reports: dict[str, dict], test_catalog: list[dict] | None = None) -> list[dict]:
    metric = {"cpk": "cpk", "cgm": "cg", "mmsa": "shift_sigma"}
    tests = {t["column"]: t for t in test_catalog or []}
    groups = {}
    for module, report in reports.items():
        for item in report["items"]:
            row = groups.setdefault(item["group_id"], {"key": item["group_id"],
                "column": item["column"], "name": item["test_name"] or "名称未记录", "number": item["test_num"],
                "unit": item["unit"], "lsl": item["effective_lsl"], "usl": item["effective_usl"],
                "conditions": item["conditions"], "cpk": None, "cgm": None, "mmsa": None})
            row[module] = _cell(item, metric[module])
        for item in report.get("unexecuted_tests", []):
            test = tests.get(item["column"], {})
            key = f"unexecuted:{item['column']}"
            row = groups.setdefault(key, {"key": key, "column": item["column"],
                "name": test.get("name") or "名称未记录", "number": test.get("test_num"), "unit": None, "lsl": None, "usl": None,
                "conditions": {}, "cpk": None, "cgm": None, "mmsa": None})
            row[module] = _cell(item, metric[module])
    for row in groups.values():
        cells = [row[module] for module in reports if row[module] is not None]
        worst = min(cells, key=lambda c: _RANK.get(c["status"], 2))
        row["status"] = worst["status"] if worst["status"] in _RANK else "REVIEW_REQUIRED"
        row["reason"] = worst["reason"]
        if any(row[module] is None for module in reports):
            if row["status"] in {"PASS", "NOT_APPLICABLE"}:
                row["status"] = "REVIEW_REQUIRED"
                row["reason"] = "部分检查没有匹配结果，请核对选择范围、Test ID、Test Name 及重复项"
    return sorted(groups.values(), key=lambda r: (_RANK[r["status"]], r["name"], r["key"]))


@dataset_computation
@computation
@timed('run.total')
@compact_sample_refs()
def run_checks(dataset_id: str, selections: dict, modules: list[str], source_ids: dict | None = None) -> dict:
    with measure('run.prepare'):
        dataset = get_dataset(dataset_id)
        source_ids = source_ids or {m: dataset_id for m in modules}
        source_members = {m: source_ids[m] if isinstance(source_ids[m], list) else [source_ids[m]] for m in modules}
        pool_cache = {}
        sources = {}
        for m, ids in source_members.items():
            key = tuple(ids)
            if key not in pool_cache:
                pool_cache[key] = _source_pool(ids) if len(ids) > 1 else get_dataset(ids[0])
            sources[m] = pool_cache[key]
        source_ids = {m: 'pool:' + sources[m].metadata['source_sha256'] if len(source_members[m]) > 1
                      else source_members[m][0] for m in modules}
        catalog = load_analysis_rules()
    count('run.modules', len(modules))
    count('run.sources', len(set(source_ids.values())))
    for module, source in sources.items():
        selection = selections[module]
        event('module_input', module=module, rows=len(source.frame),
              wide_columns=len(source.frame.columns), catalog_tests=len(source.metadata['test_catalog']),
              retained_mb=round(source.memory_bytes / 1024 ** 2, 2),
              selection_counts={key: None if selection.get(key) is None else len(selection[key])
                                for key in ('part_indices', 'sites', 'test_columns')})
    reports = {}
    if "cpk" in modules:
        source = sources["cpk"]
        with stage('cpk', '计算 bin1 CPK'):
            reports["cpk"] = build_release_bin1_cpk_report(source.frame, source.metadata,
                catalog=catalog, **selections["cpk"])
    if "cgm" in modules:
        source = sources["cgm"]
        support = reports.get("cpk")
        if support is None:
            support = build_release_bin1_cpk_report(source.frame, source.metadata,
                catalog=catalog, part_indices=[])
        with stage('cgm', '计算 CGM'):
            reports["cgm"] = build_release_cgm_report(source.frame, source.metadata,
                catalog=catalog, bin1_cpk_report=support, allow_cross_source_cpk=True, **selections["cgm"])
    if "mmsa" in modules:
        # The gate itself is the Overall bin1 CPK, so without an explicit CPK
        # module the diagnosis builds full local supports from its own selection.
        source = sources["mmsa"]
        with stage('mmsa', '计算 Site Diff'):
            reports["mmsa"] = build_release_mmsa_report(source.frame, source.metadata, catalog=catalog,
                bin1_cpk_report=reports.get("cpk"), cgm_report=reports.get("cgm"),
                allow_cross_source_cpk=True, allow_cross_source_cgm=True, **selections["mmsa"])
    with measure('run.merge'):
        checkpoint('整理检查结果')
        # Preserve originals for trace/export; presentation keys may join unique cross-file matches.
        display_reports = {m: {**r, "items": [{**i, "original_group_id": i["group_id"]} for i in r["items"]],
            "unexecuted_tests": [dict(i) for i in r.get("unexecuted_tests", [])]} for m, r in reports.items()}
        cross_source = len(set(source_ids.values())) > 1
        if cross_source:
            identities: dict[str, dict[str, list[dict]]] = {}
            for m, report in display_reports.items():
                for item in report["items"]:
                    identity = cross_source_identity(item)
                    if identity:
                        identities.setdefault(identity, {}).setdefault(m, []).append(item)
            for grouped in identities.values():
                if all(len(grouped.get(m, [])) == 1 for m in modules):
                    shared_key = grouped[modules[0]][0]["group_id"]
                    for entries in grouped.values():
                        entries[0]["group_id"] = shared_key
            for m, report in display_reports.items():
                for item in report["unexecuted_tests"]:
                    item["column"] = f"{source_ids[m]}:{item['column']}"
        display_catalog = [{**t, "column": f"{source_ids[m]}:{t['column']}" if cross_source else t["column"]}
            for m, source in sources.items() for t in source.metadata["test_catalog"]]
        rows = merge_reports(display_reports, display_catalog)
        items_by_key = {m: {i["group_id"]: i for i in r["items"]} for m, r in display_reports.items()}
        for row in rows:
            for m, report in display_reports.items():
                item = items_by_key[m].get(row["key"])
                if row[m]:
                    row[m].update(dataset_id=source_ids[m], filename=sources[m].filename,
                        group_id=item["original_group_id"] if item else None)
        result = {"id": uuid4().hex, "dataset_id": dataset_id, "rows": rows,
                  "sources": {m: {"dataset_id": source_ids[m], "dataset_ids": source_members[m],
                      "source_files": sources[m].metadata.get('source_files', []), "filename": sources[m].filename,
                      "source_sha256": sources[m].metadata.get("source_sha256")} for m in modules},
                  "modules": modules, "selections": selections,
                  "warnings": list(dict.fromkeys(w for r in reports.values() for w in r["warnings"])),
                  "rules_hash": catalog.catalog_hash, "release_decision": "ENGINEER_CONFIRMATION_REQUIRED"}
    with measure('run.remarks'):
        prepare_remarks(result, reports, catalog)
    with measure('run.retain'):
        with _lock:
            checkpoint()
            if any(key not in _datasets for ids in source_members.values() for key in ids) or _datasets.get(dataset_id) is not dataset:
                for source in sources.values():
                    invalidate_indexes(source.metadata.get('source_sha256'))
                    invalidate_store_arrays([e.store for e in source.frame.get('__test_records', []) if hasattr(e, 'store')])
                raise KeyError('文件已不在当前会话，请重新导入')
            dataset.runs[result["id"]] = (result, reports)
            dataset.run_sources[result['id']] = {m: source for m, source in sources.items()
                                                if len(source_members[m]) > 1}
            while len(dataset.runs) > 3:
                expired = next(iter(dataset.runs))
                _discard_run(dataset, expired)
    count('run.result_rows', len(rows))
    with measure('run.memory_retention'):
        _limit_retained_indexes(dataset, result['id'])
    return result


def _retained_index_bytes():
    seen, total = set(), 0
    for owner in _datasets.values():
        for _, reports in owner.runs.values():
            for report in reports.values():
                for item in report.get('items', []):
                    refs = item.get('sample_refs')
                    arrays = ([refs.event_ids, refs.mask] if hasattr(refs, 'event_ids') else
                              [refs.rows, refs.sequences] if hasattr(refs, 'rows') else [])
                    for array in arrays:
                        if array is None:
                            continue
                        while isinstance(getattr(array, 'base', None), np.ndarray):
                            array = array.base
                        if id(array) not in seen:
                            seen.add(id(array))
                            total += array.nbytes
    return total


def _limit_retained_indexes(current, run_id):
    # Older results can expire just as with the existing three-run retention.
    # Shared buffers are counted once across modules, runs and datasets.
    with _lock:
        retained = _retained_index_bytes()
        candidates = [(owner, key) for owner in _datasets.values() for key in owner.runs
                      if owner is not current or key != run_id]
        for owner, key in candidates:
            if retained <= RETAINED_INDEX_BUDGET:
                break
            _discard_run(owner, key)
            retained = _retained_index_bytes()
        count('run.retained_index_bytes', retained)


def _discard_run(owner, run_id):
    from app.release_chart_service import invalidate_chart_cache
    invalidate_chart_cache(None, run_id)
    owner.runs.pop(run_id, None)
    owner.outlier_report_exports.pop(run_id, None)
    for pool in owner.run_sources.pop(run_id, {}).values():
        invalidate_indexes(pool.metadata.get('source_sha256'))
    with owner.chart_lock:
        for key in list(owner.chart_samples):
            if key[0] == run_id:
                owner.chart_samples.pop(key)
    for key in list(owner.excel_cache):
        if key[0] == run_id:
            owner.excel_cache.pop(key)


def get_run(dataset: ReleaseDataset, run_id: str) -> tuple[dict, dict]:
    with _lock:
        if run_id not in dataset.runs:
            raise KeyError("结果已过期，请重新计算")
        return dataset.runs[run_id]


@timed('detail.total')
def item_detail(dataset_id: str, run_id: str, key: str) -> dict:
    dataset = get_dataset(dataset_id)
    result, reports = get_run(dataset, run_id)
    row = next((r for r in result["rows"] if r["key"] == key), None)
    if row is None:
        raise KeyError("找不到这个测项分组")
    details = {}
    for module, report in reports.items():
        cell = row.get(module)
        item = next((i for i in report["items"] if i["group_id"] == (cell or {}).get("group_id", key)), None)
        if item is None:
            continue
        source = run_source(dataset, result, module)
        samples: dict[int, list[float]] = {}
        with measure('detail.samples'):
            resolved = ReportSampleResolver(source.frame, source.metadata.get('source_sha256', ''),
                                            minimal=True)(item)
            for point in resolved:
                site = point['site']
                site = int(site) if as_float(site) is not None else -1
                samples.setdefault(site, []).append(point['value'])
        with measure('detail.statistics'):
            values = [v for site_values in samples.values() for v in site_values]
            edges = np.histogram_bin_edges(values, bins=24).tolist() if values else []
            sites = {s["site"]: dict(s) for s in item.get("sites", [])}
            for site, data in sorted(samples.items()):
                sites.setdefault(site, {"site": site, "cg": None})
                sites[site].update(n=len(data), mean=float(np.mean(data)),
                    std=float(np.std(data, ddof=1)) if len(data) >= 2 else None,
                    histogram=np.histogram(data, bins=edges)[0].tolist())
        details[module] = {"sites": list(sites.values()), "edges": edges,
            "mean": item.get("mean"), "std": item.get("std"), "n": item["n"],
            "excluded_counts": item["excluded_counts"], "rule_id": item.get("rule_id"),
            "reason": reason_text(item), "sample_refs": item["sample_refs"][:20],
            "sample_ref_count": len(item["sample_refs"]), "s2s": item.get("s2s")}
    return {"row": row, "details": details}


def _stream_json(payload, output):
    """Encode in bounded chunks; the multi-GB text never materializes in memory."""
    encoder = json.JSONEncoder(ensure_ascii=False, allow_nan=False, indent=2,
                               default=reference_json)
    buffer, pending = [], 0
    for chunk in encoder.iterencode(payload):
        buffer.append(chunk)
        pending += len(chunk)
        if pending >= 1024 * 1024:
            checkpoint()
            output.write(''.join(buffer).encode('utf-8'))
            buffer.clear()
            pending = 0
    if buffer:
        checkpoint()
        output.write(''.join(buffer).encode('utf-8'))


@timed('export.total')
def export_report(dataset_id: str, run_id: str) -> ReportFile:
    """Spool the full JSON export to a temporary file and return the artifact."""
    dataset = get_dataset(dataset_id)
    result, reports = get_run(dataset, run_id)
    directory = tempfile.TemporaryDirectory(prefix="deepyield-export-")
    try:
        path = Path(directory.name) / 'release-check-report.json'
        with stage('json', '序列化 JSON 报告'), path.open('wb') as output:
            _stream_json({"mode": "real", "filename": dataset.filename,
                          "result": result, "reports": reports}, output)
        artifact = ReportFile(directory, path, {'filename': 'release-check-report.json'},
                              path.stat().st_size, language.get())
    except BaseException:
        directory.cleanup()
        raise
    return artifact
