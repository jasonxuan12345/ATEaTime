"""Convert one small release STDF to a single wide_all Parquet."""
from __future__ import annotations

import argparse
import sys

from deepyield.compute.release_stdf import convert_stdf_to_wide_all


def main() -> None:
    # Windows redirected consoles may default to a non-Chinese code page.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="STDF → 单文件 wide_all Parquet")
    parser.add_argument("source", help="原始 STDF、STDF.bz2 或 STDF.gz")
    parser.add_argument("destination", help="保存的 .parquet 文件（不能覆盖已有文件）")
    args = parser.parse_args()
    print(convert_stdf_to_wide_all(args.source, args.destination))


if __name__ == "__main__":
    main()
