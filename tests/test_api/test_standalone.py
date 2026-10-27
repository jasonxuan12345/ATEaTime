"""Standalone identity, routes, and isolation from the original application."""
import json
import importlib
from pathlib import Path
import sys
import pytest
from fastapi.testclient import TestClient
from app.release_check_app import app, ROOT
from deploy import start_release_check as launcher


def test_same_service_serves_release_ui_api_and_assets():
    with TestClient(app) as client:
        identity = client.get('/health').json()
        assert identity == {'application': 'datalog-check', 'root': str(ROOT.resolve())}
        assert client.get('/').url.path == '/release-check'
        page = client.get('/release-check')
        assert page.status_code == 200 and 'Datalog Check' in page.text
        assert '/api/release-check/runs' in client.get('/openapi.json').json()['paths']
        for path in (ROOT / 'frontend/dist/assets').iterdir():
            assert client.get('/assets/' + path.name).status_code == 200
        assert client.get('/api/unknown').status_code == 404
        assert client.get('/data-hub').status_code == 404


def test_launcher_never_reuses_a_different_project(monkeypatch, tmp_path):
    monkeypatch.setattr(launcher, 'read_url', lambda url: (
        json.dumps({'application': 'datalog-check', 'root': str(tmp_path)}), {}))
    assert not launcher.api_ready('http://127.0.0.1:8012')
    monkeypatch.setattr(launcher, 'read_url', lambda url: (
        json.dumps({'application': 'datalog-check', 'root': str(ROOT)}), {}))
    assert launcher.api_ready('http://127.0.0.1:8012')


def test_core_modules_are_loaded_only_from_standalone_root():
    for name, module in list(sys.modules.items()):
        if name == 'app' or name == 'deepyield' or name.startswith(('app.', 'deepyield.')):
            file = getattr(module, '__file__', None)
            if file:
                assert Path(file).resolve().is_relative_to(ROOT.resolve()), (name, file)
    assert not any(name.startswith(('deepyield.query', 'deepyield.agent', 'app.services')) for name in sys.modules)


@pytest.mark.parametrize('body', ['[]', 'null', '{}', '{"application":"datalog-check"}', 'invalid'])
def test_foreign_health_documents_are_not_accepted(monkeypatch, body):
    monkeypatch.setattr(launcher, 'read_url', lambda url: (body, {}))
    assert not launcher.api_ready('http://127.0.0.1:8012')


@pytest.fixture
def stopper(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / 'deploy'))
    return importlib.import_module('stop_release_check')


def release_process(*extra_args):
    return {
        'executable': sys.executable,
        'command': f'"{sys.executable}" -m uvicorn app.release_check_app:app '
                   + ' '.join(extra_args) + ' --host 127.0.0.1 --port 8012',
    }


@pytest.mark.parametrize('verified', [True, False])
def test_stop_direct_launch_requires_matching_project_health(stopper, verified):
    assert stopper.belongs_to_release(release_process(), 'backend', 8012, verified) is verified


def test_stop_script_launch_uses_explicit_project_directory(stopper):
    item = release_process('--app-dir', f'"{ROOT}"')
    assert stopper.belongs_to_release(item, 'backend', 8012, False)


def test_stop_rejects_explicit_different_project_even_if_health_matches(stopper, tmp_path):
    item = release_process('--app-dir', f'"{tmp_path}"')
    assert not stopper.belongs_to_release(item, 'backend', 8012, True)


def test_stop_rejects_incomplete_directory_argument(stopper):
    item = release_process()
    item['command'] += ' --app-dir'
    assert not stopper.belongs_to_release(item, 'backend', 8012, True)


@pytest.mark.parametrize('change', ['port', 'application', 'executable'])
def test_stop_rejects_other_processes_even_if_health_matches(stopper, change):
    item = release_process()
    if change == 'port':
        item['command'] = item['command'].replace('--port 8012', '--port 8013')
    elif change == 'application':
        item['command'] = item['command'].replace('app.release_check_app:app', 'other.app:app')
    else:
        item['executable'] = str(ROOT / 'other-python.exe')
    assert not stopper.belongs_to_release(item, 'backend', 8012, True)
