"""Connect run snapshots to shared compute diagnostics and saved observations."""
from copy import deepcopy
from deepyield.compute.release_knowledge import load_knowledge
from deepyield.compute.release_cgm_diagnosis import build_cgm_context
from deepyield.compute.release_remarks import diagnose_item, numbered_remark

OBSERVATIONS = {
    'end_spikes': ('首尾跳点', 'first/last sequence spikes observed'),
    'drift': ('持续漂移', 'sequence drift observed'),
    'steps': ('离散台阶', 'discrete levels observed'),
    'random': ('随机波动', 'random variation observed'),
    'outliers': ('离群点', 'outlying samples observed'),
    'wide': ('分布较宽', 'wide distribution observed'),
    'close_limit': ('均值贴限', 'mean close to a specification limit'),
    'site_shift': ('site 均值差异', 'site mean differences observed'),
    'site_spread': ('site 波动差异', 'site spread differences observed'),
    'spatial': ('空间聚集', 'spatial concentration observed'),
}


def prepare_remarks(result, reports, catalog):
    knowledge = load_knowledge()
    cg_pass = catalog.rule("CGM-004").spec["condition"]["threshold"]
    indexes = {module: {item["group_id"]: item for item in report["items"]}
               for module, report in reports.items()}
    cgm_context = (build_cgm_context((reports.get("cgm") or {}).get("items", []),
                                     knowledge["cgm"], cg_pass) if knowledge.get("cgm") else {})
    for row in result["rows"]:
        support_cell = row.get("mmsa")
        support = indexes.get("mmsa", {}).get((support_cell or {}).get("group_id"))
        related = (support, result["sources"]["mmsa"], {"module": "mmsa"}) if support else None
        for module in result["modules"]:
            cell = row.get(module)
            if not cell:
                continue
            item = indexes[module].get(cell.get("group_id"))
            data = item if item is not None else {
                "test_num": row["number"], "test_name": row["name"], "conditions": row["conditions"],
                "status": cell["status"], "reason": "NO_SELECTED_EXECUTION", "n": 0}
            diagnostic = diagnose_item(module, {**data, "_cg_pass_threshold": cg_pass,
                "_cgm_context": cgm_context.get(data.get("group_id"), {})}, catalog=knowledge,
                source=result["sources"][module], selection={"module": module}, related=related, _share_provenance=True)
            cell["diagnosis"] = diagnostic
            if item is not None:
                item["diagnosis"] = diagnostic
    result["remark_knowledge"] = {key: knowledge[key] for key in ("version", "hash", "source")}
    if knowledge.get("cgm"):
        result["remark_knowledge"]["cgm_source"] = knowledge["cgm"]["source"]
        result["remark_knowledge"]["cgm_version"] = knowledge["cgm"]["version"]


def compose_remark(row, module, note=None):
    if note and note.get("final_remark") is not None:
        final = note["final_remark"].strip()
        if final:
            return final
    cell = row[module]
    diagnostic = cell.get("diagnosis") or {}
    note = note or {}
    if module in {"cpk", "cgm"}:
        parts = list(diagnostic.get("remark_parts", []))
        if "remark_parts" not in diagnostic and diagnostic.get("auto_remark"):
            parts.append(diagnostic["auto_remark"])
        parts.extend(OBSERVATIONS[tag][1] + "." for tag in note.get("tags", []))
        parts.extend([note.get("suggestion", "").strip(),
                      note.get("custom_remark", note.get("remark", "")).strip()])
        return numbered_remark(parts)
    automatic = diagnostic.get("auto_remark")
    if not automatic:
        value = cell.get("value")
        automatic = f"{module.upper()}={value:.4g}" if value is not None else module.upper()
    observed = "; ".join(OBSERVATIONS[tag][1] for tag in note.get("tags", []))
    additions = [observed, note.get("suggestion", "").strip(), note.get("custom_remark", note.get("remark", "")).strip()]
    return " ".join([automatic, *[part.rstrip(".") + "." for part in additions if part]])


def refresh_remarks(result):
    notes = result.get("chart_observations", {})
    for row in result["rows"]:
        for module in result["modules"]:
            cell = row.get(module)
            if not cell:
                continue
            diagnostic = cell.get("diagnosis")
            if diagnostic:
                note = notes.get(module + ":" + row["key"])
                diagnostic["remark"] = compose_remark(row, module, note)
                diagnostic["review_state"] = note["state"] if note else "AUTO_REVIEW_REQUIRED"


def remarks_view(result, compact=False):
    from app.release_presentation import diagnosis_summary
    root = f"/datasets/{result['dataset_id']}/runs/{result['id']}"
    return {module + ":" + row["key"]: (diagnosis_summary(row[module]['diagnosis'],
                f"{root}/items/{row['key']}/diagnosis/{module}") if compact else deepcopy(row[module]["diagnosis"]))
            for row in result["rows"] for module in result["modules"]
            if row.get(module) and row[module].get("diagnosis")}
