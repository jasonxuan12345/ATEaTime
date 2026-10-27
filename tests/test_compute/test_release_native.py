"""Native Release import has no Python fallback and no Parquet reload."""
import subprocess

import pytest

from deepyield.compute import release_stdf as release
from tests.test_compute.test_release_stdf import part, ptr, record, specification


def test_native_in_memory_import_does_not_read_saved_parquet(tmp_path, monkeypatch):
    source, output = tmp_path / "source.stdf", tmp_path / "release.parquet"
    source.write_bytes(record(0, 10, b"\2\4") + part(tests=ptr(optional=specification())))
    monkeypatch.setattr(release.pq, "read_table", lambda *a, **k: pytest.fail("Parquet reload"))
    monkeypatch.setattr(release.pq, "ParquetWriter", lambda *a, **k: pytest.fail("Parquet write during import"))
    frame, meta = release.import_release_stdf(source)
    assert not output.exists() and len(frame) == 1
    assert meta["parser"] == "deepyield-release-cpp-v1"
    assert frame["__test_records"][0][0]["unit"] == "V"
    assert meta['schema_version'] == release.CALCULATION_SCHEMA_VERSION
    assert meta['all_attempts_preserved'] is False


def test_calculation_ipc_matches_full_reports_and_keeps_invalid_last_execution(tmp_path):
    import struct
    from tests.test_compute.test_release_stdf import cn
    from tests.test_compute.test_release_batch import exact, reports
    from deepyield.compute.release_outliers import build_release_outlier_report
    source = tmp_path / 'repeated.stdf'
    body = record(0, 10, b'\2\4')
    for index in range(32):
        site = index % 2
        def mpr():
            payload = struct.pack('<IBBBBHH', 2001, 1, site, 0, 0, 0, 2)
            return record(15, 15, payload + struct.pack('<ff', 1., 1.5) + cn('MULTI') + cn(''))
        first = ptr(9., site=site, name='' if index == 0 else 'LATE_NAME',
                    optional=specification() if index == 0 else None)
        last = ptr(1. + index / 100, site=site, name='', flag=1 if index % 7 == 0 else 0)
        body += part(site=site, bin=2 if index % 9 == 0 else 1, tests=first + mpr() + last)
    source.write_bytes(body)
    with release._native_batches(source, calculation_only=True) as batches:
        table = batches.read_all()
    assert not {'PART_TXT', '__missing_fields', 'PASS_FAIL', 'SOURCE_FILE'} & set(table.column_names)
    assert table.column('__test_records').type.value_type.num_fields == 13
    frame, metadata = release.import_release_stdf(source)
    output = tmp_path / 'full.parquet'
    release.convert_stdf_to_wide_all(source, output)
    full, full_metadata = release.read_release_wide_all(output)
    assert all(len(events) == 4 for events in full['__test_records'])
    assert all(len(events) == 3 for events in frame['__test_records'])
    assert frame.iloc[0]['__test_records'][0]['test_flag'] == 1
    exact(reports(full, full_metadata), reports(frame, metadata))
    exact(build_release_outlier_report(full, full_metadata, minimum_samples_per_site=4),
          build_release_outlier_report(frame, metadata, minimum_samples_per_site=4))


def test_no_python_record_parser_is_used(tmp_path, monkeypatch):
    from deepyield.compute.stdf_parser import StdfToCsvConverter
    monkeypatch.setattr(StdfToCsvConverter, "iter_records",
                        lambda *a: pytest.fail("Python record parser used"))
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + part(tests=ptr()))
    frame, _ = release.import_release_stdf(source)
    assert len(frame) == 1


def test_missing_executable_fails_instead_of_falling_back(tmp_path, monkeypatch):
    monkeypatch.setattr(release, "stdf_parser_candidates", lambda: (tmp_path / "missing.exe",))
    source = tmp_path / "source.stdf"
    source.write_bytes(b"unused")
    with pytest.raises(ValueError, match="不使用 Python"):
        release.convert_stdf_to_wide_all(source, tmp_path / "release.parquet")
    assert not (tmp_path / "release.parquet").exists()


@pytest.mark.parametrize("failure", ["timeout", "start", "old", "invalid"])
def test_native_failure_is_clear_and_does_not_create_output(tmp_path, monkeypatch, failure):
    source = tmp_path / "source.stdf"
    source.write_bytes(b"unused")
    monkeypatch.setattr(release, "stdf_parser_candidates", lambda: (source,))
    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("parser", 600)
        if failure == "start":
            raise OSError("loader failed")
        return subprocess.CompletedProcess(args[0], 2 if failure == "old" else 0,
                                           stdout=b"invalid", stderr=b"")
    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(ValueError, match="超时|无法启动|重新构建"):
        release.convert_stdf_to_wide_all(source, tmp_path / "release.parquet")
    assert not (tmp_path / "release.parquet").exists()


def test_original_source_hash_is_preserved(tmp_path):
    import bz2
    import gzip
    import hashlib
    body = record(0, 10, b"\2\4") + part(tests=ptr())
    for suffix, data in ((".stdf", body), (".gz", gzip.compress(body)), (".bz2", bz2.compress(body))):
        source = tmp_path / ("source" + suffix)
        source.write_bytes(data)
        _, meta = release.import_release_stdf(source)
        assert meta["source_sha256"] == hashlib.sha256(data).hexdigest()


def test_unicode_source_path_and_latin1_measurement_name(tmp_path):
    source = tmp_path / "中文 source.stdf"
    # The existing decoder interprets STDF bytes as Latin-1, even under Windows.
    event = ptr(name="CAFE").replace(b"CAFE", b"caf\xe9")
    source.write_bytes(record(0, 10, b"\2\4") + part(tests=event))
    frame, meta = release.import_release_stdf(source)
    assert meta["test_catalog"][0]["name"] == "café"
    assert frame["__test_records"][0][0]["name"] == "café"


def test_explicit_nonfinite_limit_clears_inherited_limit(tmp_path):
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") +
        part(tests=ptr(optional=specification(low=1))) +
        part(tests=ptr(optional=specification(low=float("nan")))))
    frame, _ = release.import_release_stdf(source)
    assert frame["__test_records"][0][0]["low_limit"] == 1
    assert frame["__test_records"][1][0]["low_limit"] is None


@pytest.mark.parametrize("suffix", [".gz", ".bz2"])
def test_native_reads_every_compressed_member(tmp_path, suffix):
    import gzip
    import bz2
    compress = gzip.compress if suffix == ".gz" else bz2.compress
    source = tmp_path / ("source.stdf" + suffix)
    far = record(0, 10, b"\2\4")
    body = part(tests=ptr())
    source.write_bytes(compress(far + body) + compress(body))
    frame, _ = release.import_release_stdf(source)
    assert len(frame) == 2
    assert [events[0]['result'] for events in frame['__test_records']] == [1.25, 1.25]
    assert '1001_VDD' not in frame  # one calculation copy, no duplicate wide data


@pytest.mark.parametrize("suffix", [".gz", ".bz2"])
@pytest.mark.parametrize("cut", [1, 4, 8])
def test_native_rejects_truncated_compressed_footer(tmp_path, suffix, cut):
    import gzip
    import bz2
    compress = gzip.compress if suffix == ".gz" else bz2.compress
    source = tmp_path / ("source.stdf" + suffix)
    body = record(0, 10, b"\2\4") + part(tests=ptr())
    source.write_bytes(compress(body)[:-cut])
    output = tmp_path / "release.parquet"
    with pytest.raises(ValueError, match="Truncated compressed stream"):
        release.import_release_stdf(source)
    assert not output.exists()


def test_native_same_number_ftr_keeps_each_test_name(tmp_path):
    import struct
    from tests.test_compute.test_release_stdf import cn
    def ftr(name):
        payload = struct.pack("<IBBBB", 3001, 1, 0, 0, 0)
        payload += struct.pack("<IIIIiihHH", 1, 1, 1, 1, 1, 1, 0, 0, 0)
        payload += struct.pack("<H", 0)
        payload += b"".join(cn(s) for s in ["vector", "time", "op", name, "", "", ""])
        return record(15, 20, payload)
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") + part(tests=ftr("FUNC_A")) +
                       part(tests=ftr("FUNC_B")))
    frame, meta = release.import_release_stdf(source)
    assert [row[0]["name"] for row in frame["__test_records"]] == ["FUNC_A", "FUNC_B"]
    assert {test["column"]: test["name"] for test in meta["test_catalog"]} == {
        "3001_FUNC_A": "FUNC_A", "3001_FUNC_B": "FUNC_B"}


def test_native_part_metadata_strips_nul_but_unit_can_explicitly_clear(tmp_path):
    source = tmp_path / "source.stdf"
    source.write_bytes(record(0, 10, b"\2\4") +
                       part(name="\0P1\0", tests=ptr(optional=specification("V"))) +
                       part(name="\0P2\0", tests=ptr(optional=specification("\0"))))
    frame, _ = release.import_release_stdf(source)
    assert frame["Name"].tolist() == ["P1", "P2"]
    assert frame["STDF_PART_ID"].tolist() == ["P1", "P2"]
    assert frame["__test_records"][0][0]["unit"] == "V"
    assert frame["__test_records"][1][0]["unit"] is None
