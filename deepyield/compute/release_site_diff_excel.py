"""Site Diff workbook from saved full-population statistics."""
from datetime import date
from io import BytesIO
import json
from pathlib import Path
import xlsxwriter
from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS, DiagnosticJSON
from deepyield.compute.release_control import checkpoint, report_progress
from deepyield.compute.release_review_data import SITE_SCOPE, STD_FORMULA, filename, site_diff_rows


def render_site_diff_xlsx(report, *, metadata=None, result=None, output=None, report_date=None):
    metadata = metadata or {}
    data = site_diff_rows(report, metadata, result)
    layout = json.loads((Path(__file__).resolve().parents[2] / 'config/release_site_diff_excel.json').read_text(encoding='utf-8'))
    sites = data['sites']
    headers = layout['headers'] + [f'S{site}_{key}' for site in sites for key in ('n', 'Mean', 'Std')] + layout['summary_headers']
    if len(headers) > 16384 or len(data['rows']) > 1048574:
        raise ValueError('Site Diff report exceeds Excel worksheet limits.')
    buffer = output if output is not None else BytesIO()
    diagnostic_json = DiagnosticJSON()
    with xlsxwriter.Workbook(buffer, WORKBOOK_OPTIONS) as book:
        header = book.add_format({'bold': True, 'bg_color': '#404040', 'font_color': '#FFFFFF', 'text_wrap': True, 'valign': 'vcenter'})
        cell = book.add_format({'valign': 'top', 'num_format': 'General'})
        wrap = book.add_format({'valign': 'top', 'text_wrap': True})
        states = {s: book.add_format({'bg_color': c, 'valign': 'top'}) for s, c in layout['colors'].items()}
        book.set_properties({'title': 'Site Diff Report', 'comments': json.dumps({
            'scope': SITE_SCOPE, 'formula': STD_FORMULA, 'source': report.get('source'),
            'selection': report.get('selection'), 'policy': report.get('policy'),
            'parameters': report.get('parameters')}, ensure_ascii=False)})
        for sheet_name, rows in [('raw', data['rows']), ('Abnormal_Site_Diff', data['actions'])]:
            sheet = book.add_worksheet(sheet_name)
            sheet.merge_range(0, 0, 0, len(headers)-1, 'Data Scope: ' + SITE_SCOPE + ' | ' + STD_FORMULA, header)
            sheet.set_row(0, 30)
            sheet.write_row(1, 0, headers, header)
            sheet.set_row(1, 40)
            sheet.freeze_panes(2, 5)
            sheet.set_column(0, len(headers)-1, 17)
            sheet.set_column(1, 1, 48)
            sheet.set_column(len(headers)-1, len(headers)-1, 85)
            for line, row in enumerate(rows, 2):
                checkpoint()
                indexed = {s['site']: s for s in row.get('sites', [])}
                values = [row.get(k) for k in ('test_num','test_name','unit','effective_lsl','effective_usl','n','overall_mean','overall_std','cp')]
                values += [indexed.get(site, {}).get(key) for site in sites for key in ('n','mean','std')]
                values += [row['worst_site'], row['trigger'], row.get('mean_shift'), row.get('shift_spec_pct'), row.get('var_ratio'), row['result'], row['remark']]
                sheet.write_row(line, 0, values, cell)
                sheet.write_string(line, len(headers)-1, row['remark'][:32767], wrap)
                sheet.write_comment(line, len(headers)-1, diagnostic_json({
                    'diagnosis': row['diagnostic'], 'conditions': row.get('conditions'), 'source': report.get('source'),
                    'group_id': row.get('group_id'), 'formula': STD_FORMULA, 'status': row.get('status')})[:32767])
                sheet.write_string(line, len(headers)-2, row['result'], states.get(row['result'], wrap))
                sheet.set_row(line, 58)
            if rows:
                sheet.autofilter(1, 0, len(rows)+1, len(headers)-1)
                for state, format_ in states.items():
                    sheet.conditional_format(2,len(headers)-2,len(rows)+1,len(headers)-2,
                        {'type':'cell','criteria':'equal to','value':f'"{state}"','format':format_})
            report_progress('Writing Site Diff Excel', 90)
    return {'filename': filename(data['rows'], metadata, 'SiteDiff', 'xlsx', report_date),
            'content': buffer.getvalue() if output is None else None, 'row_count': len(data['rows']), 'action_count':len(data['actions'])}
