"""Offline browser regression for Outlier clamping, layout, and linked selection."""
from datetime import datetime
import json
import sys
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend' / 'dist'


def fixture():
    dies = [{'lot': 'L1', 'wafer': 'W08', 'x': x, 'y': y}
            for x in range(-12, 13) for y in range(-12, 13) if x*x + y*y <= 144]
    name = 'vp150__38082_extremely_long_original_filename.stdf'
    dataset = {'id': 'visual-source', 'filename': name, 'record_count': len(dies), 'sites': [1, 2, 3, 4], 'conditions': {},
        'tests': [{'column': 'VREF', 'name': 'VREF', 'number': 1001, 'unit': 'V'},
                  {'column': 'LONG', 'name': 'LONG_VOLTAGE_REFERENCE_MEASUREMENT', 'number': 1002, 'unit': 'V'}],
        'parts': [{'id': i, 'name': str(i), 'site': i % 4 + 1, 'bin': 1} for i in range(len(dies))]}
    baselines = []
    for group, test in enumerate(dataset['tests'], 1):
        for site in (1, 2, 3, 4):
            baselines.append({'baseline_id': f'b-{group}-{site}', 'group_id': f'g-{group}', 'column': test['column'], 'test_num': test['number'],
                'test_name': test['name'], 'record_type': 'PTR', 'result_index': None, 'conditions': {}, 'lot': 'L1', 'wafer': 'W08', 'head': 1, 'site': site,
                'unit': 'V', 'lsl': -1e12, 'usl': 1e12, 'n': 100, 'q1': .9865, 'median': 1, 'q3': 1.0135,
                'robust_sigma': .02, 'lower_limit': .88, 'upper_limit': 1.12, 'status': 'OUTLIER_CANDIDATES', 'reason': None, 'candidate_count': 5, 'excluded_counts': {}})
    chips = []
    for i, die in enumerate(dies[::max(1, len(dies)//20)][:20]):
        score = [8.2, 45.5, 10000, 6.36e11, 12.3][i % 5]
        site = i % 4 + 1
        hits = []
        for group in (1, 2):
            baseline = next(b for b in baselines if b['group_id'] == f'g-{group}' and b['site'] == site)
            hit = {key: baseline[key] for key in ('baseline_id', 'group_id', 'column', 'test_num', 'test_name', 'record_type', 'result_index', 'unit', 'median', 'robust_sigma', 'lower_limit', 'upper_limit')}
            hit.update(record_sequence=i * 2 + group, value=1 + score * .02, score=score, direction='HIGH')
            hits.append(hit)
        chips.append({**die, 'part_index': i, 'prr_record_sequence': i * 2 + 10, 'part_id': str(i), 'head': 1, 'site': site, 'hit_count': 2, 'max_score': score, 'hits': hits})
    report = {'dataset_id': dataset['id'], 'report_id': 'visual-report', 'report': {'baselines': baselines, 'chips': chips,
        'warnings': [], 'unexecuted_test_columns': [], 'algorithm': {'sigma_multiplier': 6, 'minimum_samples_per_site': 30},
        'summary': {'selected_record_count': len(dies), 'candidate_part_count': len(chips), 'candidate_hit_count': len(chips)*2, 'evaluated_baseline_count': 8, 'unevaluated_baseline_count': 0}}}
    cell = {'status': 'PASS', 'value': 2.3, 'n': len(dies), 'reason': '通过', 'rule_id': None, 'override': False, 'supported_by_bin1_cpk': None}
    row = {'key': 'row-1', 'column': 'VREF', 'name': 'VREF', 'number': 1001, 'unit': 'V', 'lsl': -1e12, 'usl': 1e12, 'conditions': {},
           'cpk': cell, 'cgm': cell, 'mmsa': cell, 'status': 'PASS', 'reason': '通过'}
    run = {'id': 'visual-run', 'dataset_id': dataset['id'], 'rows': [row], 'warnings': [], 'rules_hash': 'OFFLINE-VISUAL-CHECK'}
    def view(group):
        points = [{'part_index': i, 'record_sequence': i * 2, 'value': 1 + (i % 7 - 3) * .003, 'site': i % 4 + 1, 'head': 1,
            **die, 'baseline_id': f'b-{group[-1]}-{i % 4 + 1}'} for i, die in enumerate(dies)]
        return {'dataset_id': dataset['id'], 'report_id': 'visual-report', 'group_id': group, 'dies': dies, 'points': points, 'total_points': len(points), 'sampled': False}
    return dataset, run, report, view


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    output = ROOT / 'runtime' / ('outlier-layout-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True)
    dataset, run, report, view = fixture()
    errors, checks = [], {}
    def respond(route):
        url = urlparse(route.request.url)
        path = url.path
        if path in ('/', '/release-check'):
            route.fulfill(path=str(DIST / 'index.html'), content_type='text/html')
        elif path.startswith('/assets/'):
            asset = (DIST / path.lstrip('/')).resolve()
            if asset.is_relative_to(DIST.resolve()) and asset.is_file():
                route.fulfill(path=str(asset), content_type='text/css' if asset.suffix == '.css' else 'text/javascript')
            else: route.fulfill(status=404)
        elif path == '/api/release-check/datasets' and route.request.method == 'POST': route.fulfill(json=dataset)
        elif path == '/api/release-check/runs' and route.request.method == 'POST': route.fulfill(json=run)
        elif path.endswith('/outliers') and route.request.method == 'POST': route.fulfill(json=report)
        elif path.endswith('/view'): route.fulfill(json=view(parse_qs(url.query)['group_id'][0]))
        elif '/items/' in path: route.fulfill(json={'row': run['rows'][0], 'details': {}})
        else: route.fulfill(status=404, json={'detail': 'Offline check: no live service'})
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1920, 'height': 1400})
            page.route('**/*', respond)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://datalog-check.test/release-check', wait_until='networkidle')
            drawer = page.get_by_role('dialog', name='数据源与计算配置')
            if not drawer.is_visible(): page.get_by_role('button', name='数据源与计算配置', exact=True).click()
            page.get_by_label('选择 STDF 文件').set_input_files({'name': dataset['filename'], 'mimeType': 'application/octet-stream', 'buffer': b'offline fixture'})
            page.get_by_role('option', name=dataset['filename'], exact=True).wait_for(state='attached')
            page.get_by_role('button', name='计算 Release 检查', exact=True).click()
            page.get_by_text('计算完成', exact=True).wait_for()
            page.get_by_role('button', name='Outlier', exact=True).click()
            board = page.get_by_role('region', name='Outlier 测项分析', exact=True)
            board.wait_for()
            page.wait_for_load_state('networkidle')
            checks['titles'] = board.get_by_role('heading', level=3).all_text_contents()
            checks['status'] = board.get_by_role('status', name='当前 Outlier 选中状态').inner_text()
            canvases = board.locator('.base-echart')
            checks['canvases'] = canvases.evaluate_all('elements => elements.map(element => ({width: element.clientWidth, height: element.clientHeight, canvas: !!element.querySelector("canvas")}))')
            assert len(checks['titles']) == 4, checks
            assert all(item['width'] > 150 and item['height'] >= 250 and item['canvas'] for item in checks['canvases']), checks
            assert '6.36e+11x' in checks['status'], checks
            board.screenshot(path=str(output / 'outlier-desktop.png'))
            normal = board.get_by_role('button', name='选择记录 0，' + dataset['filename'] + '，偏离 8.2x', exact=True).first
            normal.click()
            page.wait_for_function("document.querySelector('[aria-label=\"当前 Outlier 选中状态\"]').textContent.includes('8.2x')")
            checks['linked_status'] = board.get_by_role('status', name='当前 Outlier 选中状态').inner_text()
            board.screenshot(path=str(output / 'outlier-selected.png'))
            bar = board.get_by_role('img', name='当前候选的命中测项偏离倍数').locator('canvas').first
            bar.click(position={'x': 120, 'y': 192})
            page.wait_for_function("document.querySelector('[aria-label=\"当前 Outlier 选中状态\"]').textContent.includes('LONG_VOLTAGE_REFERENCE_MEASUREMENT')")
            page.wait_for_load_state('networkidle')
            checks['bar_selection'] = board.get_by_role('status', name='当前 Outlier 选中状态').inner_text()
            matrix = board.get_by_role('img', name='候选记录与测项命中矩阵').locator('canvas').first
            box = matrix.bounding_box()
            matrix.click(position={'x': 54 + (box['width'] - 60) / 4, 'y': 6 + (box['height'] - 50) * 1.5 / 4})
            page.wait_for_function("document.querySelector('[aria-label=\"当前 Outlier 选中状态\"]').textContent.includes('1001 VREF')")
            page.wait_for_load_state('networkidle')
            checks['matrix_selection'] = board.get_by_role('status', name='当前 Outlier 选中状态').inner_text()
            target = report['report']['chips'][2]
            wafer_canvas = board.get_by_role('img', name='Wafer 候选坐标位置图').locator('canvas').first
            box = wafer_canvas.bounding_box()
            scale = (min(box['width'], box['height']) - 34) / (2 * 12 * 1.06)
            wafer_canvas.click(position={'x': box['width'] / 2 + target['x'] * scale,
                                         'y': box['height'] / 2 - target['y'] * scale})
            page.wait_for_function("text => document.querySelector('[aria-label=\"当前 Outlier 选中状态\"]').textContent.includes(text)",
                                   arg=f"(X:{target['x']}, Y:{target['y']})")
            checks['wafer_selection'] = board.get_by_role('status', name='当前 Outlier 选中状态').inner_text()
            board.get_by_role('button', name='测试序列趋势图', exact=True).click()
            board.get_by_role('img', name='Outlier 测试序列趋势图').wait_for()
            page.wait_for_load_state('networkidle')
            board.screenshot(path=str(output / 'outlier-trend.png'))
            checks['trend'] = True
            page.set_viewport_size({'width': 1000, 'height': 1000})
            board.get_by_role('button', name='分布区间视图', exact=True).click()
            page.wait_for_load_state('networkidle')
            board.screenshot(path=str(output / 'outlier-narrow.png'))
        finally:
            browser.close()
    checks['page_errors'] = errors
    checks['screenshots'] = str(output)
    (output / 'result.json').write_text(json.dumps(checks, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    assert not errors, errors


if __name__ == '__main__': main()
