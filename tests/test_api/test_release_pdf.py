"""PDF downloads preserve the CGM source, selection, run and CPK exemption."""
from io import BytesIO
from urllib.parse import unquote

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfReader
import pytest

from app import release_check_service as service
from app import release_chart_notes as notes
from app.release_pdf_service import export_run_cgm_pdf, export_run_cpk_pdf
from app.routers.release_check import router
from tests.test_api.test_release_excel import sources, run_pair, add_source


def test_cgm_http_uses_own_source_and_bin1_support(sources):
    result = run_pair(cgm={'sites': [1], 'part_indices': [1, 3, 5]})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        base = f'/api/release-check/datasets/a/runs/{result["id"]}/reports'
        response = client.get(base + '/cgm.pdf')
        assert response.status_code == 200, response.text
        assert response.headers['content-type'] == 'application/pdf'
        assert '_Device_b_Cgm_Report.pdf' in unquote(response.headers['content-disposition'])
        assert response.headers['cache-control'] == 'no-store'
        pdf = PdfReader(BytesIO(response.content))
        assert len(pdf.pages) == 1  # Cross-source Bin1 CPK exempts the low Cg.
        text = pdf.pages[0].extract_text()
        assert 'Active Sites: S1' in text and 'Runs per Site: 3' in text
        assert 'No Fail or Need to check items.' in text
        assert client.get(base.replace(result['id'], 'expired') + '/cgm.pdf').status_code == 404
        assert client.get(base + '/mmsa.pdf').status_code == 404


def test_nonexempt_samples_are_read_from_cgm_not_owner(sources):
    a, _ = sources
    result = run_pair()
    _, reports = a.runs[result['id']]
    reports['cgm']['items'][0]['supported_by_bin1_cpk'] = None
    output = export_run_cgm_pdf('a', result['id'])
    assert output['page_count'] == 2 and output['action_count'] == 1
    text = PdfReader(BytesIO(output['content'])).pages[1].extract_text()
    assert 'RESULT: FAIL' in text and 'Min/All' in text


def test_changed_source_rejects_export(sources):
    result = run_pair()
    service._datasets['b'].metadata['source_sha256'] = 'changed'
    with pytest.raises(ValueError, match='来源'):
        export_run_cgm_pdf('a', result['id'])


def test_cgm_pdf_uses_latest_saved_remark(sources, tmp_path):
    add_source(tmp_path, 'a', (4, 5, 6), low=1, high=9)
    result = run_pair()
    row = next(row for row in result['rows'] if row.get('cgm'))
    notes.save_observations('a', result['id'], dict(keys=[row['key']], module='cgm',
        tags=[], suggestion='', remark='', final_remark='Saved CGM PDF conclusion', state='待跟进',
        confirmed=True, overwrite=True, version=0))
    pdf = PdfReader(BytesIO(export_run_cgm_pdf('a', result['id'])['content']))
    assert 'Saved CGM PDF conclusion' in pdf.pages[1].extract_text()


@pytest.mark.parametrize('module', ['cpk', 'cgm'])
def test_pdf_remark_save_and_undo_refresh_cached_export(sources, tmp_path, module):
    add_source(tmp_path, 'a', (4, 5, 6), low=1, high=9)
    result = run_pair()
    row = next(row for row in result['rows'] if row.get(module))
    export = export_run_cpk_pdf if module == 'cpk' else export_run_cgm_pdf
    baseline = export('a', result['id'])['content']
    saved = notes.save_observations('a', result['id'], dict(keys=[row['key']], module=module,
        tags=['drift'], suggestion='', remark='Recheck contact.', state='待跟进',
        confirmed=True, overwrite=True, version=0))
    updated = export('a', result['id'])['content']
    assert updated != baseline
    text = PdfReader(BytesIO(updated)).pages[1].extract_text()
    assert 'sequence drift observed.' in text and 'Recheck contact.' in text
    assert 'Remark' in text
    notes.undo_observations('a', result['id'], saved['undo_id'])
    undone = PdfReader(BytesIO(export('a', result['id'])['content'])).pages[1].extract_text()
    assert 'Recheck contact.' not in undone and 'sequence drift observed.' not in undone


def test_cpk_http_uses_own_source_selection_and_correct_yield(sources, tmp_path):
    a = add_source(tmp_path, 'a', (4., 5., 6., 10.), low=1., high=9.)
    a.frame.loc[a.frame['__part_index'] == 4, 'SBIN'] = 2
    result = run_pair(cpk={'part_indices': [1, 2, 3, 4]})
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        base = f'/api/release-check/datasets/a/runs/{result["id"]}/reports'
        response = client.get(base + '/cpk.pdf')
        assert response.status_code == 200, response.text
        assert response.headers['content-type'] == 'application/pdf'
        assert response.headers['cache-control'] == 'no-store'
        assert '_Device_a_Cpk_Report.pdf' in unquote(response.headers['content-disposition'])
        pdf = PdfReader(BytesIO(response.content))
        assert len(pdf.pages) == 2
        summary, detail = [page.extract_text() for page in pdf.pages]
        assert 'Total Parts Executed: 4' in summary and 'Bin 1 Yield (%): 75' in summary
        assert 'Total Parts (Bin1): 3' in detail and 'RESULT: FAIL' in detail
        assert client.get(base.replace(result['id'], 'expired') + '/cpk.pdf').status_code == 404
    a.metadata['source_sha256'] = 'changed'
    with pytest.raises(ValueError, match='来源'):
        export_run_cpk_pdf('a', result['id'])
