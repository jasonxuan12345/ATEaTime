"""Export run-scoped statistics without reading measurements or copying sample refs."""
from datetime import date

from app import release_check_service as release
from deepyield.compute.release_cpk_excel import render_cpk_xlsx
from deepyield.compute.release_cgm_excel import render_cgm_xlsx
from deepyield.compute.release_numeric import require_current_cg_policy
from deepyield.compute.release_control import timed, report_progress

MAX_CACHE_BYTES = 16 * 1024 ** 2


@timed('export.xlsx')
def export_run_xlsx(dataset_id: str, run_id: str, module: str, file_delivery=False) -> dict:
    report_progress('准备导出 Excel', 0)
    if module not in {'cpk', 'cgm'}:
        raise ValueError('请选择 CPK 或 Cgm 报表')
    owner = release.get_dataset(dataset_id)
    result, reports = release.get_run(owner, run_id)
    with release._lock:
        if module not in reports or module not in result['sources']:
            raise KeyError('本次结果没有这个模块，请重新计算')
        source = release.run_source(owner, result, module)
        # Source metadata must belong to this module, not the run's owning file.
        metadata = source.metadata
        report = reports[module]
        if module == 'cgm':
            require_current_cg_policy(report)
        if report['source'].get('source_sha256') != metadata.get('source_sha256'):
            raise ValueError('报表与来源文件不一致，请重新计算')
        export_date = date.today()
        version = result.get('remark_versions', {}).get(module, 0)
        cache_key = (run_id, module, export_date, version)
        if not file_delivery and cache_key in owner.excel_cache:
            report_progress('Excel 已生成，准备下载', 100)
            owner.excel_cache.move_to_end(cache_key)
            return owner.excel_cache[cache_key]
        # Reports are immutable after computation except for their diagnostics.
        # Copy only export fields; sample_refs can contain millions of entries.
        snapshot = {key: value for key, value in report.items()
                    if key not in {'items', 'risks', 'findings'}}
        snapshot['items'] = [{key: value for key, value in item.items()
                              if key not in {'sample_refs', 'diagnosis'}}
                             for item in report['items']]
        page = {'remark_knowledge': result.get('remark_knowledge'), 'rows': []}
        for row in result['rows']:
            cell = row.get(module)
            if cell:
                page['rows'].append({**{key: row.get(key) for key in
                    ('column', 'number', 'name', 'unit')}, module: {
                        'group_id': cell.get('group_id'), 'dataset_id': cell.get('dataset_id'),
                        'diagnosis': dict(cell['diagnosis']) if cell.get('diagnosis') else None}})
    # ZIP creation and worksheet writing happen outside the session lock.
    def write(output):
        renderer = render_cpk_xlsx if module == 'cpk' else render_cgm_xlsx
        return renderer(snapshot, metadata=metadata, result=page, report_date=export_date, output=output)
    workbook = owner.report_files.build(('xlsx', *cache_key, id(report), metadata.get('source_sha256')),
        write, file_delivery=file_delivery)
    if file_delivery:
        return workbook
    with release._lock:
        if (run_id in owner.runs and owner.runs[run_id][0] is result
                and result.get('remark_versions', {}).get(module, 0) == version):
            # Keep only the current file for each module/run, with a hard memory cap.
            for key in list(owner.excel_cache):
                if key[:2] == cache_key[:2]:
                    owner.excel_cache.pop(key)
            size = len(workbook['content'])
            if size <= MAX_CACHE_BYTES:
                while owner.excel_cache and size + sum(len(value['content']) for value in owner.excel_cache.values()) > MAX_CACHE_BYTES:
                    owner.excel_cache.popitem(last=False)
                owner.excel_cache[cache_key] = workbook
    return workbook
