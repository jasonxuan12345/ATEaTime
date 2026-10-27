"""Run-bound Site Diff and Outlier report delivery."""
from collections import OrderedDict
from datetime import date
import json
from app import release_check_service as release
from deepyield.compute.release_control import checkpoint, timed, event
from deepyield.compute.release_cpk_excel import _final_diagnostics
from deepyield.compute.release_chart_samples import ReportSampleResolver
from deepyield.compute.release_site_diff_excel import render_site_diff_xlsx
from deepyield.compute.release_site_diff_pdf import render_site_diff_pdf
from deepyield.compute.release_outlier_excel import render_outlier_xlsx
from deepyield.compute.release_outlier_pdf import render_outlier_pdf
from deepyield.compute.release_outliers import _TRACE_COLUMNS, _trace, _buckets
from deepyield.compute.release_cpk import _group_events
from deepyield.io.stdf_parquet_writer import _classify_part_flag


def bind_outlier_reports(dataset_id,run_id,payload):
    owner=release.get_dataset(dataset_id)
    result,_=release.get_run(owner,run_id)
    sources=[];known=set();seen=set()
    for link in payload['sources']:
        key=link['dataset_id']
        if key in seen:raise ValueError('Duplicate Outlier source.')
        seen.add(key);dataset=release.get_dataset(key)
        with release._lock:
            original=dataset.outlier_runs.get(link['report_id'])
            if original is None:raise KeyError('Outlier result expired; calculate it again.')
            if original['source']['source_sha256']!=dataset.metadata['source_sha256']:raise ValueError('Outlier source does not match.')
            sources.append({'dataset_id':key,'report_id':link['report_id'],'filename':dataset.filename,'report':original})
            known.update((key,b['group_id']) for b in original['baselines'])
    notes={}
    for entry in payload.get('observations',[]):
        key=(entry['dataset_id'],entry['group_id'])
        if key not in known:raise ValueError('Outlier observation does not belong to the attached result.')
        if key in notes and notes[key]!=entry['note']:raise ValueError('Conflicting Outlier observations.')
        notes[key]=dict(entry['note'])
    # The run retains these exact report objects even if the source's recent-run
    # list later evicts them. Source sessions must still be available on export.
    value={'sources':sources,'observations':notes,'issues':payload.get('issues',[])}
    with release._lock:
        if owner.runs.get(run_id,(None,))[0] is not result:raise KeyError('Run expired.')
        old=owner.outlier_report_exports.get(run_id)
        same=(old is not None and [(s['dataset_id'],s['report_id']) for s in old['sources']]==[(s['dataset_id'],s['report_id']) for s in sources]
              and old['observations']==notes and old.get('issues',[])==value['issues'])
        if not same:
            value['version']=(old or {}).get('version',0)+1
            owner.outlier_report_exports[run_id]=value
    return {'source_count':len(sources),'version':owner.outlier_report_exports[run_id]['version']}


def _outlier_bundle(saved):
    if not saved or not saved['sources']:raise KeyError('No Outlier result is attached to this run.')
    sources=[];metas=[]
    for link in saved['sources']:
        dataset=release.get_dataset(link['dataset_id']);report=link['report']
        if dataset.metadata['source_sha256']!=report['source']['source_sha256']:raise ValueError('Outlier source changed; calculate it again.')
        columns=[c for c in ('__part_index','HBIN','PART_FLAG') if c in dataset.frame]
        bins={int(r['__part_index']):r for r in dataset.frame[columns].to_dict('records')}
        def chip(c):
            part=bins[c['part_index']];flag=part.get('PART_FLAG')
            state=_classify_part_flag(int(flag))[0] if flag is not None else None
            return {**c,'hard_bin':part.get('HBIN'),'part_result':state}
        snapshot={**report,'baselines':[{**b,'observation':saved['observations'].get((link['dataset_id'],b['group_id']))} for b in report['baselines']],
                  'chips':[chip(c) for c in report['chips']]}
        sources.append({**link,'report':snapshot,'metadata':dataset.metadata,'dataset':dataset})
        metas.append(dataset.metadata)
    metadata=dict(metas[0])
    # Avoid assigning the first source's device to mixed / unknown devices.
    devices={str((m.get('stdf_metadata',{}).get('mir') or {}).get('PART_TYP','')).strip() for m in metas}
    if len(devices)>1:metadata={**metadata,'stdf_metadata':{**metadata.get('stdf_metadata',{}),'mir':{}}}
    return {'sources':sources,'metadata':metadata,'issues':saved.get('issues',[])}


class OutlierSamples:
    """Build selected groups once per source; retain at most two display views."""
    def __init__(self,bundle):self.sources={s['dataset_id']:s for s in bundle['sources']};self.prepared={};self.cache=OrderedDict()
    def __call__(self,source_id,group_id):
        key=source_id,group_id
        if key in self.cache:self.cache.move_to_end(key);return self.cache[key]
        source=self.sources[source_id];report=source['report']
        if source_id not in self.prepared:
            frame=source['dataset'].frame
            selected=frame.loc[frame['__part_index'].isin(report['selection']['selected_part_indices'])]
            traces={int(r['__part_index']):_trace(r) for r in selected[[c for c in _TRACE_COLUMNS if c in selected]].to_dict('records')}
            chosen={h['column'] for c in report['chips'] for h in c['hits']}
            groups=_group_events(selected,report['source'],chosen)
            dies={}
            for trace in traces.values():
                if trace['wafer'] is not None and trace['x'] is not None and trace['y'] is not None:
                    dies[trace['lot'],trace['wafer'],trace['x'],trace['y']]={k:trace[k] for k in ('lot','wafer','x','y')}
            self.prepared[source_id]=(groups,traces,list(dies.values()))
        groups,traces,dies=self.prepared[source_id]
        group=groups.get(group_id)
        if group is None:raise ValueError('Outlier test data no longer matches the saved result.')
        points=[]
        refs={(b['lot'],b['wafer'],b['head'],b['site']):b for b in report['baselines'] if b['group_id']==group_id}
        for channel,bucket in _buckets(group,traces).items():
            baseline=refs.get(channel)
            if baseline is None:continue
            for trace,event,value in bucket['rows']:
                if len(points)%2048==0:checkpoint()
                points.append({**trace,'record_sequence':int(event['record_sequence']),'value':value,'baseline_id':baseline['baseline_id']})
        points.sort(key=lambda p:(p['part_index'],p['record_sequence']))
        total=len(points)
        if total>20000:points=[points[i*(total-1)//19999] for i in range(20000)]
        view={'points':points,'total_points':total,'sampled':len(points)<total,'dies':dies}
        self.cache[key]=view
        while len(self.cache)>2:self.cache.popitem(last=False)
        return view


@timed('export.review_report')
def export_review_report(dataset_id,run_id,module,format_,file_delivery=False):
    event('review_report_export', module=module, format=format_)
    owner=release.get_dataset(dataset_id);result,reports=release.get_run(owner,run_id)
    if module=='site_diff':
        if 'mmsa' not in reports:raise KeyError('This run has no Site Diff result.')
        source=release.run_source(owner,result,'mmsa');report=reports['mmsa']
        if report['source']['source_sha256']!=source.metadata['source_sha256']:raise ValueError('Site Diff source does not match.')
        with release._lock:
            diagnostics={k:dict(v) for k,v in _final_diagnostics(result,'mmsa').items()}
            snapshot={**report,'items':[{**item,'diagnosis':diagnostics.get(item['group_id'],item.get('diagnosis'))} for item in report['items']]}
            version=result.get('remark_versions',{}).get('mmsa',0)
        selected=source.frame.loc[source.frame['__part_index'].isin(report['selection']['selected_part_indices'])]
        values=[]
        for col in ('LOT_ID','WAFER_ID'):
            if col in selected:values.append(col+'='+', '.join(dict.fromkeys(str(v) for v in selected[col] if v is not None)))
        values.append('Temperature='+', '.join(dict.fromkeys(str(i.get('conditions',{}).get('test_temperature','--')) for i in snapshot['items'])))
        def write(stream):
            if format_=='xlsx':return render_site_diff_xlsx(snapshot,metadata=source.metadata,output=stream)
            return render_site_diff_pdf(snapshot,metadata=source.metadata,output=stream,population=' | '.join(values),
                sample_provider=ReportSampleResolver(source.frame,source.metadata['source_sha256'],identifiers=False,
                    part_indices=report['selection']['selected_part_indices'],minimal=True))
        key=('review-v1',run_id,module,format_,id(report),source.metadata['source_sha256'],version,date.today())
    elif module=='outlier':
        with release._lock:saved=owner.outlier_report_exports.get(run_id)
        bundle=_outlier_bundle(saved);version=saved['version']
        def write(stream):
            return render_outlier_xlsx(bundle,output=stream) if format_=='xlsx' else render_outlier_pdf(bundle,output=stream,sample_provider=OutlierSamples(bundle))
        key=('review-v1',run_id,module,format_,version,date.today())
    else:raise ValueError('Unsupported report module.')
    return owner.report_files.build(key,write,file_delivery=file_delivery)
