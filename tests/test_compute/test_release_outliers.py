"""Outlier detection and trace semantics, without measurement-file IO."""
from copy import deepcopy
import json

import numpy as np
import pytest

from deepyield.compute.release_outliers import build_release_outlier_report as build
from deepyield.compute.release_outlier_stats import robust_outlier_statistics
from tests.test_compute.outlier_fixtures import dataset, population


@pytest.fixture(autouse=True)
def no_measurement_files(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('STDF/Parquet IO is forbidden in this task')
    for name in ('read_table', 'write_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    monkeypatch.setattr('deepyield.compute.release_stdf.read_release_wide_all', forbidden)
    monkeypatch.setattr('deepyield.compute.release_stdf.import_release_stdf', forbidden)


@pytest.mark.parametrize('compact', [False, True])
def test_finds_exact_chip_and_retains_all_location_fields(compact):
    frame, metadata = dataset(compact=compact)
    report = build(frame, metadata)
    assert report['summary']['candidate_part_count'] == 1
    chip = report['chips'][0]
    assert (chip['part_index'], chip['site'], chip['head']) == (41, 1, 1)
    assert (chip['part_id'], chip['lot'], chip['wafer'], chip['x'], chip['y']) == ('REPEATED_ID', 'LOT', 'W1', -1, 0)
    assert chip['prr_record_sequence'] == 419
    hit = chip['hits'][0]
    assert hit['record_sequence'] == 410 and hit['value'] == 12.
    assert hit['test_num'] == 1001 and hit['direction'] == 'HIGH' and hit['score'] > 6
    baseline = next(row for row in report['baselines'] if row['baseline_id'] == hit['baseline_id'])
    q1, median, q3 = np.percentile(population() + [12.], [25, 50, 75], method='linear')
    assert baseline['median'] == median and baseline['robust_sigma'] == (q3 - q1) / 1.35
    assert hit['score'] == pytest.approx(abs(12. - median) / baseline['robust_sigma'])
    events = frame.loc[frame['__part_index'] == chip['part_index'], '__test_records'].iloc[0]
    matches = (events.find(hit['record_sequence'], hit['column'], hit['record_type'], hit['result_index'])
               if hasattr(events, 'find') else [event for event in events
                   if event['record_sequence'] == hit['record_sequence'] and event['column'] == hit['column']])
    assert len(matches) == 1 and float(matches[0]['result']) == hit['value']
    json.dumps(report, allow_nan=False)


def test_whole_site_offset_does_not_make_its_chips_outliers():
    frame, metadata = dataset({(1, 1, 'W1'): population(10.), (1, 2, 'W1'): population(50.)})
    report = build(frame, metadata)
    assert report['chips'] == []
    assert sorted(row['median'] for row in report['baselines']) == [10., 50.]
    assert report['policy']['site_shift_assessment'] == 'USE_RELEASE_SITE_DIFF'
    assert all(row['status'] == 'NO_OUTLIER_FOUND' for row in report['baselines'])


@pytest.mark.parametrize('key', ['head', 'wafer'])
def test_other_heads_and_wafers_never_share_a_reference(key):
    second = (2, 1, 'W1') if key == 'head' else (1, 1, 'W2')
    frame, metadata = dataset({(1, 1, 'W1'): population(10., 20), second: population(50., 20)})
    report = build(frame, metadata)
    assert len(report['baselines']) == 2 and report['chips'] == []
    assert all(row['status'] == 'INSUFFICIENT_DATA' and row['n'] == 20 for row in report['baselines'])


def test_multiple_tests_aggregate_hits_but_repeated_part_ids_do_not_merge():
    frame, metadata = dataset({(1, 1, 'W1'): population() + [12., 13.]}, test_count=2)
    report = build(frame, metadata)
    assert [chip['part_index'] for chip in report['chips']] == [42, 41]
    assert all(chip['hit_count'] == 2 for chip in report['chips'])
    assert report['summary']['candidate_hit_count'] == 4


@pytest.mark.parametrize('kind', ['conditions', 'specification', 'unit'])
def test_test_variants_have_independent_baselines(kind):
    frame, metadata = dataset({(1, 1, 'W1'): population(10., 20) + population(50., 20)})
    for index in range(20, 40):
        if kind == 'conditions':
            frame.at[index, '__conditions_json'] = json.dumps({'job_revision': 'B'})
        else:
            frame.at[index, '__test_records'][0]['unit' if kind == 'unit' else 'high_limit'] = 'V' if kind == 'unit' else 200.
    report = build(frame, metadata)
    assert len(report['baselines']) == 2
    assert all(row['status'] == 'INSUFFICIENT_DATA' for row in report['baselines'])


@pytest.mark.parametrize('change,reason', [
    ({'SBIN': 2}, 'NOT_BIN1'), ({'PART_FLAG': 8}, 'INVALID_OR_CONFLICTING_PART_RESULT'),
    ({'SITE': None}, 'MISSING_OR_INVALID_CHANNEL'), ({'HEAD_NUM': None}, 'MISSING_OR_INVALID_CHANNEL'),
    ({'result_valid': False}, 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'),
    ({'test_flag': 2}, 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'),
    ({'test_flag': 0x80}, 'EXPLICIT_FAILED_TEST'),
    ({'param_flag': 0x20}, 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'),
    ({'result': float('nan')}, 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'),
    ({'result': float('inf')}, 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'),
    ({'result': 101.}, 'OUTSIDE_SPECIFICATION'),
])
def test_invalid_and_failed_records_do_not_enter_reference_or_candidates(change, reason):
    frame, metadata = dataset()
    for key, value in change.items():
        if key in frame.columns:
            frame.at[40, key] = value
        else:
            frame.at[40, '__test_records'][0][key] = value
    report = build(frame, metadata)
    assert report['chips'] == []
    assert report['summary']['excluded_counts'][reason] == 1


def test_last_invalid_execution_never_falls_back_to_an_older_outlier():
    frame, metadata = dataset()
    event = deepcopy(frame.at[40, '__test_records'][0])
    event.update(record_sequence=418, result_valid=False)
    frame.at[40, '__test_records'].append(event)
    report = build(frame, metadata)
    assert report['chips'] == []
    assert report['summary']['excluded_counts']['INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'] == 1


def test_mpr_return_indices_are_separate_and_traceable():
    frame, metadata = dataset(test_count=2)
    for row in frame['__test_records']:
        for index, event in enumerate(row):
            event.update(record_type='MPR', result_index=index, test_num=1001)
    report = build(frame, metadata)
    assert {hit['result_index'] for hit in report['chips'][0]['hits']} == {0, 1}
    assert {hit['record_sequence'] for hit in report['chips'][0]['hits']} == {410, 411}


@pytest.mark.parametrize('values,status,reason', [
    ([10.] * 40, 'NOT_CALCULABLE', 'ZERO_ROBUST_SPREAD'),
    ([10.] * 3, 'INSUFFICIENT_DATA', 'TOO_FEW_VALID_SAMPLES'),
])
def test_unevaluable_populations_are_never_reported_as_pass(values, status, reason):
    frame, metadata = dataset({(1, 1, 'W1'): values})
    report = build(frame, metadata)
    assert report['chips'] == []
    assert report['baselines'][0]['status'] == status and report['baselines'][0]['reason'] == reason
    assert report['summary']['unevaluated_baseline_count'] == 1


def test_functional_test_is_not_applicable():
    frame, metadata = dataset()
    for row in frame['__test_records']:
        row[0]['record_type'] = 'FTR'
    report = build(frame, metadata)
    assert report['chips'] == []
    assert all(row['status'] == 'NOT_APPLICABLE' for row in report['baselines'])


def test_selection_is_respected_and_empty_unexecuted_are_explicit():
    frame, metadata = dataset(test_count=2)
    report = build(frame, metadata, sites=[2], test_columns=['TEST_1001'])
    assert len(report['baselines']) == 1 and report['chips'] == []
    assert report['selection']['selected_record_count'] == 40
    empty = build(frame, metadata, part_indices=[])
    assert empty['status'] == 'EMPTY' and empty['chips'] == []
    assert empty['unexecuted_test_columns'] == ['TEST_1001', 'TEST_1002']
    assert build(frame, metadata, test_columns=[])['baselines'] == []


def test_zero_spec_limit_and_negative_tail_are_preserved():
    frame, metadata = dataset({(1, 0, 'W1'): population(10.) + [5.]})
    report = build(frame, metadata)
    assert report['baselines'][0]['lsl'] == 0.
    assert report['chips'][0]['hits'][0]['direction'] == 'LOW'
    assert report['chips'][0]['site'] == 0


def test_threshold_is_strict_and_configurable():
    values = [-.675] * 12 + [0.] * 16 + [.675] * 12 + [6.]
    stats, hits = robust_outlier_statistics(values, 6., 30)
    assert stats['median'] == 0. and stats['robust_sigma'] == 1. and hits == []
    assert robust_outlier_statistics(values, 5., 30)[1] == [(40, 6.)]


def test_extreme_finite_inputs_produce_json_safe_unevaluable_state():
    stats, hits = robust_outlier_statistics([-1.e308] * 20 + [1.e308] * 20, 6., 30)
    assert stats['status'] == 'NOT_CALCULABLE' and hits == []
    json.dumps(stats, allow_nan=False)


@pytest.mark.parametrize('options', [
    {'sigma_multiplier': True}, {'sigma_multiplier': 0}, {'sigma_multiplier': float('nan')},
    {'sigma_multiplier': float('inf')}, {'minimum_samples_per_site': True},
    {'minimum_samples_per_site': 3}, {'minimum_samples_per_site': 4.5},
    {'part_indices': [True]}, {'part_indices': [999]}, {'sites': [99]},
    {'test_columns': ['missing']}, {'test_columns': 'TEST_1001'},
])
def test_invalid_parameters_are_rejected(options):
    frame, metadata = dataset()
    with pytest.raises(ValueError):
        build(frame, metadata, **options)


def test_inputs_are_unchanged_and_missing_physical_identity_is_not_invented():
    frame, metadata = dataset()
    frame = frame.drop(columns=['STDF_PART_ID', 'WAFER_ID', 'LOT_ID', 'DIE_X', 'DIE_Y', '__source_record_sequence'])
    before = deepcopy(frame.to_dict('records'))
    report = build(frame, metadata)
    assert frame.to_dict('records') == before
    chip = report['chips'][0]
    assert chip['part_index'] == 41
    assert all(chip[key] is None for key in ('part_id', 'wafer', 'lot', 'x', 'y', 'prr_record_sequence'))
