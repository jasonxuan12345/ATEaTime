"""Thin routes for the Release check page."""
from typing import Annotated, Literal
import struct
import zlib
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, UploadFile, Query, Header
from fastapi.responses import FileResponse, Response, JSONResponse
from pydantic import BaseModel, Field, StrictInt, FiniteFloat, ConfigDict
from starlette.background import BackgroundTask

from app import release_check_service as service
from app import release_chart_service as charts, release_chart_notes as chart_notes
from app import release_operations as operations
from app import release_outlier_service as outlier_service
from app import release_excel_service as excel_service
from app import release_pdf_service as pdf_service
from app import release_review_report_service as review_reports
from app import release_presentation as presentation
from app.release_export_cache import register_download, take_download
from app.release_request_timing import ReleaseTimingRoute
from deepyield.compute.release_performance_log import emit
from deepyield.compute.release_outliers import DEFAULT_MINIMUM_SAMPLES, DEFAULT_SIGMA_MULTIPLIER

router = APIRouter(prefix="/api/release-check", tags=["Release check"], route_class=ReleaseTimingRoute)
emit('instrumentation_ready', version='release-timing-v1')


class Selection(BaseModel):
    part_indices: list[Annotated[StrictInt, Field(ge=0)]] | None = None
    sites: list[Annotated[StrictInt, Field(ge=0)]] | None = None
    test_columns: list[str] | None = None
    minimum_samples: Annotated[StrictInt, Field(ge=2)] | None = None


class OutlierRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    part_indices: list[Annotated[StrictInt, Field(ge=0)]] | None = None
    sites: list[Annotated[StrictInt, Field(ge=0)]] | None = None
    test_columns: list[str] | None = None
    sigma_multiplier: Annotated[FiniteFloat, Field(gt=0, strict=True)] = DEFAULT_SIGMA_MULTIPLIER
    minimum_samples_per_site: Annotated[StrictInt, Field(ge=4)] = DEFAULT_MINIMUM_SAMPLES


class RunRequest(BaseModel):
    modules: list[Literal["cpk", "cgm", "mmsa"]] = Field(min_length=1)
    cpk: Selection = Field(default_factory=Selection)
    cgm: Selection = Field(default_factory=Selection)
    mmsa: Selection = Field(default_factory=Selection)


class SourceSelection(Selection):
    dataset_id: str | None = Field(default=None, min_length=1)
    dataset_ids: list[str] | None = Field(default=None, min_length=1, max_length=8)


class MultiSourceRunRequest(BaseModel):
    owner_dataset_id: str | None = Field(default=None, min_length=1)
    cpk: SourceSelection | None = None
    cgm: SourceSelection | None = None
    mmsa: SourceSelection | None = None


class OutlierReportLink(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str = Field(min_length=1, max_length=128)
    report_id: str = Field(min_length=1, max_length=128)


class OutlierSavedNote(BaseModel):
    model_config = ConfigDict(extra='forbid')
    tags: list[str] = Field(default_factory=list, max_length=20)
    remark: str = Field(default='', max_length=32767)
    suggestion: str = Field(default='', max_length=32767)
    final: str | None = Field(default=None, max_length=32767)
    state: str = Field(default='', max_length=100)
    preview: str = Field(default='', max_length=32767)


class OutlierSavedObservation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dataset_id: str = Field(min_length=1, max_length=128)
    group_id: str = Field(min_length=1, max_length=128)
    note: OutlierSavedNote


class OutlierRunSnapshot(BaseModel):
    model_config = ConfigDict(extra='forbid')
    sources: list[OutlierReportLink] = Field(max_length=8)
    observations: list[OutlierSavedObservation] = Field(default_factory=list, max_length=100000)
    issues: list[dict[str, str]] = Field(default_factory=list, max_length=8)


def _per_site_minimum(selection: dict) -> dict:
    selection["minimum_samples_per_site"] = selection.pop("minimum_samples")
    return selection


def _call(function, *args):
    try:
        return function(*args)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (OSError, EOFError, struct.error, zlib.error) as exc:
        raise HTTPException(422, "文件无法读取或内容不完整，请检查 STDF 文件") from exc


JobId = Annotated[str | None, Query(pattern=r'^[A-Za-z0-9-]{1,64}$')]
UIView = Annotated[str | None, Header(alias='X-Release-View')]


def _operation_call(response, job_id, function, *args):
    def execute():
        with operations.track(job_id, function.__name__) as control:
            result = function(*args)
            response.headers['Server-Timing'] = control.header()
            return result
    return _call(execute)


@router.get('/jobs/{job_id}')
async def operation_status(job_id: str):
    return _call(operations.status, job_id)


@router.delete('/jobs/{job_id}', status_code=204)
async def cancel_operation(job_id: str):
    operations.cancel(job_id)


@router.post("/datasets")
def upload(file: Annotated[UploadFile, File()], response: Response, job_id: JobId = None):
    return _operation_call(response, job_id, service.import_file, file.filename or "", file.file)


@router.delete("/datasets/{dataset_id}", status_code=204)
def remove(dataset_id: str):
    service.remove_dataset(dataset_id)


@router.get("/datasets/{dataset_id}/parquet")
def parquet(dataset_id: str, response: Response, job_id: JobId = None):
    dataset = _operation_call(response, job_id, service.prepare_parquet, dataset_id)
    return FileResponse(dataset.parquet, filename=f"{dataset.filename}.wide_all.parquet",
        media_type="application/octet-stream", headers=dict(response.headers),
        background=BackgroundTask(lambda retained: None, dataset))


@router.post("/datasets/{dataset_id}/run")
def run(dataset_id: str, request: RunRequest, response: Response, job_id: JobId = None, ui_view: UIView = None):
    selections = {m: getattr(request, m).model_dump() for m in request.modules}
    for m in ("cgm", "mmsa"):
        if m in selections:
            _per_site_minimum(selections[m])
    result = _operation_call(response, job_id, service.run_checks, dataset_id, selections, list(dict.fromkeys(request.modules)))
    return presentation.response(presentation.run_view(result), response.headers) if ui_view == 'summary-v1' else result


@router.post('/datasets/{dataset_id}/outliers')
def outliers(dataset_id: str, request: OutlierRequest, response: Response, job_id: JobId = None, ui_view: UIView = None):
    result = _operation_call(response, job_id, outlier_service.run_outlier_check, dataset_id, request.model_dump())
    return presentation.response(presentation.outlier_view(result), response.headers) if ui_view == 'summary-v1' else result


@router.get('/datasets/{dataset_id}/outliers/{report_id}/report')
def full_outlier_report(dataset_id: str, report_id: str):
    result = _call(presentation.outlier_report, dataset_id, report_id)
    return presentation.response(result, {})


@router.get('/datasets/{dataset_id}/outliers/{report_id}/baselines/{baseline_id}/diagnosis')
def outlier_diagnosis(dataset_id: str, report_id: str, baseline_id: str):
    return _call(presentation.baseline_diagnosis, dataset_id, report_id, baseline_id)


@router.get('/datasets/{dataset_id}/outliers/{report_id}/candidates')
def outlier_candidates(dataset_id: str, report_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                       limit: Annotated[int, Query(ge=1, le=200)] = 40, group_id: str | None = None):
    return _call(presentation.candidate_page, dataset_id, report_id, offset, limit, group_id)


@router.get('/datasets/{dataset_id}/outliers/{report_id}/candidates/{part_index}/hits')
def outlier_hits(dataset_id: str, report_id: str, part_index: int, offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=200)] = 40):
    return _call(presentation.hit_page, dataset_id, report_id, part_index, offset, limit)


@router.get('/datasets/{dataset_id}/outliers/{report_id}/view')
def outlier_view(dataset_id: str, report_id: str, group_id: str):
    return _call(outlier_service.outlier_view, dataset_id, report_id, group_id)


@router.post("/runs")
def multi_source_run(request: MultiSourceRunRequest, response: Response, job_id: JobId = None, ui_view: UIView = None):
    modules = [m for m in ("cpk", "cgm", "mmsa") if getattr(request, m) is not None]
    if not modules and not request.owner_dataset_id:
        raise HTTPException(422, '至少选择一项检查及其文件')
    sources = {}
    for m in modules:
        selection = getattr(request, m)
        ids = selection.dataset_ids or ([selection.dataset_id] if selection.dataset_id else [])
        if not ids or any(not value for value in ids) or len(ids) != len(set(ids)):
            raise HTTPException(422, '请选择不重复的来源文件')
        if selection.dataset_id and selection.dataset_ids and selection.dataset_ids != [selection.dataset_id]:
            raise HTTPException(422, '来源文件选择不一致')
        sources[m] = ids[0] if len(ids) == 1 else ids
    selections = {m: getattr(request, m).model_dump(exclude={"dataset_id", "dataset_ids"}) for m in modules}
    for m in ("cgm", "mmsa"):
        if m in selections:
            _per_site_minimum(selections[m])
    first = sources[modules[0]] if modules else request.owner_dataset_id
    owner = first[0] if isinstance(first, list) else first
    result = _operation_call(response, job_id, service.run_checks, owner, selections, modules, sources)
    return presentation.response(presentation.run_view(result), response.headers) if ui_view == 'summary-v1' else result


@router.get('/datasets/{dataset_id}/runs/{run_id}/items/{key}/diagnosis/{module}')
def run_diagnosis(dataset_id: str, run_id: str, key: str, module: Literal['cpk', 'cgm', 'mmsa']):
    return _call(presentation.run_diagnosis, dataset_id, run_id, key, module)


@router.get("/datasets/{dataset_id}/runs/{run_id}/items/{key}")
def detail(dataset_id: str, run_id: str, key: str, response: Response):
    return _operation_call(response, None, service.item_detail, dataset_id, run_id, key)


@router.get("/datasets/{dataset_id}/runs/{run_id}/report")
def report(dataset_id: str, run_id: str, job_id: JobId = None):
    carrier = Response(media_type="application/json")
    artifact = _operation_call(carrier, job_id, service.export_report, dataset_id, run_id)
    headers = {'Content-Disposition': 'attachment; filename="release-check-report.json"',
               'Cache-Control': 'no-store'}
    if 'Server-Timing' in carrier.headers:
        headers['Server-Timing'] = carrier.headers['Server-Timing']
    # Stream from the spool file; the JSON body never sits in server memory.
    return FileResponse(artifact.path, media_type="application/json", headers=headers,
        background=BackgroundTask(lambda retained: None, artifact))


@router.get('/datasets/{dataset_id}/runs/{run_id}/reports/{module}.xlsx')
def capability_xlsx(dataset_id: str, run_id: str, module: Literal['cpk', 'cgm', 'site_diff', 'outlier'], job_id: JobId = None, delivery: Literal['inline', 'file'] = 'inline'):
    if module in {'site_diff', 'outlier'}:
        return review_report(dataset_id, run_id, module, 'xlsx', job_id, delivery)
    response = Response(media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    workbook = _operation_call(response, job_id, excel_service.export_run_xlsx, dataset_id, run_id, module, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, workbook)
    response.body = workbook['content']
    response.headers['Content-Length'] = str(len(response.body))
    response.headers['Content-Disposition'] = (
        f'attachment; filename="{module}-report.xlsx"; '
        f"filename*=UTF-8''{quote(workbook['filename'], safe='')}")
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/datasets/{dataset_id}/runs/{run_id}/reports/cgm.pdf')
def capability_cgm_pdf(dataset_id: str, run_id: str, job_id: JobId = None, delivery: Literal['inline', 'file'] = 'inline'):
    response = Response(media_type='application/pdf')
    report = _operation_call(response, job_id, pdf_service.export_run_cgm_pdf, dataset_id, run_id, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, report)
    response.body = report['content']
    response.headers['Content-Length'] = str(len(response.body))
    response.headers['Content-Disposition'] = (
        'attachment; filename="cgm-report.pdf"; '
        f"filename*=UTF-8''{quote(report['filename'], safe='')}")
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.get('/datasets/{dataset_id}/runs/{run_id}/reports/cpk.pdf')
def capability_cpk_pdf(dataset_id: str, run_id: str, job_id: JobId = None, delivery: Literal['inline', 'file'] = 'inline'):
    response = Response(media_type='application/pdf')
    report = _operation_call(response, job_id, pdf_service.export_run_cpk_pdf, dataset_id, run_id, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, report)
    response.body = report['content']
    response.headers['Content-Length'] = str(len(response.body))
    response.headers['Content-Disposition'] = (
        'attachment; filename="cpk-report.pdf"; '
        f"filename*=UTF-8''{quote(report['filename'], safe='')}")
    response.headers['Cache-Control'] = 'no-store'
    return response


@router.put('/datasets/{dataset_id}/runs/{run_id}/outlier-reports')
def save_outlier_reports(dataset_id: str, run_id: str, request: OutlierRunSnapshot):
    return _call(review_reports.bind_outlier_reports, dataset_id, run_id, request.model_dump())


@router.get('/datasets/{dataset_id}/runs/{run_id}/reports/{module}.{extension}')
def review_report(dataset_id: str, run_id: str, module: str,
                  extension: Literal['xlsx', 'pdf'], job_id: JobId = None,
                  delivery: Literal['inline', 'file'] = 'inline'):
    if module not in {'site_diff', 'outlier'}:
        raise HTTPException(404, 'Report type not found')
    response = Response(media_type='application/pdf' if extension == 'pdf' else
                        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    report = _operation_call(response, job_id, review_reports.export_review_report,
                             dataset_id, run_id, module, extension, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, report)
    response.body = report['content']
    response.headers['Content-Length'] = str(len(response.body))
    response.headers['Content-Disposition'] = (
        f'attachment; filename="{module}-report.{extension}"; '
        f"filename*=UTF-8''{quote(report['filename'], safe='')}")
    response.headers['Cache-Control'] = 'no-store'
    return response


class ChartSelection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    ids: list[str] | None = Field(default=None, max_length=10000)
    sites: list[StrictInt] | None = Field(default=None, max_length=256)
    value_min: FiniteFloat | None = None
    value_max: FiniteFloat | None = None
    value_max_exclusive: bool = False
    sequence_min: FiniteFloat | None = None
    sequence_max: FiniteFloat | None = None
    x_min: FiniteFloat | None = None
    x_max: FiniteFloat | None = None
    y_min: FiniteFloat | None = None
    y_max: FiniteFloat | None = None
    wafer_key: tuple[str | None, str] | None = None


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    keys: list[str] = Field(min_length=1, max_length=500)
    module: Literal['cpk', 'cgm', 'mmsa']
    tags: list[str] = Field(default_factory=list, max_length=10)
    suggestion: str = Field(default='', max_length=1000)
    remark: str = Field(default='', max_length=1000)
    final_remark: str | None = Field(default=None, max_length=8000)
    state: Literal['待复核', '已确认观察', '待跟进'] = '待复核'
    confirmed: bool = False
    overwrite: bool = False
    version: int = Field(ge=0)


class MatrixScope(BaseModel):
    keys: list[str] = Field(max_length=50000)
    sort: Literal['report', 'cg'] = 'report'


@router.get('/datasets/{dataset_id}/runs/{run_id}/items/{key}/charts/{module}')
def chart_data(dataset_id: str, run_id: str, key: str, module: Literal['cpk', 'cgm', 'mmsa'], response: Response,
               bins: Annotated[int, Query(ge=1, le=200)] = 24,
               point_limit: Annotated[int, Query(ge=10, le=10000)] = 5000,
               bin_width: Annotated[float | None, Query(gt=0, allow_inf_nan=False)] = None):
    return _operation_call(response, None, charts.chart_data, dataset_id, run_id, key, module, bins, point_limit, bin_width)


@router.post('/datasets/{dataset_id}/runs/{run_id}/items/{key}/samples/{module}')
def chart_samples(dataset_id: str, run_id: str, key: str, module: Literal['cpk', 'cgm', 'mmsa'],
                  selection: ChartSelection, response: Response, offset: Annotated[int, Query(ge=0)] = 0,
                  limit: Annotated[int, Query(ge=1, le=1000)] = 100):
    return _operation_call(response, None, charts.sample_page, dataset_id, run_id, key, module, selection.model_dump(), offset, limit)


@router.get('/datasets/{dataset_id}/runs/{run_id}/chart-matrix')
def chart_matrix(dataset_id: str, run_id: str, offset: Annotated[int, Query(ge=0)] = 0,
                 limit: Annotated[int, Query(ge=1, le=200)] = 100, group: str | None = None):
    return _call(charts.matrix_data, dataset_id, run_id, offset, limit, group)


@router.post('/datasets/{dataset_id}/runs/{run_id}/chart-matrix')
def scoped_chart_matrix(dataset_id: str, run_id: str, scope: MatrixScope,
                        offset: Annotated[int, Query(ge=0)] = 0,
                        limit: Annotated[int, Query(ge=1, le=200)] = 100, group: str | None = None):
    return _call(charts.matrix_data, dataset_id, run_id, offset, limit, group, set(scope.keys), scope.sort)


@router.get('/datasets/{dataset_id}/runs/{run_id}/observations')
def observations(dataset_id: str, run_id: str, ui_view: UIView = None):
    result = _call(chart_notes.observations, dataset_id, run_id, ui_view == 'summary-v1')
    return presentation.response(result, {}) if ui_view == 'summary-v1' else result


@router.post('/datasets/{dataset_id}/runs/{run_id}/observations/preview')
def preview_observation(dataset_id: str, run_id: str, payload: ObservationRequest):
    return _call(chart_notes.preview_observation, dataset_id, run_id, payload.model_dump())


@router.post('/datasets/{dataset_id}/runs/{run_id}/observations')
def save_observations(dataset_id: str, run_id: str, payload: ObservationRequest, ui_view: UIView = None):
    result = _call(chart_notes.save_observations, dataset_id, run_id, payload.model_dump(), ui_view == 'summary-v1')
    return presentation.response(result, {}) if ui_view == 'summary-v1' else result


@router.post('/datasets/{dataset_id}/runs/{run_id}/observations/undo/{undo_id}')
def undo_observations(dataset_id: str, run_id: str, undo_id: str, ui_view: UIView = None):
    result = _call(chart_notes.undo_observations, dataset_id, run_id, undo_id, ui_view == 'summary-v1')
    return presentation.response(result, {}) if ui_view == 'summary-v1' else result


@router.get('/datasets/{dataset_id}/runs/{run_id}/observations.xlsx')
def observations_xlsx(dataset_id: str, run_id: str, job_id: JobId = None, delivery: Literal['inline', 'file'] = 'inline'):
    response = Response()
    report = _operation_call(response, job_id, chart_notes.export_observations_xlsx, dataset_id, run_id, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, report)
    return Response(report,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Server-Timing': response.headers['Server-Timing'], 'Content-Disposition': 'attachment; filename="release-observations.xlsx"', 'Cache-Control': 'no-store'})


@router.get('/datasets/{dataset_id}/outliers/{report_id}.xlsx')
def outlier_remarks_xlsx(dataset_id: str, report_id: str, job_id: JobId = None, delivery: Literal['inline', 'file'] = 'inline'):
    response = Response()
    report = _operation_call(response, job_id, outlier_service.export_outliers_xlsx, dataset_id, report_id, delivery == 'file')
    if delivery == 'file':
        return _download_ready(response, report)
    return Response(report,
        media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        headers={'Server-Timing': response.headers['Server-Timing'], 'Content-Disposition': 'attachment; filename="release-outlier-diagnostics.xlsx"', 'Cache-Control': 'no-store'})


def _download_ready(response, report):
    headers = {'Cache-Control': 'no-store'}
    if 'Server-Timing' in response.headers:
        headers['Server-Timing'] = response.headers['Server-Timing']
    return JSONResponse(register_download(report['_file']), headers=headers)


@router.get('/report-downloads/{token}')
def report_download(token: str):
    artifact = _call(take_download, token)
    media = 'application/pdf' if artifact.result['filename'].endswith('.pdf') else 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
    return FileResponse(artifact.path, filename=artifact.result['filename'], media_type=media,
        headers={'Cache-Control': 'no-store', 'Content-Language': artifact.locale}, background=BackgroundTask(lambda retained: None, artifact))
