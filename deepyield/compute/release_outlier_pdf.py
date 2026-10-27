"""Static Outlier report with distribution and sequence shown together."""
from datetime import datetime
from collections import defaultdict,Counter
from io import BytesIO
import json
import numpy as np
from deepyield.compute.release_pdf_canvas import ReportCanvas
from deepyield.compute.release_outlier_excel import flatten
from deepyield.compute.release_review_data import MATRIX_NOTE,filename,outlier_note,fmt
from deepyield.compute.release_review_pdf import Slides,WIDTH,HEIGHT,BLUE,Panel,PALETTE,chunks,sample,note,context,kpis
from deepyield.compute.release_control import report_progress,checkpoint


def _chip(c):
    return f"{c['file']} | {c.get('lot')}/{c.get('wafer')} H{c.get('head')} S{c.get('site')} #{c['part_index']} ({c.get('x')},{c.get('y')})"


def _wafer(s,rect,candidates,dies,focus):
    x,y,w,h=rect
    if not dies and not candidates:
        s.text(x+8,y+h-15,'3  No wafer coordinates available',11,BLUE,True);return
    all_points=[(d['x'],d['y']) for d in dies]+[(c['x'],c['y']) for c in candidates]
    cx=(min(v[0] for v in all_points)+max(v[0] for v in all_points))/2
    cy=(min(v[1] for v in all_points)+max(v[1] for v in all_points))/2
    radius=max(1,max(np.hypot(v[0]-cx,v[1]-cy) for v in all_points))*1.04
    scale=2*radius/min(w-72,h-72)
    p=Panel(s,rect,'3  Wafer: grey tested / orange candidates / red focus',
            [cx-scale*(w-72)/2,cx+scale*(w-72)/2],[cy-scale*(h-72)/2,cy+scale*(h-72)/2],'Die X','Die Y')
    angles=np.linspace(0,2*np.pi,100)
    p.line(cx+radius*np.cos(angles),cy+radius*np.sin(angles),'#94A3B8',.8)
    p.dots([(d['x'],d['y']) for d in dies],'#DCE3E8',1.5)
    p.dots([(c['x'],c['y']) for c in candidates],'#E69F00',2.5)
    if focus and focus.get('x') is not None and focus.get('y') is not None:
        p.dots([(focus['x'],focus['y'])],'#DC2626',4)
        p.line([focus['x']-scale*7,focus['x']+scale*7],[focus['y']]*2,'#DC2626')
        p.line([focus['x']]*2,[focus['y']-scale*7,focus['y']+scale*7],'#DC2626')


def _matrix(s,baselines,chips,groups,page,total):
    s.header('Outlier | Candidate x Test Matrix','Part 1 - Matrix Pages',page,total)
    context(s,MATRIX_NOTE,554)
    left,top,cw,rh=310,475,101,23
    index={(b['dataset_id'],b['group_id']):b for b in baselines}
    for col,g in enumerate(groups):
        b=index[g];s.text(left+col*cw,516,f"{b['test_num']} {b['test_name']}",8,width=cw-20)
        s.text(left+col*cw,500,b['file'],7,width=cw-20)
        s.c.textAnnotation(json.dumps({'file':b['file'],'group_id':g[1],'condition':b['conditions']},ensure_ascii=False),Rect=(left+(col+1)*cw-16,500,left+(col+1)*cw-3,513),Name='Comment')
    for line,c in enumerate(chips):
        checkpoint();y=top-line*rh
        s.text(30,y-15,_chip(c),7,width=250)
        s.c.textAnnotation(_chip(c),Rect=(286,y-18,299,y-5),Name='Comment')
        hits={h['group_id']:h for h in c['hits']}
        for col,(source,gid) in enumerate(groups):
            hit=hits.get(gid) if source==c['dataset_id'] else None
            score=hit['score'] if hit else None
            color='#E5EAED' if score is None else '#FDE047' if score<=10 else '#FB923C' if score<=50 else '#DC2626'
            s.box(left+col*cw,y-rh,cw-2,rh-2,color)
            if score is not None:s.text(left+col*cw+5,y-15,fmt(score),8,'#FFFFFF' if score>50 else '#193449',width=cw-10)
    s.text(30,48,'Score in robust sigma units. Yellow <=10; orange 10-50; red >50. Reference thresholds are saved per source.',8,width=1092)
    s.text(30,30,MATRIX_NOTE,9,BLUE,True,width=1092)


def _test_detail(s,source,gid,refs,candidates,view,wafer,page,total):
    first=refs[0];s.header(f"Outlier | Test #{first['test_num']} - {first['test_name']}",'Part 2 - Per-test Details',page,total)
    context(s,f"File: {source['filename']} | Unit: {first.get('unit') or '--'} | Groups: File / Test & Condition / Lot / Wafer / Head / Site",555)
    context(s,'Condition: '+json.dumps(first['conditions'],ensure_ascii=False,sort_keys=True),540)
    note(s,'\n'.join(dict.fromkeys(outlier_note(b) for b in refs)),30,472,1092,63)
    ids={b['baseline_id'] for b in refs}
    hits=[(c,h) for c in candidates for h in c['hits'] if h['baseline_id'] in ids]
    focused=max(hits,key=lambda pair:pair[1]['score'])[0] if hits else None
    xs=[v for b in refs for v in (b.get('q1'),b.get('q3'),b.get('lower_limit'),b.get('upper_limit'),b.get('lsl'),b.get('usl'))]
    xs += [h['value'] for _,h in hits]
    p=Panel(s,(30,260,530,205),'1  Reference Distribution + Outlier Deviation',xs,[-.5,len(refs)-.5],'Measured Value','Per reference group; not pooled across sites',yticks=False)
    for row,b in enumerate(refs):
        yy=len(refs)-1-row
        label=f"H{b['head']} S{b['site']} {b.get('lot')}/{b.get('wafer')}"
        if b['lower_limit'] is None:continue
        p.line([b['lower_limit'],b['upper_limit']],[yy]*2,'#1B7F79',2)
        p.polygon([(b['q1'],yy-.18),(b['q3'],yy-.18),(b['q3'],yy+.18),(b['q1'],yy+.18)],'#99D5D5')
        p.line([b['median']]*2,[yy-.25,yy+.25],'#111111',1.5)
        for spec in (b.get('lsl'),b.get('usl')):
            if spec is not None:p.line([spec]*2,[yy-.32,yy+.32],'#CC3333',.8,[3,2])
        shown=sorted(((c,h) for c,h in hits if h['baseline_id']==b['baseline_id']),key=lambda pair:-pair[1]['score'])[:1000]
        p.dots([(h['value'],yy) for _,h in shown],'#E69F00',2)
        if shown:
            c,h=shown[0];p.dots([(h['value'],yy)],'#DC2626',3);p.label(h['value'],yy+.15,f"{fmt(h['score'])} sigma")
        p.label(p.xlim[0],yy+.28,label)
    background=[pt for pt in view['points'] if pt['baseline_id'] in ids]
    p=Panel(s,(590,260,532,205),f"2  Test Sequence (background {len(background)}/{view['total_points']})",[pt['part_index'] for pt in background]+[c['part_index'] for c,h in hits],[pt['value'] for pt in background]+[h['value'] for c,h in hits],'Part Index','Value; all candidate hits overlaid')
    for i,site in enumerate(sorted({pt['site'] for pt in background})):
        p.dots([(pt['part_index'],pt['value']) for pt in background if pt['site']==site],PALETTE[i%8],.9)
    p.dots([(c['part_index'],h['value']) for c,h in hits],'#E69F00',2)
    if focused:p.dots([(c['part_index'],h['value']) for c,h in hits if c['part_index']==focused['part_index']],'#DC2626',3)
    scoped=[c for c in candidates if c.get('x') is not None and c.get('y') is not None and (c.get('lot'),c.get('wafer'))==wafer]
    dies=[d for d in view['dies'] if (d['lot'],d['wafer'])==wafer] if wafer else []
    if wafer:_wafer(s,(30,35,530,205),scoped,dies,next((c for c in scoped if focused and c['part_index']==focused['part_index']),scoped[0] if scoped else None))
    else:
        s.text(38,225,'3  Reference Statistics (no wafer coordinates)',11,BLUE,True)
        s.table(['H / Site','n','Median','Q1 / Q3','Screen Low / High'],[[f"H{b['head']} S{b['site']}",b['n'],fmt(b['median']),f"{fmt(b['q1'])} / {fmt(b['q3'])}",f"{fmt(b['lower_limit'])} / {fmt(b['upper_limit'])}"] for b in refs],[65,45,100,140,180],30,210,18,font=7)
    s.text(598,225,'4  Reference Statistics + Top Candidate Records',11,BLUE,True)
    s.table(['H / Site','n','Median','Q1 / Q3','Screen Low / High'],[[f"H{b['head']} S{b['site']}",b['n'],fmt(b['median']),f"{fmt(b['q1'])} / {fmt(b['q3'])}",f"{fmt(b['lower_limit'])} / {fmt(b['upper_limit'])}"] for b in refs],[65,35,85,120,227],590,210,13,font=6.5)
    top=sorted(hits,key=lambda pair:-pair[1]['score'])[:3]
    ypos=210-30-len(refs)*13-14
    for c,h in top:
        s.text(598,ypos,f"#{c['part_index']} {c.get('lot')}/{c.get('wafer')} S{c['site']} ({c.get('x')},{c.get('y')}) Value={fmt(h['value'])} Score={fmt(h['score'])}",7,width=516);ypos-=13
    s.text(30,15,'Candidates are not confirmed defects. Distribution <=1000 candidates/group; background <=20000/test; all candidate hits retained in trend.',7,width=1092)


def _profile(s,c,baselines,view,page,total):
    s.header('Outlier | Candidate Chip Profile','Part 3 - Multiple-hit / Reviewed Records',page,total)
    context(s,_chip(c),555)
    context(s,f"Hit tests: {c['hit_count']} | Max robust sigma: {fmt(c['max_score'])} | Bin / Result: {fmt(c.get('hard_bin'))} / {fmt(c.get('part_result'))}",539)
    hits=c['hits'][:40]
    p=Panel(s,(30,70,650,440),'Hit Tests (top 40; complete hits in Excel)',[0]+[h['score'] for h in hits],[-1,len(hits)],'Deviation (robust sigma)','',yticks=False)
    for i,h in enumerate(hits):
        y=len(hits)-i-1;p.line([0,h['score']],[y,y],'#E69F00',4);p.label(0,y,f"{h['test_num']} {h['test_name']}")
    if c.get('x') is not None and c.get('y') is not None and c.get('wafer') is not None:
        dies=[d for d in view['dies'] if d['lot']==c.get('lot') and d['wafer']==c.get('wafer')]
        _wafer(s,(700,235,422,275),[c],dies,c)
    else:s.text(708,470,'No wafer coordinates available.',11,BLUE,True)
    notes='\n'.join(dict.fromkeys(outlier_note(baselines[(c['dataset_id'],h['baseline_id'])]) for h in c['hits']))
    note(s,notes,700,70,422,145)
    s.text(30,28,'Part Index identifies an execution record; retests remain separate. Top 40 hits shown, never reclassified as confirmed defects.',8,width=1092)


def render_outlier_pdf(bundle,*,sample_provider,output=None):
    baselines,chips=flatten(bundle)
    bindex={(b['dataset_id'],b['baseline_id']):b for b in baselines}
    group_refs=defaultdict(list)
    for b in baselines:group_refs[(b['dataset_id'],b['group_id'])].append(b)
    group_hits=defaultdict(list)
    for c in chips:
        for gid in dict.fromkeys(h['group_id'] for h in c['hits']):group_hits[(c['dataset_id'],gid)].append(c)
    groups=sorted(group_hits,key=lambda g:-max(c['max_score'] for c in group_hits[g]))
    plans=[]
    summaries=max(1,len(chunks(chips,12)),len(chunks(groups,12)))
    plans += [('summary',i) for i in range(summaries)]
    if chips:
        plans += [('matrix',(cc,gg)) for cc in chunks(chips,16) for gg in chunks(groups,8)]
    for g in groups:
        for refs in chunks(group_refs[g],8):
            wafers=list(dict.fromkeys((c.get('lot'),c.get('wafer')) for c in group_hits[g] if c.get('wafer') is not None and c.get('x') is not None and c.get('y') is not None)) or [None]
            plans += [('detail',(g,refs,wafer)) for wafer in wafers]
    # Notes are currently saved per test group, not per chip. Pick one most
    # severe representative per noted group instead of profiling every hit.
    noted_focus=set()
    for g in groups:
        if any(b.get('observation') for b in group_refs[g]):
            c=max(group_hits[g],key=lambda c:max(h['score'] for h in c['hits'] if h['group_id']==g[1]))
            noted_focus.add((c['dataset_id'],c['part_index']))
    profiles=[c for c in chips if c['hit_count']>1 or (c['dataset_id'],c['part_index']) in noted_focus]
    plans += [('profile',c) for c in profiles]
    buffer=output if output is not None else BytesIO();canvas=ReportCanvas(buffer,pagesize=(WIDTH,HEIGHT),pageCompression=1)
    canvas.setTitle('Outlier Report');canvas.setAuthor('Datalog Check')
    s=Slides(canvas,datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'))
    sources={source['dataset_id']:source for source in bundle['sources']}
    top_groups=sorted(groups,key=lambda g:-len(group_hits[g]))
    for page,(kind,entry) in enumerate(plans,1):
        checkpoint();report_progress('Generating Outlier PDF',page/len(plans)*95)
        if kind=='summary':
            s.header('Outlier Analysis Report','Part 1 - Summary',page,len(plans))
            context(s,'Sources: '+'; '.join(source['filename'] for source in bundle['sources']),555)
            context(s,'Reference groups: File / Test & Condition / Lot / Wafer / Head / Site (including effective specifications)',540)
            context(s,'Reference population: selected valid Bin1 PASS records within specification; robust sigma = (Q3-Q1)/1.35.',524)
            if bundle.get('issues'):
                context(s,'PARTIAL RESULTS: '+'; '.join(f"{issue.get('filename','--')}: {issue.get('error','--')}" for issue in bundle['issues']),510)
            kpis(s,[('Reference Groups / Tests',f"{len(baselines)} / {len(group_refs)}"),('Total Tested Parts',sum(source['report']['summary']['selected_record_count'] for source in bundle['sources'])),('Outlier Candidate Records',len(chips)),('Tests with Outliers',len(groups))])
            cc=chips[entry*12:(entry+1)*12];gg=top_groups[entry*12:(entry+1)*12]
            s.table(['Top Candidate Record','Hit Tests','Max Score'],[[_chip(c),c['hit_count'],fmt(c['max_score'])] for c in cc],[385,60,80],30,420,27,font=8,wrap_columns=(0,))
            s.table(['Top Test (file / condition scoped)','Candidates'],[[f"{sources[g[0]]['filename']} | {group_refs[g][0]['test_num']} {group_refs[g][0]['test_name']}",len(group_hits[g])] for g in gg],[460,72],590,420,27,font=8,wrap_columns=(0,))
            excluded=sum(source['report']['summary']['unevaluated_baseline_count'] for source in bundle['sources'])
            s.text(30,35,f'Unevaluated reference groups: {excluded}. No candidates does not mean every group passed.',9,BLUE,width=1092)
        elif kind=='matrix':_matrix(s,baselines,*entry,page,len(plans))
        elif kind=='detail':
            g,refs,wafer=entry;view=sample_provider(*g)
            _test_detail(s,sources[g[0]],g[1],refs,group_hits[g],view,wafer,page,len(plans));del view
        else:
            c=entry;view=sample_provider(c['dataset_id'],c['hits'][0]['group_id']);_profile(s,c,bindex,view,page,len(plans));del view
        canvas.showPage()
    canvas.save()
    return {'filename':filename(baselines,bundle['metadata'],'Outlier','pdf'),'content':buffer.getvalue() if output is None else None,
            'page_count':len(plans),'candidate_count':len(chips),'profile_count':len(profiles)}
