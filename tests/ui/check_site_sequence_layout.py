"""Check real chart dimensions using the compiled UI and demo data, offline.

Run with: python tests/ui/check_site_sequence_layout.py
"""
from datetime import datetime
import json
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / 'frontend' / 'dist'


def main():
    output = ROOT / 'runtime' / ('site-sequence-layout-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    output.mkdir(parents=True)
    errors, results = [], []

    def local_files(route):
        path = urlparse(route.request.url).path
        if path in ('/', '/release-check'):
            route.fulfill(path=str(DIST / 'index.html'), content_type='text/html')
        elif path.startswith('/assets/'):
            asset = (DIST / path.lstrip('/')).resolve()
            if asset.is_relative_to(DIST.resolve()) and asset.is_file():
                route.fulfill(path=str(asset), content_type='text/css' if asset.suffix == '.css' else 'text/javascript')
            else:
                route.fulfill(status=404, body='Missing local asset')
        else:
            # Never contact a running service or the internet.
            route.fulfill(status=404, body='Offline UI check')

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page(viewport={'width': 1600, 'height': 1100})
            page.route('**/*', local_files)
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://datalog-check.test/release-check', wait_until='networkidle')
            page.get_by_role('button', name='数据源与计算配置', exact=True).wait_for()
            drawer = page.get_by_role('dialog', name='数据源与计算配置')
            if drawer.is_visible():
                drawer.get_by_role('button', name='关闭', exact=True).click()
            for module in ('Bin1 CPK', 'CGM', 'Site Diff'):
                page.get_by_role('button', name=module, exact=True).click()
                region = page.get_by_role('region', name='分 site 测试次序图', exact=True)
                region.wait_for()
                region.locator('canvas').wait_for(state='attached')
                page.wait_for_load_state('networkidle')
                dimensions = region.evaluate('''element => {
                    const host = element.querySelector('.base-echart');
                    const canvas = host.querySelector('canvas');
                    const box = host.getBoundingClientRect();
                    let drawnPixels = 0;
                    if (canvas.width && canvas.height) {
                        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
                        for (let i = 0; i < pixels.length; i += 4) {
                            if (pixels[i + 3] && Math.min(pixels[i], pixels[i + 1], pixels[i + 2]) < 220) drawnPixels++;
                        }
                    }
                    return {width: box.width, height: box.height,
                        canvasWidth: canvas.width, canvasHeight: canvas.height, drawnPixels};
                }''')
                results.append({'module': module, **dimensions})
                region.screenshot(path=str(output / (module.replace(' ', '-') + '.png')))
        finally:
            browser.close()
    report = {'charts': results, 'page_errors': errors, 'screenshots': str(output)}
    (output / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    assert not errors, errors
    assert all(item['width'] > 0 and item['height'] >= 180
               and item['canvasWidth'] > 0 and item['canvasHeight'] >= 180
               and item['drawnPixels'] > 500 for item in results), results


if __name__ == '__main__':
    main()
