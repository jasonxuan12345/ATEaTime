"""STDF v4 conversion helpers for DeepYield assetization.

The parser in this module is a reference implementation used by tests and as a
fallback when an external high-performance parser is not configured. It focuses
on the STDF records needed to build DeepYield's canonical wide CSV plus a
traceability long table.
"""

from __future__ import annotations

import bz2
import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct
import time
from typing import Any, BinaryIO


RECORD_NAMES: dict[tuple[int, int], str] = {
    (0, 10): "FAR",
    (0, 20): "ATR",
    (1, 10): "MIR",
    (1, 20): "MRR",
    (2, 10): "WIR",
    (2, 20): "WRR",
    (5, 10): "PIR",
    (5, 20): "PRR",
    (15, 10): "PTR",
    (15, 15): "MPR",
    (15, 20): "FTR",
}

WIDE_REQUIRED_COLUMNS = [
    "Name",
    "LOT_ID",
    "WAFER_ID",
    "SITE",
    "DIE_X",
    "DIE_Y",
    "HBIN",
    "SBIN",
    "STDF_PART_ID",
    "PART_FLAG",
    "PASS_FAIL",
    "SOURCE_FILE",
]

LONG_COLUMNS = [
    "SOURCE_FILE",
    "LOT_ID",
    "WAFER_ID",
    "PART_ID",
    "PART_INDEX",
    "SITE",
    "DIE_X",
    "DIE_Y",
    "RECORD_TYPE",
    "TEST_NUM",
    "TEST_NAME",
    "RESULT_INDEX",
    "RESULT",
    "LOW_LIMIT",
    "HIGH_LIMIT",
    "UNITS",
    "TEST_FLAG",
    "PARAM_FLAG",
    "PASS_FAIL",
]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_column_name(value: object) -> str:
    text = str(value or "").strip()
    text = re.sub(r"[^A-Za-z0-9_]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("_")
    return text or "UNKNOWN"


def test_column_name(test_num: object, test_text: object | None = None, *, result_index: int | None = None) -> str:
    base = f"TEST_{test_num}"
    text = safe_column_name(test_text)
    if text and text != "UNKNOWN":
        base = f"{test_num}_{text}"
    if result_index is not None:
        return f"{base}_{result_index}"
    return base


def pass_fail_from_part_flag(part_flag: int | None) -> str:
    if part_flag is None:
        return ""
    return "FAIL" if int(part_flag) & 0b00001000 else "PASS"


class StdfRecordReader:
    """Typed reader for one STDF record payload."""

    def __init__(self, payload: bytes, endian: str = "<") -> None:
        self.payload = payload
        self.offset = 0
        self.endian = endian

    @property
    def remaining(self) -> int:
        return max(len(self.payload) - self.offset, 0)

    def _read(self, size: int) -> bytes | None:
        if size < 0 or self.offset + size > len(self.payload):
            self.offset = len(self.payload)
            return None
        value = self.payload[self.offset : self.offset + size]
        self.offset += size
        return value

    def u1(self) -> int | None:
        raw = self._read(1)
        return None if raw is None else raw[0]

    def i1(self) -> int | None:
        raw = self._read(1)
        return None if raw is None else struct.unpack("b", raw)[0]

    def u2(self) -> int | None:
        raw = self._read(2)
        return None if raw is None else struct.unpack(self.endian + "H", raw)[0]

    def i2(self) -> int | None:
        raw = self._read(2)
        return None if raw is None else struct.unpack(self.endian + "h", raw)[0]

    def u4(self) -> int | None:
        raw = self._read(4)
        return None if raw is None else struct.unpack(self.endian + "I", raw)[0]

    def i4(self) -> int | None:
        raw = self._read(4)
        return None if raw is None else struct.unpack(self.endian + "i", raw)[0]

    def r4(self) -> float | None:
        raw = self._read(4)
        return None if raw is None else struct.unpack(self.endian + "f", raw)[0]

    def c1(self) -> str:
        raw = self._read(1)
        return "" if raw is None else raw.decode("latin1", errors="replace")

    def cn(self) -> str:
        length = self.u1()
        if length is None or length <= 0:
            return ""
        raw = self._read(length)
        return "" if raw is None else raw.decode("latin1", errors="replace").strip("\x00")

    def bn(self) -> bytes:
        length = self.u1()
        if length is None or length <= 0:
            return b""
        return self._read(length) or b""

    def dn(self) -> bytes:
        bit_count = self.u2()
        if bit_count is None or bit_count <= 0:
            return b""
        byte_count = (bit_count + 7) // 8
        return self._read(byte_count) or b""

    def skip(self, size: int) -> None:
        self._read(size)


@dataclass
class PartContext:
    head_num: int | None
    site_num: int | None
    part_index: int
    lot_id: str = ""
    wafer_id: str = ""
    source_file: str = ""
    values: dict[str, Any] = field(default_factory=dict)
    test_long_rows: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StdfConversionResult:
    source_file: str
    output_dir: str
    wide_csv_path: str
    long_csv_path: str
    metadata_path: str
    manifest_path: str
    row_count_wide: int
    row_count_long: int
    record_counts: dict[str, int]
    warnings: list[str]


class StdfToCsvConverter:
    """Convert one STDF stream into DeepYield-compatible intermediate files."""

    def __init__(self, source_path: Path, output_dir: Path, *, endian: str = "<") -> None:
        self.source_path = source_path
        self.output_dir = output_dir
        self.endian = endian
        self.record_counts: dict[str, int] = {}
        self.metadata: dict[str, Any] = {
            "source_file": str(source_path),
            "mir": {},
            "wafers": [],
            "wrr": [],
            "mrr": {},
            "record_counts": self.record_counts,
            "warnings": [],
        }
        self.current_lot_id = ""
        self.current_wafer_id = ""
        self.part_counter = 0
        self.part_contexts: dict[tuple[int | None, int | None], PartContext] = {}
        self.wide_rows: list[dict[str, Any]] = []
        self.long_rows: list[dict[str, Any]] = []
        self.dynamic_wide_columns: set[str] = set()

    def warning(self, message: str) -> None:
        self.metadata["warnings"].append(message)

    def _count(self, name: str) -> None:
        self.record_counts[name] = self.record_counts.get(name, 0) + 1

    def _context_key(self, head_num: int | None, site_num: int | None) -> tuple[int | None, int | None]:
        return (head_num, site_num)

    def _get_context(self, head_num: int | None, site_num: int | None) -> PartContext:
        key = self._context_key(head_num, site_num)
        context = self.part_contexts.get(key)
        if context is None:
            self.part_counter += 1
            context = PartContext(
                head_num=head_num,
                site_num=site_num,
                part_index=self.part_counter,
                lot_id=self.current_lot_id,
                wafer_id=self.current_wafer_id,
                source_file=str(self.source_path),
            )
            context.values.update(
                {
                    "LOT_ID": self.current_lot_id,
                    "WAFER_ID": self.current_wafer_id,
                    "SITE": site_num,
                    "SOURCE_FILE": str(self.source_path),
                }
            )
            self.part_contexts[key] = context
        return context

    def _parse_far(self, reader: StdfRecordReader) -> dict[str, Any]:
        cpu_type = reader.u1()
        stdf_version = reader.u1()
        self.metadata["far"] = {"cpu_type": cpu_type, "stdf_version": stdf_version}
        if cpu_type == 1:
            self.endian = ">"
        elif cpu_type == 2:
            self.endian = "<"
        return self.metadata["far"]

    def _parse_mir(self, reader: StdfRecordReader) -> dict[str, Any]:
        fields = {
            "setup_time": reader.u4(),
            "start_time": reader.u4(),
            "station_number": reader.u1(),
            "mode_code": reader.c1(),
            "retest_code": reader.c1(),
            "protection_code": reader.c1(),
            "burn_time": reader.u2(),
            "command_mode_code": reader.c1(),
            "lot_id": reader.cn(),
            "part_type": reader.cn(),
            "node_name": reader.cn(),
            "tester_type": reader.cn(),
            "job_name": reader.cn(),
            "job_revision": reader.cn(),
            "sublot_id": reader.cn(),
            "operator_name": reader.cn(),
            "exec_type": reader.cn(),
            "exec_version": reader.cn(),
            "test_code": reader.cn(),
            "test_temperature": reader.cn(),
            "user_text": reader.cn(),
            "aux_file": reader.cn(),
            "package_type": reader.cn(),
            "family_id": reader.cn(),
            "date_code": reader.cn(),
            "facility_id": reader.cn(),
            "floor_id": reader.cn(),
            "process_id": reader.cn(),
            "operation_frequency": reader.cn(),
            "spec_name": reader.cn(),
            "spec_version": reader.cn(),
            "flow_id": reader.cn(),
            "setup_id": reader.cn(),
            "design_revision": reader.cn(),
            "engineer_id": reader.cn(),
            "rom_code": reader.cn(),
            "serial_number": reader.cn(),
            "supervisor_name": reader.cn(),
        }
        self.current_lot_id = fields.get("lot_id") or ""
        self.metadata["mir"] = fields
        return fields

    def _parse_mrr(self, reader: StdfRecordReader) -> dict[str, Any]:
        fields = {
            "finish_time": reader.u4(),
            "disposition_code": reader.c1(),
            "user_description": reader.cn(),
            "exec_description": reader.cn(),
        }
        self.metadata["mrr"] = fields
        return fields

    def _parse_wir(self, reader: StdfRecordReader) -> dict[str, Any]:
        fields = {
            "head_number": reader.u1(),
            "site_group": reader.u1(),
            "start_time": reader.u4(),
            "wafer_id": reader.cn(),
        }
        self.current_wafer_id = fields.get("wafer_id") or self.current_wafer_id
        self.metadata["wafers"].append(fields)
        return fields

    def _parse_wrr(self, reader: StdfRecordReader) -> dict[str, Any]:
        fields = {
            "head_number": reader.u1(),
            "site_group": reader.u1(),
            "finish_time": reader.u4(),
            "part_count": reader.u4(),
            "retest_count": reader.u4(),
            "abort_count": reader.u4(),
            "good_count": reader.u4(),
            "functional_count": reader.u4(),
            "wafer_id": reader.cn(),
            "fab_wafer_id": reader.cn(),
            "frame_id": reader.cn(),
            "mask_id": reader.cn(),
            "user_description": reader.cn(),
            "exec_description": reader.cn(),
        }
        self.metadata["wrr"].append(fields)
        return fields

    def _parse_pir(self, reader: StdfRecordReader) -> dict[str, Any]:
        head_num = reader.u1()
        site_num = reader.u1()
        self.part_counter += 1
        context = PartContext(
            head_num=head_num,
            site_num=site_num,
            part_index=self.part_counter,
            lot_id=self.current_lot_id,
            wafer_id=self.current_wafer_id,
            source_file=str(self.source_path),
        )
        context.values.update(
            {
                "LOT_ID": self.current_lot_id,
                "WAFER_ID": self.current_wafer_id,
                "SITE": site_num,
                "SOURCE_FILE": str(self.source_path),
            }
        )
        self.part_contexts[self._context_key(head_num, site_num)] = context
        return {"head_number": head_num, "site_number": site_num}

    def _parse_ptr(self, reader: StdfRecordReader) -> dict[str, Any]:
        test_num = reader.u4()
        head_num = reader.u1()
        site_num = reader.u1()
        test_flag = reader.u1()
        param_flag = reader.u1()
        result = reader.r4()
        test_text = reader.cn()
        alarm_id = reader.cn()
        opt_flag = reader.u1() if reader.remaining else None
        low_limit = None
        high_limit = None
        units = ""
        if reader.remaining:
            reader.i1()
        if reader.remaining:
            reader.i1()
        if reader.remaining:
            reader.i1()
        if reader.remaining >= 4:
            low_limit = reader.r4()
        if reader.remaining >= 4:
            high_limit = reader.r4()
        if reader.remaining:
            units = reader.cn()
        context = self._get_context(head_num, site_num)
        column = test_column_name(test_num, test_text)
        context.values[column] = result
        self.dynamic_wide_columns.add(column)
        if str(test_num) in {"7001", "7002", "7003", "7004", "7005"}:
            context.values[str(test_num)] = result
            self.dynamic_wide_columns.add(str(test_num))
        row = self._long_row(
            context,
            "PTR",
            test_num=test_num,
            test_name=test_text,
            result_index=None,
            result=result,
            low_limit=low_limit,
            high_limit=high_limit,
            units=units,
            test_flag=test_flag,
            param_flag=param_flag,
            pass_fail="",
        )
        context.test_long_rows.append(row)
        return {"test_num": test_num, "test_text": test_text, "alarm_id": alarm_id, "opt_flag": opt_flag}

    def _parse_mpr(self, reader: StdfRecordReader) -> dict[str, Any]:
        test_num = reader.u4()
        head_num = reader.u1()
        site_num = reader.u1()
        test_flag = reader.u1()
        param_flag = reader.u1()
        rtn_count = reader.u2() or 0
        result_count = reader.u2() or 0
        for _ in range(rtn_count):
            reader.u1()
        results = [reader.r4() for _ in range(result_count)]
        test_text = reader.cn()
        alarm_id = reader.cn()
        low_limit = None
        high_limit = None
        units = ""
        if reader.remaining:
            reader.u1()
        if reader.remaining:
            reader.i1()
        if reader.remaining:
            reader.i1()
        if reader.remaining:
            reader.i1()
        if reader.remaining >= 4:
            low_limit = reader.r4()
        if reader.remaining >= 4:
            high_limit = reader.r4()
        if reader.remaining:
            units = reader.cn()
        context = self._get_context(head_num, site_num)
        for index, result in enumerate(results):
            column = test_column_name(test_num, test_text, result_index=index)
            context.values[column] = result
            self.dynamic_wide_columns.add(column)
            context.test_long_rows.append(
                self._long_row(
                    context,
                    "MPR",
                    test_num=test_num,
                    test_name=test_text,
                    result_index=index,
                    result=result,
                    low_limit=low_limit,
                    high_limit=high_limit,
                    units=units,
                    test_flag=test_flag,
                    param_flag=param_flag,
                    pass_fail="",
                )
            )
        return {"test_num": test_num, "test_text": test_text, "alarm_id": alarm_id, "result_count": result_count}

    def _parse_ftr(self, reader: StdfRecordReader) -> dict[str, Any]:
        test_num = reader.u4()
        head_num = reader.u1()
        site_num = reader.u1()
        test_flag = reader.u1()
        opt_flag = reader.u1()
        if reader.remaining >= 28:
            reader.skip(28)
        rtn_count = reader.u2() or 0 if reader.remaining >= 2 else 0
        pgm_count = reader.u2() or 0 if reader.remaining >= 2 else 0
        for _ in range(rtn_count):
            if reader.remaining >= 2:
                reader.u2()
        for _ in range(rtn_count):
            if reader.remaining:
                reader.u1()
        for _ in range(pgm_count):
            if reader.remaining >= 2:
                reader.u2()
        for _ in range(pgm_count):
            if reader.remaining:
                reader.u1()
        if reader.remaining:
            reader.dn()
        vector_name = reader.cn() if reader.remaining else ""
        time_set = reader.cn() if reader.remaining else ""
        op_code = reader.cn() if reader.remaining else ""
        test_text = reader.cn() if reader.remaining else ""
        alarm_id = reader.cn() if reader.remaining else ""
        program_text = reader.cn() if reader.remaining else ""
        result_text = reader.cn() if reader.remaining else ""
        pass_fail = "FAIL" if test_flag and int(test_flag) else "PASS"
        context = self._get_context(head_num, site_num)
        context.test_long_rows.append(
            self._long_row(
                context,
                "FTR",
                test_num=test_num,
                test_name=test_text,
                result_index=None,
                result=result_text,
                low_limit=None,
                high_limit=None,
                units="",
                test_flag=test_flag,
                param_flag=opt_flag,
                pass_fail=pass_fail,
            )
        )
        return {
            "test_num": test_num,
            "test_text": test_text,
            "alarm_id": alarm_id,
            "vector_name": vector_name,
            "time_set": time_set,
            "op_code": op_code,
            "program_text": program_text,
        }

    def _parse_prr(self, reader: StdfRecordReader) -> dict[str, Any]:
        head_num = reader.u1()
        site_num = reader.u1()
        part_flag = reader.u1()
        num_test = reader.u2()
        hard_bin = reader.u2()
        soft_bin = reader.u2()
        x_coord = reader.i2()
        y_coord = reader.i2()
        test_time = reader.u4()
        part_id = reader.cn()
        part_text = reader.cn()
        part_fix = reader.bn()
        context = self._get_context(head_num, site_num)
        pass_fail = pass_fail_from_part_flag(part_flag)
        name = part_id or f"{self.current_lot_id}_{self.current_wafer_id}_{x_coord}_{y_coord}_{site_num}_{context.part_index}"
        context.values.update(
            {
                "Name": name,
                "LOT_ID": context.lot_id or self.current_lot_id,
                "WAFER_ID": context.wafer_id or self.current_wafer_id,
                "SITE": site_num,
                "DIE_X": x_coord,
                "DIE_Y": y_coord,
                "HBIN": hard_bin,
                "SBIN": soft_bin,
                "STDF_PART_ID": part_id,
                "PART_FLAG": part_flag,
                "PASS_FAIL": pass_fail,
                "SOURCE_FILE": str(self.source_path),
                "PART_TXT": part_text,
                "TEST_TIME": test_time,
            }
        )
        self.dynamic_wide_columns.update({"PART_TXT", "TEST_TIME"})
        self.wide_rows.append(dict(context.values))
        for row in context.test_long_rows:
            row["PART_ID"] = part_id or row.get("PART_ID", "")
            row["DIE_X"] = x_coord
            row["DIE_Y"] = y_coord
            row["PASS_FAIL"] = row.get("PASS_FAIL") or pass_fail
            self.long_rows.append(row)
        self.part_contexts.pop(self._context_key(head_num, site_num), None)
        return {
            "head_number": head_num,
            "site_number": site_num,
            "part_flag": part_flag,
            "num_test": num_test,
            "hard_bin": hard_bin,
            "soft_bin": soft_bin,
            "x_coord": x_coord,
            "y_coord": y_coord,
            "part_id": part_id,
            "part_fix": part_fix.hex(),
        }

    def _long_row(
        self,
        context: PartContext,
        record_type: str,
        *,
        test_num: Any,
        test_name: Any,
        result_index: int | None,
        result: Any,
        low_limit: Any,
        high_limit: Any,
        units: str,
        test_flag: Any,
        param_flag: Any,
        pass_fail: str,
    ) -> dict[str, Any]:
        return {
            "SOURCE_FILE": str(self.source_path),
            "LOT_ID": context.lot_id,
            "WAFER_ID": context.wafer_id,
            "PART_ID": context.values.get("PART_ID", ""),
            "PART_INDEX": context.part_index,
            "SITE": context.site_num,
            "DIE_X": context.values.get("DIE_X", ""),
            "DIE_Y": context.values.get("DIE_Y", ""),
            "RECORD_TYPE": record_type,
            "TEST_NUM": test_num,
            "TEST_NAME": test_name,
            "RESULT_INDEX": "" if result_index is None else result_index,
            "RESULT": result,
            "LOW_LIMIT": low_limit,
            "HIGH_LIMIT": high_limit,
            "UNITS": units,
            "TEST_FLAG": test_flag,
            "PARAM_FLAG": param_flag,
            "PASS_FAIL": pass_fail,
        }

    def parse_record(self, record_type: int, record_subtype: int, payload: bytes) -> None:
        name = RECORD_NAMES.get((record_type, record_subtype), f"{record_type}:{record_subtype}")
        self._count(name)
        reader = StdfRecordReader(payload, self.endian)
        try:
            if name == "FAR":
                self._parse_far(reader)
            elif name == "MIR":
                self._parse_mir(reader)
            elif name == "MRR":
                self._parse_mrr(reader)
            elif name == "WIR":
                self._parse_wir(reader)
            elif name == "WRR":
                self._parse_wrr(reader)
            elif name == "PIR":
                self._parse_pir(reader)
            elif name == "PTR":
                self._parse_ptr(reader)
            elif name == "MPR":
                self._parse_mpr(reader)
            elif name == "FTR":
                self._parse_ftr(reader)
            elif name == "PRR":
                self._parse_prr(reader)
        except Exception as exc:
            self.warning(f"Failed to parse {name} record: {exc}")

    def iter_records(self, handle: BinaryIO) -> None:
        while True:
            header = handle.read(4)
            if not header:
                break
            if len(header) != 4:
                raise ValueError("Truncated STDF record header.")
            record_length, record_type, record_subtype = struct.unpack(self.endian + "HBB", header)
            payload = handle.read(record_length)
            if len(payload) != record_length:
                raise ValueError("Truncated STDF record payload.")
            self.parse_record(record_type, record_subtype, payload)

    def write_outputs(self) -> StdfConversionResult:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        wide_csv_path = self.output_dir / "part_wide.csv"
        long_csv_path = self.output_dir / "test_long.csv"
        metadata_path = self.output_dir / "metadata.json"
        manifest_path = self.output_dir / "parse_manifest.json"

        dynamic_columns = sorted(column for column in self.dynamic_wide_columns if column not in WIDE_REQUIRED_COLUMNS)
        wide_columns = WIDE_REQUIRED_COLUMNS + dynamic_columns
        with wide_csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=wide_columns, extrasaction="ignore")
            writer.writeheader()
            for row in self.wide_rows:
                writer.writerow(row)

        with long_csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=LONG_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            for row in self.long_rows:
                writer.writerow(row)

        self.metadata["record_counts"] = self.record_counts
        metadata_path.write_text(json.dumps(self.metadata, indent=2, sort_keys=True), encoding="utf-8")

        source_hash = hashlib.sha256(self.source_path.read_bytes()).hexdigest()
        manifest = {
            "source_file": str(self.source_path),
            "source_size_bytes": self.source_path.stat().st_size,
            "source_sha256": source_hash,
            "parser_version": "deepyield-python-reference-1",
            "started_at": None,
            "finished_at": utc_now(),
            "status": "succeeded",
            "wide_csv_path": str(wide_csv_path),
            "long_csv_path": str(long_csv_path),
            "metadata_path": str(metadata_path),
            "row_count_wide": len(self.wide_rows),
            "row_count_long": len(self.long_rows),
            "error_message": None,
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return StdfConversionResult(
            source_file=str(self.source_path),
            output_dir=str(self.output_dir),
            wide_csv_path=str(wide_csv_path),
            long_csv_path=str(long_csv_path),
            metadata_path=str(metadata_path),
            manifest_path=str(manifest_path),
            row_count_wide=len(self.wide_rows),
            row_count_long=len(self.long_rows),
            record_counts=dict(self.record_counts),
            warnings=list(self.metadata["warnings"]),
        )


def parse_stdf_bz2_to_outputs(input_path: str | Path, output_dir: str | Path) -> StdfConversionResult:
    """Parse one STDF or STDF.bz2 file and write DeepYield intermediate files."""
    source_path = Path(input_path)
    if not source_path.is_file():
        raise ValueError(f"STDF file not found: {source_path}")
    out_dir = Path(output_dir)
    converter = StdfToCsvConverter(source_path, out_dir)
    started_at = utc_now()
    start = time.monotonic()
    try:
        if source_path.suffix.lower() == ".bz2":
            with bz2.open(source_path, "rb") as handle:
                converter.iter_records(handle)
        else:
            with source_path.open("rb") as handle:
                converter.iter_records(handle)
        result = converter.write_outputs()
        manifest_path = Path(result.manifest_path)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["started_at"] = started_at
        manifest["duration_total_seconds"] = round(time.monotonic() - start, 6)
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        return result
    except Exception as exc:
        out_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = out_dir / "parse_manifest.json"
        manifest = {
            "source_file": str(source_path),
            "source_size_bytes": source_path.stat().st_size if source_path.exists() else 0,
            "source_sha256": hashlib.sha256(source_path.read_bytes()).hexdigest() if source_path.exists() else "",
            "parser_version": "deepyield-python-reference-1",
            "started_at": started_at,
            "finished_at": utc_now(),
            "status": "failed",
            "wide_csv_path": str(out_dir / "part_wide.csv"),
            "long_csv_path": str(out_dir / "test_long.csv"),
            "metadata_path": str(out_dir / "metadata.json"),
            "row_count_wide": 0,
            "row_count_long": 0,
            "error_message": str(exc),
            "duration_total_seconds": round(time.monotonic() - start, 6),
        }
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        raise
