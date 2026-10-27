"""Discovery helpers for the native STDF parser executable."""

from __future__ import annotations

from pathlib import Path


def stdf_parser_candidates(repo_root: Path | None = None) -> tuple[Path, ...]:
    """Return supported native-parser locations in runtime preference order."""

    root = (repo_root or Path(__file__).resolve().parents[2]).resolve()
    return (
        root / "build" / "stdf_parser_msvc_ninja" / "stdf2parquet.exe",
        root / "cpp" / "stdf_parser" / "build_release" / "stdf2parquet.exe",
        root / "cpp" / "stdf_parser" / "build" / "stdf2parquet.exe",
    )
