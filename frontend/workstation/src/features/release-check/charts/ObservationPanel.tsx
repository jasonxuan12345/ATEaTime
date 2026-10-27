import { t, localize, useLanguage } from "../../../i18n";
import { Diagnosis } from '../Diagnosis';
import { useEffect, useState } from 'react';
import { format, request, type Module, type Row } from '../model';
import { features, type MatrixGroup, type Notes } from './types';
import s from './charts.module.css';

type Draft = { tags: string[]; suggestion: string; remark: string; final_remark: string | null; state: string };
const empty = (): Draft => ({ tags: [], suggestion: '', remark: '', final_remark: null, state: '待复核' });
export function ObservationPanel({ base, row, module, group, rows, notes, onNotes, onDirty, onNext, demo, compact = false, disabled = false }: {
  base: string; row: Row; module: Module; group: MatrixGroup | null; rows: Row[]; notes: Notes;
  onNotes: (notes: Notes) => void; onDirty: (dirty: boolean) => void; onNext: () => void; demo: boolean; compact?: boolean; disabled?: boolean }) {
  useLanguage();
  const [serverPreview, setServerPreview] = useState<string | null>(null), [previewBusy, setPreviewBusy] = useState(false);
  const [draft, setDraft] = useState<Draft>(empty), [baseline, setBaseline] = useState('');
  const [overwrite, setOverwrite] = useState(false);
  const [expanded, setExpanded] = useState(!compact);
  const [batch, setBatch] = useState(false), [keys, setKeys] = useState<string[]>([row.key]);
  const [busy, setBusy] = useState(false), [message, setMessage] = useState(''), [undo, setUndo] = useState<string | null>(null);
  const noteId = module + ':' + row.key;
  const saved = notes.notes[noteId];
  useEffect(() => {
    const value = saved ? { tags: saved.tags, suggestion: saved.suggestion, remark: saved.custom_remark, final_remark: saved.final_remark ?? null, state: saved.state } : empty();
    setDraft(value); setBaseline(JSON.stringify(value)); setKeys([row.key]); setBatch(false); setMessage('');
  }, [noteId, saved, row.key]);
  const dirty = baseline !== '' && JSON.stringify(draft) !== baseline;
  useEffect(() => { onDirty(dirty); return () => onDirty(false); }, [dirty, onDirty]);
  const cell = row[module];
  const prefix = `${module.toUpperCase()}=${format(cell?.value, 4)}`;
  const diagnosis = notes.remarks?.[noteId] ?? cell?.diagnosis;
  const savedPreview = saved?.remark ?? diagnosis?.remark ?? prefix;
  const compactRemark = module === 'cpk' || module === 'cgm';
  const demoParts = compactRemark ? (diagnosis?.auto_remark ?? '').split('\n').map(line => line.replace(/^\d+\.\s*/, '')) : [prefix];
  const demoItems = [...demoParts, ...draft.tags.map(tag => features[tag][1]), draft.suggestion.trim(), draft.remark.trim()].filter(Boolean);
  const demoPreview = compactRemark ? demoItems.map((part, index) => `${index + 1}. ${part}`).join('\n') : demoItems.join('. ');
  const preview = demo ? draft.final_remark?.trim() || demoPreview : dirty ? serverPreview ?? savedPreview : savedPreview;
  useEffect(() => {
    if (demo || !dirty) { setPreviewBusy(false); setServerPreview(null); return; }
    const abort = new AbortController(); setPreviewBusy(true); setServerPreview(null);
    const timer = setTimeout(() => {
      request<{ remark: string }>(`${base}/observations/preview`, { method: 'POST', signal: abort.signal,
        headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...draft, keys: [row.key],
          module, version: notes.version, confirmed: false, overwrite: false }) })
        .then(value => { if (!abort.signal.aborted) { setServerPreview(value.remark); setMessage(''); } })
        .catch(error => { if (!abort.signal.aborted) setMessage('Remark 预览失败：' + error.message); })
        .finally(() => { if (!abort.signal.aborted) setPreviewBusy(false); });
    }, 150);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [demo, dirty, base, row.key, module, draft, notes.version]);
  const candidates = [...new Set([row.key, ...(group?.keys ?? [])])].slice(0, 500);
  async function save(next: boolean) {
    if (disabled || !cell || busy || previewBusy || (!demo && dirty && serverPreview === null)) return;
    setBusy(true); setMessage('');
    try {
      if (demo) {
        const updated = { ...notes.notes };
        for (const key of batch ? keys : [row.key]) {
          const target = rows.find(r => r.key === key); if (!target?.[module]) continue;
          if (updated[module + ':' + key] && !overwrite && key !== row.key) continue;
          updated[module + ':' + key] = { key, module, tags: draft.tags, suggestion: draft.suggestion, custom_remark: draft.remark, final_remark: draft.final_remark,
            remark: preview, state: draft.state, revision: notes.version + 1, saved_at: '示例会话', source: 'DEMO', selection: null, rules_hash: 'DEMO' };
        }
        onNotes({ notes: updated, version: notes.version + 1 });
        setMessage('示例观察已记录，仅本次示例会话有效。');
      } else {
        const response = await request<Notes & { undo_id: string | null; saved: string[]; skipped: string[] }>(`${base}/observations`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ ...draft, keys: batch ? keys : [row.key],
            module, version: notes.version, confirmed: true, overwrite: batch ? overwrite : true }) });
        onNotes(response); setUndo(response.undo_id);
        setMessage(`已保存 ${response.saved.length} 项；保留未覆盖的已有记录 ${response.skipped.length} 项。`);
      }
      setBaseline(JSON.stringify(draft)); onDirty(false);
      if (next) onNext();
    } catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  async function undoLast() {
    if (!undo || busy) return;
    setBusy(true);
    try { onNotes(await request<Notes>(`${base}/observations/undo/${undo}`, { method: 'POST' })); setUndo(null); setMessage('已撤销最近一次记录。'); }
    catch (error) { setMessage((error as Error).message); }
    finally { setBusy(false); }
  }
  return <section className={s.notes} aria-label={t("图形观察与结论记录")}>
    <div className={s.noteHeading}><strong>Remark {localize(dirty && t("· 尚未保存"))}</strong><button onClick={() => setExpanded(!expanded)}>{localize(expanded ? t("收起") : t("展开"))}</button></div>
    {localize(expanded && <><p className={s.caption}>{t("系统依据：")}{localize(cell?.rule_id ?? t("无命中规则"))} · {localize(cell?.reason ?? t("此模块未参与"))}{t("。记录观察不会改变原始判断，也不代表放行。")}</p>
    <Diagnosis value={diagnosis} title={t("当前导出 Remark")} /></>)}
    <div className={s.tags}>{localize(Object.entries(features).map(([tag, [label]]) => <label key={tag}><input type='checkbox' checked={draft.tags.includes(tag)} disabled={busy}
      onChange={event => setDraft({ ...draft, tags: event.target.checked ? [...draft.tags, tag] : draft.tags.filter(t => t !== tag) })} />{localize(label)}</label>))}</div>
    <label>{t("补充 Remark（可编辑）")}</label><textarea aria-label={t("补充 Remark")} rows={2} maxLength={1000} value={draft.remark} disabled={busy} onChange={event => setDraft({ ...draft, remark: event.target.value })} />
    {expanded && <>    <label>{t("建议检查（不代表已确认原因）")}</label><textarea aria-label={t("建议检查")} rows={2} maxLength={1000} value={draft.suggestion} disabled={busy} onChange={event => setDraft({ ...draft, suggestion: event.target.value })} />
    <label><input type='checkbox' checked={draft.final_remark !== null} disabled={busy || batch} onChange={event =>
      setDraft({ ...draft, final_remark: event.target.checked ? preview : null })} />{t("编辑最终 Remark（自动说明仍保留）")}</label>
    {draft.final_remark !== null && <label>{t("最终 Remark")}<textarea aria-label={t("最终 Remark")} rows={4} maxLength={8000}
      value={draft.final_remark} disabled={busy} onChange={event => setDraft({ ...draft, final_remark: event.target.value })} /></label>}
    <div className={s.remark} aria-label={t("英文 Remark 预览")}>{(previewBusy ? t("正在生成统一 Remark…") : preview)}</div>
    <div className={s.tools}><label>{t("备注复核状态 ")}<select aria-label={t("备注复核状态")} value={draft.state} onChange={event => setDraft({ ...draft, state: event.target.value })}>
      {localize(['待复核', '已确认观察', '待跟进'].map(state => <option key={state} value={state}>{localize(state)}</option>))}</select></label>
      <button disabled={!group || group.count < 2 || busy} onClick={() => { setBatch(!batch); if (!batch) setDraft({ ...draft, final_remark: null }); }}>{t("将当前观察应用至本组…")}</button></div>
    {batch && <div className={s.notice}><strong>{t("批量范围预览：")}{localize(keys.length)}/{localize(group?.count ?? 0)} {t(" 项")}</strong>
      <p>{t("请逐项核对观察是否适用；热力图共性不能证明全部趋势相同。最多每次 500 项。")}</p>
      <button onClick={() => setKeys(candidates)}>{t("选择本页全部测项")}</button><button onClick={() => setKeys([])}>{t("清空所选测项")}</button>
      <div className={s.batch}>{candidates.map(key => <label key={key}><input type='checkbox' checked={keys.includes(key)} onChange={event => setKeys(event.target.checked ? [...keys, key] : keys.filter(k => k !== key))} />
        {rows.find(r => r.key === key)?.name ?? key}{localize(notes.notes[module + ':' + key] && t("（已有记录）"))}</label>)}</div>
      <label><input type='checkbox' checked={overwrite} onChange={event => setOverwrite(event.target.checked)} />{t("明确覆盖所选项已有记录（默认保留）")}</label></div>}
    </>}
    <div className={s.tools}><button className={s.primary} disabled={disabled || !cell || busy || previewBusy || (!demo && dirty && serverPreview === null) || (batch && !keys.length)} onClick={() => void save(!batch)}>{localize(busy ? t("正在保存…") : batch ? t("确认批量应用") : t("确认并保存，下一项"))}</button>
      {localize(!batch && <button disabled={disabled || !cell || busy || previewBusy || (!demo && dirty && serverPreview === null)} onClick={() => void save(false)}>{t("保存当前观察")}</button>)}
      {localize(expanded && <button disabled={!undo || busy} onClick={() => void undoLast()}>{t("撤销最近记录")}</button>)}</div>
    {localize(message && <p role='status' className={s.notice}>{localize(message)}</p>)}
    <p className={s.caption}>{t("记录仅保存在当前运行会话，刷新、清空或过期后不恢复；请及时导出。重新计算后需要重新核对，不自动复制旧观察。")}</p>
  </section>;
}
