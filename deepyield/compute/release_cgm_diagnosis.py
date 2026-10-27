"""Repeatability observations from current statistics and optional local references."""
from collections import defaultdict
from fnmatch import fnmatchcase
import json
import math
from statistics import median

from deepyield.rules.conditions import as_float


def match_cgm_reference(item, knowledge):
    """Family references never establish current cause or release acceptance."""
    name = item.get("test_name") or ""
    matches = [entry for entry in knowledge["entries"]
               if any(fnmatchcase(name, pattern) for pattern in entry["patterns"])]
    if len(matches) > 1:
        return {"status": "ambiguous", "message": "Multiple CGM family references; not applied."}
    if not matches:
        return {"status": "none"}
    entry = matches[0]
    return {"status": "reference", "id": entry["id"], "category": entry["category"],
            "subcategory": entry["subcategory"], "historical_remark": entry["historical_remark"],
            "conditions": entry["conditions"], "source": knowledge["source"],
            "match_basis": "family_name_pattern", "patterns": entry["patterns"],
            "scope_note": "Family-name historical reference; product/program and site topology are unconfirmed."}


def cgm_features(item, threshold):
    finite, bad, good, zero_sites, inf_sites = [], [], [], [], []
    unavailable = 0
    for site in item.get("sites", []):
        value = as_float(site.get("cg"))
        zero = as_float(site.get("std")) == 0 and (as_float(site.get("n")) or 0) >= 2
        try:
            positive_inf = float(site.get("cg")) == math.inf
        except (TypeError, ValueError, OverflowError):
            positive_inf = False
        if zero:
            zero_sites.append(site.get("site"))
        if zero or positive_inf:
            inf_sites.append(site.get("site"))
        if value is None:
            unavailable += 1
            continue
        finite.append(value)
        if threshold is not None and value < threshold:
            bad.append({"site": site.get("site"), "cg": value})
        elif threshold is not None:
            good.append(value)
    minimum = min(finite) if finite else None
    good_median = median(good) if good else None
    ratio = good_median / minimum if good_median is not None and minimum is not None and minimum > 0 else None
    if ratio is not None and not math.isfinite(ratio):
        ratio = None
    return {"Min_CGM": minimum, "Bad_Sites": bad, "Inf_Sites_Count": len(inf_sites),
            "Inf_or_zero_std_sites": inf_sites, "Zero_std_sites": zero_sites,
            "Good_Median_CGM": good_median, "Contrast_Ratio": ratio,
            "Finite_Sites_Count": len(finite), "Unavailable_Sites_Count": unavailable,
            "CGM_pass_threshold": threshold}


def _condition_key(item):
    # A single report already has one source and one selection.
    return json.dumps(item.get("conditions") or {}, sort_keys=True, ensure_ascii=False)


def build_cgm_context(items, knowledge, threshold):
    """Count unique tests per condition; specification variants cannot inflate counts."""
    families, sectors, pending = defaultdict(dict), defaultdict(set), []
    for item in items:
        features = cgm_features(item, threshold)
        ref = match_cgm_reference(item, knowledge)
        condition = _condition_key(item)
        identity = (item.get("test_num"), item.get("test_name"))
        bad = tuple(sorted((str(site["site"]) for site in features["Bad_Sites"])))
        complete = bool(item.get("sites")) and all(
            site.get("status") == "CALCULATED" for site in item["sites"])
        key = (condition, ref.get("id"))
        if ref["status"] == "reference":
            # Keep all variants, including incomplete ones, in the family denominator.
            families[key].setdefault(identity, set()).add(bad if complete else None)
        if complete and bad:
            sectors[(condition, bad)].add(identity)
        pending.append((item.get("group_id"), key, condition, bad))
    result = {}
    for group_id, key, condition, bad in pending:
        group = families.get(key, {})
        all_single = bool(group) and len(bad) == 1 and all(
            signatures == {bad} for signatures in group.values())
        result[group_id] = {"family_item_count": len(group),
                            "family_all_same_single_site": all_single,
                            "same_bad_sites_item_count": len(sectors.get((condition, bad), set()))}
    return result


def diagnose_cgm(item, knowledge, threshold):
    """Describe observed low Cg without inferring a product-specific root cause."""
    features = cgm_features(item, threshold)
    result = {"rule_id": None, "category": None, "subcategory": None,
              "features": features, "current_remark": "", "advice": "",
              "context": item.get("_cgm_context") or {}}
    minimum = features["Min_CGM"]
    if threshold is None or minimum is None or minimum >= threshold:
        return result
    bad = features["Bad_Sites"]
    if not bad:
        return result
    cpk = as_float(item.get("supported_by_bin1_cpk"))
    sites = ", ".join(f"S{site['site']} (Cg={site['cg']:.4g})" for site in bad)
    text = f"Observed Cg below the configured pass threshold on {sites}."
    advice = "Check site repeatability, contact and distribution."
    if cpk is not None:
        text += f" Matching bin1 CPK={cpk:.4g}."
    else:
        text += " Matching evaluable bin1 CPK is unavailable."
    if features["Unavailable_Sites_Count"]:
        text += " Some sites remain unevaluable; these are diagnostic hints from the available sites."
    result.update(rule_id="CGM-OBS-001", source_rule="Configured Cg pass threshold",
                  category="REPEATABILITY_REVIEW", subcategory="LOW_CG",
                  current_remark=text, advice=advice)
    return result
