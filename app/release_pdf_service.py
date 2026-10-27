"""Run-scoped PDF exports using each module's own source and sample refs."""
from datetime import date

from app import release_check_service as release
from deepyield.compute.release_cgm_pdf import render_cgm_pdf, PDF_LAYOUT_VERSION
from deepyield.compute.release_numeric import require_current_cg_policy
from deepyield.compute.release_cpk_pdf import render_cpk_pdf, cpk_population
from deepyield.compute.release_chart_samples import ReportSampleResolver
from deepyield.compute.release_control import timed
from deepyield.compute.release_cpk_excel import _final_diagnostics


@timed('export.pdf')
def export_run_cgm_pdf(dataset_id: str, run_id: str, file_delivery=False) -> dict:
    owner = release.get_dataset(dataset_id)
    result, reports = release.get_run(owner, run_id)
    with release._lock:
        if 'cgm' not in reports or 'cgm' not in result['sources']:
            raise KeyError('本次结果没有 CGM 模块，请重新计算')
        source = release.run_source(owner, result, 'cgm')
        metadata, report = source.metadata, reports['cgm']
        require_current_cg_policy(report)
        if report['source'].get('source_sha256') != metadata.get('source_sha256'):
            raise ValueError('报表与来源文件不一致，请重新计算')
        version = result.get('remark_versions', {}).get('cgm', 0)
        saved = {key: dict(value) for key, value in _final_diagnostics(result, 'cgm').items()}
        snapshot = {**report, 'items': [{**item,
            'diagnosis': saved.get(item.get('group_id')) or dict(item.get('diagnosis') or {})}
            for item in report['items']]}
    def write(output):
        return render_cgm_pdf(snapshot, metadata=metadata, output=output,
            sample_provider=ReportSampleResolver(source.frame, metadata['source_sha256'], identifiers=False,
                part_indices=(report.get('selection') or {}).get('selected_part_indices'), minimal=True))
    return owner.report_files.build(('pdf', PDF_LAYOUT_VERSION, run_id, 'cgm', id(report), metadata['source_sha256'], date.today(), version),
                                    write, file_delivery=file_delivery)


@timed('export.pdf')
def export_run_cpk_pdf(dataset_id: str, run_id: str, file_delivery=False) -> dict:
    owner = release.get_dataset(dataset_id)
    result, reports = release.get_run(owner, run_id)
    with release._lock:
        if 'cpk' not in reports or 'cpk' not in result['sources']:
            raise KeyError('本次结果没有 CPK 模块，请重新计算')
        source = release.run_source(owner, result, 'cpk')
        metadata, report = source.metadata, reports['cpk']
        if report['source'].get('source_sha256') != metadata.get('source_sha256'):
            raise ValueError('报表与来源文件不一致，请重新计算')
        version = result.get('remark_versions', {}).get('cpk', 0)
        saved = {key: dict(value) for key, value in _final_diagnostics(result, 'cpk').items()}
        snapshot = {**report, 'items': [{**item,
            'diagnosis': saved.get(item.get('group_id')) or dict(item.get('diagnosis') or {})}
            for item in report['items']]}
    def write(output):
        return render_cpk_pdf(snapshot, metadata=metadata, output=output,
            population=cpk_population(source.frame, report),
            sample_provider=ReportSampleResolver(source.frame, metadata['source_sha256'], identifiers=False,
                part_indices=(report.get('selection') or {}).get('selected_part_indices')))
    return owner.report_files.build(('pdf', run_id, 'cpk', id(report), metadata['source_sha256'], date.today(), version),
                                    write, file_delivery=file_delivery)
