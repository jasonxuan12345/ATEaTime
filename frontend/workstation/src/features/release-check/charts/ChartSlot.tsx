import { t, localize, useLanguage } from "../../../i18n";
import type { ReactNode } from 'react';
import s from './charts.module.css';

export function ChartSlot({ role, label, children }: { role: '主图' | '辅图'; label: string; children: ReactNode }) {
  useLanguage();
  return <section className={s.chartSlot} aria-label={t("{0}区域", t(role))}>
    <div className={s.slotHeading}><strong>{localize(role)}</strong><span>{localize(label)}</span></div>{localize(children)}
  </section>;
}
