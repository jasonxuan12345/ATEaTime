"""Report requests register pollable jobs; missing DeviceID is descriptive only."""
from io import BytesIO
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from pypdf import PdfReader
import pytest

from app import release_operations as operations, release_excel_service, release_pdf_service
from app.routers.release_check import router
from deepyield.compute.release_control import report_progress, ReleaseCancelled
from tests.test_api.test_release_excel import sources, run_pair


@pytest.mark.parametrize('suffix,service,function', [
    ('cgm.xlsx', release_excel_service, 'export_run_xlsx'),
    ('cpk.xlsx', release_excel_service, 'export_run_xlsx'),
    ('cgm.pdf', release_pdf_service, 'export_run_cgm_pdf'),
    ('cpk.pdf', release_pdf_service, 'export_run_cpk_pdf')])
def test_download_jobs_publish_progress_and_are_cleaned_up(monkeypatch, suffix, service, function):
    def export(*args):
        report_progress('正在生成报表', 37.5)
        status = operations.status('export-job')
        assert status['progress'] == 37.5 and status['phase'] == '正在生成报表'
        report_progress('文件生成完成', 100)
        assert operations.status('export-job')['progress'] == 100
        return {'content': b'report', 'filename': suffix}
    monkeypatch.setattr(service, function, export)
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        response = client.get(f'/api/release-check/datasets/d/runs/r/reports/{suffix}?job_id=export-job')
        assert response.status_code == 200, response.text
        assert client.get('/api/release-check/jobs/export-job').status_code == 404


def test_cancel_stops_export_at_the_next_progress_update():
    with pytest.raises(ReleaseCancelled):
        with operations.track('cancel-export'):
            report_progress('生成第一页', 20)
            operations.cancel('cancel-export')
            report_progress('生成第二页', 50)
    with pytest.raises(KeyError):
        operations.status('cancel-export')


def test_real_cgm_excel_and_pdf_export_without_device_id(sources):
    _, source = sources
    source.metadata['stdf_metadata']['mir'].pop('part_type')
    result = run_pair()
    app = FastAPI(); app.include_router(router)
    with TestClient(app) as client:
        base = f'/api/release-check/datasets/a/runs/{result["id"]}/reports'
        excel = client.get(base + '/cgm.xlsx?job_id=excel-optional')
        assert excel.status_code == 200, excel.text
        assert '_Cgm_Report.xlsx' in unquote(excel.headers['Content-Disposition'])
        assert load_workbook(BytesIO(excel.content))['raw']['A4'].value == 1001
        pdf = client.get(base + '/cgm.pdf?job_id=pdf-optional')
        assert pdf.status_code == 200, pdf.text
        assert '_Cgm_Report.pdf' in unquote(pdf.headers['Content-Disposition'])
        assert 'Device ID:' not in PdfReader(BytesIO(pdf.content)).pages[0].extract_text()
