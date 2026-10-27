"""Fictional examples for testing optional historical references."""
from copy import deepcopy
from deepyield.configuration import DEFAULTS
from deepyield.compute.release_knowledge import load_knowledge


def example_catalog():
    catalog = deepcopy(load_knowledge(DEFAULTS))
    catalog["source"] = {"name": "Synthetic example references", "sha256": "synthetic-source"}
    for number, name, remark in (
        (1001, "ExampleVoltage", "Synthetic reference: review the example limits."),
        (1002, "ExampleRepeatability", "Synthetic reference: review the example setup on Site 8."),
        (1003, "ExampleResolution", "Synthetic reference: compare the generated measurement runs."),
    ):
        catalog["entries"][(number, name)] = [{
            "id": f"EXAMPLE-{number}", "test_num": number, "test_name": name,
            "category": "EXAMPLE", "subcategory": "SYNTHETIC_REFERENCE",
            "historical_remark": remark, "conditions": {}, "scope_confirmed": False,
        }]
    return catalog
