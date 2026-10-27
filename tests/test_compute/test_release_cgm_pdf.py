"""Decision, pagination, exact sample, and 1..16 site PDF contracts."""
from copy import deepcopy
from datetime import datetime
from io import BytesIO
from unittest.mock import Mock

import numpy as np
from pypdf import PdfReader
import pytest

from deepyield.compute.release_cgm_pdf import prepare_cgm_pdf, render_cgm_pdf, _cg_display, _summary_slide, COLORS
from deepyield.compute.release_cgm_pdf import _detail_slide, _plot
from tests.test_compute.test_release_cgm_excel import report_fixture


def samples(item):
    return [{'site': site['site'], 'value': float(value)} for site in item['sites']
            for value in np.linspace(site['mean'] - 1, site['mean'] + 1, site['n'])]


def render(report, provider=samples, **kwargs):
    return render_cgm_pdf(report, sample_provider=provider,
                          generated_at=datetime(2026, 10, 20, 12), **kwargs)


@pytest.mark.parametrize('cg,cpk,result', [
    (1.33, None, 'Pass'), ('Infinity', None, 'Pass'), (1.0, None, 'Need to check'),
    (1.329999, 1.99999, 'Need to check'), (.99999, None, 'Fail'),
    (.8, 2., 'Pass'), (1.1, 2., 'Pass'), (1.33, -10., 'Pass')])
def test_joint_decisions(cg, cpk, result):
    report = report_fixture((cg,), cpk)
    if cg == 'Infinity':
        report['items'][0]['sites'][0]['std'] = 0
    before = deepcopy(report)
    data = prepare_cgm_pdf(report)
    assert data['rows'][0]['result'] == result
    assert data['rows'][0]['minimum'] == cg
    assert report == before


def test_infinite_site_never_masks_finite_min_and_state_bits_are_excluded():
    report = report_fixture(('Infinity', .8))
    report['items'][0]['sites'][0]['std'] = 0
    status = deepcopy(report['items'][0])
    status.update(group_id='status', effective_usl=0.)
    for site in status['sites']:
        site.update(std=0., cg=None)
    report['items'].append(status)
    data = prepare_cgm_pdf(report)
    assert data['excluded'] == 1 and len(data['rows']) == 1
    assert data['actions'][0]['minimum'] == .8
    assert data['actions'][0]['maximum'] == 'Infinity'
    assert data['actions'][0]['worst_site'] == 2


@pytest.mark.parametrize('cgs,minimum,result', [
    (('Infinity', 'Infinity'), 'Infinity', 'Pass'), (('Infinity', .8), .8, 'Fail'),
    (('NaN', 'NaN'), 'NaN', 'Need to check'), (('NaN', .8), 'NaN', 'Need to check'),
    ((1.5, 'NaN'), 'NaN', 'Need to check'), (('Infinity', 'NaN'), 'NaN', 'Need to check')])
def test_pdf_preserves_special_minimum_values_and_can_render_nan(cgs, minimum, result):
    report = report_fixture(cgs)
    data = prepare_cgm_pdf(report)
    assert data['rows'][0]['minimum'] == minimum
    assert data['rows'][0]['result'] == result
    output = render(report)
    assert output['page_count'] == (1 if result == 'Pass' else 2)
    if minimum == 'NaN':
        pdf = PdfReader(BytesIO(output['content']))
        assert 'min Cg: NaN' in pdf.pages[1].extract_text()


@pytest.mark.parametrize('count', [2, 16])
@pytest.mark.parametrize('cpk,state', [(None, None), (1.66, 'Fail'), (1.67, 'Need to check'),
                                     (1.999999, 'Need to check'), (2., 'Pass'), (2.01, 'Pass')])
def test_pdf_bin1_color_is_independent_and_remark_follows_result(count, cpk, state):
    report = report_fixture(tuple([.8] * count), cpk)
    report['items'][0]['diagnosis'] = {'remark': 'Saved engineering conclusion'}
    data = prepare_cgm_pdf(report)
    assert data['rows'][0]['cpk_state'] == state
    # Include the otherwise exempt Pass row to inspect each numeric color.
    data['actions'] = data['rows']
    slides = Mock()
    _summary_slide(slides, data, {}, 'DeviceA', '', 0, 2)
    headers, rows, _, _, _, _, fills, _ = slides.table.call_args.args
    assert headers[-2:] == ['Result', 'Remark']
    assert rows[0][-1] == 'Saved engineering conclusion'
    assert fills.get((0, headers.index('Bin1 CPK'))) == COLORS.get(state)
    assert fills[0, headers.index('min Cg')] == COLORS['Fail']


def test_pdf_displays_complete_saved_chinese_remark_on_detail_slide():
    report = report_fixture(tuple([.8] * 16))
    report['items'][0]['diagnosis'] = {'remark': '已根据规则和知识库检查：请复核接触与重复性。'}
    pdf = PdfReader(BytesIO(render(report)['content']))
    assert 'Remark' in pdf.pages[0].extract_text()
    assert '已根据规则和知识库检查' in pdf.pages[1].extract_text()


@pytest.mark.parametrize('count', [1, 16])
def test_numbered_remark_is_below_result_and_above_site_statistics(count):
    report = report_fixture(tuple([.8] * count))
    report['items'][0]['diagnosis'] = {'remark':
        '1. Check contact and hardware.\n2. Site Diff: S1, S4.\n3. 人工复核：检查接触。'}
    pdf = PdfReader(BytesIO(render(report)['content']))
    page = pdf.pages[1]
    positions = {}
    def capture(text, cm, tm, font, size):
        for label in ('RESULT:', 'Remark', '4  Site Statistics'):
            if label in text:
                positions[label] = (cm[0] * tm[4] + cm[2] * tm[5] + cm[4],
                                    cm[1] * tm[4] + cm[3] * tm[5] + cm[5])
    text = page.extract_text(visitor_text=capture)
    assert text.count('Remark') == 1 and '3. 人工复核' in text
    assert positions['RESULT:'][1] > positions['Remark'][1] > positions['4  Site Statistics'][1]
    assert positions['Remark'][0] >= 729
    summary = pdf.pages[0].extract_text()
    assert '2. Site Diff: S1, S4.' in summary
    assert '3. 人工复核' not in summary  # Two-line preview; full numbered note is on the detail page.


def test_long_remark_keeps_full_annotation_and_all_sixteen_sites():
    report = report_fixture(tuple([.8] * 16))
    remark = '1. ' + 'Long engineering observation. ' * 200
    report['items'][0]['diagnosis'] = {'remark': remark}
    page = PdfReader(BytesIO(render(report)['content'])).pages[1]
    assert 'Full remark in PDF annotation.' in page.extract_text()
    assert any(annotation.get_object().get('/Contents') == remark for annotation in page['/Annots'])
    assert 'S16' in page.extract_text() and 'Min/All' in page.extract_text()


@pytest.mark.parametrize('count', range(1, 17))
def test_each_site_count_fits_single_detail_slide(count):
    report = report_fixture(tuple(.8 + i * .08 for i in range(count)))
    output = render(report)
    pdf = PdfReader(BytesIO(output['content']))
    assert output['filename'] == '20261020_DeviceA_Cgm_Report.pdf'
    assert len(pdf.pages) == 2
    for page in pdf.pages:
        assert tuple(map(float, page.mediabox)) == (0., 0., 1152., 648.)
    summary, detail = [page.extract_text() for page in pdf.pages]
    assert ('Worst Site' in summary) == (count > 4)
    assert f'S{count}' in detail and 'Min/All' in detail and 'RESULT: FAIL' in detail
    assert 'All Tests' not in summary + detail
    assert 'Measurement Sequence' in detail and 'Within-Site Deviation' in detail and 'Moving Range' in detail
    fonts = [float(operand[1]) for operand, operator in pdf.pages[1].get_contents().operations if operator == b'Tf']
    assert min(fonts) >= 8


def test_no_actions_never_reads_samples_and_emits_only_summary():
    def forbidden(item):
        raise AssertionError('must not read Pass samples')
    output = render(report_fixture(('Infinity',)), forbidden)
    assert output['page_count'] == 1 and output['action_count'] == 0
    assert 'No Fail or Need to check items.' in PdfReader(BytesIO(output['content'])).pages[0].extract_text()


def test_pagination_fail_first_then_ir_and_ascending_minimum():
    report = report_fixture((.8,))
    template = report['items'].pop()
    for index, cg in enumerate([1.1, .9, .4] + [.7] * 10):
        item = deepcopy(template)
        item.update(group_id=f'g{index}', test_num=100 + index)
        item['sites'][0]['cg'] = cg
        report['items'].append(item)
    called = []
    def provider(item):
        called.append(item['test_num'])
        return samples(item)
    output = render(report, provider)
    pdf = PdfReader(BytesIO(output['content']))
    assert len(pdf.pages) == 15  # 2 summary pages + 13 actions
    assert called[0] == 102 and called[-2:] == [101, 100]
    assert 'Device ID:' in pdf.pages[0].extract_text()
    assert 'Device ID:' not in pdf.pages[1].extract_text()
    assert 'Test #102' in pdf.pages[2].extract_text()


def test_wrong_sample_count_refuses_fabricated_or_truncated_charts():
    with pytest.raises(ValueError, match='样本'):
        render(report_fixture((.5,)), lambda item: [])


def test_site_diff_uses_pooled_mean_preserves_cg_and_leaves_missing_site_blank():
    report = report_fixture((.8, 1.1))
    report['sites'] = [1, 2, 3]
    report['items'][0]['sites'][0].update(n=2, mean=10.)
    report['items'][0]['sites'][1].update(n=6, mean=14.)
    before = deepcopy(report)
    prepared = prepare_cgm_pdf(report)
    slides = Mock()
    slides.remark.return_value = 450
    _detail_slide(slides, prepared['actions'][0], samples(prepared['actions'][0]), '', 2, 2, prepared['sites'])
    headers, rows, widths, x, top, height, fills, font = slides.table.call_args.args
    assert headers == ['Site', 'Count', 'Mean', 'Shift', 'StdDev', 'Cg*', 'Cg(MR)', 'Status']
    assert rows[0][2:4] == ['10.0000', '-3.0000']
    assert rows[1][2:4] == ['14.0000', '+1.0000']
    assert rows[2][1:4] == [0, None, None]
    assert rows[-1][1:4] == [8, '13.0000', '+0.0000']
    assert [rows[0][5], rows[0][7]] == ['0.8', 'Fail'] and [rows[1][5], rows[1][7]] == ['1.1', 'IR']
    assert fills[0, 5] == COLORS['Fail'] and fills[1, 5] == COLORS['Need to check']
    assert sum(widths) == 400 and font >= 8
    assert report == before


def test_deviation_plot_keeps_site_centering_and_includes_large_offset_markers():
    slides = Mock()
    sites = [dict(site=1, n=2, mean=100., state='Fail')]
    _plot(slides, (0, 0, 300, 100), '', sites, [np.asarray([99., 101.])],
          'centered', {'low': 0., 'high': 10.}, grand_mean=0.)
    dots, radius = slides.dots.call_args.args
    points = list(dots)
    assert points[0][1] < 50 < points[1][1]  # Still value - site mean, around zero.
    assert radius == 1.25
    shift_line = next(c for c in slides.line.call_args_list if c.args[4] == '#76528B')
    marker_y = shift_line.args[1]
    assert shift_line.args[0] == 0 and shift_line.args[2] == 300
    assert shift_line.kwargs['width'] == 1.5
    assert 50 < marker_y < 100  # +100 offset remains inside the expanded plot.
    pdf = PdfReader(BytesIO(render(report_fixture((.8,)))['content']))
    assert 'Shift' in pdf.pages[1].extract_text()
    assert 'Site Shift (Mean - Grand Mean)' in pdf.pages[1].extract_text()
    assert all(c.args == (.15,) for c in slides.c.setFillAlpha.call_args_list)


def test_chinese_identifiers_and_source_mir():
    report = report_fixture((.5,))
    report['items'][0]['test_name'] = '电压重复性测试'
    output = render(report, device_id='设备A')
    pdf = PdfReader(BytesIO(output['content']))
    assert output['filename'] == '20261020_设备A_Cgm_Report.pdf'
    assert '电压重复性测试' in pdf.pages[1].extract_text()


def test_more_than_sixteen_sites_is_explicit_error():
    with pytest.raises(ValueError, match='16'):
        render(report_fixture(tuple([.8] * 17)))


@pytest.mark.parametrize('mixed', [False, True])
def test_missing_or_ambiguous_device_omits_device_without_blocking_pdf(mixed):
    report = report_fixture((.5,))
    report['items'][0]['conditions'] = {}
    if mixed:
        report['items'][0]['conditions'] = {'part_type': 'A'}
        report['items'].append({**deepcopy(report['items'][0]), 'group_id': 'other', 'conditions': {'part_type': 'B'}})
    output = render(report)
    assert output['filename'] == '20261020_Cgm_Report.pdf'
    first = PdfReader(BytesIO(output['content'])).pages[0].extract_text()
    assert 'CGM Analysis Report' in first and 'Device ID:' not in first


def test_cg_rounding_does_not_visually_cross_a_decision_boundary():
    assert _cg_display(.99999997) == '<1.00'
    assert _cg_display(1.32999999) == '<1.33'
    assert _cg_display(1.33) == '1.33'
    assert _cg_display('Infinity') == 'Infinity'
