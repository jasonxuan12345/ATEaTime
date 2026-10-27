"""20%-tolerance MR diagnostics, record ordering and unchanged release decisions."""
from io import BytesIO
from unittest.mock import Mock

import numpy as np
import pytest
from pypdf import PdfReader

from deepyield.compute.release_cgm_mr import moving_range_metrics
from deepyield.compute.release_cgm_pdf import _detail_samples, _detail_slide, _plot, prepare_cgm_pdf, render_cgm_pdf
from deepyield.compute.release_numeric import site_metrics, cpk_metrics
from tests.test_compute.test_release_cgm_excel import report_fixture
from deepyield.compute.release_cgm_excel import render_cgm_xlsx


def test_twenty_percent_formula_and_cpk_remains_unchanged():
    std, cg = site_metrics([4., 5., 6.], 1., 9.)
    assert std == 1 and cg == pytest.approx(.20 * 8 / 6)
    assert cpk_metrics([4., 5., 6.], 1., 9.)['cpk'] == pytest.approx(8 / 6)
    mr = moving_range_metrics([4., 5., 6.], 8.)
    np.testing.assert_allclose(mr['values'], [np.nan, 1., 1.], equal_nan=True)
    assert mr['average'] == 1
    assert mr['sigma'] == pytest.approx(1 / 1.128)
    assert mr['cg'] == pytest.approx(.20 * 8 / (6 / 1.128))
    assert mr['target_limit'] == pytest.approx(1.128 * .20 * 8 / (6 * 1.33))
    assert mr['spike_ucl'] == pytest.approx(3.267 * mr['target_limit'])


@pytest.mark.parametrize('fraction', [None, .10, 1.])
def test_old_or_unmarked_reports_require_recalculation(fraction):
    report = report_fixture((.5,))
    report['policy'] = {} if fraction is None else {'cg_tolerance_fraction': fraction}
    for render in (lambda: render_cgm_xlsx(report),
                   lambda: render_cgm_pdf(report, sample_provider=lambda row: [])):
        with pytest.raises(ValueError, match='20%.*重新计算'):
            render()


def test_pdf_band_fill_retains_fifteen_percent_opacity():
    report = report_fixture((.5,))
    output = render_cgm_pdf(report, sample_provider=lambda row: [{'site': 1, 'value': 5.}] * 25)
    page = PdfReader(BytesIO(output['content'])).pages[1]
    states = page['/Resources']['/ExtGState']
    alpha, stack, color, band_alphas = 1., [], None, []
    for args, op in page.get_contents().operations:
        if op == b'q':
            stack.append((alpha, color))
        elif op == b'Q':
            alpha, color = stack.pop()
        elif op == b'gs':
            alpha = float(states[args[0]].get('/ca', alpha))
        elif op == b'rg':
            color = tuple(round(float(v), 4) for v in args)
        elif op in (b'f', b'f*') and color in ((.9451, .7686, .0588), (.1804, .8, .4431)):
            band_alphas.append(alpha)
    assert band_alphas == [.15, .15, .15]


@pytest.mark.parametrize('values', [[], [5.]])
def test_no_mr_for_fewer_than_two_measurements(values):
    mr = moving_range_metrics(values, 10)
    assert mr['average'] is None and mr['sigma'] is None and mr['cg'] is None
    assert np.isnan(mr['values']).all()


def test_constant_and_spike_sequences():
    assert moving_range_metrics([5, 5, 5], 10)['cg'] == 'Infinity'
    mr = moving_range_metrics([5, 5, 5, 7, 5], 10)
    assert mr['spike_count'] == 2
    assert mr['average'] == 1


@pytest.mark.parametrize('values,tol', [([1, np.nan], 10), ([1, np.inf], 10), ([[1, 2]], 10),
                                      ([1, 2], 0), ([1, 2], -1), ([1, 2], float('inf'))])
def test_invalid_mr_is_not_fabricated(values, tol):
    with pytest.raises(ValueError):
        moving_range_metrics(values, tol)


def test_mr_orders_measurements_and_never_differences_across_sites():
    report = report_fixture((.5, .6))
    for site in report['items'][0]['sites']:
        site['n'] = 3
    row = prepare_cgm_pdf(report)['actions'][0]
    samples = [{'site': site, 'value': value, 'record_sequence': seq, 'part_index': seq}
               for site, value, seq in [(2, 102, 6), (1, 10, 1), (2, 100, 2), (1, 13, 5), (1, 11, 3), (2, 101, 4)]]
    sites, values = _detail_samples(row, samples, [1, 2])
    np.testing.assert_array_equal(values[0], [10, 11, 13])
    np.testing.assert_array_equal(values[1], [100, 101, 102])
    assert [moving_range_metrics(v, 10)['average'] for v in values] == [1.5, 1.]
    samples[0].pop('record_sequence')
    with pytest.raises(ValueError, match='顺序'):
        _detail_samples(row, samples, [1, 2])


def test_single_sample_site_is_in_statistics_and_grand_mean_but_has_no_mr():
    report = report_fixture((.5,))
    report['sites'] = [1, 2, 3]
    report['items'][0]['sites'][0].update(n=2, mean=5.)
    report['items'][0]['sites'].append(dict(site=2, n=1, mean=8., std=None, cg=None))
    row = prepare_cgm_pdf(report)['actions'][0]
    slides = Mock()
    slides.remark.return_value = 450
    _detail_slide(slides, row, [{'site': 1, 'value': 4}, {'site': 1, 'value': 6},
                               {'site': 2, 'value': 8}], '', 2, 2, [1, 2, 3])
    rows = slides.table.call_args.args[1]
    assert rows[0][2:4] == ['5.0000', '-1.0000']
    assert rows[1][1:4] == [1, '8.0000', '+2.0000']
    assert rows[1][5:] == ['--', '--', '--']
    assert rows[2][1] == 0
    assert rows[-1][1:4] == [3, '6.0000', '+0.0000']


def test_mr_pass_never_changes_failed_cg_or_generates_drift_safety_conclusions():
    report = report_fixture((.5,))
    output = render_cgm_pdf(report, sample_provider=lambda row: [{'site': 1, 'value': 5.}] * 25)
    page = PdfReader(BytesIO(output['content'])).pages[1].extract_text()
    assert 'RESULT: FAIL' in page and 'Infinity' in page
    assert 'Cg(MR) is diagnostic only' in page
    assert 'DRIFT (PASS*)' not in page and 'Safe for single-shot' not in page
    report['items'][0]['supported_by_bin1_cpk'] = 2.
    def forbidden(row):
        raise AssertionError('CPK-exempt tests must stay out of the action detail pages')
    assert render_cgm_pdf(report, sample_provider=forbidden)['action_count'] == 0


def test_mr_dots_start_at_second_measurement_and_axis_begins_at_zero():
    slides = Mock()
    sites = [dict(site=1, n=3, mean=5, state='Fail')]
    values = [np.asarray([4, 5, 7])]
    metrics = [moving_range_metrics(values[0], 10)]
    _plot(slides, (0, 0, 100, 100), '', sites, values, 'mr', {'low': 0, 'high': 10}, mr_data=metrics)
    points = list(slides.dots.call_args.args[0])
    assert [p[0] for p in points] == [50, 92]  # First point at x=8 is NaN.
    assert any(c.args[:3] == (-45, -3, 0.) for c in slides.text.call_args_list)
    references = [c for c in slides.line.call_args_list if len(c.args) > 5 and c.args[5] == [5, 3]]
    assert len(references) == 2
    assert references[0].args[1] < references[1].args[1]
