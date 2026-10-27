import { downloadBlob } from './export';
import { t } from '../../../i18n';
import type { MatrixRow } from './types';

export function matrixCsv(rows: MatrixRow[], rulesHash: string) {
  const records: unknown[][] = [['Test ID', 'Name', 'Group', 'Family', 'Rule', 'Unit', 'Source', 'SHA256', 'Rules version', 'Report status', 'CPK accepted', 'Site', 'Cg', 'n', 'Site status', 'Reason']];
  for (const row of rows) for (const site of row.sites.length ? row.sites : [null]) {
    records.push([row.number, row.name, row.key, row.family, row.rule, row.unit, row.filename, row.source_sha256, rulesHash,
      row.status, row.cpk_override_applied ? 'YES' : 'NO', site?.site, site?.cg, site?.n, site?.status, t(site?.reason ?? '')]);
  }
  return '\ufeff' + records.map(record => record.map(value => {
    let text = value == null ? '' : String(value);
    if (typeof value === 'string' && /^[=+@\-\t\r]/.test(text)) text = "'" + text;
    return '"' + text.replaceAll('"', '""') + '"';
  }).join(',')).join('\r\n');
}
export function downloadMatrix(rows: MatrixRow[], rulesHash: string) {
  downloadBlob(new Blob([matrixCsv(rows, rulesHash)], { type: 'text/csv;charset=utf-8' }), 'release-cg-matrix.csv');
}
