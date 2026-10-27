import { t, localize, useLanguage } from "../../../i18n";
import { useEffect, useState } from 'react';
import { features } from '../charts/types';
import { downloadBlob } from '../charts/export';
import type { Row } from '../model';
import type { OutlierCell } from './model';
import s from '../charts/charts.module.css';

export type OutlierNote = { tags: string[]; remark: string; suggestion: string; final: string | null; state: string; preview: string };
export type OutlierNotes = Record<string, OutlierNote>;
const empty = (): OutlierNote => ({tags:[],remark:'',suggestion:'',final:null,state:'待复核',preview:''});
export function exportOutlierNotes(notes: OutlierNotes, rows: Row[]) {
  const escape=(text:string)=>text.replace(/[<>&"']/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&apos;'}[c]!));
  const cells=[['Test ID','名称','模块','观察标签','补充 Remark','建议检查','最终 Remark','备注复核状态','英文 Remark'].map(label=>t(label)),...Object.entries(notes).map(([key,n])=>{const row=rows.find(r=>r.key===key);return [String(row?.number??''),row?.name??key,'Outlier',n.tags.map(tag=>t(features[tag][0])).join(', '),n.remark,n.suggestion,n.final??'',t(n.state),n.preview];})];
  const xml='<?xml version="1.0" encoding="UTF-8"?><?mso-application progid="Excel.Sheet"?><Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet" xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet"><Worksheet ss:Name="Outlier observations"><Table>'+cells.map(row=>'<Row>'+row.map(cell=>'<Cell><Data ss:Type="String">'+escape(cell)+'</Data></Cell>').join('')+'</Row>').join('')+'</Table></Worksheet></Workbook>';
  downloadBlob(new Blob([xml],{type:'application/vnd.ms-excel;charset=utf-8'}),'outlier-observations.xls');
}
export function OutlierRemark({row, rows, notes, onNotes, onDirty, onNext, stale}:{row:Row & {outlier?:OutlierCell};rows:(Row & {outlier?:OutlierCell})[];notes:OutlierNotes;onNotes:(notes:OutlierNotes)=>void;onDirty:(dirty:boolean)=>void;onNext:()=>void;stale:boolean}) {
  useLanguage();
  const [draft,setDraft]=useState<OutlierNote>(empty),[baseline,setBaseline]=useState(''),[expanded,setExpanded]=useState(false),[batch,setBatch]=useState(false),[overwrite,setOverwrite]=useState(false),[undo,setUndo]=useState<OutlierNotes|null>(null),[message,setMessage]=useState('');
  useEffect(()=>{const next=notes[row.key]??empty();setDraft(next);setBaseline(JSON.stringify(next));setBatch(false);},[row.key,notes]);
  const dirty=baseline!==''&&JSON.stringify(draft)!==baseline;
  useEffect(()=>{onDirty(dirty);return()=>onDirty(false);},[dirty,onDirty]);
  useEffect(()=>{const before=(event:BeforeUnloadEvent)=>{if(dirty){event.preventDefault();event.returnValue='';}};window.addEventListener('beforeunload',before);return()=>window.removeEventListener('beforeunload',before);},[dirty]);
  const preview=draft.final?.trim() || ['Outlier: statistical candidates, not confirmed defects',draft.tags.map(t=>features[t][1]).join('; '),draft.suggestion.trim(),draft.remark.trim()].filter(Boolean).join('. ');
  const groups=new Set(row.outlier?.baselines.map(b=>b.group_id));
  const family=rows.filter(r=>r.key===row.key||r.outlier?.baselines.some(b=>groups.has(b.group_id)));
  function save(next:boolean){if(stale)return;const updated={...notes};for(const r of batch?family:[row])if(r.key===row.key||overwrite||!updated[r.key])updated[r.key]={...draft,preview};setUndo(notes);onNotes(updated);setBaseline(JSON.stringify(draft));onDirty(false);setMessage('观察已保存在当前会话，请及时导出。');if(next&&!batch)onNext();}
  return <section className={s.notes} aria-label={t("Outlier 观察记录")}><div className={s.noteHeading}><strong>Remark {localize(dirty&&t("· 尚未保存"))}</strong><button onClick={()=>setExpanded(!expanded)}>{localize(expanded?t("收起"):t("展开"))}</button></div>
    <div className={s.tags}>{localize(Object.entries(features).map(([key,[label]])=><label key={key}><input type='checkbox' checked={draft.tags.includes(key)} onChange={e=>setDraft({...draft,tags:e.target.checked?[...draft.tags,key]:draft.tags.filter(t=>t!==key)})}/>{localize(label)}</label>))}</div>
    <textarea aria-label={t("补充 Remark")} rows={1} value={draft.remark} maxLength={1000} placeholder={t("补充 Remark")} onChange={e=>setDraft({...draft,remark:e.target.value})}/>
    {expanded&&<><label>{t("建议检查")}<textarea aria-label={t("建议检查")} value={draft.suggestion} onChange={e=>setDraft({...draft,suggestion:e.target.value})}/></label><label><input type='checkbox' checked={draft.final!==null} onChange={e=>setDraft({...draft,final:e.target.checked?preview:null})}/>{t("编辑最终 Remark")}</label>{localize(draft.final!==null&&<textarea aria-label={t("最终 Remark")} value={draft.final} onChange={e=>setDraft({...draft,final:e.target.value})}/>)}<div className={s.remark} aria-label={t("英文 Remark 预览")}>{preview}</div><label>{t("备注复核状态")}<select aria-label={t("备注复核状态")} value={draft.state} onChange={e=>setDraft({...draft,state:e.target.value})}>{localize(['待复核','已确认观察','待跟进'].map(v=><option key={v} value={v}>{localize(v)}</option>))}</select></label><label><input type='checkbox' checked={batch} onChange={e=>setBatch(e.target.checked)}/>{t("应用到本测项组（")}{localize(family.length)} {t(" 项）")}</label>{batch&&<><p>{family.map(r=>r.name).join(t("、"))}</p><label><input type='checkbox' checked={overwrite} onChange={e=>setOverwrite(e.target.checked)}/>{t("覆盖已有记录")}</label></>}<button disabled={!undo||stale} onClick={()=>{if(undo){onNotes(undo);setUndo(null);}}}>{t("撤销最近记录")}</button></>}
    <div className={s.tools}><button className={s.primary} disabled={stale} onClick={()=>save(true)}>{localize(batch?t("确认批量应用"):t("确认并保存，下一项"))}</button><button disabled={stale} onClick={()=>save(false)}>{t("保存当前观察")}</button></div>{localize(message&&<p className={s.notice} role='status'>{localize(message)}</p>)}<p className={s.caption}>{t("观察记录仅保存在当前会话，刷新后不恢复，请及时导出。")}</p>
  </section>;
}
