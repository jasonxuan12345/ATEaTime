"""Calculation-only imports; full Parquet conversion is an explicit operation."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from deepyield.io.stdf_parser_executable import stdf_parser_candidates
from deepyield.compute.release_event_store import release_frame
from deepyield.compute.release_compact import DefinitionPool, calculation_frame, join_calculation_frames
from deepyield.compute.release_control import checkpoint, current_operation, event, stage

SCHEMA_VERSION = "deepyield-release-wide-all-v1"
CALCULATION_SCHEMA_VERSION = "deepyield-release-calculation-v1"
COMPUTE_SCHEMAS = {SCHEMA_VERSION, CALCULATION_SCHEMA_VERSION}
METADATA_KEY = b"deepyield.release"
NATIVE_STATS_PREFIX = "release_native "


def _forward_native_stats(errors) -> None:
    """Surface C++ phase timings (stderr markers) in the Release perf log."""
    try:
        errors.seek(0)
        for line in errors.read().decode("utf-8", errors="replace").splitlines():
            if not line.startswith(NATIVE_STATS_PREFIX):
                continue
            try:
                stats = json.loads(line[len(NATIVE_STATS_PREFIX):])
            except json.JSONDecodeError:
                continue
            if isinstance(stats, dict):
                event('native_stats', **{key: value for key, value in stats.items()
                                         if key != 'event'})
    except (OSError, ValueError):
        pass


def _metadata(table: pa.Table, expected=SCHEMA_VERSION) -> dict[str, Any]:
    encoded = (table.schema.metadata or {}).get(METADATA_KEY)
    if encoded is None:
        raise ValueError("不是 Release wide_all 文件：缺少内置元数据")
    metadata = json.loads(encoded)
    if metadata.get("schema_version") != expected:
        raise ValueError("不支持的 Release wide_all 版本")
    return metadata


@contextmanager
def _native_batches(source: Path, *, calculation_only=False):
    executable = next((p for p in stdf_parser_candidates() if p.is_file()), None)
    if executable is None:
        raise ValueError("缺少 C++ STDF 解析器，请先构建 stdf2parquet；Release 不使用 Python 解析回退")
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join((str(executable.parent), str(Path(pa.__file__).parent),
                                  str(Path(sys.prefix) / "Library" / "bin"), env.get("PATH", "")))
    # Spool IPC into a temporary file instead of retaining a complete stdout
    # bytes object and pipe buffers in addition to Arrow. Parquet stays unchanged.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        command = [str(executable), "--release-calculation-ipc" if calculation_only else "--release-ipc", str(source)]
        # The child only exits after the whole stream is written, so this wall
        # time is the C++ parse plus Arrow build; batch conversion follows below.
        with stage('native', 'C++ 解析'):
            try:
                if current_operation() is None:
                    result = subprocess.run(command, stdout=output, stderr=errors,
                                            timeout=600, env=env, check=False)
                else:
                    with subprocess.Popen(command, stdout=output, stderr=errors, env=env) as process:
                        deadline = time.monotonic() + 600
                        try:
                            while process.poll() is None:
                                checkpoint()
                                if time.monotonic() >= deadline:
                                    raise subprocess.TimeoutExpired(command, 600)
                                try:
                                    process.wait(timeout=.05)
                                except subprocess.TimeoutExpired:
                                    pass
                        except BaseException:
                            if process.poll() is None:
                                process.kill()
                            process.wait()
                            raise
                        result = subprocess.CompletedProcess(command, process.returncode)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("C++ Release STDF 解析超时") from exc
            except OSError as exc:
                raise ValueError("C++ STDF 解析器无法启动，请检查构建和运行依赖") from exc
        checkpoint()
        if result.returncode:
            errors.seek(0)
            message = (result.stderr or errors.read()).decode("utf-8", errors="replace").strip()
            if result.returncode == 2:
                message = "C++ 解析器版本不支持 Release，请重新构建 stdf2parquet"
            raise ValueError(message or "C++ Release STDF 解析失败")
        _forward_native_stats(errors)
        try:
            output.seek(0)
            reader = pa.ipc.open_stream(pa.BufferReader(result.stdout) if result.stdout is not None else output)
            _metadata(reader, CALCULATION_SCHEMA_VERSION if calculation_only else SCHEMA_VERSION)
        except (pa.ArrowException, ValueError) as exc:
            raise ValueError("C++ 解析器未返回有效 Release Arrow 数据，请重新构建 stdf2parquet") from exc
        with reader:
            yield reader


def _native_table(source: Path) -> pa.Table:
    """Compatibility for callers explicitly requesting a complete Arrow table."""
    with _native_batches(source) as reader:
        return reader.read_all()


def _paths(source: str | Path, destination: str | Path) -> tuple[Path, Path]:
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if not source.is_file():
        raise ValueError(f"找不到 STDF 文件：{source}")
    if destination.exists():
        raise FileExistsError(f"目标文件已存在：{destination}")
    if destination.suffix.lower() != ".parquet":
        raise ValueError("输出文件必须以 .parquet 结尾")
    return source, destination


def _save(table, destination: Path, on_batch=None) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryFile(dir=destination.parent) as temporary:
        # Keep native batch boundaries as Parquet row groups. Otherwise a later
        # read can rebuild a >2 GiB nested string array from a giant row group.
        with pq.ParquetWriter(temporary, table.schema, compression="zstd") as writer:
            batches = table.to_batches() if isinstance(table, pa.Table) else table
            for batch in batches:
                checkpoint()
                writer.write_batch(batch)
                if on_batch is not None:
                    on_batch(batch)
        temporary.seek(0)
        with destination.open("xb") as output:
            while chunk := temporary.read(1024 * 1024):
                checkpoint()
                output.write(chunk)


def import_release_stdf(source: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read bounded calculation batches without writing any Parquet file."""
    source = Path(source).resolve()
    if not source.is_file():
        raise ValueError(f"找不到 STDF 文件：{source}")
    with stage('parse', '解析 STDF'):
        with _native_batches(source, calculation_only=True) as reader:
            metadata = _metadata(reader, CALCULATION_SCHEMA_VERSION)
            frames = []
            definitions = DefinitionPool()
            with stage('prepare', '准备计算数据'):
                for batch in reader:
                    checkpoint()
                    frames.append(calculation_frame(batch, metadata, definitions))
    with stage('events', '整理计算数据'):
        frame = join_calculation_frames(frames, definitions)
    return frame, metadata


def convert_stdf_to_wide_all(source: str | Path, destination: str | Path) -> Path:
    """Convert STDF / gzip / bzip2 using C++; preserve all attempts and effective specs."""
    source, destination = _paths(source, destination)
    with _native_batches(source) as reader:
        _save(reader, destination)
    return destination


def read_release_wide_all(path: str | Path) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Read an existing standalone Release Parquet (not used during upload)."""
    table = pq.read_table(path)
    return release_frame(table), _metadata(table)
