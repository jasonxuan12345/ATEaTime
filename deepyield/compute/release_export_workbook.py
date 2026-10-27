"""Shared export settings: stream worksheet XML and reuse diagnostic fragments."""
import json
from deepyield.i18n import localize_report


WORKBOOK_OPTIONS = {'constant_memory': True, 'strings_to_formulas': False, 'strings_to_urls': False}


class DiagnosticJSON:
    def __init__(self):
        self.shared = {}

    def __call__(self, diagnostic):
        fragments = []
        for key, value in diagnostic.items():
            previous = self.shared.get(key)
            if previous is not None and (value is previous[0] or value == previous[0]):
                encoded = previous[1]
            else:
                encoded = json.dumps(localize_report(value, key), ensure_ascii=False, allow_nan=False)
                self.shared[key] = (value, encoded)
            fragments.append(json.dumps(key, ensure_ascii=False) + ': ' + encoded)
        return '{' + ', '.join(fragments) + '}'
