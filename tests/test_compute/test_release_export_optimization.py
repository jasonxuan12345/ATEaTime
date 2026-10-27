"""Performance invariants that retain exact report data and validation."""
from copy import deepcopy
from io import BytesIO
from tempfile import TemporaryFile

import numpy as np
import pytest
from pypdf import PdfReader

from deepyield.compute.release_chart_samples import ReportSampleResolver, report_samples
from deepyield.compute.release_cpk_pdf import prepare_cpk_pdf, render_cpk_pdf
from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_export_session import export_release_reports
from tests.test_compute.test_release_cpk import dataset
from tests.test_compute.test_release_cpk_pdf import fixture
from deepyield.rules.loader import load_analysis_rules


def test_pass_tests_never_fetch_raw_samples_and_actions_keep_validation():
    report, population, provider = fixture(targets=(2.6, .8, 2.6))
    calls = []
    def samples(item):
        calls.append(item['group_id'])
        assert item['group_id'] == '1002'
        return provider(item)
    with TemporaryFile() as spool:
        prepared = prepare_cpk_pdf(report, sample_provider=samples, population=population, sample_store=spool)
        assert len(prepared['rows']) == 3 and len(prepared['actions']) == 1
        assert all('points' not in row and 'values' not in row for row in prepared['rows'])
        assert all('data' not in site for row in prepared['actions'] for site in row['sites'])
    assert calls == ['1002']
    def changed(item):
        points = deepcopy(provider(item))
        points[0]['value'] += 1
        return points
    with pytest.raises(ValueError, match='统计'):
        render_cpk_pdf(report, population=population, sample_provider=changed)


def test_pdf_can_write_to_file_without_returning_a_second_byte_buffer(tmp_path):
    report, population, provider = fixture(targets=(.8,))
    path = tmp_path / 'report.pdf'
    with path.open('w+b') as output:
        result = render_cpk_pdf(report, population=population, sample_provider=provider, output=output)
    assert result['content'] is None
    assert len(PdfReader(path).pages) == 2


@pytest.mark.parametrize('compact', [False, True])
def test_indexed_samples_match_existing_identity_order_and_values(compact):
    frame, metadata = dataset((4., 5., 6., 7.))
    if compact:
        import pyarrow as pa
        from deepyield.compute.release_event_store import release_frame
        frame = release_frame(pa.Table.from_pandas(frame, preserve_index=False))
    report = build_release_bin1_cpk_report(frame, metadata, catalog=load_analysis_rules())
    resolver = ReportSampleResolver(frame, metadata['source_sha256'])
    item = report['items'][0]
    assert resolver(item) == report_samples(frame, item, metadata['source_sha256'])
    index = resolver.indices.copy()
    assert resolver(item) == report_samples(frame, item, metadata['source_sha256'])
    assert all(resolver.indices[key] is value for key, value in index.items())
    broken = {**item, 'n': item['n'] + 1, 'sample_refs': list(item['sample_refs']) + [item['sample_refs'][0]]}
    with pytest.raises(ValueError, match='重复'):
        resolver(broken)
    missing = {**item, 'sample_refs': [{**ref, 'record_sequence': -100} for ref in item['sample_refs']]}
    with pytest.raises(ValueError, match='唯一定位'):
        resolver(missing)


def test_batch_export_parses_and_calculates_each_module_once(tmp_path, monkeypatch):
    import deepyield.compute.release_stdf as stdf
    import deepyield.compute.release_cpk as cpk
    from deepyield.compute.release_run_cache import cached_report
    frame, metadata = dataset((4., 5., 6., 7.))
    metadata['stdf_metadata'] = {'mir': {'part_type': 'BatchDevice', 'test_temperature': '25'}}
    reads, calculations = [], []
    def read(path):
        reads.append(path)
        return frame, metadata
    original = cpk.build_release_bin1_cpk_report
    @cached_report
    def build(frame, metadata, **kwargs):
        calculations.append(1)
        return original(frame, metadata, **kwargs)
    monkeypatch.setattr(stdf, 'read_release_wide_all', read)
    monkeypatch.setattr(cpk, 'build_release_bin1_cpk_report', build)
    import deepyield.compute.release_cpk_excel_input as excel
    monkeypatch.setattr(excel, 'read_release_wide_all', read)
    monkeypatch.setattr(excel, 'build_release_bin1_cpk_report', build)
    result = export_release_reports(tmp_path / 'input.parquet', tmp_path, formats=('cpk.xlsx', 'cpk.pdf'))
    assert set(result) == {'cpk.xlsx', 'cpk.pdf'}
    assert len(reads) == len(calculations) == 1


def test_legacy_excel_retains_values_styles_and_does_not_mutate_input(tmp_path):
    import pandas as pd
    from openpyxl import load_workbook
    from deepyield.compute.report_engine import export_bin1_cpk_report_excel
    frame = pd.DataFrame({'name': ['Low', 'Good', 'Unavailable'], 'cpk': [1., 3., np.nan]})
    original = frame.copy(deep=True)
    path = tmp_path / 'legacy.xlsx'
    export_bin1_cpk_report_excel(frame, str(path))
    pd.testing.assert_frame_equal(frame, original)
    sheet = load_workbook(path).active
    assert sheet['B2'].value == 1 and sheet['B3'].value == 3 and sheet['B4'].value is None
    assert sheet['B2'].fill.fill_type == sheet['B3'].fill.fill_type == 'solid'
    assert sheet['B2'].fill.fgColor.rgb != sheet['B3'].fill.fgColor.rgb
    assert sheet.freeze_panes == 'A2'
