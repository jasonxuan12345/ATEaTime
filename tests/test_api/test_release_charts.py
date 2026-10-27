"""Traceable chart, sample-selection and run-scoped observation HTTP contracts."""
from io import BytesIO
from copy import deepcopy

import openpyxl
import pytest

from deepyield.compute.release_remarks import diagnose_item

from app import release_check_service as service
from tests.test_api.test_release_check import client, upload, run


def addresses(client):
    dataset = upload(client)
    result = run(client, dataset)
    root = f"/api/release-check/datasets/{dataset['id']}/runs/{result['id']}"
    key = result['rows'][0]['key']
    return root, key, dataset, result


def test_charts_use_their_own_report_population_and_sampling(client):
    root, key, dataset, result = addresses(client)
    a = client.get(f'{root}/items/{key}/charts/cpk').json()
    b = client.get(f'{root}/items/{key}/charts/cgm').json()
    assert a['statistics']['n'] == 6 and b['statistics']['n'] == 10
    assert a['statistics']['mean'] == pytest.approx(8)
    assert a['statistics']['cp'] is not None
    for metric in ('mean', 'std', 'cp'):
        assert result['rows'][0]['cpk'][metric] == pytest.approx(a['statistics'][metric])
    assert a['context']['module'] == 'cpk'
    assert a['context']['source_sha256']
    assert b['context']['group_id']
    assert a['wafer']['empty_reason']
    selected = client.post(f'{root}/items/{key}/samples/cgm?limit=2', json={'value_max': 2}).json()
    assert selected['total'] == 2 and all(p['value'] <= 2 for p in selected['points'])
    assert result['rows'][0]['cgm']['n'] == 10
    assert client.get(f'{root}/items/{key}/charts/cpk?bins=0').status_code == 422
    assert client.get(f'{root}/items/{key}/charts/cpk?point_limit=10001').status_code == 422
    assert client.post(f'{root}/items/{key}/samples/cpk', json={'unexpected': 1}).status_code == 422


def test_wafer_identity_can_be_adapted_without_a_formal_asset(client):
    dataset = upload(client)
    source = service.get_dataset(dataset['id'])
    source.frame['LOT_ID'] = 'L'
    source.frame['WAFER_ID'] = 'W'
    source.frame['DIE_X'] = list(range(10))
    source.frame['DIE_Y'] = 0
    result = run(client, dataset)
    root = f"/api/release-check/datasets/{dataset['id']}/runs/{result['id']}"
    chart = client.get(f"{root}/items/{result['rows'][0]['key']}/charts/cgm").json()
    assert chart['wafer']['wafers'][0]['total_dies'] == 10
    assert chart['wafer']['display'] is None
    selected = client.post(f"{root}/items/{result['rows'][0]['key']}/samples/cgm",
        json={'wafer_key': ['L', 'W'], 'x_min': 0, 'x_max': 1}).json()
    assert selected['total'] == 2


def test_matrix_and_observations_preserve_verdict_and_support_undo_and_xlsx(client):
    root, key, dataset, result = addresses(client)
    original = result['rows'][0]['status']
    matrix = client.get(f'{root}/chart-matrix').json()
    assert matrix['total'] == 1 and matrix['groups'][0]['count'] == 1
    assert client.post(f'{root}/chart-matrix', json={'keys': []}).json()['total'] == 0
    scoped = client.post(f'{root}/chart-matrix', json={'keys': [key]}).json()
    assert scoped['total'] == 1 and scoped['groups'][0]['keys'] == [key]
    payload = {'keys': [key], 'module': 'cgm', 'tags': ['end_spikes'], 'suggestion': 'Check settling time',
        'confirmed': True, 'overwrite': False, 'version': 0}
    saved = client.post(f'{root}/observations', json=payload)
    assert saved.status_code == 200, saved.text
    note = saved.json()['notes']['cgm:' + key]
    assert 'sequence spikes observed' in note['remark']
    assert note['source']['source_sha256'] and note['rules_hash']
    report = client.get(f'{root}/report').json()
    assert report['result']['rows'][0]['status'] == original
    assert report['result']['chart_observations']['cgm:' + key] == note
    assert client.post(f'{root}/observations', json=payload).status_code == 422  # stale version
    xlsx = client.get(f'{root}/observations.xlsx')
    assert xlsx.status_code == 200
    sheet = openpyxl.load_workbook(BytesIO(xlsx.content), read_only=True).active
    assert 'Remark' in next(sheet.values)
    assert any('Check settling time' in str(cell) for values in sheet.values for cell in values)
    undo = client.post(f"{root}/observations/undo/{saved.json()['undo_id']}")
    assert undo.status_code == 200 and undo.json()['notes'] == {}
    assert client.post(f'{root}/observations', json={**payload, 'version': 2, 'confirmed': False}).status_code == 422


def test_expired_run_and_missing_module_never_fall_back_to_other_data(client):
    root, key, dataset, result = addresses(client)
    assert client.get(f'{root}/items/{key}/charts/mmsa').status_code == 404
    client.delete(f"/api/release-check/datasets/{dataset['id']}")
    assert client.get(f'{root}/items/{key}/charts/cpk').status_code == 404


def test_64_by_16_family_sort_paging_scope_and_confirmed_batch(client, monkeypatch, tmp_path):
    family_config = tmp_path / 'release_chart_families.yaml'
    family_config.write_text('families:\n  - name: ExampleChannels\n    prefixes: [EXAMPLE_CH]\n', encoding='utf-8')
    monkeypatch.setattr('app.release_chart_service.configuration_file', lambda name: family_config)
    root, key, dataset, _ = addresses(client)
    result, reports = service.get_run(service.get_dataset(dataset['id']), root.rsplit('/', 1)[-1])
    template, metric = deepcopy(result['rows'][0]), deepcopy(reports['cgm']['items'][0])
    result['rows'], reports['cgm']['items'] = [], []
    for index in range(64):
        row, item = deepcopy(template), deepcopy(metric)
        row.update(key=f'family-{index}', name=f'P{1000 + index}_EXAMPLE_CH{index}', number=1000 + index)
        row['cgm']['group_id'] = item['group_id'] = row['key']
        row['cgm']['value'] = .5 + index / 1000
        item['sites'] = [{'site': site, 'cg': row['cgm']['value'] if site == 11 else 2., 'n': 50,
            'status': 'PASS', 'reason': None} for site in range(1, 17)]
        item.update(cg=row['cgm']['value'], test_num=row['number'], test_name=row['name'])
        row['cgm']['diagnosis'] = item['diagnosis'] = diagnose_item('cgm', {**item, '_cg_pass_threshold': 1.33})
        result['rows'].append(row)
        reports['cgm']['items'].append(item)
    scope = [r['key'] for r in result['rows']]
    matrix = client.post(f'{root}/chart-matrix?offset=3&limit=5', json={'keys': scope, 'sort': 'cg'}).json()
    assert matrix['total'] == 64 and len(matrix['groups']) == 1
    assert matrix['groups'][0]['count'] == 64 and matrix['groups'][0]['bad_sites'] == [11]
    assert len(matrix['rows'][0]['sites']) == 16
    assert [r['key'] for r in matrix['rows']] == scope[3:8]
    subset = client.post(f'{root}/chart-matrix', json={'keys': scope[60:], 'sort': 'cg'}).json()
    assert subset['total'] == 4 and subset['groups'][0]['count'] == 4
    payload = {'keys': scope[:2], 'module': 'cgm', 'tags': ['end_spikes'], 'confirmed': True, 'version': 0}
    saved = client.post(f'{root}/observations', json=payload)
    assert saved.status_code == 200, saved.text
    remarks = [n['remark'] for n in saved.json()['notes'].values()]
    assert all(text.startswith('1. Check') and 'Min Cg=' not in text for text in remarks)
    # Each row retains its own measured evidence even when the advice is the same.
    assert 'Min Cg=0.5;' in result['rows'][0]['cgm']['diagnosis']['current_remark']
    assert 'Min Cg=0.501;' in result['rows'][1]['cgm']['diagnosis']['current_remark']
    preserved = client.post(f'{root}/observations', json={**payload, 'version': 1})
    assert preserved.json()['saved'] == [] and preserved.json()['skipped'] == scope[:2]
