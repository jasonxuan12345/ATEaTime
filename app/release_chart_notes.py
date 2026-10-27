"""Run-scoped, reversible engineering observations and spreadsheet export."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from uuid import uuid4

import xlsxwriter
from deepyield.i18n import translate

from app import release_check_service as release
from app.release_chart_service import matrix_data

from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS, DiagnosticJSON
from deepyield.compute.release_control import report_progress, timed

from app.release_remark_service import OBSERVATIONS, compose_remark, refresh_remarks, remarks_view


def observations(dataset_id: str, run_id: str, compact=False) -> dict:
    result, _ = release.get_run(release.get_dataset(dataset_id), run_id)
    with release._lock:
        return {'notes': deepcopy(result.get('chart_observations', {})),
                'version': result.get('observation_version', 0), 'remarks': remarks_view(result, compact)}


def save_observations(dataset_id: str, run_id: str, payload: dict, compact=False) -> dict:
    result, _ = release.get_run(release.get_dataset(dataset_id), run_id)
    rows = {r['key']: r for r in result['rows']}
    keys = list(dict.fromkeys(payload['keys']))
    module = payload['module']
    if not keys or any(key not in rows or not rows[key].get(module) for key in keys):
        raise ValueError('所选测项没有该模块结果')
    if any(tag not in OBSERVATIONS for tag in payload['tags']):
        raise ValueError('存在未知观察标签')
    if not payload['confirmed']:
        raise ValueError('请先确认观察适用于所选测项')
    if len(keys) > 1 and payload.get('final_remark') is not None:
        raise ValueError('批量只能应用观察与补充，不得复制单项最终 Remark')
    if len(keys) > 1:
        groups = matrix_data(dataset_id, run_id)['groups']
        if not any(set(keys).issubset(set(group['keys'])) for group in groups):
            raise ValueError('批量记录只能应用至来源、条件和异常一致的同一组')
    with release._lock:
        if payload['version'] != result.get('observation_version', 0):
            raise ValueError('观察记录已被其他操作更新，请刷新后重试')
        notes = result.setdefault('chart_observations', {})
        before, skipped, saved = {}, [], []
        revision = result.get('observation_version', 0) + 1
        for key in keys:
            note_id = module + ':' + key
            if note_id in notes and not payload['overwrite']:
                skipped.append(key)
                continue
            before[note_id] = deepcopy(notes.get(note_id))
            cell = rows[key][module]
            remark = compose_remark(rows[key], module, {**payload, 'custom_remark': payload['remark']})
            notes[note_id] = {'key': key, 'module': module, 'tags': payload['tags'],
                'suggestion': payload['suggestion'].strip(), 'custom_remark': payload['remark'].strip(),
                'remark': remark, 'final_remark': payload.get('final_remark'), 'state': payload['state'], 'revision': revision,
                'saved_at': datetime.now(timezone.utc).isoformat(),
                'source': deepcopy(result['sources'][module]), 'selection': deepcopy(result['selections'][module]),
                'rules_hash': result['rules_hash'], 'rule_id': cell.get('rule_id')}
            saved.append(key)
        undo_id = None
        if saved:
            result['observation_version'] = revision
            versions = result.setdefault('remark_versions', {})
            versions[module] = versions.get(module, 0) + 1
            refresh_remarks(result)
            undo_id = uuid4().hex
            history = result.setdefault('observation_history', [])
            history.append({'id': undo_id, 'before': before, 'revision': revision})
            del history[:-20]
        return {'saved': saved, 'skipped': skipped, 'undo_id': undo_id,
                'version': result.get('observation_version', 0), 'notes': deepcopy(notes), 'remarks': remarks_view(result, compact)}


def undo_observations(dataset_id: str, run_id: str, undo_id: str, compact=False) -> dict:
    result, _ = release.get_run(release.get_dataset(dataset_id), run_id)
    with release._lock:
        history = result.get('observation_history', [])
        if not history or history[-1]['id'] != undo_id or history[-1]['revision'] != result.get('observation_version', 0):
            raise ValueError('只能撤销最近一次未被后续操作修改的记录')
        change = history.pop()
        notes = result['chart_observations']
        for key, previous in change['before'].items():
            if previous is None:
                notes.pop(key, None)
            else:
                notes[key] = previous
        result['observation_version'] += 1
        versions = result.setdefault('remark_versions', {})
        for module in {key.split(':', 1)[0] for key in change['before']}:
            versions[module] = versions.get(module, 0) + 1
        refresh_remarks(result)
        return {'notes': deepcopy(notes), 'version': result['observation_version'], 'remarks': remarks_view(result, compact)}


@timed('export.observations.xlsx')
def export_observations_xlsx(dataset_id: str, run_id: str, file_delivery=False):
    owner = release.get_dataset(dataset_id)
    result, _ = release.get_run(owner, run_id)
    with release._lock:
        version = result.get('observation_version', 0)
        snapshot = {key: result.get(key, {}) for key in ('sources', 'selections', 'rules_hash', 'modules')}
        snapshot['rows'] = []
        for row in result['rows']:
            copy = {key: row[key] for key in ('key', 'number', 'name')}
            for module in result['modules']:
                cell = row.get(module)
                if cell:
                    copy[module] = {key: cell.get(key) for key in ('rule_id', 'value', 'n', 'status')}
                    copy[module]['diagnosis'] = dict(cell['diagnosis']) if cell.get('diagnosis') else None
            snapshot['rows'].append(copy)
        snapshot['chart_observations'] = dict(result.get('chart_observations', {}))
    output = owner.report_files.build(('observations', run_id, version),
        lambda stream: _write_observations(snapshot, stream), file_delivery=file_delivery)
    return output if file_delivery else output['content']


def _write_observations(snapshot, output):
    diagnostic_json = DiagnosticJSON()
    selections = {module: str(snapshot["selections"][module]) for module in snapshot["modules"]}
    total = sum(bool(row.get(module)) for row in snapshot["rows"] for module in snapshot["modules"])
    # Reuse shared strings here: full selection text repeats on every row.
    # Inline strings would multiply that large text in worksheet XML.
    with xlsxwriter.Workbook(output, {**WORKBOOK_OPTIONS, 'constant_memory': False}) as book:
        sheet = book.add_worksheet('Release observations')
        headers = ['Test ID', 'Test name', 'Module', 'Rule', 'Value', 'n', 'Status',
                   'Observed features', 'Suggested check', 'Remark', 'Review state', 'Source',
                   'Source SHA256', 'Selection', 'Rules hash', 'Saved at']
        header = book.add_format({'bold': True, 'bg_color': '#DCEEF0', 'text_wrap': True})
        wrap = book.add_format({'text_wrap': True, 'valign': 'top'})
        sheet.write_row(0, 0, headers, header)
        line = 1
        for row in snapshot['rows']:
            for module in snapshot['modules']:
                if not row.get(module):
                    continue
                report_progress(f'写入观察记录：{line} / {total}', 90 * (line - 1) / max(1, total))
                cell = row[module]
                note = snapshot.get('chart_observations', {}).get(module + ':' + row['key'], {})
                source = snapshot['sources'][module]
                values = [row['number'], row['name'], module, cell.get('rule_id'), cell['value'], cell['n'],
                    cell['status'], ', '.join(translate(OBSERVATIONS[t][0]) for t in note.get('tags', [])),
                    note.get('suggestion', ''), compose_remark(row, module, note), translate(note.get('state', '自动生成，待复核')),
                    source['filename'], source.get('source_sha256'),
                    selections[module], snapshot['rules_hash'], note.get('saved_at', '')]
                sheet.write_row(line, 0, values, wrap)
                diagnostic = cell.get('diagnosis')
                if diagnostic:
                    sheet.write_comment(line, 9, diagnostic_json(diagnostic))

                line += 1
        sheet.freeze_panes(1, 2)
        sheet.autofilter(0, 0, max(0, line - 1), len(headers) - 1)
        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 35)
        sheet.set_column(2, 8, 18)
        sheet.set_column(9, 15, 45)
        report_progress('正在封装观察记录 Excel', 95)
    report_progress('观察记录 Excel 已生成', 100)
    return {'filename': 'release-observations.xlsx'}

def preview_observation(dataset_id: str, run_id: str, payload: dict) -> dict:
    result, _ = release.get_run(release.get_dataset(dataset_id), run_id)
    with release._lock:
        if payload['version'] != result.get('observation_version', 0):
            raise ValueError('观察记录已更新，请刷新后重试')
        rows = {row['key']: row for row in result['rows']}
        if len(payload['keys']) != 1 or payload['keys'][0] not in rows:
            raise ValueError('预览需要一个当前测项')
        row = rows[payload['keys'][0]]
        if not row.get(payload['module']) or any(tag not in OBSERVATIONS for tag in payload['tags']):
            raise ValueError('没有模块结果或观察标签无效')
        return {'remark': compose_remark(row, payload['module'],
                {**payload, 'custom_remark': payload['remark']})}
