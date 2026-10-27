"""Minimal STDF fixture; no asset service dependency."""
import struct

def _cn(value: str) -> bytes:
    raw = value.encode("latin1")
    return bytes([len(raw)]) + raw

def _record(record_type: int, record_subtype: int, payload: bytes) -> bytes:
    return struct.pack("<HBB", len(payload), record_type, record_subtype) + payload

def _u1(value: int) -> bytes:
    return struct.pack("<B", value)

def _u2(value: int) -> bytes:
    return struct.pack("<H", value)

def _i2(value: int) -> bytes:
    return struct.pack("<h", value)

def _u4(value: int) -> bytes:
    return struct.pack("<I", value)

def _r4(value: float) -> bytes:
    return struct.pack("<f", value)

def _minimal_stdf_bytes() -> bytes:
    records: list[bytes] = []
    records.append(_record(0, 10, _u1(2) + _u1(4)))
    records.append(
        _record(
            1,
            10,
            _u4(10)
            + _u4(20)
            + _u1(1)
            + b"P"
            + b" "
            + b" "
            + _u2(0)
            + b" "
            + _cn("LOT_A")
            + _cn("PART_TYPE")
            + _cn("NODE")
            + _cn("ATE")
            + _cn("JOB")
            + _cn("A")
            + _cn("")
            + _cn("OP")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn("")
            + _cn(""),
        )
    )

    records.append(_record(2, 10, _u1(1) + _u1(1) + _u4(30) + _cn("W1")))
    records.append(_record(5, 10, _u1(1) + _u1(0)))
    for test_num, value, name in [
        (7001, 101.0, "7001"),
        (7002, 1.0, "7002"),
        (7003, 0.0, "7003"),
        (7004, 2.0, "7004"),
        (7005, 3.0, "7005"),
        (1001, 1.25, "TEST_A"),
    ]:
        records.append(
            _record(
                15,
                10,
                _u4(test_num)
                + _u1(1)
                + _u1(0)
                + _u1(0)
                + _u1(0)
                + _r4(value)
                + _cn(name)
                + _cn("")
                + _u1(0)
                + struct.pack("<bbb", 0, 0, 0)
                + _r4(0.0)
                + _r4(10.0)
                + _cn("V"),
            )
        )
    records.append(
        _record(
            5,
            20,
            _u1(1)
            + _u1(0)
            + _u1(0)
            + _u2(6)
            + _u2(1)
            + _u2(1)
            + _i2(10)
            + _i2(20)
            + _u4(100)
            + _cn("P001")
            + _cn("TXT")
            + _u1(0),
        )
    )
    records.append(_record(2, 20, _u1(1) + _u1(1) + _u4(40) + _u4(1) + _u4(0) + _u4(0) + _u4(1) + _u4(1) + _cn("W1") + _cn("") + _cn("") + _cn("") + _cn("") + _cn("")))
    records.append(_record(1, 20, _u4(50) + b" " + _cn("") + _cn("")))
    return b"".join(records)
