"""Offline bilingual browser checks against the production build.

Run with the project's Python and Playwright. All requests are intercepted;
no live service, saved user files or external websites are contacted.
"""
import argparse
from datetime import datetime
import json
from pathlib import Path
import re
import sys
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright
from tests.ui.check_outlier_layout import fixture

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend/dist'


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'runtime' / ('i18n-' + datetime.now().strftime('%Y%m%d-%H%M%S')))
    parser.add_argument('--quick', action='store_true')
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    dataset, run, report, view = fixture()

    def respond(route):
        url = urlparse(route.request.url)
        path = url.path
        if path in ('/', '/release-check'):
            route.fulfill(path=str(DIST / 'index.html'), content_type='text/html')
        elif path.startswith('/assets/'):
            asset = (DIST / path.lstrip('/')).resolve()
            if asset.is_relative_to(DIST.resolve()) and asset.is_file():
                route.fulfill(path=str(asset), content_type='text/css' if asset.suffix == '.css' else 'text/javascript')
            else:
                route.fulfill(status=404)
        elif path == '/api/release-check/datasets' and route.request.method == 'POST':
            route.fulfill(json=dataset)
        elif path == '/api/release-check/runs' and route.request.method == 'POST':
            route.fulfill(json=run)
        elif path.endswith('/outliers') and route.request.method == 'POST':
            route.fulfill(json=report)
        elif path.endswith('/outlier-reports') and route.request.method == 'PUT':
            route.fulfill(json={'source_count': 1, 'version': 1})
        elif path.endswith('/view'):
            route.fulfill(json=view(parse_qs(url.query)['group_id'][0]))
        elif '/items/' in path:
            route.fulfill(json={'row': run['rows'][0], 'details': {}})
        else:
            route.fulfill(status=404, json={'detail': 'Offline layout check'})

    results, errors = [], []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, channel='msedge')
        try:
            page = browser.new_page(viewport={'width': 1440, 'height': 1100})
            page.route('**/*', respond)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://datalog-check.test/release-check', wait_until='networkidle')

            def record(name, language, width):
                page.wait_for_load_state('networkidle')
                data = page.evaluate('''() => {
                    const visible = element => { const r = element.getBoundingClientRect(); const s = getComputedStyle(element); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
                    const controls = [...document.querySelectorAll('button, summary')].filter(visible);
                    const clipped = controls.filter(element => element.scrollWidth > element.clientWidth + 2).map(element => element.textContent.trim());
                    const overlap = [];
                    for (let i=0; i<controls.length; i++) for (let j=i+1; j<controls.length; j++) {
                        if (controls[i].parentElement !== controls[j].parentElement) continue;
                        const a=controls[i].getBoundingClientRect(), b=controls[j].getBoundingClientRect();
                        if (Math.min(a.right,b.right)-Math.max(a.left,b.left)>2 && Math.min(a.bottom,b.bottom)-Math.max(a.top,b.top)>2) overlap.push([controls[i].textContent.trim(),controls[j].textContent.trim()]);
                    }
                    const outside = [...document.querySelectorAll('*')].filter(visible).filter(el=>el.getBoundingClientRect().right>innerWidth+2).slice(0,20).map(el=>({tag:el.tagName,cls:el.getAttribute('class'),right:el.getBoundingClientRect().right,width:el.clientWidth,scroll:el.scrollWidth,overflow:getComputedStyle(el).overflowX}));
                    return {overflow:document.documentElement.scrollWidth>innerWidth+2, width:document.documentElement.scrollWidth, outside, clipped, overlap, text:document.body.innerText};
                }''')
                chinese = [line for line in data.pop('text').splitlines() if re.search(r'[\u3400-\u9fff]', line) and line.strip() != '中文'] if language == 'en' else []
                results.append({'screen': name, 'language': language, 'viewport': width, 'untranslated': chinese, **data})
                page.screenshot(path=str(args.output / f'{name}-{language}-{width}.png'), full_page=True)

            widths = (390,) if args.quick else (1920, 1440, 1280, 1024, 768, 390)
            for width in widths:
                page.set_viewport_size({'width': width, 'height': 1100})
                for language in ('zh', 'en'):
                    page.get_by_role('button', name='English' if language == 'en' else '中文', exact=True).click()
                    for module in ('Bin1 CPK', 'CGM', 'Site Diff'):
                        page.get_by_role('button', name=module, exact=True).click()
                        record(module.replace(' ', '-'), language, width)
                    notes = page.get_by_role('region', name='Chart observations and remarks' if language == 'en' else '图形观察与结论记录', exact=True)
                    notes.get_by_role('button', name='Expand' if language == 'en' else '展开', exact=True).click()
                    record('remarks-expanded', language, width)
                    notes.get_by_role('button', name='Collapse' if language == 'en' else '收起', exact=True).click()
                    page.get_by_role('button', name='Overview' if language == 'en' else '综合总览', exact=True).click()
                    record('overview', language, width)
                    page.locator('header').first.get_by_text('Export' if language == 'en' else '导出', exact=True).click()
                    record('export-menu', language, width)
                    page.locator('header').first.get_by_text('Export' if language == 'en' else '导出', exact=True).click()
                    page.get_by_role('button', name='Data and calculation settings' if language == 'en' else '数据源与计算配置', exact=True).click()
                    record('settings', language, width)
                    for module in ('CGM', 'Site Diff', 'Outlier'):
                        page.get_by_role('dialog').get_by_role('button', name=module, exact=True).click()
                        record('settings-' + module.replace(' ', '-'), language, width)
                    page.get_by_role('dialog').get_by_role('button', name='bin1 CPK', exact=True).click()
                    for title in ('Select test', 'Select test record') if language == 'en' else ('选择测项', '选择测试记录'):
                        page.get_by_role('dialog').locator('summary').filter(has_text=title).first.click()
                    record('settings-expanded', language, width)
                    page.get_by_role('dialog').get_by_role('button', name='Close' if language == 'en' else '关闭', exact=True).click()

            # Check real-data Outlier views with long filenames and extreme scores.
            page.set_viewport_size({'width': 1440, 'height': 1100})
            page.get_by_role('button', name='中文', exact=True).click()
            page.get_by_role('button', name='数据源与计算配置', exact=True).click()
            page.get_by_label('选择 STDF 文件').set_input_files({'name': dataset['filename'], 'mimeType': 'application/octet-stream', 'buffer': b'offline fixture'})
            page.get_by_role('button', name='计算 Release 检查', exact=True).click()
            page.get_by_text('计算完成', exact=True).wait_for()
            dialog = page.get_by_role('dialog')
            if dialog.is_visible():
                dialog.get_by_role('button', name='关闭', exact=True).click()
            page.get_by_role('button', name='Outlier', exact=True).click()
            for width in widths:
                page.set_viewport_size({'width': width, 'height': 1100})
                for language in ('zh', 'en'):
                    page.get_by_role('button', name='English' if language == 'en' else '中文', exact=True).click()
                    record('outlier', language, width)
                    page.get_by_role('button', name='Test sequence trend' if language == 'en' else '测试序列趋势图', exact=True).click()
                    record('outlier-sequence', language, width)
                    page.get_by_role('button', name='Distribution range view' if language == 'en' else '分布区间视图', exact=True).click()
            page.reload(wait_until='networkidle')
            assert page.locator('html').get_attribute('lang') == 'en'
        finally:
            browser.close()
    failed = [item for item in results if item['overflow'] or item['clipped'] or item['overlap'] or item['untranslated']]
    summary = {'checks': len(results), 'failures': failed, 'page_errors': errors, 'screenshots': str(args.output)}
    (args.output / 'result.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    assert not failed and not errors, 'See result.json and screenshots'


if __name__ == '__main__':
    main()
