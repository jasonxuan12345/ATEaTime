"""Deterministic English diagnostics from existing results, without scanning measurements."""
from copy import deepcopy
from deepyield.compute.release_knowledge import load_knowledge, match_reference
from deepyield.compute.release_cgm_diagnosis import diagnose_cgm, match_cgm_reference
from deepyield.rules.conditions import as_float


def number(value):
    if value == 'Infinity':
        return '∞'
    value = as_float(value)
    return f"{value:.4g}" if value is not None else "unavailable"


def _site_names(entries):
    return ", ".join(f"S{entry['site']}" for entry in entries if entry.get("site") is not None) or "unavailable"


def numbered_remark(parts):
    return "\n".join(f"{index}. {part.strip()}" for index, part in
                     enumerate((part for part in parts if part and part.strip()), 1))


def _site_diff_text(item):
    """Name only sites supported by the existing mean/spread difference checks."""
    sites = item.get("sites", [])
    rule = item.get("rule_id")
    affected = set()
    if item.get("shift_severity") == "RED" or rule == "MMSA-003":
        means = [as_float(site.get("mean")) for site in sites]
        means = [value for value in means if value is not None]
        if means and min(means) < max(means):
            affected.update(site["site"] for site in sites if site.get("site") is not None
                            and as_float(site.get("mean")) in {min(means), max(means)})
    if item.get("var_severity") == "RED" or rule == "MMSA-004" or item.get("secondary_rule_id") == "MMSA-004":
        stds = [as_float(site.get("std")) for site in sites]
        stds = [value for value in stds if value is not None]
        if stds and min(stds) < max(stds):
            s2s = item.get("s2s") or {}
            median_std = as_float(item.get("median_site_std", s2s.get("median_site_std")))
            red_ge = as_float(((s2s.get("bands") or {}).get("var_ratio") or {}).get("red_ge"))
            def abnormal_spread(site):
                std = as_float(site.get("std"))
                if std is None:
                    return False
                if median_std == 0:
                    return std > 0
                if median_std is not None and median_std > 0 and red_ge is not None:
                    return std / median_std >= red_ge
                return std == max(stds)
            affected.update(site["site"] for site in sites if site.get("site") is not None
                            and abnormal_spread(site))
    if not affected:
        return ""
    return "Site Diff: " + ", ".join(f"S{site}" for site in sorted(affected)) + "."


def _mmsa_text(item, templates):
    reason = item.get("reason")
    text = templates["mmsa"].get(reason, "Site Diff evidence requires engineering review.")
    evidence = []
    for field, label in (("shift_sigma", "site mean shift (sigma)"),
                         ("shift_spec_pct", "site shift (% spec)"), ("var_ratio", "site std ratio")):
        if as_float(item.get(field)) is not None:
            evidence.append(f"{label}={number(item[field])}")
    if reason == "CLOSE_TO_LIMIT_MARGIN":
        side = item.get("close_to")
        if side in {"HIGH", "LOW"}:
            evidence.append(f"near {side.lower()} limit")
    sites = [site for site in item.get("sites", []) if as_float(site.get("std")) is not None]
    if sites:
        worst = max(site["std"] for site in sites)
        evidence.append("largest std: " + _site_names([s for s in sites if s["std"] == worst]))
    return " ".join([text, "; ".join(evidence) + "." if evidence else ""]).strip()


def _current(module, item, templates):
    status = item.get("status")
    reason = item.get("reason")
    if module == "mmsa" and str(item.get("rule_id", "")).startswith("MMSA-"):
        return _mmsa_text(item, templates)
    if status not in {"PASS", "FAIL", "RISK"}:
        return templates["unavailable"].get(reason, "Result is not fully evaluable; engineering review required.")
    if module == "mmsa":
        return _mmsa_text(item, templates)
    if module == "cpk":
        text = f"Bin1 CPK={number(item.get('cpk'))}; {status} under the configured CPK rules."
        low, high, std = (as_float(item.get(key)) for key in ("effective_lsl", "effective_usl", "std"))
        if low is not None and high is not None and low < high and std is not None and std > 0:
            text += f" Cp={number((high-low)/(6*std))}."
        if status in {"FAIL", "RISK"}:
            text += " Review specification centering, distribution and site evidence."
        return text
    sites = item.get("sites", [])
    text = f"Min Cg={number(item.get('cg'))}; {status} under the configured CGM rules."
    # Read the actual rule threshold, not a second copy of the number 1.33.
    threshold = as_float(item.get("_cg_pass_threshold"))
    bad = [site for site in sites if threshold is not None and
           as_float(site.get("cg")) is not None and site["cg"] < threshold]
    if bad:
        text += " Sites below the Cg pass threshold: " + ", ".join(
            f"S{s['site']} (Cg={number(s['cg'])})" for s in bad) + "."
    if item.get("cpk_override_applied"):
        text += f" Accepted by matching bin1 CPK={number(item.get('supported_by_bin1_cpk'))}; low Cg remains recorded."
    elif status in {"FAIL", "RISK"}:
        text += " Check repeatability, contact, settling time and test resolution."
    return text


def diagnose_item(module, item, *, catalog=None, source=None, selection=None, related=None, _share_provenance=False):
    catalog = catalog or load_knowledge()
    templates = catalog["templates"]
    current = _current(module, item, templates)
    reference = match_reference(item, catalog)
    cgm_diagnosis = None
    if module == "cgm" and catalog.get("cgm"):
        cgm_diagnosis = diagnose_cgm(item, catalog["cgm"], as_float(item.get("_cg_pass_threshold")))
        if cgm_diagnosis["current_remark"]:
            current += " " + cgm_diagnosis["current_remark"]
        cgm_reference = match_cgm_reference(item, catalog["cgm"])
        if cgm_reference["status"] != "none":
            reference = cgm_reference
    concise = module in {"cpk", "cgm"}
    parts = [] if concise else [current]
    if concise and item.get("status") not in {"PASS", "FAIL", "RISK"} and item.get("reason") in templates["unavailable"]:
        parts.append(_current(module, item, templates))
    if module == "cgm" and cgm_diagnosis:
        parts.append(cgm_diagnosis.get("advice", ""))
    related_evidence = None
    if related and module in {"cpk", "cgm"}:
        support, support_source, support_selection = related
        if support.get("status") in {"RISK", "FAIL", "REVIEW_REQUIRED"} and str(support.get("rule_id", "")).startswith("MMSA-"):
            parts.append(_site_diff_text(support))
            related_evidence = {"rule_id": support["rule_id"],
                "source": support_source if _share_provenance else deepcopy(support_source),
                "selection": support_selection if _share_provenance else deepcopy(support_selection)}
    if reference["status"] in {"reference", "applicable"}:
        label = "Historical project reference (scope unconfirmed)" if reference["status"] == "reference" else "Applicable project reference (historical observation)"
        parts.append(reference["historical_remark"] if concise else label + ": " + reference["historical_remark"])
    elif reference["status"] == "ambiguous" and not concise:
        parts.append(reference["message"])
    # Family hints are recommendations only; never an assertion of the root cause.
    if not concise and item.get("status") in {"FAIL", "RISK"} and reference["status"] == "none":
        name = item.get("test_name") or ""
        for family in templates["families"]:
            if any(token in name for token in family["contains"]):
                parts.append(family["suggestion"])
                break
    parts = [part.strip() for part in parts if part and part.strip()]
    text = numbered_remark(parts) if concise else " ".join(parts)
    return {"current_remark": current, "auto_remark": text, "remark": text,
            "remark_parts": parts,
            "category": (cgm_diagnosis or {}).get("category") or reference.get("category"),
            "cgm_diagnosis": cgm_diagnosis, "knowledge": reference,
            "knowledge_version": catalog["version"], "knowledge_hash": catalog["hash"],
            "source": (source or {}) if _share_provenance else deepcopy(source or {}),
            "selection": (selection or {}) if _share_provenance else deepcopy(selection or {}),
            "related_evidence": related_evidence, "review_state": "AUTO_REVIEW_REQUIRED"}


def diagnose_outlier(item, *, catalog=None, source=None, selection=None, _share_provenance=False):
    catalog = catalog or load_knowledge()
    count = item.get("candidate_count", 0)
    if item.get("status") == "OUTLIER_CANDIDATES":
        text = f"{count} statistical outlier candidate(s) in H{item.get('head')}/S{item.get('site')}, n={item.get('n')}. Review the distribution and measurement evidence; candidates do not establish a physical defect."
    elif item.get("status") == "NO_OUTLIER_FOUND":
        text = f"No outlier candidates found in the evaluated group, n={item.get('n')}."
    else:
        text = catalog["templates"]["unavailable"].get(item.get("reason"),
            "Outlier baseline is not evaluable; review sample size, spread and channel identity.")
    diagnostic = diagnose_item("outlier", {**item, "status": "NOT_EVALUATED"}, catalog=catalog,
                               source=source, selection=selection, _share_provenance=_share_provenance)
    history = diagnostic["knowledge"]
    suffix = (" Historical project reference (scope unconfirmed): " + history["historical_remark"]
              if count and history["status"] in {"reference", "applicable"} else "")
    diagnostic.update(current_remark=text, auto_remark=text + suffix, remark=text + suffix)
    return diagnostic
