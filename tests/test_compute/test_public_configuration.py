"""Public operation without local product files or historical observations."""
from copy import deepcopy
import json

import pytest
import yaml

from deepyield.configuration import DEFAULTS, PRIVATE_CONFIG_ENV, configuration_file
from deepyield.compute.release_knowledge import load_knowledge, match_reference
from deepyield.compute.release_remarks import diagnose_item
from deepyield.compute.release_cgm_diagnosis import build_cgm_context, match_cgm_reference
from deepyield.device_config import registry
from tests.test_compute.remark_fixtures import example_catalog


@pytest.fixture(autouse=True)
def clean_configuration(monkeypatch):
    monkeypatch.delenv(PRIVATE_CONFIG_ENV, raising=False)
    load_knowledge.cache_clear()
    monkeypatch.setattr(registry, "_registry", None)
    yield
    load_knowledge.cache_clear()


def low_cg(**overrides):
    return {"test_num": 1001, "test_name": "ExampleMeasurement", "status": "PASS",
            "cg": .5, "_cg_pass_threshold": 1.5, "supported_by_bin1_cpk": 3.,
            "cpk_override_applied": True,
            "sites": [{"site": 1, "cg": .5, "status": "CALCULATED"},
                      {"site": 2, "cg": 2., "status": "CALCULATED"}], **overrides}


def test_public_defaults_have_no_historical_or_product_groups():
    catalog = load_knowledge()
    assert catalog["entries"] == {} and catalog["cgm"]["entries"] == []
    assert catalog["templates"]["families"] == []
    assert configuration_file("release_knowledge.json").parent == DEFAULTS
    assert yaml.safe_load(configuration_file("release_chart_families.yaml").read_text())["families"] == []


def test_empty_private_directory_uses_defaults_for_every_file(monkeypatch, tmp_path):
    monkeypatch.setenv(PRIVATE_CONFIG_ENV, str(tmp_path))
    catalog = load_knowledge()
    assert catalog == load_knowledge(DEFAULTS)
    data = low_cg()
    before = deepcopy(data)
    diagnosis = diagnose_item("cgm", data)
    assert diagnosis["knowledge"] == {"status": "none"}
    assert diagnosis["remark"] == "1. Check site repeatability, contact and distribution."
    assert "S1 (Cg=0.5)" in diagnosis["current_remark"]
    assert "Accepted by matching bin1 CPK=3" in diagnosis["current_remark"]
    assert data == before


def test_partial_private_catalog_uses_default_templates_and_cgm(monkeypatch, tmp_path):
    catalog = example_catalog()
    entry = catalog["entries"][(1001, "ExampleVoltage")][0]
    private = {"version": "1.0", "source": catalog["source"], "entries": [entry]}
    (tmp_path / "release_knowledge.json").write_text(json.dumps(private), encoding="utf-8")
    monkeypatch.setenv(PRIVATE_CONFIG_ENV, str(tmp_path))
    loaded = load_knowledge()
    assert loaded["templates"] == load_knowledge(DEFAULTS)["templates"]
    assert loaded["cgm"]["entries"] == []
    assert match_reference(entry, loaded)["historical_remark"] == entry["historical_remark"]
    assert loaded["hash"] != load_knowledge(DEFAULTS)["hash"]
    monkeypatch.delenv(PRIVATE_CONFIG_ENV)
    assert load_knowledge()["entries"] == {}


def test_private_template_only_does_not_require_history(tmp_path):
    template = deepcopy(load_knowledge()["templates"])
    template["unavailable"]["EXAMPLE_REASON"] = "Synthetic local explanation."
    (tmp_path / "release_remark_templates.json").write_text(json.dumps(template), encoding="utf-8")
    catalog = load_knowledge(tmp_path)
    assert not catalog["entries"]
    assert catalog["templates"]["unavailable"]["EXAMPLE_REASON"] == "Synthetic local explanation."


@pytest.mark.parametrize("content", ['{', '{"version": "unsupported"}'])
def test_invalid_optional_file_is_reported_instead_of_hidden(tmp_path, content):
    (tmp_path / "release_knowledge.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        load_knowledge(tmp_path)


def test_missing_explicit_directory_is_reported(monkeypatch, tmp_path):
    monkeypatch.setenv(PRIVATE_CONFIG_ENV, str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="Configuration directory does not exist"):
        load_knowledge()


def test_current_cgm_guidance_does_not_depend_on_test_name():
    first = diagnose_item("cgm", low_cg(test_name="ExampleVoltage"))
    second = diagnose_item("cgm", low_cg(test_name="UnrelatedMeasurement"))
    assert first == second
    assert first["cgm_diagnosis"]["category"] == "REPEATABILITY_REVIEW"


@pytest.mark.parametrize("threshold", [None, .4, .5])
def test_missing_or_untriggered_cgm_threshold_adds_no_advice(threshold):
    diagnosis = diagnose_item("cgm", low_cg(_cg_pass_threshold=threshold))
    assert diagnosis["remark"] == ""


def test_partial_cgm_reference_file_needs_no_expert_rulebook(tmp_path):
    reference = {"id": "EXAMPLE-FAMILY", "patterns": ["Example*"],
                 "category": "EXAMPLE", "subcategory": "SYNTHETIC",
                 "conditions": {}, "historical_remark": "Synthetic family observation."}
    private = {"version": "1.0", "source": {"name": "Synthetic fixture"}, "entries": [reference]}
    (tmp_path / "release_cgm_knowledge.json").write_text(json.dumps(private), encoding="utf-8")
    catalog = load_knowledge(tmp_path)
    diagnosis = diagnose_item("cgm", low_cg(), catalog=catalog)
    assert diagnosis["remark"].endswith("2. Synthetic family observation.")
    assert diagnosis["knowledge"]["id"] == "EXAMPLE-FAMILY"
    assert "historical_bad_site_pattern" not in diagnosis["knowledge"]
    catalog["cgm"]["entries"].append(deepcopy(reference))
    assert match_cgm_reference(low_cg(), catalog["cgm"])["status"] == "ambiguous"


def test_cgm_context_does_not_inflate_counts_for_specification_variants():
    catalog = {"entries": [{"id": "EXAMPLE-FAMILY", "patterns": ["Example*"],
                "category": "EXAMPLE", "subcategory": "SYNTHETIC", "conditions": {},
                "historical_remark": "Synthetic observation."}], "source": {"name": "Synthetic"}}
    first = low_cg(group_id="a")
    variant = {**deepcopy(first), "group_id": "b", "effective_usl": 20.}
    context = build_cgm_context([first, variant], catalog, 1.5)
    assert context["a"]["family_item_count"] == 1
    assert context["a"]["same_bad_sites_item_count"] == 1


def test_default_registry_and_wafer_display_need_no_private_products(monkeypatch, tmp_path):
    from deepyield.compute.release_chart_wafer import release_wafer_data
    monkeypatch.setenv(PRIVATE_CONFIG_ENV, str(tmp_path))
    loaded = registry.load_device_registry(force_reload=True)
    assert loaded.find_by_part_type("UnconfiguredExample") is None
    assert not any(device.enabled for device in loaded.devices)
    points = [{"lot": "EXAMPLE_LOT", "wafer": "EXAMPLE_WAFER", "x": 1, "y": 2,
               "record_sequence": 1, "part_index": 1, "value": 3.}]
    result = release_wafer_data(points, {"conditions": {"part_type": "UnconfiguredExample"}})
    assert result["wafers"][0]["points"][0]["x"] == 1
    assert result["wafers"][0]["points"][0]["y"] == 2
