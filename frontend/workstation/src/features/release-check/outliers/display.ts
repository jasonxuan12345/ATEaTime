import { t } from "../../../i18n";
import type { Candidate, Hit } from './model';

export const SELECTED = '#EF4444';
export const scoreText = (score: number) => Number.isFinite(score) ? (Math.abs(score) >= 1000 ? score.toExponential(2) : score.toFixed(1)) + 'x' : '—';
export const chipLabel = (chip: Candidate) => `${chip.wafer ?? 'W?'} (X:${chip.x ?? '—'}, Y:${chip.y ?? '—'}) S${chip.site ?? '?'}`;
export const chipTooltip = (chip: Candidate) => t("{0}\n{1}\n记录 #{2} · Head {3} · Lot {4}", chipLabel(chip), chip.filename, chip.part_index, chip.head ?? "—", chip.lot ?? "—");
export const signedScore = (hit: Hit) => hit.direction === 'LOW' ? -hit.score : hit.score;
