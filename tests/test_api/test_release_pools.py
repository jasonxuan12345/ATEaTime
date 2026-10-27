"""Pool calculations and all downstream consumers, using in-memory fixtures only."""
import numpy as np
import pytest

from app import release_check_service as service
from tests.test_api.test_release_check import client
from tests.test_compute.test_release_cpk import dataset


@pytest.fixture
def sources(client, tmp_path):
    values = {'a': [4, 4.1, 4.2, 4.3], 'b': [5, 5.1, 5.2, 5.3]}
    for key, data in values.items():
        frame, metadata = dataset(data)
        frame['SITE'] = [1, 2, 1, 2] if key == 'a' else [16, 16, 1, 1]
        metadata.update(source_file=key + '.stdf', source_sha256=key * 64)
        service._datasets[key] = service.ReleaseDataset(None, tmp_path / key, key + '.stdf', frame, metadata)
    return values


def calculate(client, **choices):
    response = client.post('/api/release-check/runs', json=choices)
    assert response.status_code == 200, response.text
    return response.json()


def test_pools_combine_samples_and_resolve_details_charts_exports(client, sources):
    selection = {'dataset_ids': ['a', 'b']}
    result = calculate(client, cpk=selection, cgm=selection, mmsa=selection)
    row = result['rows'][0]
    assert row['cpk']['n'] == row['cgm']['n'] == row['mmsa']['n'] == 8
    assert row['cpk']['mean'] == pytest.approx(np.mean(sources['a'] + sources['b']))
    assert row['cpk']['std'] == pytest.approx(np.std(sources['a'] + sources['b'], ddof=1))
    assert len(service._datasets) == 2  # derived pools do not consume import slots
    info = result['sources']['cpk']
    assert info['dataset_ids'] == ['a', 'b']
    assert [member['part_index_offset'] for member in info['source_files']] == [0, 5]
    owner = service.get_dataset(result['dataset_id'])
    pooled = service.run_source(owner, result, 'cpk')
    assert pooled.frame['__part_index'].tolist() == [1, 2, 3, 4, 6, 7, 8, 9]
    assert pooled.frame['__test_records'][4] is service._datasets['b'].frame['__test_records'][0]
    base = f"/api/release-check/datasets/{result['dataset_id']}/runs/{result['id']}"
    detail = client.get(f"{base}/items/{row['key']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()['details']['cpk']['n'] == 8
    chart = client.get(f"{base}/items/{row['key']}/charts/cpk")
    assert chart.status_code == 200, chart.text
    report = client.get(f'{base}/report')
    assert report.status_code == 200
    assert len(report.json()['reports']['cpk']['items'][0]['sample_refs']) == 8
    for suffix in ('cpk.xlsx', 'cgm.xlsx', 'cpk.pdf', 'cgm.pdf'):
        response = client.get(f'{base}/reports/{suffix}')
        assert response.status_code == 200, response.text[:200]
        assert response.content.startswith(b'PK' if suffix.endswith('xlsx') else b'%PDF')


def test_file_local_indices_do_not_collide_and_sites_are_pool_wide(client, sources):
    result = calculate(client, cpk={'dataset_ids': ['a', 'b'], 'part_indices': [1, 6], 'sites': [1, 16]})
    assert result['rows'][0]['cpk']['n'] == 2
    assert result['rows'][0]['cpk']['mean'] == pytest.approx(4.5)
    assert result['rows'][0]['cgm'] is None
    assert result['rows'][0]['mmsa'] is None
    result = calculate(client, cgm={'dataset_ids': ['a', 'b'], 'sites': [16]})
    assert result['rows'][0]['cgm']['n'] == 2


def test_pool_specs_and_conditions_remain_separate(client, sources):
    for events in service._datasets['b'].frame['__test_records']:
        events[0]['high_limit'] = 10
    result = calculate(client, cpk={'dataset_ids': ['a', 'b']})
    assert len(result['rows']) == 2
    assert sorted(row['cpk']['n'] for row in result['rows']) == [4, 4]


def test_removed_member_invalidates_pool_consumers(client, sources):
    result = calculate(client, cpk={'dataset_ids': ['a', 'b']})
    service.remove_dataset('b')
    base = f"/api/release-check/datasets/a/runs/{result['id']}"
    assert client.get(f"{base}/items/{result['rows'][0]['key']}").status_code == 404
    assert client.get(f'{base}/reports/cpk.xlsx').status_code == 404


@pytest.mark.parametrize('selection', [{}, {'dataset_ids': []}, {'dataset_ids': ['a', 'a']},
    {'dataset_id': 'a', 'dataset_ids': ['b']}, {'dataset_ids': ['']}])
def test_rejects_invalid_pool_choices(client, sources, selection):
    assert client.post('/api/release-check/runs', json={'cpk': selection}).status_code == 422


def test_outlier_only_run_keeps_a_real_exportable_owner(client, sources):
    result = calculate(client, owner_dataset_id='b')
    assert result['dataset_id'] == 'b' and result['modules'] == [] and result['rows'] == []
    assert client.get(f"/api/release-check/datasets/b/runs/{result['id']}/report").status_code == 200
