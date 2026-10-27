"""Each Release module uses its chosen file, with traceable cross-file support."""
import json
import pytest
from app import release_check_service as service
from tests.test_api.test_release_check import client, stdf

CONDITIONS = {"part_type": "PART", "job_name": "PROGRAM", "job_revision": "R1",
              "test_code": "FT", "test_temperature": "25"}


def upload(client, name, values):
    response = client.post('/api/release-check/datasets', files={'file': (name, stdf(values))})
    assert response.status_code == 200
    dataset = response.json()
    source = service.get_dataset(dataset['id'])
    source.frame['__conditions_json'] = json.dumps(CONDITIONS)
    # These fixtures intentionally edit events; production EventView is immutable.
    source.frame['__test_records'] = source.frame['__test_records'].map(
        lambda events: [dict(event) for event in events])
    return dataset


def calculate(client, a, b, **cg_selection):
    return client.post('/api/release-check/runs', json={
        'cpk': {'dataset_id': a['id']}, 'cgm': {'dataset_id': b['id'], **cg_selection}})


def test_different_sources_compute_separately_match_and_trace(client):
    a = upload(client, 'capability.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'repeatability.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    response = calculate(client, a, b)
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['cpk']['value'] > 2 and row['cgm']['value'] < 1
    assert row['cgm']['override'] and row['status'] == 'PASS'
    assert row['cpk']['dataset_id'] == a['id'] and row['cgm']['dataset_id'] == b['id']
    assert row['cpk']['mean'] == pytest.approx(8)
    assert row['cpk']['std'] < .2
    assert row['cpk']['cp'] == pytest.approx(14 / (6 * row['cpk']['std']))
    assert result['sources']['cgm']['filename'] == 'repeatability.stdf'
    assert row['cpk']['diagnosis']['source']['filename'] == 'capability.stdf'
    assert row['cgm']['diagnosis']['source']['filename'] == 'repeatability.stdf'
    url = f"/api/release-check/datasets/{result['dataset_id']}/runs/{result['id']}"
    detail = client.get(f"{url}/items/{row['key']}").json()
    assert detail['details']['cpk']['mean'] == pytest.approx(8)
    assert detail['details']['cpk']['std'] < .2
    assert all(site['std'] > 5 for site in detail['details']['cgm']['sites'])
    exported = client.get(f'{url}/report').json()
    assert exported['result']['sources']['cpk']['filename'] == 'capability.stdf'
    assert exported['reports']['cgm']['bin1_cpk_source'] == exported['reports']['cpk']['source']
    # Calculation and JSON export use the calculation view. Full Parquet is
    # generated only by the explicit Save Parquet action.
    assert not service.get_dataset(a['id']).parquet.exists()
    assert not service.get_dataset(b['id']).parquet.exists()
    assert service.get_dataset(a['id']).source.exists() and service.get_dataset(b['id']).source.exists()


@pytest.mark.parametrize('field,value', [('job_revision', 'R2'), ('test_temperature', '125'),
                                        ('job_name', ''), ('test_code', 'CP')])
def test_different_or_missing_conditions_do_not_block_cross_file_match(client, field, value):
    a = upload(client, 'A.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'B.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    service.get_dataset(b['id']).frame['__conditions_json'] = json.dumps({**CONDITIONS, field: value})
    result = calculate(client, a, b).json()
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert row['cgm']['override'] and row['cgm']['value'] < 1
    assert row['status'] == 'PASS'


def test_source_selections_and_part_ids_are_local_to_each_file(client):
    a = upload(client, 'A.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'B.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    result = calculate(client, a, b, sites=[1], part_indices=[1, 3, 5]).json()
    row = result['rows'][0]
    assert row['cpk']['n'] == 6 and row['cgm']['n'] == 3
    service.remove_dataset(b['id'])
    assert calculate(client, a, b).status_code == 404


def test_cross_source_unexecuted_rows_keep_catalog_names(client):
    a = upload(client, 'A.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'B.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    response = calculate(client, a, b, part_indices=[])
    row = next(r for r in response.json()['rows'] if r['cgm'])
    assert row['name'] == 'VDD' and row['number'] == 1001
    assert row['cgm']['n'] == 0 and not row['cgm']['override']


@pytest.mark.parametrize('unit', ['mV', None])
def test_three_results_join_by_id_and_name_with_independent_specs(client, unit):
    a = upload(client, 'capability.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'repeatability.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    source = service.get_dataset(b['id'])
    source.frame['__conditions_json'] = json.dumps({})
    for events in source.frame['__test_records']:
        events[0].update(unit=unit, low_limit=0, high_limit=16)
    response = client.post('/api/release-check/runs', json={
        'cpk': {'dataset_id': a['id']}, 'cgm': {'dataset_id': b['id']},
        'mmsa': {'dataset_id': a['id']}})
    assert response.status_code == 200, response.text
    result = response.json()
    assert len(result['rows']) == 1
    row = result['rows'][0]
    assert all(row[module] is not None for module in ('cpk', 'cgm', 'mmsa'))
    assert row['cpk']['dataset_id'] == row['mmsa']['dataset_id'] == a['id']
    assert row['cgm']['dataset_id'] == b['id']
    url = f"/api/release-check/datasets/{result['dataset_id']}/runs/{result['id']}"
    reports = client.get(f'{url}/report').json()['reports']
    cpk, cgm, mmsa = (reports[m]['items'][0] for m in ('cpk', 'cgm', 'mmsa'))
    assert cgm['supported_by_bin1_cpk'] == cpk['cpk']
    assert mmsa['cgm_min_cg'] == cgm['cg']
    assert (cpk['effective_lsl'], cpk['effective_usl']) == (1, 15)
    assert (cgm['effective_lsl'], cgm['effective_usl'], cgm['unit']) == (0, 16, unit)
    assert row['cpk']['unit'] == 'V' and row['cgm']['unit'] == unit
    assert cgm['cg'] == pytest.approx(.20 * 16 / 6 / cgm['sites'][0]['std'])
    assert cgm['status'] == 'PASS' and cgm['cpk_override_applied']
    if unit is None:
        assert '单位缺失' in reports['cgm']['warnings']
    detail = client.get(f"{url}/items/{row['key']}").json()['details']
    assert set(detail) == {'cpk', 'cgm', 'mmsa'}
    assert detail['cpk']['std'] < .2 and all(s['std'] > 5 for s in detail['cgm']['sites'])


@pytest.mark.parametrize('field,value', [('test_num', 1002), ('name', 'OTHER')])
def test_different_id_or_name_does_not_join(client, field, value):
    a = upload(client, 'A.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'B.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    for events in service.get_dataset(b['id']).frame['__test_records']:
        events[0][field] = value
    result = calculate(client, a, b).json()
    assert len(result['rows']) == 2
    assert all(not r['cgm']['override'] for r in result['rows'] if r['cgm'])


@pytest.mark.parametrize('duplicate_source', ['cpk', 'cgm'])
def test_duplicate_test_pair_is_not_arbitrarily_joined(client, duplicate_source):
    a = upload(client, 'A.stdf', [7.9, 7.9, 8, 8, 8.1, 8.1])
    b = upload(client, 'B.stdf', [1.1, 1.1, 8, 8, 14.9, 14.9])
    # Two local specification groups with the same ID/name are ambiguous.
    duplicated = a if duplicate_source == 'cpk' else b
    for events in service.get_dataset(duplicated['id']).frame['__test_records'][3:]:
        events[0]['high_limit'] = 16
    result = calculate(client, a, b).json()
    assert len(result['rows']) == 3
    assert not next(r['cgm'] for r in result['rows'] if r['cgm'])['override']
