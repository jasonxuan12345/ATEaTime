import { t } from "../../../i18n";
import type { CgValue, Diagnosis, Module, Row } from '../model';

export type Sample = { id: string; part_index: number; record_sequence: number; column: string;
  result_index: number | null; record_type: string; head: number | null; site: number | null;
  value: number; lot: string | null; wafer: string | null; x: number | null; y: number | null; part_id: string | null };
export type Selection = { ids?: string[]; sites?: number[]; value_min?: number; value_max?: number;
  value_max_exclusive?: boolean; sequence_min?: number; sequence_max?: number;
  x_min?: number; x_max?: number; y_min?: number; y_max?: number; wafer_key?: [string | null, string] };
export type ChartSite = { site: number | null; n: number; mean: number | null; std: number | null;
  cg: CgValue | null; status: string | null; reason: string | null; cg_status: string; histogram: number[]; heads: number[];
  box: { q1: number | null; median: number | null; q3: number | null; low: number | null; high: number | null;
    outlier_count: number; outliers: Sample[] } };
export type ChartContext = { dataset_id: string; run_id: string; filename: string; source_sha256: string;
  module: Module; group_id: string; column: string; record_type: string; result_index: number | null;
  conditions: Record<string, string>; selection: unknown; rules_hash: string; sample_type: string; selection_policy: string };
export type WaferPoint = Sample & { x: number; y: number; raw_x: number; raw_y: number; count: number };
export type WaferGroup = { key: string; lot: string; wafer: string; total_dies: number; points: WaferPoint[];
  bounds?: { minX: number; maxX: number; minY: number; maxY: number };
  sampled: boolean; duplicate_positions: number };
export type Display = { origin: 'corner' | 'center'; corner?: 'top_left' | 'top_right' | 'bottom_left' | 'bottom_right' | null; flip_x: boolean; flip_y: boolean; applied?: boolean };
export type ChartData = { context: ChartContext; row: Row; sample_page?: SamplePage;
  statistics: { n: number; mean: number | null; std: number | null; cp: number | null; cpk: number | null;
    lsl: number | null; usl: number | null; spec_valid: boolean; unit: string | null };
  sites: ChartSite[]; histogram: { edges: number[] }; points: Sample[]; total_points: number; displayed_points: number;
  sampled: boolean; std_threshold: number | null; thresholds: { cg_pass: number; cg_fail: number; var_red: number };
  status: string; reason: string | null; cpk_override_applied: boolean; excluded_counts: Record<string, number>;
  empty_reason: string | null; quality: { mixed_head_sites: number[]; warnings: string[] };
  wafer: { wafers: WaferGroup[]; excluded_count: number; display: Display | null; display_note: string;
    value_range?: [number, number] | null; policy: string; empty_reason: string | null } };
export type MatrixRow = { key: string; name: string; number: number | null; group: string; family: string;
  rule: string; sites: ChartSite[]; unit: string | null; status: string; filename: string | null; source_sha256: string | null; cpk_override_applied?: boolean };
export type MatrixGroup = { key: string; family: string; rule: string; basis: string; bad_sites: number[]; keys: string[]; count: number };
export type MatrixData = { rows: MatrixRow[]; groups: MatrixGroup[]; total: number; offset: number; limit: number;
  thresholds: ChartData['thresholds'] };
export type SamplePage = { points: Sample[]; total: number; offset: number; limit: number; context: ChartContext };
export type Observation = { key: string; module: Module; tags: string[]; suggestion: string; custom_remark: string;
  remark: string; final_remark?: string | null; state: string; revision: number; saved_at: string; source: unknown; selection: unknown; rules_hash: string };
export type Notes = { notes: Record<string, Observation>; version: number; remarks?: Record<string, Diagnosis> };
export type ChartKind = 'histogram' | 'cg' | 'box' | 'sequence' | 'wafer';
export type Pick = { selection?: Selection; site?: number | null; row?: string };
export type ChartSettings = { sites: number[] | null; sort: 'site' | 'mean' | 'std' | 'cg'; fullSpec: boolean;
  stdMode: boolean; connect: boolean; wafer: string; outOfSpec: boolean; selected: Selection };
export const siteName = (site: number | null) => site === null ? t('未知 site') : `S${site}`;
export const siteColor = (site: number | null) => ['#087f88', '#6384c7', '#bd864b', '#a269ab', '#71964b', '#af6169', '#658d9e', '#a8a04c'][Math.abs(site ?? 0) % 8];
export const features: Record<string, [string, string]> = {
  end_spikes: ['首尾跳点', 'first/last sequence spikes observed'], drift: ['持续漂移', 'sequence drift observed'],
  steps: ['离散台阶', 'discrete levels observed'], random: ['随机波动', 'random variation observed'],
  outliers: ['离群点', 'outlying samples observed'], wide: ['分布较宽', 'wide distribution observed'],
  close_limit: ['均值贴限', 'mean close to a specification limit'], site_shift: ['site 均值差异', 'site mean differences observed'],
  site_spread: ['site 波动差异', 'site spread differences observed'], spatial: ['空间聚集', 'spatial concentration observed'] };
