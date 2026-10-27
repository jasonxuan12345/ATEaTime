"""Release imports retain first definitions, all attempts and one-file metadata."""
import bz2
import gzip
import io
import os
from pathlib import Path
import struct
import subprocess
import sys

import pandas as pd
import pytest

from deepyield.compute.release_stdf import convert_stdf_to_wide_all, read_release_wide_all
from deepyield.compute.stdf_parser import StdfToCsvConverter


def cn(text):
    raw = text.encode("latin1")
    return bytes([len(raw)]) + raw


def record(typ, sub, payload, endian="<"):
    return struct.pack(endian + "HBB", len(payload), typ, sub) + payload


def ptr(value=1.25, *, number=1001, site=0, optional=None, flag=0, name="VDD", endian="<"):
    payload = struct.pack(endian + "IBBBBf", number, 1, site, flag, 0, value)
    return record(15, 10, payload + cn(name) + cn("") + (optional or b""), endian)


def specification(unit="V", low=0.0, high=2.0, flag=2, endian="<"):
    return struct.pack(endian + "Bbbbff", flag, -3, -3, -3, low, high) + cn(unit)


def part(*, name="P1", site=0, bin=1, endian="<", tests=None):
    payload = struct.pack(endian + "BBBHHHhhI", 1, site, 0, 1, bin, bin, 10, 20, 100)
    return (record(5, 10, bytes([1, site]), endian) + (tests or b"") +
            record(5, 20, payload + cn(name) + cn("") + b"\0", endian))


def convert(tmp_path, body, *, extension=".stdf", endian="<"):
    source = tmp_path / ("source" + extension)
    data = record(0, 10, bytes([1 if endian == ">" else 2, 4]), endian) + body
    source.write_bytes(bz2.compress(data) if extension.endswith("bz2") else
                       gzip.compress(data) if extension.endswith("gz") else data)
    destination = tmp_path / "wide_all.parquet"
    convert_stdf_to_wide_all(source, destination)
    return read_release_wide_all(destination), source, destination


@pytest.mark.parametrize("extension", [".stdf", ".stdf.bz2", ".stdf.gz"])
def test_first_chip_specs_survive_omitted_fields_and_retests(tmp_path, extension):
    body = part(bin=2, tests=ptr(optional=specification())) + part(tests=ptr(1.5, name=""))
    (frame, meta), source, destination = convert(tmp_path, body, extension=extension)
    assert len(frame) == 2
    assert frame["SBIN"].tolist() == [2, 1]
    assert frame["1001_VDD"].tolist() == [1.25, 1.5]
    assert frame["__part_index"].tolist() == [1, 2]
    for row in frame["__test_records"]:
        assert row[0]["unit"] == "V"
        assert row[0]["low_limit"] == 0
        assert row[0]["high_limit"] == 2
        assert row[0]["res_scal"] == -3
    # Standalone read after moving the sole output away from the source.
    moved = tmp_path / "standalone.parquet"
    destination.rename(moved)
    source.unlink()
    assert len(read_release_wide_all(moved)[0]) == 2
    assert meta["all_attempts_preserved"] is True
    assert len(meta["test_catalog"]) == 1


def test_record_override_does_not_replace_first_default(tmp_path):
    body = part(tests=ptr(optional=specification()))
    body += part(name="P2", tests=ptr(optional=specification("A", high=4)))
    body += part(name="P3", tests=ptr())
    (frame, _), _, _ = convert(tmp_path, body)
    events = [row[0] for row in frame["__test_records"]]
    assert [e["unit"] for e in events] == ["V", "A", "V"]
    assert [e["high_limit"] for e in events] == [2, 4, 2]


def test_late_full_name_preserves_one_column_and_names_all_samples(tmp_path):
    body = part(tests=ptr(name="", optional=specification()))
    body += part(name="P2", tests=ptr(name="P1001_FULL_TEST_NAME"))
    body += part(name="P3", tests=ptr(name=""))
    (frame, meta), _, _ = convert(tmp_path, body)
    assert len(meta["test_catalog"]) == 1
    assert meta["test_catalog"][0]["column"] == "TEST_1001"
    assert meta["test_catalog"][0]["name"] == "P1001_FULL_TEST_NAME"
    assert [row[0]["name"] for row in frame["__test_records"]] == ["P1001_FULL_TEST_NAME"] * 3
    assert frame["TEST_1001"].notna().all()


def test_invalid_limit_fields_inherit_but_no_limit_flag_clears(tmp_path):
    body = part(tests=ptr(optional=specification()))
    body += part(name="P2", tests=ptr(optional=specification("", 99, 99, flag=0x32)))
    body += part(name="P3", tests=ptr(optional=specification("", 99, 99, flag=0xC2)))
    (frame, _), _, _ = convert(tmp_path, body)
    events = [row[0] for row in frame["__test_records"]]
    assert events[1]["low_limit"] == 0 and events[1]["high_limit"] == 2
    assert events[1]["unit"] == "V"
    assert events[2]["low_limit"] is None and events[2]["high_limit"] is None


def test_explicit_null_unit_does_not_inherit(tmp_path):
    body = part(tests=ptr(optional=specification()))
    body += part(name="P2", tests=ptr(optional=specification("\x00")))
    (frame, _), _, _ = convert(tmp_path, body)
    assert frame["__test_records"][1][0]["unit"] is None


def test_definition_before_first_chip_not_counted_as_result(tmp_path):
    body = ptr(flag=0x10, optional=specification()) + part(tests=ptr())
    (frame, meta), _, _ = convert(tmp_path, body)
    assert len(frame) == 1 and frame["__part_index"].tolist() == [1]
    assert len(frame["__test_records"][0]) == 1
    assert frame["__test_records"][0][0]["unit"] == "V"
    assert meta["stdf_metadata"]["record_counts"]["PTR"] == 2


def test_missing_first_spec_not_guessed_and_invalid_result_retained(tmp_path):
    (frame, meta), _, _ = convert(tmp_path, part(tests=ptr(flag=2)))
    event = frame["__test_records"][0][0]
    assert "unit" not in event or event["unit"] is None
    assert "low_limit" not in event or event["low_limit"] is None
    assert event["result"] == 1.25 and not event["result_valid"]
    assert meta["stdf_metadata"]["mir"] == {}


def test_late_test_column_and_repeated_execution_preserved(tmp_path):
    body = part(tests=ptr() + ptr(1.75))
    body += part(name="P2", tests=ptr(number=2002, name="I"))
    (frame, _), _, _ = convert(tmp_path, body)
    assert "2002_I" in frame and pd.isna(frame["2002_I"][0])
    assert frame["1001_VDD"][0] == 1.75
    assert [e["result"] for e in frame["__test_records"][0]] == [1.25, 1.75]


def test_multisite_inherits_same_test_definition(tmp_path):
    body = part(tests=ptr(optional=specification())) + part(site=3, tests=ptr(site=3))
    (frame, _), _, _ = convert(tmp_path, body)
    assert frame["SITE"].tolist() == [0, 3]
    assert frame["__test_records"][1][0]["unit"] == "V"


def test_mpr_packed_states_and_unit_position(tmp_path):
    payload = struct.pack("<IBBBBHH", 2002, 1, 0, 0, 0, 3, 2)
    payload += b"\x21\x03" + struct.pack("<ff", 0.25, 0.5) + cn("MULTI") + cn("")
    payload += struct.pack("<BbbbffffHHH", 2, 0, 0, 0, 0, 1, 0, 0.1, 1, 2, 3) + cn("A")
    body = part(tests=record(15, 15, payload))
    (frame, _), _, _ = convert(tmp_path, body)
    assert frame["2002_MULTI_0"][0] == 0.25 and frame["2002_MULTI_1"][0] == 0.5
    assert frame["__test_records"][0][0]["unit"] == "A"


def test_mpr_late_name_keeps_each_result_column_stable(tmp_path):
    def mpr(name):
        payload = struct.pack("<IBBBBHH", 2002, 1, 0, 0, 0, 0, 2)
        return record(15, 15, payload + struct.pack("<ff", .25, .5) + cn(name) + cn(""))
    body = part(tests=mpr("")) + part(name="P2", tests=mpr("FULL_MULTI_NAME"))
    (frame, meta), _, _ = convert(tmp_path, body)
    assert {t["column"] for t in meta["test_catalog"]} == {"TEST_2002_0", "TEST_2002_1"}
    assert all(t["name"] == "FULL_MULTI_NAME" for t in meta["test_catalog"])
    for events in frame["__test_records"]:
        assert [e["name"] for e in events] == ["FULL_MULTI_NAME", "FULL_MULTI_NAME"]
    assert frame[["TEST_2002_0", "TEST_2002_1"]].notna().all().all()


def test_big_endian(tmp_path):
    (frame, _), _, _ = convert(tmp_path, part(endian=">", tests=ptr(
        endian=">", optional=specification(endian=">"))), endian=">")
    assert frame["1001_VDD"][0] == 1.25
    assert frame["__test_records"][0][0]["high_limit"] == 2


def test_matches_existing_reference_parser_values_and_bins(tmp_path):
    from tests.test_compute.test_stdf_assetization import _minimal_stdf_bytes
    source = tmp_path / "reference.stdf"
    data = _minimal_stdf_bytes()
    source.write_bytes(data)
    legacy = StdfToCsvConverter(source, tmp_path)
    legacy.iter_records(io.BytesIO(data))
    output = convert_stdf_to_wide_all(source, tmp_path / "wide_all.parquet")
    frame, meta = read_release_wide_all(output)
    for column, value in legacy.wide_rows[0].items():
        if column in frame:
            assert frame[column][0] == value
    assert meta["stdf_metadata"]["mir"]["job_revision"] == "A"


@pytest.mark.parametrize("body", [b"junk", record(0, 10, b"\2\4") + b"\x01",
                                   record(0, 10, b"\2\4") + ptr(),
                                   record(0, 10, b"\2\4") + record(5, 10, b"\1\0")])
def test_bad_or_unfinished_input_does_not_save(tmp_path, body):
    source, output = tmp_path / "bad.stdf", tmp_path / "out.parquet"
    source.write_bytes(body)
    with pytest.raises(ValueError):
        convert_stdf_to_wide_all(source, output)
    assert not output.exists()


def test_does_not_overwrite_output_or_original(tmp_path):
    (_, _), source, destination = convert(tmp_path, part(tests=ptr()))
    original, saved = source.read_bytes(), destination.read_bytes()
    with pytest.raises(FileExistsError):
        convert_stdf_to_wide_all(source, destination)
    with pytest.raises(FileExistsError):
        convert_stdf_to_wide_all(source, source)
    assert source.read_bytes() == original and destination.read_bytes() == saved


def test_rejects_existing_plain_parquet(tmp_path):
    path = tmp_path / "plain.parquet"
    pd.DataFrame({"a": [1]}).to_parquet(path)
    with pytest.raises(ValueError, match="缺少内置元数据"):
        read_release_wide_all(path)


def test_atr_and_omitted_prr_tail_are_legal(tmp_path):
    prr = struct.pack("<BBBHHHhhI", 1, 0, 0, 1, 1, 1, 10, 20, 100)
    body = record(0, 20, struct.pack("<I", 10) + cn("audit"))
    body += record(5, 10, b"\1\0") + ptr() + record(5, 20, prr)
    (frame, meta), _, _ = convert(tmp_path, body)
    assert len(frame) == 1 and frame["SBIN"][0] == 1
    assert meta["stdf_metadata"]["record_counts"]["ATR"] == 1


def test_first_missing_name_keeps_stable_column(tmp_path):
    body = part(tests=ptr(name="")) + part(name="P2", tests=ptr(name="new name"))
    (frame, _), _, _ = convert(tmp_path, body)
    assert frame["TEST_1001"].tolist() == [1.25, 1.25]
    assert "1001_new_name" not in frame


def test_nonfinite_spec_is_not_used_as_valid_limit(tmp_path):
    body = part(tests=ptr(optional=specification(low=float("nan"))))
    (frame, _), _, _ = convert(tmp_path, body)
    assert frame["__test_records"][0][0]["low_limit"] is None


def test_ftr_packed_arrays_keep_correct_test_name_and_outcome(tmp_path):
    payload = struct.pack("<IBBBB", 3001, 1, 0, 0x80, 0)
    payload += struct.pack("<IIIIiihHH", 1, 1, 1, 1, 1, 1, 0, 3, 1)
    payload += struct.pack("<HHH", 1, 2, 3) + b"\x21\x03"
    payload += struct.pack("<H", 1) + b"\x01" + struct.pack("<H", 0)
    payload += b"".join(cn(s) for s in ["vector", "time", "op", "FUNC", "", "", ""])
    (frame, _), _, _ = convert(tmp_path, part(tests=record(15, 20, payload)))
    assert frame["3001_FUNC"][0] == 0
    assert frame["__test_records"][0][0]["record_type"] == "FTR"


@pytest.mark.parametrize("flag,expected", [(0, "PASS"), (8, "FAIL"), (16, "UNKNOWN"), (4, "ABORT")])
def test_minimal_prr_and_mrr_allow_absent_optional_fields(tmp_path, flag, expected):
    body = record(5, 10, b"\1\0") + ptr()
    body += record(5, 20, struct.pack("<BBBHH", 1, 0, flag, 1, 1))
    body += record(1, 20, struct.pack("<I", 100))
    (frame, meta), _, _ = convert(tmp_path, body)
    assert frame["PASS_FAIL"][0] == expected
    assert frame["SBIN"][0] is None
    assert frame["DIE_X"][0] is None
    assert "SBIN" in frame["__missing_fields"][0]
    assert meta["stdf_metadata"]["mrr"]["finish_time"] == 100


def test_prr_sentinels_are_missing(tmp_path):
    payload = struct.pack("<BBBHHHhhI", 1, 0, 0, 1, 1, 65535, -32768, -32768, 100)
    (frame, _), _, _ = convert(tmp_path, record(5, 20, payload))
    assert frame["SBIN"][0] is None and frame["DIE_X"][0] is None


@pytest.mark.parametrize("typ,sub,payload", [(1, 10, b""), (2, 10, b"\1"),
                                             (2, 20, b"\1\0"), (1, 20, b"\0")])
def test_missing_metadata_required_prefix_is_rejected(tmp_path, typ, sub, payload):
    source = tmp_path / "truncated.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + record(typ, sub, payload) + part(tests=ptr()))
    with pytest.raises(ValueError, match="必需字段不完整"):
        convert_stdf_to_wide_all(source, tmp_path / "wide_all.parquet")


def test_cli_help_in_windows_redirected_encoding():
    env = dict(os.environ, PYTHONIOENCODING="cp1252")
    result = subprocess.run([sys.executable, "-m", "deepyield.cli.release_stdf", "--help"],
                            capture_output=True, encoding="utf-8", env=env)
    assert result.returncode == 0, result.stderr
    assert "单文件 wide_all Parquet" in result.stdout


def test_cli_writes_only_one_parquet(tmp_path):
    source, output = tmp_path / "cli.stdf", tmp_path / "cli.parquet"
    source.write_bytes(record(0, 10, b"\2\4") + part(tests=ptr(optional=specification())))
    result = subprocess.run([sys.executable, "-m", "deepyield.cli.release_stdf",
                             str(source), str(output)], capture_output=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert len(read_release_wide_all(output)[0]) == 1
    assert {p.name for p in tmp_path.iterdir()} == {"cli.stdf", "cli.parquet"}
