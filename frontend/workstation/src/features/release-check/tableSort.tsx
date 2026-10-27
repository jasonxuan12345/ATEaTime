import { t, localize, useLanguage } from "../../i18n";
import s from './table-sort.module.css';

export type SortDirection = 'asc' | 'desc';
export type SortValue = number | string | null | undefined;
const textOrder = new Intl.Collator('zh-CN', { numeric: true });

export function compareSortKeys(a: SortValue[], b: SortValue[], direction: SortDirection) {
  for (let index = 0; index < Math.max(a.length, b.length); index++) {
    const av = a[index], bv = b[index];
    const missingA = av == null || av === '' || (typeof av === 'number' && Number.isNaN(av));
    const missingB = bv == null || bv === '' || (typeof bv === 'number' && Number.isNaN(bv));
    if (missingA !== missingB) return missingA ? 1 : -1;
    if (missingA) continue;
    const order = typeof av === 'number' && typeof bv === 'number'
      ? av === bv ? 0 : av < bv ? -1 : 1
      : textOrder.compare(String(av), String(bv));
    if (order) return direction === 'desc' ? -order : order;
  }
  return 0;
}

export function SortHeader({ label, direction, onClick }: {
  label: string; direction?: SortDirection; onClick: () => void;
}) {
  useLanguage();
  return <th scope='col' aria-sort={direction === 'desc' ? 'descending' : direction === 'asc' ? 'ascending' : 'none'}>
    <button className={s.header} aria-label={t("按 {0} 排序", t(label))} onClick={onClick}>
      {localize(label)}<span aria-hidden='true'>{localize(direction === 'desc' ? '↓' : direction === 'asc' ? '↑' : '↕')}</span>
    </button>
  </th>;
}
