import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import release_check_service, release_operations
from app.release_export_cache import ReportFile
from app.routers.release_check import router


def spooled_report(payload):
    directory = TemporaryDirectory()
    path = Path(directory.name) / 'release-check-report.json'
    path.write_bytes(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
    return ReportFile(directory, path, {'filename': 'release-check-report.json'},
                      path.stat().st_size, 'zh')


def test_report_language_does_not_translate_names_or_modify_saved_data(monkeypatch):
    saved = {'result': {'reason': '通过', 'status_label': '通过', 'warnings': ['单位缺失'],
                        'name': '通过', 'filename': '通过.stdf', 'remark': '通过',
                        'conditions': {'reason': '通过'}, 'state': '待复核'}}
    monkeypatch.setattr(release_check_service, 'export_report', lambda *_: spooled_report(saved))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        english = client.get('/api/release-check/datasets/file/runs/run/report?lang=en')
        chinese = client.get('/api/release-check/datasets/file/runs/run/report?lang=zh')
    assert english.headers['content-language'] == 'en'
    result = english.json()['result']
    assert result['reason'] == 'Pass' and result['state'] == 'Pending review'
    assert result['warnings'] == ['Missing unit']
    for key in ('name', 'filename', 'remark', 'conditions'):
        assert result[key] == saved['result'][key]
    assert chinese.json() == saved and saved['result']['reason'] == '通过'


def test_progress_and_errors_use_request_language_without_leaking_between_requests(monkeypatch):
    monkeypatch.setattr(release_operations, 'status', lambda _: {'phase': '写入 CPK Excel：3 / 10'})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        def fetch(locale):
            return client.get('/api/release-check/jobs/test?lang=' + locale).json()['phase']
        with ThreadPoolExecutor(max_workers=2) as workers:
            results = list(workers.map(fetch, ['zh', 'en', 'zh', 'en']))
        assert results == ['写入 CPK Excel：3 / 10', 'Writing CPK Excel: 3 / 10'] * 2
        english = client.get('/api/release-check/datasets/missing/runs/missing/items/missing?lang=en')
        chinese = client.get('/api/release-check/datasets/missing/runs/missing/items/missing')
    assert english.status_code == chinese.status_code == 404
    assert english.json()['detail'] == 'The file is no longer in this session. Import it again.'
    assert chinese.json()['detail'] == '文件已不在当前会话，请重新导入'
