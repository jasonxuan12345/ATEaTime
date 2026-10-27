"""Presentation-only translations; saved reports and input identities stay canonical."""
from contextvars import ContextVar
from functools import lru_cache
import json
from pathlib import Path
import re

language = ContextVar('release_language', default='zh')


@lru_cache(maxsize=1)
def _catalog():
    return json.loads((Path(__file__).resolve().parents[1] / 'config/i18n/zh-en.json').read_text(encoding='utf-8'))


@lru_cache(maxsize=1)
def _patterns():
    entries = sorted(_catalog().items(), key=lambda item: len(re.sub(r'\{\d+\}', '', item[0])), reverse=True)
    patterns = []
    for source, target in entries:
        if not re.search(r'\{\d+\}', source):
            continue
        slots = []
        parts = []
        for part in re.split(r'(\{\d+\})', source):
            if re.fullmatch(r'\{\d+\}', part):
                slots.append(int(part[1:-1]))
                parts.append('(.*?)')
            else:
                parts.append(re.escape(part))
        patterns.append((re.compile('^' + ''.join(parts) + '$', re.S), slots, target))
    return patterns


def translate(source, *, locale=None):
    if not isinstance(source, str) or (locale or language.get()) != 'en':
        return source
    exact = _catalog().get(source)
    if exact is not None:
        return exact
    if not re.search(r'[\u3400-\u9fff]', source):
        return source
    for pattern, slots, target in _patterns():
        match = pattern.fullmatch(source)
        if match:
            values = dict(zip(slots, match.groups()))
            return re.sub(r'\{(\d+)\}', lambda m: values.get(int(m[1]), m[0]), target)
    return source


DISPLAY_FIELDS = {'reason', 'warnings', 'status_label', 'phase', 'sample_type', 'selection_policy',
                  'display_note', 'policy', 'empty_reason', 'basis', 'review_state', 'state', 'label', 'detail'}
SOURCE_FIELDS = {'conditions', 'selection', 'selections', 'metadata', 'test_catalog'}


def localize_report(value, field=''):
    if isinstance(value, str):
        return translate(value) if field in DISPLAY_FIELDS else value
    if isinstance(value, list):
        return [localize_report(item, field) for item in value]
    if isinstance(value, dict):
        return {key: item if key in SOURCE_FIELDS else localize_report(item, key) for key, item in value.items()}
    return value
