"""Chart statistics use exact report populations, including MPR subresults."""
from copy import deepcopy
import json

import numpy as np
import pandas as pd
import pytest

from deepyield.compute.release_chart_samples import report_samples
from deepyield.compute.release_charts import build_chart_statistics, select_chart_samples
from deepyield.compute.release_chart_wafer import release_wafer_data


def item(n, **changes):
    return {'n': n, 'column': 'A', 'record_type': 'PTR', 'result_index': None,
        'effective_lsl': 0., 'effective_usl': 10., 'unit': 'V', 'status': 'RISK',
        'cpk': .5, 'sample_refs': [], 'sites': [], **changes}


def points(values):
    return [{'id': str(i), 'part_index': i, 'record_sequence': i * 3, 'column': 'A',
        'result_index': None, 'record_type': 'PTR', 'site': i % 2 + 1, 'head': 1,
        'value': float(value), 'lot': 'L', 'wafer': 'W', 'x': i, 'y': 0, 'part_id': None}
        for i, value in enumerate(values)]


def test_histogram_box_and_population_independent_of_point_thinning():
    data = points(list(range(100)))
    original = deepcopy(data)
    chart = build_chart_statistics(data, item(100), bins=7, point_limit=10)
    assert sum(sum(site['histogram']) for site in chart['sites']) == 100
    assert chart['statistics']['mean'] == 49.5
    assert chart['statistics']['std'] == pytest.approx(np.std(range(100), ddof=1))
    assert chart['sampled'] and len(chart['points']) == 10
    assert chart['sites'][0]['box']['q1'] == 24.5
    assert data == original
    json.dumps(chart, allow_nan=False)


@pytest.mark.parametrize('values', [[], [0.], [3., 3.], [-9., 2., 1000.]])
@pytest.mark.parametrize('width', [None, 20.])
def test_empty_single_constant_zero_limit_and_extremes(values, width):
    chart = build_chart_statistics(points(values), item(len(values)), bin_width=width)
    assert chart['statistics']['lsl'] == 0
    assert sum(sum(s['histogram']) for s in chart['sites']) == len(values)
    if len(set(values)) < 2:
        assert chart['statistics']['cp'] is None
    json.dumps(chart, allow_nan=False)


def test_outliers_are_display_marks_and_remain_in_statistics():
    data = points([1., 2., 3., 100.])
    for point in data:
        point['site'] = 1
    chart = build_chart_statistics(data, item(4))
    box = chart['sites'][0]['box']
    assert box['outlier_count'] == 1 and box['outliers'][0]['value'] == 100
    assert box['high'] == 3 and chart['statistics']['mean'] == 26.5


def test_exact_mpr_identity_and_source_scoping():
    frame = pd.DataFrame([{'__part_index': 1, 'HEAD_NUM': 2, '__test_records': [
        {'record_sequence': 5, 'column': 'A', 'record_type': 'MPR', 'result_index': 0, 'result': 1.},
        {'record_sequence': 5, 'column': 'B', 'record_type': 'MPR', 'result_index': 1, 'result': 9.}]}])
    report = item(1, column='B', record_type='MPR', result_index=1,
        sample_refs=[{'part_index': 1, 'record_sequence': 5, 'site': None}])
    a = report_samples(frame, report, 'source-a')[0]
    b = report_samples(frame, report, 'source-b')[0]
    assert a['value'] == 9 and a['head'] == 2 and a['site'] is None
    assert a['id'] != b['id']
    report['result_index'] = 99
    with pytest.raises(ValueError, match='唯一定位'):
        report_samples(frame, report, 'source-a')


def test_selection_is_exact_and_right_histogram_boundary_excludes_next_bin():
    data = points(range(10))
    selected = select_chart_samples(data, {'value_min': 2., 'value_max': 5., 'value_max_exclusive': True})
    assert [p['value'] for p in selected] == [2., 3., 4.]
    assert len(select_chart_samples(data, {'sites': []})) == 0
    assert len(data) == 10


def test_wafer_uses_existing_coordinate_quality_and_preserves_repeated_locations():
    data = points([1, 2, 3, 4])
    data[1]['x'] = 0
    data[2]['x'] = -32768
    data[3]['wafer'] = 'W2'
    chart = release_wafer_data(data, item(4, conditions={}))
    assert len(chart['wafers']) == 2 and chart['excluded_count'] == 1
    die = chart['wafers'][0]['points'][0]
    assert die['value'] == 2 and die['count'] == 2 and die['raw_x'] == 0


def test_wafer_keeps_full_bounds_and_color_range_when_extremes_are_not_sampled():
    data = points(range(50))
    data[1]['x'] = -100
    data[1]['y'] = 100
    data[1]['value'] = -999
    chart = release_wafer_data(data, item(50), limit=10)
    wafer = chart['wafers'][0]
    assert wafer['sampled'] and len(wafer['points']) == 10
    assert wafer['bounds'] == {'minX': -100, 'maxX': 49, 'minY': 0, 'maxY': 100}
    assert chart['value_range'] == [-999, 49]


@pytest.mark.parametrize('kwargs', [{'bins': 0}, {'bins': 201}, {'bin_width': 0}, {'point_limit': 1}])
def test_invalid_chart_parameters_rejected(kwargs):
    with pytest.raises(ValueError):
        build_chart_statistics(points([1, 2]), item(2), **kwargs)


@pytest.mark.parametrize('value', [1e30, -1e30, 1e-30])
def test_large_and_small_constant_values_have_a_real_single_bin(value):
    chart = build_chart_statistics(points([value, value]), item(2))
    edges = chart['histogram']['edges']
    assert len(edges) == 2 and edges[0] < value < edges[1]
    assert sum(sum(s['histogram']) for s in chart['sites']) == 2
    json.dumps(chart, allow_nan=False)
