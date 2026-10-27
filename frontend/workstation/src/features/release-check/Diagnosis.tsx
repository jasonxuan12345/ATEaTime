import { t, localize, useLanguage } from "../../i18n";
import { request, type Diagnosis as DiagnosisValue } from './model';
import { useEffect, useRef, useState } from 'react';

export function Diagnosis({ value, title = 'Remark' }: { value?: DiagnosisValue; title?: string }) {
  useLanguage();
  const [full, setFull] = useState<DiagnosisValue | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState('');
  const abort = useRef<AbortController | null>(null);
  const disclosure = useRef<HTMLDetailsElement | null>(null);
  useEffect(() => { setFull(null); setError(''); setLoading(false); abort.current?.abort();
    if (disclosure.current?.open) load(true, true);
    return () => abort.current?.abort(); }, [value?.detail_url, value?.remark]);
  if (!value) return null;
  const detail = full ?? value;
  const knowledge = detail.knowledge;
  function load(open: boolean, force = false) {
    if (!open || !value?.detail_url || (!force && (full || loading))) return;
    const controller = new AbortController(); abort.current = controller; setLoading(true); setError('');
    request<DiagnosisValue>(value.detail_url, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setFull(result);
    }).catch(reason => { if (!controller.signal.aborted) setError((reason as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
  }
  return <section aria-label={t("项目诊断说明")} style={{ margin: '12px 0', overflowWrap: 'anywhere' }}>
    <strong>{localize(title)}</strong><p style={{ whiteSpace: 'pre-wrap' }}>{value.remark}</p>
    <small>{localize(value.review_state === 'AUTO_REVIEW_REQUIRED' ? t("自动生成，待工程师复核") : value.review_state)}</small>
    <details ref={disclosure} onToggle={event => load(event.currentTarget.open)}><summary>{t("本次依据与项目经验来源")}</summary>
      {loading && <p role='status'>{t("正在读取数据…")}</p>}{error && <p role='alert'>{localize(error)}</p>}
      <p>{localize(detail.current_remark)}</p>
      {localize(knowledge.historical_remark && <p>{t("历史参考（")}{localize(knowledge.status === 'reference' ? t("适用范围尚未确认") : knowledge.status)}{t("）：")}{knowledge.historical_remark}</p>)}
      {knowledge.source && <p>{t("来源：")}{knowledge.source.name} · {localize(knowledge.id)}</p>}
      {localize(knowledge.status === 'ambiguous' && <p>{t("匹配到多条历史参考记录，未采用。")}</p>)}
      <small>{t("知识库 v")}{localize(detail.knowledge_version)} · {detail.knowledge_hash}</small>
      {localize(Boolean(detail.related_evidence) && <p>{t("关联 Site Diff 使用其独立来源与选样，详见该模块数据与判定依据。")}</p>)}
    </details>
  </section>;
}
