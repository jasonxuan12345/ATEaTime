"""Generic defaults with optional, explicitly selected local references."""
from functools import lru_cache
import hashlib
import json
from deepyield.configuration import configuration_directory, configuration_file


def load_knowledge(directory=None):
    return _load_knowledge(configuration_directory(directory))


@lru_cache(maxsize=4)
def _load_knowledge(directory):
    raw = [configuration_file(name, directory).read_bytes() for name in
           ("release_knowledge.json", "release_remark_templates.json", "release_cgm_knowledge.json")]
    knowledge, templates, cgm = [json.loads(data) for data in raw]
    if any(data.get("version") != "1.0" for data in (knowledge, templates, cgm)):
        raise ValueError("Unsupported Release remark configuration version")
    entries = {}
    for entry in knowledge["entries"]:
        if not isinstance(entry["test_num"], int) or not entry["test_name"].strip():
            raise ValueError("Invalid Release knowledge identity")
        if not entry.get("historical_remark") or not isinstance(entry.get("conditions"), dict):
            raise ValueError("Incomplete Release knowledge entry")
        entries.setdefault((entry["test_num"], entry["test_name"]), []).append(entry)
    return {"version": knowledge["version"], "hash": hashlib.sha256(b"\0".join(raw)).hexdigest(),
            "source": knowledge["source"], "entries": entries, "templates": templates, "cgm": cgm}


load_knowledge.cache_clear = _load_knowledge.cache_clear


def match_reference(item, catalog):
    candidates = catalog["entries"].get((item.get("test_num"), item.get("test_name")), [])
    if len(candidates) > 1:
        return {"status": "ambiguous", "message": "Multiple historical entries; not applied."}
    if not candidates:
        return {"status": "none"}
    entry = candidates[0]
    actual = item.get("conditions") or {}
    conditions = entry["conditions"]
    mismatch = any(actual.get(key) and actual[key] != value for key, value in conditions.items())
    missing = any(not actual.get(key) for key in conditions)
    status = ("not_applicable" if mismatch else "reference" if not conditions or missing
              or not entry.get("scope_confirmed") else "applicable")
    return {"status": status, "id": entry["id"], "category": entry["category"],
            "subcategory": entry["subcategory"], "historical_remark": entry["historical_remark"],
            "conditions": conditions, "source": catalog["source"],
            "scope_note": "Historical project observation; verify applicability to the current run."}
