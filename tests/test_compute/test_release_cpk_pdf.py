"""Saved CPK rules, exact populations and adaptive five-panel slides."""
from copy import deepcopy
from datetime import datetime
from io import BytesIO

import numpy as np
from pypdf import PdfReader
from reportlab.lib.colors import HexColor
import pytest

from deepyield.compute.release_cpk_pdf import (cpk_state, cpk_display, cpk_population,
    prepare_cpk_pdf, render_cpk_pdf, highlighted_sites, export_release_cpk_pdf)
from deepyield.compute.release_numeric import cpk_metrics
from deepyield.rules.loader import load_analysis_rules


def bands():
    catalog = load_analysis_rules()
    return [{'rule_id': f'CPK-00{i}', 'condition': catalog.rule(f'CPK-00{i}').spec['condition'],
             'status': catalog.rule(f'CPK-00{i}').status} for i in range(1, 4)]


def fixture(count=4, targets=(.8,), wafer=False):
    items, populations = [], {}
    for nr, target in enumerate(targets, 1001):
        points = []
        for site in range(1, count + 1):
            values = np.sin(np.linspace(0, 4 * np.pi, 25))
            values = 5 + values / values.std(ddof=1) * 10 / (6 * (target + (site - 1) * .08))
            for j, value in enumerate(values):
                points.append({'site': site, 'value': float(value), 'part_index': j * count + site,
                    'record_sequence': j * count + site, 'lot': 'L1' if wafer else None,
                    'wafer': 'W1' if wafer else None, 'x': j, 'y': site,
                    'id': f'{site}-{j}', 'head': 1})
        metrics = cpk_metrics([p['value'] for p in points], 0., 10.)
        item = {'group_id': str(nr), 'column': f'T{nr}', 'test_num': nr, 'test_name': f'Voltage_{nr}',
                'record_type': 'PTR', 'conditions': {'part_type': 'DeviceA'}, 'unit': 'V',
                'effective_lsl': 0., 'effective_usl': 10., 'n': len(points),
                'mean': metrics['Mean'], 'std': metrics['Std'], 'cpk': metrics['cpk'],
                'fail_count': metrics['FailCount'], 'status': 'PASS'}
        items.append(item)
        populations[str(nr)] = points
    report = {'report_type': 'release_bin1_cpk', 'source': {}, 'items': items,
              'rules': {'bands': bands()}}
    population = {'sites': list(range(1, count + 1)), 'total_parts': 30 * count,
                  'bin1_parts': 25 * count, 'bin1_yield': 100 * 25 / 30, 'lots': ['L1'], 'wafers': ['W1'] if wafer else []}
    return report, population, lambda item: populations[item['group_id']]


def render(count=4, targets=(.8,), wafer=False):
    report, population, provider = fixture(count, targets, wafer)
    return render_cpk_pdf(report, population=population, sample_provider=provider,
                          generated_at=datetime(2026, 10, 20, 12))


@pytest.mark.parametrize('value,fail,state', [
    (1.669999, 0, 'Fail'), (1.67, 0, 'Need to check'), (1.999999, 0, 'Need to check'),
    (2., 0, 'Pass'), (20., 1, 'Fail'), (None, 1, 'Fail'), (None, 0, None)])
def test_exact_saved_bands_and_fail_count_override(value, fail, state):
    assert cpk_state(value, fail, bands()) == state


def test_saved_custom_bands_are_authoritative():
    custom = bands()
    for band, condition in zip(custom, ['< 1.0', '>= 1.0 and < 1.33', '>= 1.33']):
        band['condition'] = condition
    assert cpk_state(1.2, 0, custom) == 'Need to check'
    assert cpk_state(1.33, 0, custom) == 'Pass'


@pytest.mark.parametrize('count', range(1, 17))
def test_one_detail_page_and_all_five_regions_for_every_site_count(count):
    result = render(count)
    pdf = PdfReader(BytesIO(result['content']))
    assert result['filename'] == '20261020_DeviceA_Cpk_Report.pdf'
    assert len(pdf.pages) == 2
    assert all(tuple(map(float, p.mediabox)) == (0., 0., 1152., 648.) for p in pdf.pages)
    summary, detail = [p.extract_text() for p in pdf.pages]
    for title in ['1  Measurement Sequence', '2  Histogram + KDE', '3  Normal Q-Q', '4  Site Boxplots', '5  Site Statistics']:
        assert title in detail
    assert f'S{count}' in detail
    assert 'All Tests' not in summary + detail
    assert ('Worst\nSite' in summary) == (count > 4)
    assert ('two columns' in detail) == (count > 8)
    assert result['detail_modes'] == ['site_table_2col' if count > 8 else 'site_table']
    sizes = [float(operands[1]) for operands, op in pdf.pages[1].get_contents().operations if op == b'Tf']
    assert min(sizes) >= 8


def test_wafer_map_requires_unique_valid_identity_and_coordinates():
    result = render(8, wafer=True)
    assert result['detail_modes'] == ['wafer']
    detail = PdfReader(BytesIO(result['content'])).pages[1].extract_text()
    assert 'Wafer Map | W1' in detail and 'X / Y (1:1)' in detail
    report, population, provider = fixture(8, wafer=True)
    def multiple(item):
        points = deepcopy(provider(item))
        points[-1]['wafer'] = 'W2'
        return points
    result = render_cpk_pdf(report, population=population, sample_provider=multiple)
    assert result['detail_modes'] == ['site_table']
    assert 'Multiple wafers' in PdfReader(BytesIO(result['content'])).pages[1].extract_text()


def test_only_top_three_abnormal_sites_are_highlighted_and_sorted_by_cpk():
    report, population, provider = fixture(16)
    data = prepare_cpk_pdf(report, population=population, sample_provider=provider)
    assert highlighted_sites(data['rows'][0]['sites']) == [1, 2, 3]
    assert 'Pass' in {s['state'] for s in data['rows'][0]['sites']}


def test_pagination_order_and_no_full_all_tests_output():
    result = render(1, targets=(1.8, 1.3, .4) + tuple([.9] * 10) + (2.6,))
    pdf = PdfReader(BytesIO(result['content']))
    assert len(pdf.pages) == 15 and result['action_count'] == 13 and result['evaluated'] == 14
    assert 'Device ID:' in pdf.pages[0].extract_text()
    assert 'Device ID:' not in pdf.pages[1].extract_text()
    assert 'Test #1003' in pdf.pages[2].extract_text()
    assert 'Test #1001' in pdf.pages[-1].extract_text()
    assert 'Test #1014' not in '\n'.join(p.extract_text() for p in pdf.pages)


def test_all_pass_is_single_summary_with_no_detail():
    result = render(4, targets=(2.6,))
    assert result['page_count'] == 1 and result['action_count'] == 0
    assert 'No Fail or Need to check items.' in PdfReader(BytesIO(result['content'])).pages[0].extract_text()


def test_missing_or_changed_samples_fail_without_inventing_charts():
    report, population, provider = fixture()
    with pytest.raises(ValueError, match='样本'):
        render_cpk_pdf(report, population=population, sample_provider=lambda item: [])
    def changed(item):
        points = deepcopy(provider(item))
        points[0]['value'] += 1
        return points
    with pytest.raises(ValueError, match='统计'):
        render_cpk_pdf(report, population=population, sample_provider=changed)


def test_constant_out_of_spec_samples_are_fail_and_kde_does_not_crash():
    report, population, provider = fixture(1)
    item = report['items'][0]
    item.update(mean=11., std=0., cpk=None, fail_count=25, status='NOT_CALCULABLE')
    def constant(item):
        return [{**p, 'value': 11.} for p in provider(item)]
    result = render_cpk_pdf(report, population=population, sample_provider=constant)
    assert result['action_count'] == 1 and result['page_count'] == 2


def test_rounding_does_not_cross_a_saved_cpk_band():
    for value in [1.669999999, 1.999999999]:
        assert cpk_state(float(cpk_display(value, bands())), 0, bands()) == cpk_state(value, 0, bands())


def test_yield_uses_all_selected_records_and_valid_bin1_flags():
    from tests.test_compute.test_release_cpk import dataset
    frame, _ = dataset((4., 5., 6., 7.))
    frame['SBIN'] = [1, 1, 2, 1]
    frame['PART_FLAG'] = [0, 8, 0, 0]
    population = cpk_population(frame, {'selection': {'selected_part_indices': [1, 2, 3]}})
    assert population['total_parts'] == 3 and population['bin1_parts'] == 1
    assert population['bin1_yield'] == pytest.approx(100 / 3)
    with pytest.raises(ValueError, match='不存在'):
        cpk_population(frame, {'selection': {'selected_part_indices': [999]}})


def test_chinese_title_and_site_count_limit():
    report, population, provider = fixture()
    report['items'][0]['test_name'] = '量产电压能力'
    result = render_cpk_pdf(report, population=population, sample_provider=provider, device_id='设备A')
    assert '量产电压能力' in PdfReader(BytesIO(result['content'])).pages[1].extract_text()
    population['sites'] = list(range(17))
    with pytest.raises(ValueError, match='16'):
        render_cpk_pdf(report, population=population, sample_provider=provider)


@pytest.mark.parametrize('count,wafer', [(1, False), (16, False), (8, True)])
def test_numbered_remark_is_below_result_and_above_statistics_or_wafer(count, wafer):
    report, population, provider = fixture(count, wafer=wafer)
    remark = '1. Site Diff: S1, S4.\n2. Check trim target.\n3. 已复核接触，请检查 S4。'
    report['items'][0]['diagnosis'] = {'remark': remark}
    before = deepcopy(report)
    output = render_cpk_pdf(report, population=population, sample_provider=provider)
    page = PdfReader(BytesIO(output['content'])).pages[1]
    positions = {}
    def capture(text, cm, tm, font, size):
        for label in ('RESULT:', 'Remark', '5  Site Statistics', '5  Wafer Map'):
            if label in text:
                positions[label] = (cm[0] * tm[4] + cm[2] * tm[5] + cm[4],
                                    cm[1] * tm[4] + cm[3] * tm[5] + cm[5])
    text = page.extract_text(visitor_text=capture)
    assert '1. Site Diff: S1, S4.' in text and '3. 已复核接触' in text
    stats = positions['5  Wafer Map' if wafer else '5  Site Statistics']
    assert positions['RESULT:'][1] > positions['Remark'][1] > stats[1]
    assert positions['Remark'][0] >= 760
    assert report == before


def test_long_remark_does_not_cover_charts_and_keeps_complete_annotation():
    report, population, provider = fixture(16)
    remark = '1. ' + 'Long engineering observation. ' * 200
    report['items'][0]['diagnosis'] = {'remark': remark}
    page = PdfReader(BytesIO(render_cpk_pdf(report, population=population, sample_provider=provider)['content'])).pages[1]
    assert 'Full remark in PDF annotation.' in page.extract_text()
    assert any(annotation.get_object().get('/Contents') == remark for annotation in page['/Annots'])
    assert '5  Site Statistics' in page.extract_text() and '4  Site Boxplots' in page.extract_text()


def test_native_stdf_parquet_selection_export_and_no_overwrite(tmp_path):
    from examples.generate_cpk_pdf_demo import demo_stdf
    from deepyield.compute.release_stdf import convert_stdf_to_wide_all
    source, parsed = tmp_path / 'demo.stdf', tmp_path / 'demo.parquet'
    source.write_bytes(demo_stdf(4))
    convert_stdf_to_wide_all(source, parsed)
    output = export_release_cpk_pdf(parsed, tmp_path / 'out', sites=[1, 2])
    pdf = PdfReader(output['path'])
    assert len(pdf.pages) == 3
    summary = pdf.pages[0].extract_text()
    assert 'Active Sites: S1, S2' in summary
    assert 'Total Parts Executed: 200' in summary and 'Bin 1 Yield (%): 90' in summary
    with pytest.raises(FileExistsError):
        export_release_cpk_pdf(parsed, tmp_path / 'out', sites=[1, 2])


def test_small_site_distribution_panels_use_four_distinct_site_colors():
    result = render(4)
    page = PdfReader(BytesIO(result['content'])).pages[1]
    colors = {tuple(round(float(v), 6) for v in operands) for operands, operator in page.get_contents().operations
              if operator in (b'rg', b'RG')}
    for code in ['#005691', '#4198CF', '#D98200', '#7B61A8']:
        assert tuple(round(v, 6) for v in HexColor(code).rgb()) in colors


def test_device_is_optional_in_pdf_title_and_filename():
    report, population, provider = fixture(1)
    report['items'][0]['conditions'] = {}
    result = render_cpk_pdf(report, population=population, sample_provider=provider,
                            generated_at=datetime(2026, 10, 20, 12))
    assert result['filename'] == '20261020_Cpk_Report.pdf'
    assert 'Device ID:' not in PdfReader(BytesIO(result['content'])).pages[0].extract_text()
