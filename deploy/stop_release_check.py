"""Stop the verified local Release services, including the earlier preview host."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time

from start_release_check import ROOT, api_ready, port, port_in_use, read_url

QUERY = r"""
$ErrorActionPreference = 'Stop'
$items = foreach ($number in ($env:DEEPYIELD_STOP_PORTS -split ',')) {
  $connections = @(Get-NetTCPConnection -State Listen -ErrorAction Stop |
    Where-Object { $_.LocalPort -eq [int]$number -and $_.LocalAddress -in @('127.0.0.1','0.0.0.0','::') })
  foreach ($connection in $connections) {
    $item = Get-CimInstance Win32_Process -Filter "ProcessId=$($connection.OwningProcess)"
    if ($null -eq $item) { continue }
    [pscustomobject]@{port=[int]$number; pid=[int]$item.ProcessId; command=$item.CommandLine;
      executable=$item.ExecutablePath; created=$item.CreationDate.ToUniversalTime().Ticks.ToString()}
  }
}
ConvertTo-Json -InputObject @($items) -Compress
"""
STOP = r"""
$ErrorActionPreference = 'Stop'
$expected = $env:DEEPYIELD_STOP_PROCESS | ConvertFrom-Json
$item = Get-CimInstance Win32_Process -Filter "ProcessId=$($expected.pid)"
if ($null -eq $item) { exit 0 }
if ($item.CommandLine -ne $expected.command -or
    $item.CreationDate.ToUniversalTime().Ticks.ToString() -ne $expected.created) {
  throw 'Process identity changed; no process was stopped.'
}
Stop-Process -Id $item.ProcessId -ErrorAction Stop
"""


def powershell(script: str, extra_env: dict[str, str]) -> str:
    script = '[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)\n' + script
    result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
        env={**os.environ, **extra_env}, capture_output=True, text=True, encoding='utf-8',
        errors='replace', timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or '无法读取或关闭服务进程。')
    return result.stdout


def belongs_to_release(item: dict, kind: str, number: int, verified: bool) -> bool:
    command = item.get('command') or ''
    if not re.search(r'--port\s+' + str(number) + r'(?:\s|$)', command) and kind == 'frontend':
        return False
    if kind == 'frontend':
        return verified and bool(re.search(r'(?:^|[/\\])vite[/\\]bin[/\\]vite\.js(?:\s|"|$)', command))
    if Path(item.get('executable') or '').resolve() != Path(sys.executable).resolve():
        return False
    try:
        tokens = [token.strip('"') for token in shlex.split(command, posix=False)]
    except ValueError:
        return False
    if re.search(r'(?:^|\s)app\.release_check_app:app(?:\s|$)', command):
        if not re.search(r'--port\s+' + str(number) + r'(?:\s|$)', command):
            return False
        # Direct uvicorn launches use the working directory instead of --app-dir.
        # In that case /health must confirm this project's exact directory.
        if '--app-dir' not in tokens:
            return verified
        try:
            app_dir = Path(tokens[tokens.index('--app-dir') + 1]).resolve()
        except (ValueError, IndexError):
            return False
        return app_dir == ROOT.resolve()
    # Compatibility with this project's earlier standalone preview helper.
    return verified and any(
        Path(token).name == 'serve_api.py' and Path(token).is_absolute()
        and Path(token).resolve().is_relative_to(ROOT.resolve()) for token in tokens)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='关闭 Release 检查前后端（当前导入数据会清空）')
    parser.add_argument('--port', '--backend-port', '-BackendPort', type=port, default=8012)
    args = parser.parse_args()
    try:
        if os.name != 'nt':
            raise RuntimeError('此关闭入口仅支持 Windows。')
        items = json.loads(powershell(QUERY, {'DEEPYIELD_STOP_PORTS': str(args.port)}))
        checks = {'backend': api_ready(f'http://127.0.0.1:{args.port}')}
        targets = []
        # Verify both services before stopping either one.
        for kind, number in (('backend', args.port),):
            matching = {item['pid']: item for item in items if item['port'] == number}
            if not matching and port_in_use(number):
                raise RuntimeError(f'无法确认端口 {number} 的服务来源，未关闭。')
            for item in matching.values():
                if not belongs_to_release(item, kind, number, checks[kind]):
                    raise RuntimeError(f'端口 {number} 不属于本项目的独立 Release 服务，未关闭。')
                targets.append((kind, number, item))
        for kind, number, item in targets:
            powershell(STOP, {'DEEPYIELD_STOP_PROCESS': json.dumps(item)})
            deadline = time.monotonic() + 5
            while port_in_use(number) and time.monotonic() < deadline:
                time.sleep(.1)
            if port_in_use(number):
                raise RuntimeError(f'端口 {number} 仍在使用，请检查服务状态。')
            print(f'{"前端" if kind == "frontend" else "后端"}已关闭。')
        print('Release 服务已关闭。下次使用时双击 start_release_check.bat。')
        return 0
    except Exception as exc:
        print(f'关闭失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
