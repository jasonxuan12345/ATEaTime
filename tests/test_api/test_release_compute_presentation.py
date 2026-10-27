"""UI summaries, lazy evidence and complete export snapshots agree."""
from copy import deepcopy
from tests.test_api.test_release_outliers import client


UI = {'X-Release-View': 'summary-v1'}


def test_summary_run_loads_full_diagnosis_on_demand_and_keeps_exports(client):
    http, source = client
    response = http.post('/api/release-check/datasets/memory/run', headers=UI,
        json={'modules': ['cpk', 'cgm', 'mmsa']})
    assert response.status_code == 200, response.text
    result = response.json()
    assert 'Server-Timing' in response.headers
    saved, reports = source.runs[result['id']]
    original = deepcopy(saved)
    assert len(result['rows']) == len(saved['rows'])
    for row in result['rows']:
        for module in result['modules']:
            if not row.get(module):
                continue
            diagnosis = row[module]['diagnosis']
            assert 'source' not in diagnosis and 'selection' not in diagnosis
            full = http.get('/api/release-check' + diagnosis['detail_url'])
            assert full.status_code == 200, full.text
            stored = next(r for r in saved['rows'] if r['key'] == row['key'])[module]['diagnosis']
            assert full.json() == stored
            assert diagnosis['remark'] == stored['remark']
    assert saved == original
    root = f"/api/release-check/datasets/memory/runs/{result['id']}"
    notes = http.get(root + '/observations', headers=UI).json()
    assert all('source' not in value for value in notes['remarks'].values())
    exported = http.get(root + '/report')
    assert exported.status_code == 200
    assert all('source' in row['cpk']['diagnosis'] for row in exported.json()['result']['rows'] if row['cpk'])


def test_compact_outlier_hits_and_lazy_evidence_preserve_full_report(client):
    http, source = client
    result = http.post('/api/release-check/datasets/memory/outliers', headers=UI, json={}).json()
    assert result['wire_format'] == 'release-ui-v1'
    original = source.outlier_runs[result['report_id']]
    report = result['report']
    for chip, expected in zip(report['chips'], original['chips']):
        for packed, hit in zip(chip['hits'], expected['hits']):
            index, sequence, value, score, direction = packed
            baseline = report['baselines'][index]
            assert baseline['baseline_id'] == hit['baseline_id']
            assert [sequence, value, score, direction] == [hit[k] for k in ('record_sequence', 'value', 'score', 'direction')]
    baseline = report['baselines'][0]
    assert http.get('/api/release-check' + baseline['diagnosis']['detail_url']).json() == original['baselines'][0]['diagnosis']
    root = f"/api/release-check/datasets/memory/outliers/{result['report_id']}"
    assert http.get(root + '/report').json() == original
    first = http.get(root + '/candidates?limit=1').json()
    assert first['total'] == original['summary']['candidate_part_count']
    assert 'hits' not in first['rows'][0]
    part = first['rows'][0]['part_index']
    hits = http.get(root + f'/candidates/{part}/hits?limit=1').json()
    expected = next(c for c in original['chips'] if c['part_index'] == part)
    assert hits['total'] == expected['hit_count'] and hits['rows'] == expected['hits'][:1]
    assert http.get(root + '/candidates?limit=201').status_code == 422
    assert http.get(root + '/candidates?offset=-1').status_code == 422
    assert http.get(root + '/baselines/missing/diagnosis').status_code == 404
