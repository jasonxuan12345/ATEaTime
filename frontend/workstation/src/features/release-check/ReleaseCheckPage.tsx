import { t, localize, useLanguage, setLanguage, localizeReport } from "../../i18n";
import { useEffect, useMemo, useRef, useState, type DragEvent } from "react";
import { apiSelection, changeSources, pooledDataset, sourceFiles, demoDataset, demoDetail, demoRun, initialSelections, moduleLabels, request,
  startingDemoSelections, validate, type Dataset, type Detail, type Module, type Row, type Run, type Selections } from "./model";
import { SelectionPanel } from "./SelectionPanel";
import { FilePool } from "./FilePool";
import { ReviewWorkbench } from "./ReviewWorkbench";
import { useReleaseOperation } from './useReleaseOperation';
import { fetchRunWorkbook, fetchRunCgmPdf, fetchRunCpkPdf, downloadReport, fetchReportFile, fetchRunReviewReport, saveRunOutlierSnapshot } from './reportExport';
import { downloadBlob } from './charts/export';
import { OutlierSettings } from './outliers/OutlierSettings';
import { outlierExport } from './outliers/export';
import { fullOutlierReports } from './presentation';
import { hasOutlierSelection, initialOutliers, integrateOutliers, outlierRequest, validateOutliers,
  type OutlierReport, type SourceIssue, type SourceReport } from './outliers/model';
import type { OutlierNotes } from './outliers/OutlierRemark';
import s from "./release-check.module.css";

const emptyRows: Row[] = [];
const checkModules = ['cpk', 'cgm', 'mmsa', 'outlier'] as const;
const checkLabels = { ...moduleLabels, outlier: 'Outlier' };

export function ReleaseCheckPage() {
  const language = useLanguage();
  const [dataset, setDataset] = useState<Dataset | null>(demoDataset);
  const [files, setFiles] = useState<Dataset[]>([demoDataset]);
  const [selections, setSelections] = useState<Selections>(startingDemoSelections);
  const [run, setRun] = useState<Run | null>(() => demoRun(startingDemoSelections()));
  const [resultVersion,setResultVersion]=useState(0);
  const [snapshot, setSnapshot] = useState<Selections>(startingDemoSelections);
  const [module, setModule] = useState<Module | 'outlier'>("cpk");
  const [analysisDirty, setAnalysisDirty] = useState(false);
  const [outlierSettings, setOutlierSettings] = useState(initialOutliers);
  const [outlierSnapshot, setOutlierSnapshot] = useState<string | null>(null);
  const [outlierNotes,setOutlierNotes]=useState<OutlierNotes>({});
  const [outlierReports, setOutlierReports] = useState<SourceReport[]>([]);
  const [outlierIssues, setOutlierIssues] = useState<SourceIssue[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [exporting, setExporting] = useState<Module | 'outlier' | `${Module | 'outlier'}-pdf` | 'auxiliary' | null>(null);
  const workbookRequest = useRef<AbortController | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [calculationErrors,setCalculationErrors]=useState<SourceIssue[]>([]);
  const [importOpen, setImportOpen] = useState(false);
  const [selectedKey, setSelectedKey] = useState("1001");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [detailBusy, setDetailBusy] = useState(false);
  const [detailError, setDetailError] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const activeDatasets = useRef<Dataset[]>(files);
  const mounted = useRef(true);
  const operation = useReleaseOperation();
  const demo = dataset?.id === "demo";
  const sourceKey = JSON.stringify((['cpk','cgm','mmsa'] as Module[]).map(m =>
    selections[m].datasetIds ?? [selections[m].datasetId ?? dataset?.id]));
  const sources = useMemo(() => Object.fromEntries((['cpk','cgm','mmsa'] as Module[]).map(m =>
    [m, pooledDataset(sourceFiles(files, selections[m], dataset?.id))])) as Record<Module, Dataset | undefined>, [files, sourceKey]);
  const sourceFor = (m: Module) => sources[m];
  const moduleSource = module === 'outlier' ? null : sourceFor(module);
  const dirty = !!run && (JSON.stringify(snapshot) !== JSON.stringify(selections)
    || (!demo && outlierSnapshot !== JSON.stringify(outlierSettings)));
  useEffect(() => () => { workbookRequest.current?.abort(); }, [run?.id, dirty, analysisDirty]);
  const emptySelection = (['cpk', 'cgm', 'mmsa'] as Module[]).some(m => selections[m].parts === null
    && (selections[m].excludedParts?.length ?? 0) >= (sourceFor(m)?.record_count ?? Infinity));
  const invalid = dataset ? ((!Object.values(sources).some(Boolean) && !outlierSettings.sourceIds.length) ? '请在文件名旁勾选至少一项检查。'
    : emptySelection ? '至少选择一条测试记录。' : validate(selections) || (!demo && outlierSettings.sourceIds.length ? validateOutliers(outlierSettings, files) : null)) : "请先导入 STDF 文件。";
  const rows = useMemo(() => demo ? (run?.rows ?? emptyRows).map(row => ({ ...row, outlier: undefined }))
    : integrateOutliers(run?.rows ?? emptyRows, outlierReports, outlierIssues, outlierSnapshot === null), [run, demo, outlierReports, outlierIssues, outlierSnapshot]);
  const selected = rows.find(row => row.key === selectedKey) ?? rows[0] ?? null;

  useEffect(() => { activeDatasets.current = files; }, [files]);
  useEffect(() => {
    mounted.current = true;
    const release = () => { for (const file of activeDatasets.current.filter(f => f.id !== 'demo')) void fetch(`/api/release-check/datasets/${file.id}`, {method:'DELETE',keepalive:true}).catch(()=>undefined); };
    const hide = (event:PageTransitionEvent) => { if(!event.persisted)release(); };
    window.addEventListener('pagehide',hide);
    return()=>{mounted.current=false;window.removeEventListener('pagehide',hide);release();};
  },[]);
  useEffect(()=>{
    setDetail(null);setDetailError(null);
    if(!selected||!run||!dataset||selected.key.startsWith('outlier:')){setDetailBusy(false);return;}
    if(demo){setDetail(demoDetail(selected,snapshot));setDetailBusy(false);return;}
    const abort=new AbortController();setDetailBusy(true);
    request<Detail>(`/datasets/${run.dataset_id}/runs/${run.id}/items/${encodeURIComponent(selected.key)}`,{signal:abort.signal})
      .then(value=>{if(!abort.signal.aborted)setDetail(value);}).catch(error=>{if(!abort.signal.aborted)setDetailError(error.message);}).finally(()=>{if(!abort.signal.aborted)setDetailBusy(false);});
    return()=>abort.abort();
  },[selected,run,dataset,demo,snapshot]);
  const [view,setView]=useState('overview');
  function leaveAnalysis(){return !analysisDirty||window.confirm(t('当前观察尚未保存，是否放弃编辑并继续？'));}
  function releasePrevious(file:Dataset){if(file.id!=='demo')void fetch(`/api/release-check/datasets/${file.id}`,{method:'DELETE'}).catch(()=>undefined);}
  function resetOutliers(source?:Dataset){setOutlierSettings(initialOutliers(source));setOutlierReports([]);setOutlierIssues([]);setOutlierSnapshot(null);setOutlierNotes({});setCalculationErrors([]);}
  function loadDemo(){
    if(busy||!leaveAnalysis())return;
    files.forEach(releasePrevious);const next=startingDemoSelections();setDataset(demoDataset);setFiles([demoDataset]);setSelections(next);setSnapshot(next);setRun(demoRun(next));setResultVersion(v=>v+1);resetOutliers();setError(null);setImportOpen(false);setSelectedKey('1001');
  }
  function clear(){if(busy||!leaveAnalysis())return;files.forEach(releasePrevious);setFiles([]);setDataset(null);setRun(null);setDetail(null);setError(null);setImportOpen(true);resetOutliers();}
  function removeFile(file:Dataset){
    if(busy||!leaveAnalysis())return;
    releasePrevious(file);const remaining=files.filter(f=>f.id!==file.id);setFiles(remaining);if(dataset?.id===file.id)setDataset(remaining[0]??null);
    setOutlierSettings(prev=>({...prev,sourceIds:prev.sourceIds.filter(id=>id!==file.id),selections:Object.fromEntries(Object.entries(prev.selections).filter(([id])=>id!==file.id))}));
    setOutlierReports(prev=>prev.filter(r=>r.dataset_id!==file.id));setOutlierIssues(prev=>prev.filter(r=>r.dataset_id!==file.id));
    if(run?.dataset_id===file.id||(['cpk','cgm','mmsa'] as Module[]).some(m=>sourceFiles(files,selections[m]).some(source=>source.id===file.id))){
      setSelections(prev=>Object.fromEntries(Object.entries(prev).map(([m,value])=>[m,changeSources(files,value,sourceFiles(files,value).filter(source=>source.id!==file.id).map(source=>source.id))])) as Selections);setRun(null);setDetail(null);
    }
  }
  async function upload(incoming:File[]){
    if(!incoming.length||busy||!leaveAnalysis())return;
    setImportOpen(true);const existing=files.filter(f=>f.id!=='demo');if(incoming.length+existing.length>8){setError('最多保留 8 份文件，请先移除不用的文件。');return;}
    setBusy('正在导入文件…');setError(null);const accepted:Dataset[]=[],errors:string[]=[];
    const jobs:File[]=[];for(const file of incoming){if(/\.(stdf|std)(\.(gz|bz2))?$/i.test(file.name))jobs.push(file);else errors.push(`${file.name}：请选择 STDF、STDF.gz 或 STDF.bz2 文件。`);}
    // Imports are independent C++ subprocesses server-side; four at a time keep
    // the transient memory bounded while overlapping upload, parse and prepare.
    let cursor=0,finished=0;const runOne=async(file:File)=>{
      const job=operation.start();
      try{const body=new FormData();body.append('file',file);const next=await request<Dataset>(`/datasets?job_id=${job.id}`,{method:'POST',body,signal:job.controller.signal});if(!mounted.current){releasePrevious(next);return;}accepted.push(next);activeDatasets.current=[...existing,...accepted];}
      catch(error){errors.push(`${file.name}：${job.controller.signal.aborted?'导入已取消':(error as Error).message}`);}
      finally{operation.finish(job);finished+=1;if(mounted.current)setBusy(`正在并行解析 ${finished} / ${jobs.length} 个文件…`);}
    };
    try{const worker=async()=>{while(cursor<jobs.length){await runOne(jobs[cursor++]);}};
      await Promise.all(Array.from({length:Math.min(4,jobs.length)},worker));
    if(accepted.length&&mounted.current){setFiles([...existing,...accepted]);setDataset(accepted[0]);if(!existing.length){const next=initialSelections(accepted[0]);setSelections(next);setSnapshot(next);resetOutliers(accepted[0]);setRun(null);setDetail(null);}}
    if(mounted.current&&errors.length)setError(errors.join('；'));
    }finally{if(mounted.current)setBusy(null);}
  }
  function drop(event:DragEvent<HTMLDivElement>){event.preventDefault();if(!busy)void upload(Array.from(event.dataTransfer.files));}
  async function calculate(){
    if(!dataset||invalid||busy||!leaveAnalysis())return;
    setBusy(demo?'正在更新示例…':'正在计算 CPK、CGM 和 Site Diff…');setError(null);setCalculationErrors([]);const job=demo?null:operation.start();
    try{
      const result=demo?demoRun(selections):await request<Run>(`/runs?job_id=${job!.id}`,{method:'POST',signal:job!.controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify({owner_dataset_id:dataset.id,...Object.fromEntries((['cpk','cgm','mmsa'] as Module[]).filter(m=>sourceFor(m)).map(m=>[m,{dataset_ids:sourceFiles(files,selections[m]).map(file=>file.id),...apiSelection(selections[m],sourceFor(m))}]))})});
      if(!mounted.current||job?.controller.signal.aborted)return;
      setRun(result);setResultVersion(v=>v+1);setSnapshot(structuredClone(selections));setSelectedKey(result.rows[0]?.key??'');setOutlierNotes({});
      if(demo){setImportOpen(false);return;}
      operation.finish(job!);const reports:SourceReport[]=[],issues:SourceIssue[]=[];setOutlierReports([]);setOutlierIssues([]);setOutlierSnapshot(null);
      const outlierIds=outlierSettings.sourceIds.filter(id=>{const file=files.find(f=>f.id===id);return file&&hasOutlierSelection(outlierSettings,file);});
      for(const [index,id] of outlierIds.entries()){
        if(!mounted.current)break;const file=files.find(f=>f.id===id)!;setBusy(`正在计算 Outlier ${index+1} / ${outlierIds.length}：${file.filename}…`);const current=operation.start();
        try{const response=await request<{dataset_id:string;report:OutlierReport;report_id?:string}>(`/datasets/${id}/outliers?job_id=${current.id}`,{method:'POST',signal:current.controller.signal,headers:{'Content-Type':'application/json'},body:JSON.stringify(outlierRequest(outlierSettings,file))});if(current.controller.signal.aborted)throw new Error('计算已取消');reports.push({...response,filename:file.filename});}
        catch(error){issues.push({dataset_id:id,filename:file.filename,error:current.controller.signal.aborted?'计算已取消':(error as Error).message});if(current.controller.signal.aborted){for(const pending of outlierIds.slice(index+1))issues.push({dataset_id:pending,filename:files.find(f=>f.id===pending)!.filename,error:'计算已取消，尚未执行'});break;}}
        finally{operation.finish(current);}
      }
      if(!mounted.current)return;if(reports.length)await saveRunOutlierSnapshot(result,reports,{},[],issues);setOutlierReports(reports);setOutlierIssues(issues);setOutlierSnapshot(JSON.stringify(outlierSettings));
      if(issues.length)setError(`部分完成：${issues.map(i=>`${i.filename}：${i.error}`).join('；')}`);else setImportOpen(false);
    }catch(error){if(mounted.current){const message=job?.controller.signal.aborted?'计算已取消':(error as Error).message;setError(message);setCalculationErrors([...new Set((['cpk','cgm','mmsa'] as Module[]).flatMap(m=>sourceFiles(files,selections[m]).map(file=>file.id)))].map(id=>({dataset_id:id,filename:files.find(f=>f.id===id)!.filename,error:message})));}}
    finally{if(job)operation.finish(job);if(mounted.current)setBusy(null);}
  }
  async function download(kind:'report'|'parquet'){
    if(!dataset||busy||(kind==='report'&&(!run||dirty)))return;
    const job=kind==='parquet'&&!demo?operation.start():null;
    if(job){setBusy('正在从原始 STDF 生成 Parquet…');setError(null);}
    try{
      let blob:Blob;
      if(demo)blob=new Blob([JSON.stringify(localizeReport({mode:'design_preview',note:t('示例，非真实检测结果'),result:run,outlier_observations:outlierNotes}),null,2)],{type:'application/json'});
      else{const response=await fetch(`/api/release-check/datasets/${kind==='parquet'?dataset.id:run!.dataset_id}/${kind==='parquet'?`parquet?job_id=${job!.id}`:`runs/${run!.id}/report`}`,{signal:job?.controller.signal});if(!response.ok)throw new Error('下载失败，文件或结果可能已过期，请重新导入或计算。');blob=await response.blob();if(kind==='report'){const fullReports=await fullOutlierReports(outlierReports,job?.controller.signal);blob=new Blob([JSON.stringify(localizeReport({...outlierExport(JSON.parse(await blob.text()),rows,fullReports,outlierIssues,outlierSnapshot),outlier_observations:outlierNotes}),null,2)],{type:'application/json'});}}
      if(job?.controller.signal.aborted||!mounted.current)return;
      if(kind==='parquet'){downloadBlob(blob,`${dataset.filename}.wide_all.parquet`);return;}
      const url=URL.createObjectURL(blob),link=document.createElement('a');link.href=url;link.download=`${demo?'DEMO_':''}release-check-report.json`;link.click();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){if(mounted.current)setError(job?.controller.signal.aborted?'导出已取消':(error as Error).message);}
    finally{if(job){operation.finish(job);if(mounted.current)setBusy(null);}}
  }
  async function downloadWorkbook(module: 'cpk' | 'cgm' | 'mmsa' | 'outlier', format: 'xlsx' | 'pdf' = 'xlsx') {
    if (!run || demo || dirty || analysisDirty || busy || workbookRequest.current) return;
    const job = operation.start();
    const controller = job.controller;
    setBusy(`正在导出 ${module === 'cpk' ? 'CPK' : module === 'cgm' ? 'CGM' : module === 'mmsa' ? 'Site Diff' : 'Outlier'} ${format === 'pdf' ? 'PDF' : 'Excel'}…`);
    workbookRequest.current = controller; setExporting(format === 'pdf' ? `${module}-pdf` : module); setError(null);
    try {
      if(module==='outlier')await saveRunOutlierSnapshot(run,outlierReports,outlierNotes,rows,outlierIssues,controller.signal);
      const workbook = module==='mmsa'||module==='outlier' ? await fetchRunReviewReport(run,module==='mmsa'?'site_diff':'outlier',format,controller.signal,job.id) : format === 'pdf' ? await (module === 'cpk' ? fetchRunCpkPdf : fetchRunCgmPdf)(run, controller.signal, job.id) : await fetchRunWorkbook(run, module, controller.signal, job.id);
      if (!controller.signal.aborted && mounted.current) downloadReport(workbook);
    } catch (error) {
      if (!controller.signal.aborted && mounted.current) setError((error as Error).message);
    } finally {
      operation.finish(job);
      if (workbookRequest.current === controller) workbookRequest.current = null;
      if (mounted.current) { setExporting(null); setBusy(null); }
    }
  }
  async function downloadAuxiliary(path: string) {
    if (demo || dirty || analysisDirty || busy || workbookRequest.current) return;
    const job = operation.start();
    workbookRequest.current = job.controller;
    setBusy('正在导出 Excel…'); setExporting('auxiliary'); setError(null);
    try {
      const report = await fetchReportFile(path, job.controller.signal, job.id);
      if (!job.controller.signal.aborted && mounted.current) downloadReport(report);
    } catch (error) {
      if (!job.controller.signal.aborted && mounted.current) setError((error as Error).message);
    } finally {
      operation.finish(job);
      if (workbookRequest.current === job.controller) workbookRequest.current = null;
      if (mounted.current) { setBusy(null); setExporting(null); }
    }
  }
  useEffect(() => {
    const handler = (event: Event) => {
      const path = (event as CustomEvent<unknown>).detail;
      if (typeof path === 'string' && /^\/api\/release-check\/datasets\/[^/?]+\/(runs\/[^/?]+\/observations|outliers\/[^/?]+)\.xlsx$/.test(path)) {
        event.preventDefault();
        void downloadAuxiliary(path);
      }
    };
    window.addEventListener('release-report-download', handler);
    return () => window.removeEventListener('release-report-download', handler);
  }, [demo, dirty, analysisDirty, busy]);
  function moduleState(m:typeof checkModules[number]){if(m==='outlier'&&!outlierSettings.sourceIds.length)return '未选择';if(m!=='outlier'&&!sourceFor(m))return '未选择';if(m==='outlier'&&outlierIssues.length)return outlierReports.length?'部分完成':'失败';if(m!=='outlier'&&calculationErrors.length)return '失败';if(dirty)return '待更新';return m==='outlier'?outlierSnapshot?'已计算':'未计算':run?'已计算':'未计算';}
  const exportProgress = exporting && <span className={s.exportProgress}><progress aria-label={t("报告导出进度")} max={100} value={operation.progress ?? undefined}/>{localize(operation.progress != null && <span>{localize(Math.floor(operation.progress))}%</span>)}</span>;
  function assignRole(file:Dataset,role:Module|'outlier',checked:boolean){
    if(role==='outlier')setOutlierSettings(prev=>({...prev,sourceIds:checked?[...new Set([...prev.sourceIds,file.id])]:prev.sourceIds.filter(id=>id!==file.id),selections:{...prev.selections,[file.id]:prev.selections[file.id]??initialSelections(file).cpk}}));
    else setSelections(prev=>{const ids=sourceFiles(files,prev[role]).map(source=>source.id);return {...prev,[role]:changeSources(files,prev[role],checked?[...ids,file.id]:ids.filter(id=>id!==file.id))};});
  }
  return <div className={s.page}>
    <header className={s.topbar}><strong>Datalog Check</strong><div className={s.languageSwitch} role="group" aria-label={t("界面语言")}><button type="button" lang="zh-CN" aria-pressed={language === "zh"} onClick={() => setLanguage("zh")}>中文</button><button type="button" lang="en" aria-pressed={language === "en"} onClick={() => setLanguage("en")}>English</button></div><span>{localize(files.length)} {t(" 份文件")}</span>{localize(demo&&<span className={s.demoTag}>{t("示例数据")}</span>)}
      <span className={s.srOnly} role='status'>{localize(busy?t("正在计算"):!run?t("等待计算"):dirty?t("待更新"):outlierIssues.length?t("部分完成"):t("计算完成"))}</span>
      <div className={s.moduleStates}>{localize(checkModules.map(m=><span key={m}>{localize(checkLabels[m])} · {localize(moduleState(m))}</span>))}</div>{localize(dirty&&<span className={s.pending}>{t("配置已修改，未重新计算")}</span>)}
      <button onClick={()=>setImportOpen(true)}>{t("数据源与计算配置")}</button>
      <details className={s.exportMenu}><summary>{t("导出")}</summary><div><strong>{t("本次结果")}</strong><button disabled={!run||dirty||!!busy} onClick={()=>void download('report')}>{localize(demo?t("导出示例报告"):t("导出报告 JSON"))}</button><button disabled={!run||dirty||!!busy} onClick={()=>window.dispatchEvent(new CustomEvent('release-export',{detail:'notes'}))}>{t("观察 Excel")}</button>
        {localize((['cpk', 'cgm'] as const).map(module => <button key={module} disabled={!run||demo||dirty||analysisDirty||!!busy||!!exporting||run.modules?.includes(module)===false}
          title={localize(demo?t("请先导入真实 STDF 并计算"):analysisDirty?t("请先保存当前观察备注"):dirty?t("配置已修改，请重新计算"):t("导出本次已计算的完整报表"))}
          onClick={()=>void downloadWorkbook(module)}>{localize(exporting===module?t("正在导出…"):`${module==='cpk'?'CPK':'Cgm'} Excel (.xlsx)`)}</button>))}
        {localize((['cpk', 'cgm'] as const).map(module => <button key={`${module}-pdf`} disabled={!run||demo||dirty||analysisDirty||!!busy||!!exporting||run.modules?.includes(module)===false}
          title={localize(demo?t("请先导入真实 STDF 并计算"):analysisDirty?t("请先保存当前观察备注"):dirty?t("配置已修改，请重新计算"):t("导出 {0} 汇总、异常清单和异常测项详情", module.toUpperCase()))}
          onClick={()=>void downloadWorkbook(module, 'pdf')}>{localize(exporting===`${module}-pdf`?t("正在导出…"):`${module==='cpk'?'CPK':'Cgm'} PDF (.pdf)`)}</button>))}
        {outlierReports.filter(r=>r.report_id).map(r=><button key={r.dataset_id} disabled={dirty||!!busy} onClick={async()=>{try{await downloadAuxiliary(`/api/release-check/datasets/${r.dataset_id}/outliers/${r.report_id}.xlsx`);}catch(error){setError((error as Error).message);}}}>{t("Outlier 诊断 Excel · ")}{r.filename}</button>)}
        {(['mmsa','outlier'] as const).flatMap(m=>(['xlsx','pdf'] as const).map(format=><button key={`${m}-${format}`} disabled={!run||demo||dirty||analysisDirty||!!busy||(m==='outlier'?!outlierReports.length:!run.rows.some(r=>r.mmsa))} onClick={()=>void downloadWorkbook(m,format)}>{m==='mmsa'?'Site Diff':'Outlier'} {format==='pdf'?'PDF (.pdf)':'Excel (.xlsx)'}</button>))}
        <strong>{t("当前测项")}</strong>{localize([['images',t("图组 · 当前视图")],['full',t("图组 · 完整范围")],['context',t("图表上下文 JSON")]].map(([id,label])=><button key={id} title={localize(view==='overview'?t("请先进入一个模块查看图表"):undefined)} disabled={!run||dirty||!!busy||view==='overview'} onClick={()=>window.dispatchEvent(new CustomEvent('release-export',{detail:id}))}>{localize(label)}</button>))}
        <strong>{t("原始数据")}</strong><button disabled={!dataset||demo||!!busy} onClick={()=>void download('parquet')}>{t("保存 Parquet")}</button><strong>{t("测项组 Cg 表")}</strong><button disabled={!run||dirty||!!busy||view!=='cgm'} onClick={()=>window.dispatchEvent(new CustomEvent('release-export',{detail:'matrix'}))}>CSV</button>
      </div></details>
    </header>
    <input type='file' ref={input} hidden multiple accept='.stdf,.std,.gz,.bz2' aria-label={t("选择 STDF 文件")} disabled={!!busy} onChange={e=>{void upload(Array.from(e.target.files??[]));e.target.value='';}}/>
    {localize(error&&<div className={s.errorNotice} role='alert'>{localize(error)}<button onClick={()=>setError(null)} aria-label={t("关闭错误提示")}>×</button></div>)}
    {localize(importOpen&&<div className={s.drawerBackdrop}><section className={s.configDrawer} role='dialog' aria-modal='true' aria-label={t("数据源与计算配置")}><header><div><h2>{t("数据源与计算配置")}</h2><p>{t("CPK 与 Outlier 用 bin1 有效结果，CGM 与 Site Diff 用全部有效结果。")}</p></div><button onClick={()=>setImportOpen(false)}>{t("关闭")}</button></header>
      <div className={s.drawerActions}><button disabled={!!busy} onClick={()=>input.current?.click()}>{t("添加文件")}</button><button disabled={!!busy} onClick={clear}>{t("清空")}</button><button disabled={!!busy} onClick={loadDemo}>{t("使用示例")}</button></div>
      <div className={s.dropzone} onDragOver={e=>e.preventDefault()} onDrop={drop}>{t("拖入 STDF 文件，最多保留 8 份")}</div>
      <FilePool files={files} activeId={dataset?.id} selections={selections} outlierSourceIds={outlierSettings.sourceIds} disabled={!!busy} onSelect={setDataset} onRemove={removeFile} onRole={assignRole} issues={[...outlierIssues,...calculationErrors]}/>
      <aside className={s.sidebar}><div className={s.selectionHeading}><h3>{t("计算范围")}</h3><span>{t("各项分别选择")}</span></div><div className={s.selectionTabs}>{localize(checkModules.map(m=><button key={m} disabled={!!busy} aria-label={localize(checkLabels[m])} aria-pressed={module===m} onClick={()=>setModule(m)}>{localize(checkLabels[m])}{localize(m!=='outlier'&&<small>{localize(selections[m].sites.length)} site · {localize(selections[m].tests.length)} {t(" 测项 · ")}{localize(selections[m].parts===null&&!selections[m].excludedParts?.length?t("全部记录"):(selections[m].parts?.length??Math.max(0,(sourceFor(m)?.record_count??0)-(selections[m].excludedParts?.length??0)))+t(" 条记录"))}</small>)}</button>))}</div>
        {localize(module==='outlier'?<OutlierSettings files={files} value={outlierSettings} disabled={!!busy} onChange={setOutlierSettings}/>:moduleSource?<SelectionPanel key={`${moduleSource.id}-${module}`} dataset={moduleSource} module={module} selection={selections[module]} disabled={!!busy} onChange={next=>setSelections(prev=>({...prev,[module]:next}))}/>:<p className={s.help}>{t("在上方文件名旁勾选 ")}{localize(moduleLabels[module])}{t("，即可设置计算范围。")}</p>)}
        <div className={s.calculateArea}>{localize(invalid&&<p className={s.error}>{localize(invalid)}</p>)}<button className={s.primary} disabled={!!invalid||!!busy} onClick={()=>void calculate()}>{localize(run?demo?t("更新示例结果"):t("重新计算"):t("计算 Release 检查"))}</button></div>
      </aside>
      {localize(busy&&<div className={s.busyNotice} role='status'><span className={s.spinner}/>{localize(busy)}{localize(exportProgress)}<span>{localize(operation.phase)}</span>{localize(operation.active&&<button disabled={operation.cancelling} onClick={()=>void operation.cancel()}>{t("取消操作")}</button>)}</div>)}
    </section></div>)}
    {localize(busy&&!importOpen&&<div className={s.busyNotice} role='status'>{localize(busy)}{localize(exportProgress)} · {localize(operation.phase)}{localize(operation.active&&<button disabled={operation.cancelling} onClick={()=>void operation.cancel()}>{t("取消操作")}</button>)}</div>)}
    <ReviewWorkbench key={resultVersion} onTab={setView} onOutlierNotes={setOutlierNotes} detail={detail} run={run} rows={rows} selections={snapshot} demo={!!demo} stale={dirty} selectedKey={selectedKey} onSelect={setSelectedKey} onDirty={setAnalysisDirty} reports={outlierReports} issues={outlierIssues}/>
  </div>;
}
