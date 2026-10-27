"""Saved Site Diff evidence shared by the two report formats."""
from collections import Counter
from datetime import date
from deepyield.compute.release_cgm_excel import _items
from deepyield.compute.release_cpk_excel import _identifier, _report_filename, _final_diagnostics
from deepyield.compute.release_remarks import numbered_remark

SITE_SCOPE = 'Valid module records (PASS/FAIL included; not Bin 1 only)'
STD_FORMULA = 'Site Std Ratio = max(Site Std) / median(Site Std); not max/min'
MATRIX_NOTE = 'Grey cells mean no outlier hit; they do not mean evaluated PASS.'
RESULTS = {'PASS': 'Pass', 'FAIL': 'Fail', 'RISK': 'Need to check', 'REVIEW_REQUIRED': 'Need to check'}


def fmt(value):
    return '--' if value is None else f'{value:.5g}' if isinstance(value, float) else str(value)


def filename(items, metadata, module, extension, day=None):
    device = _identifier(items, metadata, 'part_type', None, 'DeviceID') or 'UnknownDevice'
    return _report_filename(day or date.today(), device, f'{module}_Report.{extension}')


def site_diff_rows(report, metadata, result=None):
    if report.get('report_type') != 'release_mmsa':
        raise ValueError('Site Diff report is missing; calculate the module again.')
    saved = _final_diagnostics(result, 'mmsa')
    rows = []
    for item in _items(report, metadata):
        sites = item.get('sites') or []
        shift = item.get('shift_severity') in {'YELLOW', 'RED'}
        spread = item.get('var_severity') in {'YELLOW', 'RED'}
        rule = item.get('rule_id')
        trigger = (item.get('reason') or 'Not evaluated' if rule is None else
                   'CGM repeatability' if rule in {'MMSA-001', 'MMSA-002'}
                   else 'Both' if shift and spread else 'Mean Diff' if shift else 'Std Ratio' if spread
                   else rule or item.get('reason') or 'Not evaluated')
        valid = [s for s in sites if s.get('std') is not None and s.get('mean') is not None]
        if valid and (spread and not shift or rule == 'MMSA-004'):
            worst = [s['site'] for s in valid if s['std'] == max(s['std'] for s in valid)]
        elif valid and item.get('overall_mean') is not None:
            distance = max(abs(s['mean'] - item['overall_mean']) for s in valid)
            worst = [s['site'] for s in valid if abs(s['mean'] - item['overall_mean']) == distance]
        else:
            worst = []
        diagnostic = saved.get(item.get('group_id')) or item.get('diagnosis') or {}
        evidence = (f"Mean Diff={fmt(item.get('mean_shift'))}; Mean Diff % Spec={fmt(item.get('shift_spec_pct'))}; "
                    f"Std Ratio={fmt(item.get('var_ratio'))}; Rule={rule or '--'}; Trigger={trigger}.")
        remark = numbered_remark([diagnostic.get('auto_remark') or item.get('reason') or '', evidence,
                                  diagnostic.get('remark') or ''])
        rows.append({**item, 'result': RESULTS.get(item.get('status'), item.get('status') or 'Not evaluated'),
                     'trigger': trigger, 'worst_sites': worst, 'worst_site': ', '.join(f'S{s}' for s in worst) or '--',
                     'remark': remark, 'diagnostic': diagnostic})
    actions = sorted((r for r in rows if r['result'] in {'Fail', 'Need to check'}),
                     key=lambda r: (r['result'] != 'Fail', -(r.get('shift_spec_pct') or 0), -(r.get('var_ratio') or 0)))
    counts = Counter(r['result'] for r in rows)
    return {'rows': rows, 'actions': actions, 'counts': counts,
            'excluded': sum(r['result'] not in {'Pass', 'Fail', 'Need to check'} for r in rows),
            'sites': sorted({s['site'] for r in rows for s in r.get('sites', []) if s['site'] is not None})}


def outlier_note(baseline):
    diagnostic = baseline.get('diagnosis') or {}
    note = baseline.get('observation') or {}
    return numbered_remark([diagnostic.get('remark') or diagnostic.get('auto_remark') or '',
                            note.get('preview') or note.get('final') or note.get('remark') or ''])
