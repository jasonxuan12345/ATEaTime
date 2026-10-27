"""Old Cg snapshots must not bypass the formula check through export caches."""
import pytest

from app.release_excel_service import export_run_xlsx
from app.release_pdf_service import export_run_cgm_pdf
from tests.test_api.test_release_excel import sources, run_pair


@pytest.mark.parametrize('file_delivery', [False, True])
@pytest.mark.parametrize('format', ['xlsx', 'pdf'])
def test_old_cg_policy_rejected_even_after_file_is_cached(sources, file_delivery, format):
    owner, _ = sources
    result = run_pair()
    def export():
        if format == 'xlsx':
            return export_run_xlsx('a', result['id'], 'cgm', file_delivery=file_delivery)
        return export_run_cgm_pdf('a', result['id'], file_delivery=file_delivery)
    export()
    owner.runs[result['id']][1]['cgm']['policy']['cg_tolerance_fraction'] = 1.
    with pytest.raises(ValueError, match='20%.*重新计算'):
        export()
