"""Start the independent local API and compiled UI, without Node.js."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parents[1]
CLIENT = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read_url(url):
    try:
        with CLIENT.open(url, timeout=2) as response:
            return response.read().decode('utf-8'), response.headers
    except (OSError, ValueError):
        return '', {}


def api_ready(base):
    body, _ = read_url(base + '/health')
    try:
        identity = json.loads(body)
        return (isinstance(identity, dict) and isinstance(identity.get('root'), str)
                and bool(identity['root']) and identity.get('application') == 'datalog-check'
                and Path(identity.get('root', '')).resolve() == ROOT.resolve())
    except (ValueError, TypeError):
        return False


def port_in_use(number):
    try:
        with socket.create_connection(('127.0.0.1', number), timeout=.5):
            return True
    except OSError:
        return False


def port(value):
    number = int(value)
    if not 1024 <= number <= 65535:
        raise argparse.ArgumentTypeError('端口必须在 1024–65535 之间')
    return number


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description='启动独立 Datalog Check')
    parser.add_argument('--port', '--backend-port', '-BackendPort', type=port, default=8012)
    parser.add_argument('--no-browser', '-NoBrowser', action='store_true')
    args = parser.parse_args()
    base = f'http://127.0.0.1:{args.port}'
    process = None
    try:
        if api_ready(base):
            print('复用本目录已运行的 Datalog Check。')
        else:
            if port_in_use(args.port):
                raise RuntimeError(f'端口 {args.port} 被其他服务占用，请使用 --port 指定其他端口。')
            if not (ROOT / 'frontend/dist/index.html').is_file():
                raise RuntimeError('缺少前端构建结果，请在 frontend 目录运行 npm ci 和 npm run build。')
            if not (ROOT / 'build/stdf_parser_msvc_ninja/stdf2parquet.exe').is_file():
                raise RuntimeError('缺少 C++ STDF 解析器，请按 README 从源码构建 stdf2parquet。')
            logs = ROOT / 'runtime/release-check'
            logs.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime('%Y%m%d-%H%M%S-%f')
            error = logs / (stamp + '.error.log')
            env = {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'NO_COLOR': '1'}
            # Prevent an inherited source-workspace PYTHONPATH from leaking in.
            env.pop('PYTHONPATH', None)
            flags = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
            with (logs / (stamp + '.log')).open('wb') as output, error.open('wb') as errors:
                process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'app.release_check_app:app',
                    '--app-dir', str(ROOT), '--host', '127.0.0.1', '--port', str(args.port)],
                    cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=output, stderr=errors,
                    creationflags=flags)
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline and process.poll() is None:
                if api_ready(base):
                    break
                time.sleep(.25)
            if not api_ready(base):
                detail = error.read_text(encoding='utf-8', errors='replace')[-2000:]
                raise RuntimeError(f'服务未能启动，请查看 {error}\n{detail}')
            print('Datalog Check 已启动。')
        print(f'页面：{base}/release-check\n关闭窗口后服务继续运行；使用 stop_release_check.bat 关闭服务。')
        if not args.no_browser:
            webbrowser.open(base + '/release-check')
        return 0
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        print(f'启动失败：{exc}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
