"""Site Diff vector report. Full statistics with at most 5000 shape points."""
from datetime import datetime
from io import BytesIO
from collections import Counter
import json
import numpy as np
from reportlab.lib.colors import HexColor
from scipy.stats import gaussian_kde
from deepyield.compute.release_pdf_canvas import ReportCanvas
from deepyield.compute.release_review_data import SITE_SCOPE, STD_FORMULA, filename, site_diff_rows, fmt
from deepyield.compute.release_review_pdf import Slides, WIDTH, HEIGHT, COLORS, BLUE, Panel, PALETTE, chunks, sample, note, context, kpis
from deepyield.compute.release_control import report_progress, checkpoint


def _statistics(s,row,rect):
    x,y,w,h=rect;s.text(x+8,y+h-15,'4  Site Statistics (full population)',11,BLUE,True)
    sites=row.get('sites') or []
    if len(sites)>16:raise ValueError('Site Diff PDF supports at most 16 sites.')
    groups=chunks(sites,8)
    for col,entries in enumerate(groups):
        width=w/len(groups)
        values=[[f"S{v['site']}",v['n'],fmt(v['mean']),fmt(v['std']),'--','Not classified' if v['n']>=2 else 'Insufficient'] for v in entries]
        s.table(['Site','n','Mean','Std','Cg','Status'],values,[width*.1,width*.1,width*.2,width*.2,width*.1,width*.3],x+col*width,y+h-25,16,font=7)
    s.text(x+8,y+5,'No independent per-site pass/fail is assigned by Site Diff.',7,width=w-16)


def _detail(s,row,points,page,total):
    sites=row.get('sites') or []
    if len(sites)>16:raise ValueError('Site Diff PDF supports at most 16 sites.')
    if len(points)!=row.get('n'):raise ValueError('Site Diff sample count does not match the saved report.')
    actual=np.asarray([p['value'] for p in points],dtype=float)
    if (not np.isfinite(actual).all() or not len(actual)
        or not np.isclose(np.mean(actual),row['overall_mean'],rtol=1e-8,atol=1e-12)
        or (len(actual)>1 and not np.isclose(np.std(actual,ddof=1),row['overall_std'],rtol=1e-8,atol=1e-12))):
        raise ValueError('Site Diff samples do not match the saved statistics; calculate again.')
    s.header(f"Site Diff | Test #{row.get('test_num')} - {row.get('test_name')}",'Part 2 - Abnormal Test Details',page,total,row['result'])
    context(s,f"Unit: {row.get('unit') or '--'} | LSL / USL: {fmt(row.get('effective_lsl'))} / {fmt(row.get('effective_usl'))} | n / Mean / Std / Cp: {row.get('n')} / {fmt(row.get('overall_mean'))} / {fmt(row.get('overall_std'))} / {fmt(row.get('cp'))}",557)
    context(s,f"Trigger: {row['trigger']} | Worst: {row['worst_site']} | Mean Diff % Spec: {fmt(row.get('shift_spec_pct'))} | Std Ratio: {fmt(row.get('var_ratio'))}",542)
    note(s,row['remark'],30,472,1092,63)
    shown=sample(points,5000)
    low,high=row.get('effective_lsl'),row.get('effective_usl')
    ys=[p['value'] for p in points]+[low,high]
    p=Panel(s,(30,260,530,205),f'1  Test Sequence by Site (display {len(shown)} / {len(points)})',range(max(1,len(sites))),ys,'Site / within-site execution order',row.get('unit') or 'Value',xticks=False)
    for i,site in enumerate(sites):
        group=sorted((v for v in shown if v['site']==site['site']),key=lambda v:(v['record_sequence'],v['part_index']))
        xx=np.linspace(i-.32,i+.32,len(group))
        p.dots(zip(xx,[v['value'] for v in group]),PALETTE[i%8])
        s.text(p.xx(i)-9,p.y0-13,f"S{site['site']}",7,width=24)
        if site['mean'] is not None:p.line([i-.36,i+.36],[site['mean']]*2,'#111111',2)
    p.specs(low,high)
    p=Panel(s,(590,260,532,205),'2  Site Violin + Full-population Mean',range(max(1,len(sites))),ys,'Site',row.get('unit') or 'Value',xticks=False)
    for i,site in enumerate(sites):
        values=np.asarray([v['value'] for v in shown if v['site']==site['site']])
        if len(values)>=2 and np.ptp(values)>0:
            grid=np.linspace(values.min(),values.max(),80)
            try:
                density=gaussian_kde(values)(grid);width=density/max(density)*.34
                p.polygon(list(zip(i-width,grid))+list(zip((i+width)[::-1],grid[::-1])),PALETTE[i%8])
            except np.linalg.LinAlgError:pass
        if site['mean'] is not None:p.line([i-.36,i+.36],[site['mean']]*2,'#111111',2)
        s.text(p.xx(i)-9,p.y0-13,f"S{site['site']}",7,width=24)
    p.specs(low,high)
    values=np.asarray([v['value'] for v in points]);edges=np.histogram_bin_edges(values,bins=24) if len(values) else np.array([0,1])
    counts=[np.histogram([v['value'] for v in points if v['site']==site['site']],edges)[0] for site in sites]
    maximum=float(np.max(np.sum(counts,axis=0))) if counts else 1
    p=Panel(s,(30,35,530,205),'3  Site Histogram (all valid records)',list(edges)+[low,high],[0,maximum],'Value','Count')
    ranked=[v['site'] for v in sorted(sites,key=lambda v:(-abs((v['mean'] or 0)-(row.get('overall_mean') or 0)),-(v['std'] or 0)))]
    if row.get('var_severity')=='RED':ranked=row['worst_sites']+ranked
    highlighted=set(list(dict.fromkeys(ranked))[:3])
    if len(sites)>4:s.text(175,243-29,'Highlighted: '+', '.join(f'S{v}' for v in sorted(highlighted)),7,width=350)
    bottom=np.zeros(len(edges)-1)
    for i,(site,count) in enumerate(zip(sites,counts)):
        color=PALETTE[i%8] if len(sites)<=4 or site['site'] in highlighted else '#DCE3E8'
        for j,n in enumerate(count):
            p.polygon([(edges[j],bottom[j]),(edges[j],bottom[j]+n),(edges[j+1],bottom[j]+n),(edges[j+1],bottom[j])],color)
        bottom+=count
    for v,label in ((low,'LSL'),(high,'USL')):
        if v is not None:p.line([v,v],[0,maximum],'#CC3333',.8,[3,2]);p.label(v,maximum,label)
    _statistics(s,row,(590,35,532,205))
    s.text(30,15,'Full n / Mean / Std / Cp / histogram; sequence and violin <=5000 points. Black = Mean; red dashed = LSL / USL.',7,width=1092)


def render_site_diff_pdf(report,*,metadata=None,result=None,sample_provider,population,output=None):
    data=site_diff_rows(report,metadata or {},result)
    buffer=output if output is not None else BytesIO()
    canvas=ReportCanvas(buffer,pagesize=(WIDTH,HEIGHT),pageCompression=1)
    canvas.setTitle('Site Diff Report');canvas.setAuthor('Datalog Check')
    s=Slides(canvas,datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S'))
    pages=chunks(data['actions'],12);total=len(pages)+len(data['actions'])
    for page,rows in enumerate(pages,1):
        report_progress('Generating Site Diff summary',page/max(1,total)*90)
        s.header('Site Diff Analysis Report','Summary',page,total)
        context(s,'Data Scope: '+SITE_SCOPE,554)
        selected=(report.get('selection') or {}).get('selected_record_count',len((report.get('selection') or {}).get('selected_part_indices',[])))
        context(s,f"Source: {(metadata or {}).get('source_file','--')} | Total Executed Parts: {selected} | Sites: {', '.join('S'+str(v) for v in data['sites'])}",539)
        context(s,'Lot / Wafer / Temperature: '+population,524)
        kpis(s,[(f"Total Evaluated Tests (excluded {data['excluded']})",len(data['rows'])-data['excluded']),('Pass',data['counts']['Pass']),('Need to check',data['counts']['Need to check']),('Fail',data['counts']['Fail'])])
        values=[[r.get('test_num'),r.get('test_name'),r.get('unit'),f"{fmt(r.get('effective_lsl'))} / {fmt(r.get('effective_usl'))}",f"{r.get('n')} / {fmt(r.get('overall_mean'))} / {fmt(r.get('overall_std'))} / {fmt(r.get('cp'))}",r['worst_site'],fmt(r.get('shift_spec_pct')),fmt(r.get('var_ratio')),r['trigger'],r['result'],r['remark']] for r in rows]
        widths=[45,170,35,80,132,65,70,60,95,80,260]
        s.table(['Test Nr.','Test Name','Unit','LSL / USL','n / Mean / Std / Cp','Worst Site','Mean Diff\n% Spec','Std Ratio','Trigger','Result','Remark'],values,widths,30,421,27,font=7,wrap_columns=(1,10),fills={(i,9):COLORS[r['result']] for i,r in enumerate(rows)})
        for i,r in enumerate(rows):canvas.textAnnotation(r['remark'],Rect=(1104,421-30-(i+1)*27,1118,421-30-i*27),Name='Comment')
        if not rows:s.text(45,365,'No Fail or Need to check tests.',15,BLUE,True)
        s.text(30,16,STD_FORMULA+' | Unavailable / unexecuted tests are excluded, not counted as Pass.',7,width=1092)
        canvas.showPage()
    for offset,row in enumerate(data['actions'],len(pages)+1):
        report_progress('Generating Site Diff details',offset/total*95)
        checkpoint();_detail(s,row,sample_provider(row),offset,total);canvas.showPage()
    canvas.save()
    return {'filename':filename(data['rows'],metadata or {},'SiteDiff','pdf'),'content':buffer.getvalue() if output is None else None,'page_count':total,'action_count':len(data['actions'])}
