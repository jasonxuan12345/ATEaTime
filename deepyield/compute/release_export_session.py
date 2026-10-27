"""Explicit batch exports reuse one parsed source and one calculation per selection."""
from contextvars import ContextVar
from pathlib import Path

from deepyield.compute.release_run_cache import computation

_inputs = ContextVar('release_export_inputs', default=None)


def export_input(path, reader):
    cache = _inputs.get()
    if cache is None:
        return reader(path)
    key = (reader, str(Path(path).resolve()))
    if key not in cache:
        cache[key] = reader(path)
    return cache[key]


def export_catalog(rule_dir=None):
    from deepyield.rules.loader import load_analysis_rules
    cache = _inputs.get()
    if cache is None:
        return load_analysis_rules(rule_dir)
    key = ('rules', str(Path(rule_dir).resolve()) if rule_dir else None)
    if key not in cache:
        cache[key] = load_analysis_rules(rule_dir)
    return cache[key]


@computation
def export_release_reports(input_path, output_dir, *, formats=('cpk.xlsx', 'cpk.pdf', 'cgm.xlsx', 'cgm.pdf'), **selection):
    """Export several formats with an identical explicit selection; no persistent input cache."""
    from deepyield.compute.release_cpk_excel_input import export_release_cpk_xlsx
    from deepyield.compute.release_cgm_excel_input import export_release_cgm_xlsx
    from deepyield.compute.release_cpk_pdf import export_release_cpk_pdf
    from deepyield.compute.release_cgm_pdf import export_release_cgm_pdf
    exporters = {'cpk.xlsx': export_release_cpk_xlsx, 'cpk.pdf': export_release_cpk_pdf,
                 'cgm.xlsx': export_release_cgm_xlsx, 'cgm.pdf': export_release_cgm_pdf}
    if not formats or any(name not in exporters for name in formats) or len(set(formats)) != len(formats):
        raise ValueError('请选择不重复的 CPK/CGM XLSX/PDF 格式')
    token = _inputs.set({})
    try:
        return {name: exporters[name](input_path, output_dir, **selection) for name in formats}
    finally:
        _inputs.reset(token)
