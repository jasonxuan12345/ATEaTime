"""TSR names restore Release catalog/events without weakening source matching."""
import io
import struct

import pytest

from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.rules.loader import load_analysis_rules
from tests.test_compute.test_release_stdf import cn, convert, part, ptr, record, specification


def tsr(name, *, kind="P", number=1001, site=0, endian="<"):
    payload = struct.pack(endian + "BBcIIII", 1, site, kind.encode("ascii"), number, 2, 0, 0)
    return record(10, 30, payload + cn(name), endian)


@pytest.mark.parametrize("extension", [".stdf", ".stdf.gz", ".stdf.bz2"])
@pytest.mark.parametrize("endian", ["<", ">"])
def test_trailing_tsr_fills_all_events_and_catalog_without_renaming_columns(tmp_path, extension, endian):
    body = part(endian=endian, tests=ptr(name="", endian=endian, optional=specification(endian=endian)))
    body += part(site=2, endian=endian, tests=ptr(1.5, name="", site=2, endian=endian))
    body += tsr("FULL_TSR_NAME", endian=endian) + tsr("FULL_TSR_NAME", site=2, endian=endian)
    (frame, meta), _, _ = convert(tmp_path, body, extension=extension, endian=endian)
    assert meta["test_catalog"][0]["column"] == "TEST_1001"
    assert meta["test_catalog"][0]["name"] == "FULL_TSR_NAME"
    assert frame["TEST_1001"].tolist() == [1.25, 1.5]
    assert [events[0]["name"] for events in frame["__test_records"]] == ["FULL_TSR_NAME"] * 2
    assert all(events[0]["test_text"] is None for events in frame["__test_records"])
    assert meta["stdf_metadata"]["record_counts"]["TSR"] == 2
    assert not meta["stdf_metadata"]["warnings"]


@pytest.mark.parametrize("before", [False, True])
def test_measurement_name_takes_precedence_over_summary(tmp_path, before):
    summary = tsr("SUMMARY_NAME")
    body = part(tests=ptr(name="", optional=specification())) + part(tests=ptr(name="MEASURED_NAME"))
    body = summary + body if before else body + summary
    (frame, meta), _, _ = convert(tmp_path, body)
    assert meta["test_catalog"][0]["name"] == "MEASURED_NAME"
    assert [events[0]["name"] for events in frame["__test_records"]] == ["MEASURED_NAME"] * 2
    assert "TEST_1001" in frame


@pytest.mark.parametrize("names", [("SITE_ONE", "SITE_TWO"), ("SITE_TWO", "SITE_ONE")])
def test_conflicting_summaries_do_not_guess_a_name_and_warning_reaches_report(tmp_path, names):
    body = part(tests=ptr(name="", optional=specification())) + part(tests=ptr(1.5, name=""))
    body += tsr(names[0]) + tsr(names[1], site=2)
    (frame, meta), _, _ = convert(tmp_path, body)
    assert meta["test_catalog"][0]["name"] is None
    assert all(events[0]["name"] is None for events in frame["__test_records"])
    report = build_release_bin1_cpk_report(frame, meta, catalog=load_analysis_rules())
    assert any("1001" in w and "名称不一致" in w for w in report["warnings"])


@pytest.mark.parametrize("kind", ["F", "M", " "])
def test_other_or_unknown_summary_type_never_names_a_ptr(tmp_path, kind):
    (frame, meta), _, _ = convert(tmp_path, part(tests=ptr(name="")) + tsr("WRONG_TYPE", kind=kind))
    assert meta["test_catalog"][0]["name"] is None
    assert frame["__test_records"][0][0]["name"] is None


def test_mpr_summary_names_each_result_and_keeps_result_indices(tmp_path):
    payload = struct.pack("<IBBBBHHff", 2002, 1, 0, 0, 0, 0, 2, .25, .5) + cn("") + cn("")
    body = part(tests=record(15, 15, payload)) + tsr("MULTI_RESULT", kind="M", number=2002)
    (frame, meta), _, _ = convert(tmp_path, body)
    assert {t["column"] for t in meta["test_catalog"]} == {"TEST_2002_0", "TEST_2002_1"}
    assert all(t["name"] == "MULTI_RESULT" for t in meta["test_catalog"])
    assert [e["name"] for e in frame["__test_records"][0]] == ["MULTI_RESULT"] * 2
    assert [e["result_index"] for e in frame["__test_records"][0]] == [0, 1]


def test_functional_summary_fills_name_without_changing_result(tmp_path):
    payload = struct.pack("<IBBBB", 3001, 1, 0, 0, 0)
    (frame, meta), _, _ = convert(tmp_path, part(tests=record(15, 20, payload)) +
                                 tsr("FUNCTIONAL_NAME", kind="F", number=3001))
    assert meta["test_catalog"][0]["name"] == "FUNCTIONAL_NAME"
    event = frame["__test_records"][0][0]
    assert event["name"] == "FUNCTIONAL_NAME" and event["result"] == 1


@pytest.mark.parametrize("payload", [b"", b"\x01" * 6, struct.pack("<BBcI", 1, 0, b"P", 1001) + b"\x00",
    struct.pack("<BBcIIII", 1, 0, b"P", 1001, 1, 0, 0) + b"\x05ab"])
def test_truncated_tsr_is_rejected_without_saving(tmp_path, payload):
    source = tmp_path / "bad.stdf"
    source.write_bytes(record(0, 10, b"\x02\x04") + part(tests=ptr()) + record(10, 30, payload))
    from deepyield.compute.release_stdf import convert_stdf_to_wide_all
    with pytest.raises(ValueError, match="TSR|字段不完整"):
        convert_stdf_to_wide_all(source, tmp_path / "wide_all.parquet")
    assert not (tmp_path / "wide_all.parquet").exists()


def test_minimal_summary_without_trailing_fields_is_legal(tmp_path):
    summary = record(10, 30, struct.pack("<BBcI", 1, 0, b"P", 1001))
    (_, meta), _, _ = convert(tmp_path, part(tests=ptr(name="")) + summary)
    assert meta["test_catalog"][0]["name"] is None


def test_legacy_reference_parser_keeps_skipping_tsr(tmp_path):
    from deepyield.compute.stdf_parser import StdfToCsvConverter
    (_, _), source, _ = convert(tmp_path, part(tests=ptr(name="")) + tsr("SUMMARY"))
    parser = StdfToCsvConverter(source, tmp_path)
    parser.iter_records(io.BytesIO(source.read_bytes()))
    assert len(parser.wide_rows) == 1


def test_tsr_does_not_cross_record_types_with_the_same_storage_column(tmp_path):
    functional = record(15, 20, struct.pack("<IBBBB", 1001, 1, 0, 0, 0))
    body = part(tests=ptr(name="") + functional) + tsr("PTR_ONLY")
    (frame, _), _, _ = convert(tmp_path, body)
    events = frame["__test_records"][0]
    assert events[0]["column"] == events[1]["column"] == "TEST_1001"
    assert events[0]["name"] == "PTR_ONLY"
    assert events[1]["record_type"] == "FTR" and events[1]["name"] is None


def test_late_measurement_name_replaces_initial_nul_before_tsr_fallback(tmp_path):
    body = part(tests=ptr(name="\x00")) + part(tests=ptr(name="MEASURED")) + tsr("SUMMARY")
    (frame, meta), _, _ = convert(tmp_path, body)
    assert meta["test_catalog"][0]["name"] == "MEASURED"
    assert [events[0]["name"] for events in frame["__test_records"]] == ["MEASURED"] * 2


def test_full_tsr_optional_tail_is_read_without_changing_measurements(tmp_path):
    payload = struct.pack("<BBcIIII", 1, 0, b"P", 1001, 1, 0, 0)
    payload += cn("FROM_TSR") + cn("SEQ") + cn("LABEL") + struct.pack("<Bfffff", 0, 1, 2, 3, 4, 5)
    (frame, meta), _, _ = convert(tmp_path, part(tests=ptr(name="")) + record(10, 30, payload))
    assert meta["test_catalog"][0]["name"] == "FROM_TSR"
    assert frame["TEST_1001"].tolist() == [1.25]
