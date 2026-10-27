import { useSyncExternalStore } from 'react';
import catalog from '../../../../config/i18n/zh-en.json';

export type Language = 'zh' | 'en';
const storageKey = 'datalog-check-language';
const listeners = new Set<() => void>();
let language: Language = (() => {
  try { return localStorage.getItem(storageKey) === 'en' ? 'en' : 'zh'; }
  catch { return 'zh'; }
})();
export const getLanguage = () => language;
export function setLanguage(next: Language) {
  if (next === language) return;
  language = next;
  try { localStorage.setItem(storageKey, next); } catch { /* Storage can be disabled. */ }
  if (typeof document !== 'undefined') document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN';
  listeners.forEach(listener => listener());
}
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
export const useLanguage = () => useSyncExternalStore(subscribe, getLanguage, () => 'zh' as Language);
if (typeof window !== 'undefined') window.addEventListener('storage', event => {
  if (event.key === storageKey) setLanguage(event.newValue === 'en' ? 'en' : 'zh');
});
if (typeof document !== 'undefined') document.documentElement.lang = language === 'en' ? 'en' : 'zh-CN';

const dictionary: Record<string, string> = catalog;
const escape = (value: string) => value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const patterns = Object.entries(dictionary).filter(([source]) => /\{\d+\}/.test(source))
  .sort((a, b) => b[0].replace(/\{\d+\}/g, '').length - a[0].replace(/\{\d+\}/g, '').length)
  .map(([source, target]) => {
    const slots: number[] = [];
    const fragments = source.split(/(\{\d+\})/).map(part => {
      if (/^\{\d+\}$/.test(part)) { slots.push(Number(part.slice(1, -1))); return '([\\s\\S]*?)'; }
      return escape(part);
    });
    return { expression: new RegExp('^' + fragments.join('') + '$'), source, target, slots };
  });
const interpolate = (text: string, values: unknown[]) => text.replace(/\{(\d+)\}/g, (slot, index) =>
  Number(index) < values.length ? String(values[Number(index)] ?? '') : slot);

/** Translate application messages only. Never pass filenames, test names or user notes. */
export function t(source: string, ...values: unknown[]): string {
  let target = source;
  if (language === 'en') {
    target = dictionary[source] ?? source;
    if (target === source && !values.length && /[\u3400-\u9fff]/.test(source)) {
      for (const pattern of patterns) {
        const match = pattern.expression.exec(source);
        if (!match) continue;
        const captures: unknown[] = [];
        pattern.slots.forEach((slot, index) => { captures[slot] = match[index + 1]; });
        // These slots contain application errors, while the other slots contain
        // source filenames. Never translate the filename along with the error.
        const nested = pattern.source === '{0}：{1}' ? 1 :
          ['Remark 预览失败：{0}', '部分完成：{0}'].includes(pattern.source) ? 0 : -1;
        if (nested >= 0) captures[nested] = t(String(captures[nested]));
        return interpolate(pattern.target, captures);
      }
    }
  }
  return interpolate(target, values);
}

/** Localize a render value; React elements and source objects retain their identity. */
export function localize<T>(value: T): T {
  if (typeof value === 'string') return t(value) as T;
  if (Array.isArray(value)) return value.map(item => localize(item)) as T;
  return value;
}

const displayFields = new Set(['reason', 'warnings', 'status_label', 'phase', 'sample_type', 'selection_policy',
  'display_note', 'policy', 'empty_reason', 'basis', 'review_state', 'state', 'label']);
const sourceFields = new Set(['conditions', 'selection', 'selections', 'metadata', 'test_catalog']);
export function localizeReport<T>(value: T, field = ''): T {
  if (typeof value === 'string') return (displayFields.has(field) ? t(value) : value) as T;
  if (Array.isArray(value)) return value.map(item => localizeReport(item, field)) as T;
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, item]) =>
    [key, sourceFields.has(key) ? item : localizeReport(item, key)])) as T;
  return value;
}

export const reportLanguageQuery = () => getLanguage();
