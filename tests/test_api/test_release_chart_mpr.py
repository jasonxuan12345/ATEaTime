"""Every MPR result must use its own values in evidence, not the first result."""
import struct

import pytest

from tests.test_api.test_release_check import client, cn, record


def test_mpr_details_match_each_subresult_population(client):
    data = record(0, 10, bytes([2, 4]))
    for index, (a, b) in enumerate([(1., 10.), (2., 20.), (3., 30.)]):
        mpr = struct.pack('<IBBBBHH', 4001, 1, 1, 0, 0, 0, 2)
        mpr += struct.pack('<ff', a, b) + cn('MULTI') + cn('')
        mpr += struct.pack('<Bbbbffff', 2, 0, 0, 0, 0., 100., 0., 0.)
        mpr += cn('V') + cn('')
        prr = struct.pack('<BBBHHHhhI', 1, 1, 0, 2, 1, 1, index, 0, 0)
        data += record(5, 10, bytes([1, 1])) + record(15, 15, mpr)
        data += record(5, 20, prr + cn(str(index)) + cn('') + b'\0')
    uploaded = client.post('/api/release-check/datasets', files={'file': ('multi.stdf', data)})
    assert uploaded.status_code == 200, uploaded.text
    dataset = uploaded.json()['id']
    response = client.post(f'/api/release-check/datasets/{dataset}/run', json={'modules': ['cpk', 'cgm']})
    assert response.status_code == 200, response.text
    result = response.json()
    means = []
    for row in result['rows']:
        detail = client.get(f"/api/release-check/datasets/{dataset}/runs/{result['id']}/items/{row['key']}")
        assert detail.status_code == 200, detail.text
        modules = detail.json()['details']
        for module in ('cpk', 'cgm'):
            site = modules[module]['sites'][0]
            assert site['n'] == 3 and sum(site['histogram']) == 3
            if module == 'cpk':
                assert site['mean'] == pytest.approx(modules[module]['mean'])
        means.append(modules['cgm']['sites'][0]['mean'])
    assert sorted(means) == pytest.approx([2., 20.])
