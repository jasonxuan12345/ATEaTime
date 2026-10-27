import { t, localize, useLanguage } from "../../i18n";
import { sourceFiles, type Dataset, type Selections } from "./model";
import { Icon } from "./Icon";
import s from "./release-check.module.css";

export function FilePool({ files, activeId, selections, disabled, onSelect, onRemove, outlierSourceIds = [], onRole, issues = [] }: {
  files: Dataset[]; activeId?: string; selections: Selections; disabled: boolean; outlierSourceIds?: string[];
  onSelect: (file: Dataset) => void; onRemove: (file: Dataset) => void;
  onRole?: (file: Dataset, role: 'cpk' | 'cgm' | 'mmsa' | 'outlier', checked: boolean) => void;
  issues?: { dataset_id: string; error: string }[];
}) {
  useLanguage();
  if (onRole) return <div className={s.fileTable}><table><thead><tr>{localize([t("文件名"), t("阶段"), t("温度"), t("site 数"), t("记录数"), t("测项数"), 'CPK', 'CGM', 'Site Diff', 'Outlier', t("操作")].map(label => <th key={label}>{localize(label)}</th>))}</tr></thead><tbody>{files.map(file => <tr key={file.id} className={issues.some(i => i.dataset_id === file.id) ? s.failedFile : ''}><td><button disabled={disabled} onClick={() => onSelect(file)} aria-pressed={activeId === file.id}>{file.filename}</button>{localize(issues.filter(i => i.dataset_id === file.id).map(i => <small key={i.error}>{localize(i.error)}</small>))}</td><td>{file.conditions.test_code || t("未记录")}</td><td>{file.conditions.test_temperature || t("未记录")}</td><td>{localize(file.sites.length)}</td><td>{localize(file.record_count)}</td><td>{localize(file.tests.length)}</td>{(['cpk', 'cgm', 'mmsa', 'outlier'] as const).map(role => <td key={role}><input type="checkbox" name={'source-' + role} aria-label={t("{0} 用于 {1}", file.filename, role)} disabled={disabled} checked={role === 'outlier' ? outlierSourceIds.includes(file.id) : sourceFiles(files, selections[role], activeId).some(source => source.id === file.id)} onChange={e => onRole(file, role, e.target.checked)} /></td>)}<td><button disabled={disabled} onClick={() => onRemove(file)} aria-label={t("移除文件 {0}", file.filename)}>{t("移除")}</button></td></tr>)}</tbody></table></div>;
  if (!files.length || files[0].id === "demo") return null;
  return <section className={s.filePool} aria-label={t("已导入文件")}>
    <div className={s.poolHeading}><strong>{t("已导入文件 ")}<span>{localize(files.length)} / 8</span></strong><span>{t("各检查独立选样；Outlier 可选一份或多份 STDF")}</span></div>
    <div className={s.poolFiles}>{files.map(file => <div key={file.id} className={`${s.poolFile} ${activeId === file.id ? s.poolActive : ""}`}>
      <button className={s.poolSelect} disabled={disabled} onClick={() => onSelect(file)} aria-pressed={activeId === file.id}>
        <Icon name="file" /><span><strong>{file.filename}</strong><small>{localize(file.record_count.toLocaleString())} {t(" 条记录 · ")}{localize(file.sites.length)} site · {localize(file.tests.length.toLocaleString())} {t(" 测项")}</small></span>
      </button><div className={s.poolRoles}>{localize(sourceFiles(files, selections.cpk, activeId).some(source => source.id === file.id) && <span>CPK</span>)}{localize(sourceFiles(files, selections.cgm, activeId).some(source => source.id === file.id) && <span>CGM</span>)}
        {localize(sourceFiles(files, selections.mmsa, activeId).some(source => source.id === file.id) && <span>Site Diff</span>)}{localize(outlierSourceIds.includes(file.id) && <span>Outlier</span>)}</div>
      <button className={s.poolRemove} disabled={disabled} onClick={() => onRemove(file)} aria-label={t("移除文件 {0}", file.filename)}>×</button>
    </div>)}</div>
  </section>;
}
