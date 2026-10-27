from deepyield.i18n import language, localize_report, translate
from app.release_export_cache import ReportFiles


def test_translation_keeps_source_and_user_text_unchanged():
    token = language.set('en')
    try:
        original = {'reason': '通过', 'filename': '通过', 'name': '通过', 'remark': '通过',
                    'suggestion': '通过', 'conditions': {'reason': '通过'}, 'state': '待复核'}
        assert localize_report(original) == {**original, 'reason': 'Pass', 'state': 'Pending review'}
        assert original['reason'] == '通过'
        assert translate('写入 CPK Excel：3 / 10') == 'Writing CPK Excel: 3 / 10'
        assert translate('Wafer Map') == 'Wafer Map'
    finally:
        language.reset(token)


def test_report_file_cache_separates_languages():
    cache = ReportFiles()

    def writer(stream):
        stream.write(translate('待复核').encode())
        return {'filename': 'observations.xlsx'}

    token = language.set('zh')
    try:
        assert cache.build('same-run', writer)['content'].decode() == '待复核'
        language.set('en')
        assert cache.build('same-run', writer)['content'] == b'Pending review'
        language.set('zh')
        assert cache.build('same-run', writer)['content'].decode() == '待复核'
    finally:
        language.reset(token)


def test_observation_excel_translates_tags_and_status_but_keeps_user_text():
    from io import BytesIO
    from zipfile import ZipFile
    from app.release_chart_notes import _write_observations
    snapshot = {'modules': ['cpk'], 'selections': {'cpk': {}}, 'rules_hash': 'rules',
                'sources': {'cpk': {'filename': '通过.stdf'}},
                'rows': [{'key': 'test', 'number': 1, 'name': '通过',
                          'cpk': {'status': 'PASS', 'value': 2, 'n': 10, 'rule_id': None}}],
                'chart_observations': {'cpk:test': {'tags': ['drift'], 'state': '待复核',
                                                 'suggestion': '用户检查建议', 'final_remark': '用户最终备注'}}}
    token = language.set('en')
    try:
        stream = BytesIO()
        _write_observations(snapshot, stream)
        with ZipFile(stream) as book:
            text = ''.join(book.read(name).decode() for name in book.namelist() if name.endswith('.xml'))
        assert 'Continuous drift' in text and 'Pending review' in text
        assert '用户检查建议' in text and '用户最终备注' in text and '通过.stdf' in text
        assert '持续漂移' not in text and '待复核' not in text
    finally:
        language.reset(token)
