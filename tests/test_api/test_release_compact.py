"""Synthetic native uploads stay compact through pooled charts and exports."""
import numpy as np
import pytest

from app import release_check_service as service
from deepyield.compute.release_stdf import read_release_wide_all
from tests.test_api.test_release_check import client
from tests.test_compute.test_release_stdf import record, part, ptr, specification


def generated_stdf():
    body = record(0, 10, b'\2\4')
    for index in range(96):
        site = 1 if index < 64 else 2
        value = 1.8 if index == 63 else 1.2 + (index % 7) * .005
        events = ptr(9., site=site, optional=specification() if index == 0 else None)
        events += ptr(value, site=site, flag=1 if index % 29 == 0 else 0)
        body += part(name=str(index), site=site, tests=events)
    return body


def test_native_compact_uploads_compute_pool_charts_outliers_and_exports(client, monkeypatch):
    ids, values = [], []
    for number in range(2):
        response = client.post('/api/release-check/datasets',
            files={'file': (f'generated-{number}.stdf', generated_stdf())})
        assert response.status_code == 200, response.text
        info = response.json()
        ids.append(info['id'])
        source = service.get_dataset(info['id'])
        assert not source.parquet.exists()
        assert source.source.read_bytes() == generated_stdf()
        assert '1001_VDD' not in source.frame
        assert all(len(events) == 1 for events in source.frame['__test_records'])
        response = client.get(f"/api/release-check/datasets/{info['id']}/parquet")
        assert response.status_code == 200, response.text[:200]
        full, _ = read_release_wide_all(source.parquet)
        assert all(len(events) == 2 for events in full['__test_records'])
        for events in full['__test_records']:
            event = events.latest(None)['1001_VDD']
            if event['test_flag'] == 0:
                values.append(event['result'])

    # After import, calculations, visualisation and exports use the compact
    # memory data. A hidden full-Parquet reload is a regression.
    def forbidden(*args, **kwargs):
        pytest.fail('Calculation must not reload complete Parquet data')
    for name in ('read_table', 'ParquetFile'):
        monkeypatch.setattr('pyarrow.parquet.' + name, forbidden)
    selection = {'dataset_ids': ids, 'minimum_samples': 2}
    response = client.post('/api/release-check/runs',
        json={module: selection for module in ('cpk', 'cgm', 'mmsa')})
    assert response.status_code == 200, response.text
    result = response.json()
    saved_run_id = result['id']
    row = result['rows'][0]
    assert row['cpk']['n'] == row['cgm']['n'] == row['mmsa']['n'] == len(values)
    assert row['cpk']['mean'] == pytest.approx(np.mean(values))
    assert row['cpk']['std'] == pytest.approx(np.std(values, ddof=1))
    root = f"/api/release-check/datasets/{result['dataset_id']}/runs/{result['id']}"
    assert client.get(f"{root}/items/{row['key']}").status_code == 200
    for module in ('cpk', 'cgm', 'mmsa'):
        chart = client.get(f"{root}/items/{row['key']}/charts/{module}")
        assert chart.status_code == 200, chart.text
        assert chart.json()['statistics']['n'] == len(values)
    assert client.get(root + '/report').status_code == 200
    for extension in ('cpk.xlsx', 'cgm.xlsx', 'cpk.pdf', 'cgm.pdf'):
        response = client.get(root + '/reports/' + extension)
        assert response.status_code == 200, response.text[:200]
        assert response.content.startswith(b'PK' if extension.endswith('xlsx') else b'%PDF')

    root = f'/api/release-check/datasets/{ids[0]}/outliers'
    response = client.post(root, json={'minimum_samples_per_site': 4})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result['report']['summary']['candidate_part_count'] >= 1
    group = result['report']['baselines'][0]['group_id']
    response = client.get(root + f"/{result['report_id']}/view", params={'group_id': group})
    assert response.status_code == 200, response.text
    assert response.json()['points']

    # New review reports also resolve native event-ID references, including
    # a pooled Site Diff source and a file-local Outlier reference population.
    run_root = f'/api/release-check/datasets/{ids[0]}/runs/{saved_run_id}'
    linked = client.put(run_root + '/outlier-reports', json={
        'sources': [{'dataset_id': ids[0], 'report_id': result['report_id']}]})
    assert linked.status_code == 200, linked.text
    for module in ('site_diff', 'outlier'):
        for extension in ('xlsx', 'pdf'):
            exported = client.get(run_root + f'/reports/{module}.{extension}')
            assert exported.status_code == 200, exported.text[:200]
            assert exported.content.startswith(b'PK' if extension == 'xlsx' else b'%PDF')
