"""Four-sheet Outlier evidence workbook. No sample references are materialized."""
from io import BytesIO
import json
from pathlib import Path
import xlsxwriter
from deepyield.compute.release_control import checkpoint, report_progress
from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS
from deepyield.compute.release_review_data import MATRIX_NOTE, filename, outlier_note


def flatten(bundle):
    baselines, chips = [], []
    for source in bundle['sources']:
        baselines.extend({**b, 'dataset_id':source['dataset_id'], 'file':source['filename']} for b in source['report']['baselines'])
        chips.extend({**c, 'dataset_id':source['dataset_id'], 'file':source['filename']} for c in source['report']['chips'])
    chips.sort(key=lambda c:(-c['max_score'],c['dataset_id'],c['part_index']))
    return baselines, chips


def render_outlier_xlsx(bundle, *, output=None, report_date=None):
    baselines, chips = flatten(bundle)
    index = {(b['dataset_id'],b['baseline_id']):b for b in baselines}
    groups = list(dict.fromkeys((c['dataset_id'],h['group_id']) for c in chips for h in c['hits']))
    by_group = {(b['dataset_id'],b['group_id']):b for b in baselines}
    if len(groups)+1 > 16384 or max(len(baselines),len(chips),sum(len(c['hits']) for c in chips)) > 1048575:
        raise ValueError('Outlier report exceeds Excel worksheet limits.')
    config=json.loads((Path(__file__).resolve().parents[2]/'config/release_outlier_excel.json').read_text(encoding='utf-8'))
    buffer=output if output is not None else BytesIO()
    with xlsxwriter.Workbook(buffer,WORKBOOK_OPTIONS) as book:
        header=book.add_format({'bold':True,'bg_color':'#404040','font_color':'#FFFFFF','text_wrap':True,'valign':'vcenter'})
        wrap=book.add_format({'text_wrap':True,'valign':'top','num_format':'General'})
        grey=book.add_format({'bg_color':'#E5EAED'})
        yellow=book.add_format({'bg_color':'#F9E8BB','num_format':'0.00'})
        red=book.add_format({'bg_color':'#F4D2D4','num_format':'0.00'})
        def sheet(name,headers):
            s=book.add_worksheet(name);s.write_row(0,0,headers,header);s.set_row(0,42)
            s.freeze_panes(1,2);s.set_column(0,len(headers)-1,18);s.set_column(len(headers)-1,len(headers)-1,65)
            return s
        def write(s,line,values):
            checkpoint()
            s.write_row(line,0,[v[:32767] if isinstance(v,str) else v for v in values],wrap)
            s.set_row(line,48)
        s=sheet('Reference_Group_Stats',config['reference_headers'])
        scope='Reference groups: File / Test & Condition / Lot / Wafer / Head / Site. Selected valid Bin1 PASS records within specification.'
        if bundle.get('issues'):scope+=' PARTIAL RESULTS: '+json.dumps(bundle['issues'],ensure_ascii=False)
        s.write_comment(0,0,scope[:32767])
        for line,b in enumerate(baselines,1):
            values=[b['file'],b.get('test_num'),b.get('test_name'),json.dumps(b['conditions'],ensure_ascii=False,sort_keys=True)]
            values += [b.get(k) for k in ('lot','wafer','head','site','unit','lsl','usl','n','median','q1','q3','robust_sigma','lower_limit','upper_limit','candidate_count','status')]
            values += [outlier_note(b)]
            write(s,line,values)
            s.write_comment(line,len(values)-1,json.dumps({k:b.get(k) for k in ('baseline_id','group_id','conditions','excluded_counts','diagnosis','observation')},ensure_ascii=False)[:32767])
        s.autofilter(0,0,max(1,len(baselines)),len(config['reference_headers'])-1)
        s=sheet('Candidate_Records',config['candidate_headers'])
        for line,c in enumerate(chips,1):
            notes=list(dict.fromkeys(outlier_note(index[(c['dataset_id'],h['baseline_id'])]) for h in c['hits']))
            values=[f"{c['dataset_id']}:{c['part_index']}",c['file']]
            values += [c.get(k) for k in ('lot','wafer','head','site','x','y','hard_bin','part_result','hit_count','max_score')]
            values += ['; '.join(f"{h['test_num']} {h['test_name']} = {h['value']:.6g} (score {h['score']:.5g})" for h in c['hits'][:10]),'\n'.join(notes)]
            write(s,line,values)
            s.write_comment(line,12,'Top 10 hits shown here. Every hit is retained in Outlier_Hit_Details.')
        s.autofilter(0,0,max(1,len(chips)),len(config['candidate_headers'])-1)
        s=sheet('Candidate_Test_Matrix',['Candidate Record (File / Lot / Wafer / Head / Site / Part / X,Y)']+
                [f"{by_group[g]['file']} | {by_group[g]['test_num']} {by_group[g]['test_name']} | {json.dumps(by_group[g]['conditions'],ensure_ascii=False,sort_keys=True)} | {g[1]}" for g in groups])
        s.set_column(0,0,60);s.set_column(1,max(1,len(groups)),18)
        s.write_comment(0,0,MATRIX_NOTE)
        for col,g in enumerate(groups,1):s.write_comment(0,col,MATRIX_NOTE+'\nGroup: '+g[1])
        for line,c in enumerate(chips,1):
            checkpoint()
            label=f"{c['file']} / {c.get('lot')} / {c.get('wafer')} / H{c['head']} / S{c['site']} / #{c['part_index']} / ({c['x']},{c['y']})"
            s.write_string(line,0,label,wrap)
            hits={h['group_id']:h['score'] for h in c['hits']}
            for col,(source_id,gid) in enumerate(groups,1):
                score=hits.get(gid) if source_id==c['dataset_id'] else None
                if score is None:s.write_blank(line,col,None,grey)
                else:s.write_number(line,col,score,red if score>10 else yellow)
            s.set_row(line,42)
        s=sheet('Outlier_Hit_Details',config['hit_headers'])
        line=1
        for c in chips:
            for h in c['hits']:
                b=index[(c['dataset_id'],h['baseline_id'])]
                values=[c['file']]+[c.get(k) for k in ('lot','wafer','head','site','part_index','x','y')]
                values += [h.get(k) for k in ('test_num','test_name','value','median','lower_limit','upper_limit','robust_sigma','score')]
                values += [h.get('record_sequence'),b['baseline_id'],json.dumps(b['conditions'],ensure_ascii=False,sort_keys=True),outlier_note(b)]
                write(s,line,values);line+=1
        s.autofilter(0,0,max(1,line-1),len(config['hit_headers'])-1)
        book.set_properties({'title':'Outlier Report','comments':json.dumps({'reference_groups':'File / Test & Condition / Lot / Wafer / Head / Site',
            'scope':'Selected valid Bin1 PASS records within specification; candidates are not confirmed defects.',
            'sources':[{'filename':s['filename'],'source':s['report']['source'],'selection':s['report']['selection'],'algorithm':s['report']['algorithm']} for s in bundle['sources']]},ensure_ascii=False)})
        report_progress('Writing Outlier Excel',95)
    metadata=bundle['metadata']
    return {'filename':filename(baselines,metadata,'Outlier','xlsx',report_date),'content':buffer.getvalue() if output is None else None,
            'baseline_count':len(baselines),'candidate_count':len(chips)}
