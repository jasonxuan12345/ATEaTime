export type SummaryModel = {
  total: number;
  passed: number;
  failed: number;
  yieldRate: number | null;
  topFailItems: Array<Record<string, unknown>>;
  yieldByLot: Array<Record<string, unknown>>;
  kpis: Record<string, unknown>;
  siteRisk: Record<string, unknown>;
  cpkTrend: Record<string, unknown>;
  waferRisk: Record<string, unknown>;
  raw: Record<string, unknown>;
};

/** v2 Overview response context block. */
export type V2OverviewContext = {
  dataset_version_id: string;
  scope_id: string | null;
  scope_applied: boolean;
  source_asset_count: number;
  query_policy_version: string;
  final_device_policy: string;
  result_mode: string;
  estimated_device_count: number | null;
};

/** v2 Overview aggregate response (bounded, no device-level data). */
export type V2OverviewResponse = {
  context: V2OverviewContext;
  kpis: Record<string, unknown>;
  yield_trend: Array<Record<string, unknown>>;
  top_failures: Array<Record<string, unknown>>;
  site_risk: Record<string, unknown>;
  cpk_trend: Record<string, unknown>;
  wafer_risk: Record<string, unknown>;
  query_metrics: Record<string, unknown>;
};

/** Frozen OVERVIEW-AGG-001 data returned inside the target envelope. */
export type OverviewTargetData = {
  context: {
    dataset_version_id: string;
    scope_id: string | null;
    group_ref: { kind: "EWS_WAFER" | "FT_LOT"; ids: string[] } | null;
    scope_hash: string | null;
    member_count?: number;
    requested_member_count?: number;
  };
  kpis: {
    total_device_count: number | null;
    classified_device_count: number | null;
    pass_device_count: number | null;
    fail_device_count: number | null;
    final_yield: number | null;
    final_classified_resolution: "RESOLVED" | "CONFLICT" | "UNAVAILABLE";
    first_pass_yield?: number | null;
    retest_rate?: number | null;
    recovery_rate?: number | null;
    regression_rate?: number | null;
    quality_counts: Record<string, number | null>;
  };
  yield_trend: Array<{
    group_ref: { kind: "EWS_WAFER" | "FT_LOT"; ids: string[] };
    numerator: number;
    denominator: number;
    ratio: number | null;
    status: "RESOLVED" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    reason_codes: string[];
  }>;
  top_failures: Array<{
    rank: number;
    failure_ref: string;
    label: string | null;
    test_item_id?: string | null;
    test_item_key?: string | null;
    test_name?: string | null;
    fail_count: number;
    fail_rate: number | null;
    status: "RESOLVED" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    reason_codes: string[];
  }>;
  /** Independent DVRI-backed Test Item Pareto; never a projection of Bin rows. */
  test_item_pareto?: {
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    values: Array<{
      rank: number;
      test_item_id: string | null;
      test_item_key: string | null;
      test_num: number | null;
      record_type?: string | null;
      test_name: string | null;
      label: string | null;
      fail_count: number;
      fail_rate: number | null;
      denominator_count: number;
      device_fail_count: number;
      device_pass_count: number;
      count_basis: string;
      identity_resolution: "RESOLVED" | "PARTIAL" | "CONFLICT" | "UNAVAILABLE";
      coverage_status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
      covered_asset_count: number;
      eligible_asset_count: number;
      coverage_rate: number | null;
      capability?: TargetCapabilitySummary;
      provenance?: Record<string, unknown>;
      reason_codes: string[];
      cum_rate?: number | null;
    }>;
    reason_codes: string[];
    metric_basis?: string;
    denominator_basis?: string;
    capability?: TargetCapabilitySummary;
    provenance?: Record<string, unknown>;
  };
  site_risk: OverviewTargetModule<{
    site_ref: string;
    total_count: number;
    fail_count: number;
    fail_rate: number | null;
    risk_level: string | null;
    reason_codes: string[];
  }>;
  cpk_trend: OverviewTargetModule<{
    bucket_ref: string;
    bucket_kind: "LOT" | "WAFER" | "TIME" | "SITE";
    cpk: number | null;
    ncpk: number | null;
    sample_count: number;
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    reason_codes: string[];
  }>;
  wafer_risk: OverviewTargetModule<{
    group_ref: { kind: "EWS_WAFER" | "FT_LOT"; ids: string[] };
    total_count: number;
    fail_count: number;
    yield: number | null;
    risk_level: "INFO" | "WARNING" | "CRITICAL" | null;
    reason_codes: string[];
  }>;
  capabilities: Array<{
    name: string;
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    eligible_count: number | null;
    total_count: number | null;
    count_basis: string;
    coverage_rate: number | null;
    reason_codes: string[];
    capability_rule_version: string;
  }>;
};

export type OverviewTargetModule<T> = {
  status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
  values: T[];
  reason_codes: string[];
};

// ---------------------------------------------------------------------------
// Frozen target Test Item data. These are consumed directly from the target
// envelope; the V2* DTOs below are retained only for deferred legacy work.
// ---------------------------------------------------------------------------

export type TargetCapabilitySummary = {
  name: string;
  status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
  eligible_count: number | null;
  total_count: number | null;
  count_basis: "ASSET" | "GROUP" | "DEVICE" | "RECORD" | "TEST_ITEM";
  coverage_rate: number | null;
  reason_codes: string[];
  capability_rule_version: string;
};

export type TestItemTargetReference = {
  test_item_id: string;
  test_name: string | null;
  unit: string | null;
};

export type TestItemCatalogTargetItem = TestItemTargetReference & {
  test_item_key?: string | null;
  program_identity?: string | null;
  identity_policy_version?: string | null;
  identity_resolution: "RESOLVED" | "PARTIAL" | "CONFLICT" | "UNAVAILABLE";
  coverage_status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
  covered_asset_count: number;
  eligible_asset_count: number;
  covered_asset_ids: string[];
  record_type: string | null;
  test_num: number | null;
  limits: { lsl: number | null; usl: number | null; nominal: number | null };
  unit_resolution: "RESOLVED" | "PARTIAL" | "CONFLICT" | "UNAVAILABLE";
  limits_resolution: "RESOLVED" | "PARTIAL" | "CONFLICT" | "UNAVAILABLE";
  capability: TargetCapabilitySummary;
  provenance: Record<string, unknown>;
  reason_codes: string[];
};

export type TestItemCatalogTargetData = {
  items: TestItemCatalogTargetItem[];
  catalog_counts: {
    matched_item_count: number;
    eligible_selected_asset_count: number;
    eligible_selected_asset_ids?: string[];
    returned_item_count: number;
  };
};

export type TestItemAggregateTargetData = {
  context: OverviewTargetData["context"];
  item: TestItemTargetReference;
  outcome: {
    eligible_device_count: number | null;
    executed_device_count: number | null;
    classified_device_count: number | null;
    pass_device_count: number | null;
    fail_device_count: number | null;
    conflict_device_count: number | null;
    unavailable_device_count: number | null;
    final_position_test_item_fail_rate: number | null;
    execution_coverage: number | null;
    classification_coverage: number | null;
    status: "RESOLVED" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    reason_codes: string[];
  };
  summary: {
    count: number | null;
    valid_count: number | null;
    null_count: number | null;
    mean: number | null;
    std: number | null;
    min: number | null;
    max: number | null;
    median: number | null;
    p05: number | null;
    p95: number | null;
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
  };
  histogram: {
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    bin_count: number;
    bins: Array<{ bin_start: number; bin_end: number; count: number }>;
    reason_codes: string[];
  };
  trend: {
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    group_by: "chip_index" | "lot_id" | "wafer_id" | "site" | "day" | "week" | "month" | "ews_wafer_id" | "ft_lot_id" | null;
    points: Array<{ group: string; value: number | null; count: number }>;
    reason_codes: string[];
  };
  limits: {
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    lsl: number | null;
    usl: number | null;
    unit: string | null;
  };
  failures: {
    status: "AVAILABLE" | "PARTIAL" | "UNAVAILABLE" | "CONFLICT";
    items: Array<{ rank: number; failure_ref: string; bin_label: string | null; fail_count: number; fail_rate: number | null }>;
    reason_codes: string[];
  };
  capabilities: TargetCapabilitySummary[];
};

export type TestItemPreviewTargetData = {
  context: OverviewTargetData["context"];
  item: TestItemTargetReference;
  rows: Array<{
    member_ordinal?: number | null;
    source_asset_id?: string | null;
    lot_id: string | null;
    wafer_id: string | null;
    die_x: number | null;
    die_y: number | null;
    hbin: number | null;
    sbin: number | null;
    site: string | null;
    test_value: number | null;
    is_pass: boolean | null;
  }>;
  fields: Array<"member_ordinal" | "source_asset_id" | "lot_id" | "wafer_id" | "die_x" | "die_y" | "hbin" | "sbin" | "site" | "test_value" | "is_pass">;
};

// ---------------------------------------------------------------------------
// v2 Test Item Analysis types — Phase 4B
// ---------------------------------------------------------------------------

export type V2TestItemContext = {
  dataset_version_id: string;
  scope_id: string | null;
  scope_applied: boolean;
  source_asset_count: number;
  query_policy_version: string;
  result_mode: "catalog" | "aggregate";
  final_device_policy?: string;
};

export type V2TestItemCatalogItem = {
  test_item_id: string;
  display_name: string;
  unit: string | null;
  has_limits: boolean;
  lsl: number | null;
  usl: number | null;
  source_asset_count: number;
};

export type V2TestItemListResponse = {
  context: V2TestItemContext;
  items: V2TestItemCatalogItem[];
  total_count: number;
  truncated: boolean;
  /** Present for target-catalog consumers; legacy test doubles may omit it. */
  next_cursor?: string | null;
};

export type V2TestItemHistogramBin = {
  bin_start: number;
  bin_end: number;
  count: number;
};

export type V2TestItemTrendPoint = {
  group: string;
  value: number;
  count: number;
};

export type V2TestItemFailureItem = {
  bin_label: string;
  fail_count: number;
  fail_rate: number;
};

export type V2TestItemSummary = {
  available: boolean;
  count?: number;
  valid_count?: number;
  null_count?: number;
  pass_count?: number;
  fail_count?: number;
  mean?: number | null;
  std?: number | null;
  min?: number | null;
  max?: number | null;
  median?: number | null;
  p05?: number | null;
  p95?: number | null;
  cpk?: number | null;
  ncpk?: number | null;
  empty_reason?: string | null;
};

export type V2TestItemAnalysisResponse = {
  context: V2TestItemContext;
  summary: V2TestItemSummary;
  histogram: {
    available: boolean;
    bins: V2TestItemHistogramBin[];
    bin_count: number;
    empty_reason?: string | null;
  };
  trend: {
    available: boolean;
    points: V2TestItemTrendPoint[];
    group_by: string;
    empty_reason?: string | null;
  };
  limits: {
    available: boolean;
    lsl?: number | null;
    usl?: number | null;
    unit?: string | null;
    empty_reason?: string | null;
  };
  failures: {
    available: boolean;
    items: V2TestItemFailureItem[];
    pareto_truncated?: boolean;
    empty_reason?: string | null;
  };
  query_metrics: Record<string, unknown>;
};

export type V2TestItemRawPreviewResponse = {
  context: V2TestItemContext;
  rows: Array<Record<string, unknown>>;
  row_count: number;
  max_rows: number;
  truncated: boolean;
  /** Present for target-preview consumers; legacy test doubles may omit it. */
  next_cursor?: string | null;
  fields: string[];
};

export type TestGroupModel = {
  groups: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
};

export type FailListModel = {
  summary: Record<string, unknown>;
  hardFail: Array<Record<string, unknown>>;
  marginalFail: Array<Record<string, unknown>>;
  riskPass: Array<Record<string, unknown>>;
};

// ---------------------------------------------------------------------------
// v2 Wafer Map types - Phase 4C
// ---------------------------------------------------------------------------

export type V2WaferMapDisplay = {
  origin: "corner" | "center" | string;
  corner?: "top_left" | "top_right" | "bottom_left" | "bottom_right" | string | null;
  flip_x: boolean;
  flip_y: boolean;
  applied: boolean;
};

export type V2WaferMapContext = {
  dataset_version_id: string;
  scope_id: string | null;
  scope_applied: boolean;
  source_asset_count: number;
  query_policy_version: string;
  final_device_policy: "enforced" | string;
  result_mode: "aggregate" | string;
  bin_mode: "hard_bin" | "soft_bin";
  view_mode?: "bin" | "parametric" | "stacked";
  test_item_id?: string | null;
  wafer_map_display?: V2WaferMapDisplay | null;
};

export type V2WaferSummary = {
  lot_id: string;
  wafer_id: string;
  total_count: number;
  pass_count: number;
  fail_count: number;
  no_result_count?: number;
  yield_rate: number;
  min_x: number;
  max_x: number;
  min_y: number;
  max_y: number;
};

export type V2WaferMapPoint = {
  ews_ref_id?: string | null;
  lot_id: string;
  wafer_id: string;
  x: number;
  y: number;
  bin: number;
  pass_fail: "pass" | "fail" | string;
  count: number;
};

export type V2WaferLegendItem = {
  bin: number;
  pass_fail: "pass" | "fail" | string;
  count: number;
};

export type V2ParametricScale = {
  status: "AVAILABLE" | "UNIT_CONFLICT" | "UNAVAILABLE" | string;
  unit: string | null;
  units: string[];
  min_value: number | null;
  max_value: number | null;
  scope_basis: "CURRENT_SCOPE" | string;
  value_basis: "RAW" | string;
  clipping: "NONE" | string;
  reason_codes: string[];
  missing_asset_count: number;
};

export type V2WaferMapResponse = {
  context: V2WaferMapContext;
  wafer_summaries: V2WaferSummary[];
  map_points: V2WaferMapPoint[];
  map_pagination?: {
    page_count: number;
    page_number?: number;
    total_count: number;
    count_basis: "EXACT" | "ESTIMATED" | "NOT_COMPUTED";
    page_size: number;
    next_cursor: string | null;
  };
  legend: V2WaferLegendItem[];
  parametric_scale?: V2ParametricScale | null;
  query_metrics: Record<string, unknown>;
};

// ---------------------------------------------------------------------------
// v2 Correlation types - Phase 4D
// ---------------------------------------------------------------------------

export type V2CorrelationContext = {
  dataset_version_id: string;
  scope_id: string | null;
  scope_applied: boolean;
  source_asset_count: number;
  final_device_policy: string;
  method: string;
};

export type V2CorrelationItem = {
  test_item_id: string;
  display_name: string;
  unit: string | null;
};

export type V2CorrelationSummary = {
  valid_pairs: number;
  null_pairs_removed: number;
  correlation: number;
  r_squared: number;
  method: string;
  x_mean: number;
  y_mean: number;
  x_std: number;
  y_std: number;
};

export type V2CorrelationGroup = {
  group_name: string;
  valid_pairs: number;
  correlation: number;
  r_squared: number;
  x_mean: number;
  y_mean: number;
  x_std: number;
  y_std: number;
};

export type V2ScatterPoint = {
  x: number;
  y: number;
};

export type V2CorrelationResponse = {
  context: V2CorrelationContext;
  items: {
    x: V2CorrelationItem;
    y: V2CorrelationItem;
  };
  summary: V2CorrelationSummary;
  optional_grouped: V2CorrelationGroup[] | null;
  scatter_preview: V2ScatterPoint[];
  query_metrics: Record<string, unknown>;
};

export type V2RiskItem = {
  risk_type: "yield" | "bin" | "wafer" | "site" | "test_item";
  severity: "info" | "warning" | "critical";
  metric: string;
  observed_value: number;
  reference_value: number;
  affected_count: number;
  group: Record<string, string>;
  reason_code: string;
};

export type V2AnomalyResponse = {
  context: V2OverviewContext;
  summary: { risk_count: number; risk_types: string[] };
  risks: V2RiskItem[];
  unsupported_risk_types: string[];
};

export type V2ReportResponse = {
  context: V2OverviewContext;
  sections?: Record<string, unknown>;
  unsupported_sections: string[];
  aggregate_csv?: string;
};

export type V2ReportRequest = {
  dataset_version_id: string;
  scope_id?: string | null;
  included_sections: string[];
  selected_test_item_ids?: string[];
  correlation_pair?: { test_item_x_id: string; test_item_y_id: string };
  format?: "json" | "aggregate_csv";
};

// Batch all-item CPK / Cg report sections served by /api/v2/level4/reports/summary.
export type V2ReportCpkPopulation = {
  device_count: number;
  n: number;
  mean: number | null;
  std: number | null;
  min: number | null;
  max: number | null;
  fail_count: number;
  cpu: number | null;
  cpl: number | null;
  cp?: number | null;
  cpk: number | null;
  cpk_cp?: number | null;
};

export type V2ReportSiteRow = {
  site: string;
  n: number;
  mean: number | null;
  std: number | null;
  cg: number | null;
  fail_count: number;
};

export type V2ReportCpkItem = {
  test_item_id: string;
  test_name: string;
  unit: string | null;
  stage: string[];
  lsl: number | null;
  usl: number | null;
  limits_available: boolean;
  pass: V2ReportCpkPopulation;
  bin1: V2ReportCpkPopulation | null;
  all: { exec_count: number; fail_count: number };
  sites: V2ReportSiteRow[];
  min_cg: number | null;
  status: string;
  unavailable_reason?: string;
};

export type V2ReportCpkSection = {
  summary: {
    item_count: number;
    available_count: number;
    min_pass_cpk: number | null;
    min_bin1_cpk: number | null;
    sbin_source: string;
  };
  population: Record<string, string>;
  items: V2ReportCpkItem[];
};

export type V2ReportCgmItem = {
  test_item_id: string;
  test_name: string;
  unit: string | null;
  lsl: number | null;
  usl: number | null;
  tolerance: number | null;
  exec_count: number;
  fail_count: number;
  sites: V2ReportSiteRow[];
  min_cg: number | null;
  status: string;
};

export type V2ReportCgmSection = {
  summary: { item_count: number; available_count: number; min_cg: number | null };
  items: V2ReportCgmItem[];
};

// ---------------------------------------------------------------------------
// Full-population item trend (Level 4 M4 pyramid) and cross-DatasetVersion
// batch trend / raw preview — the trend tab data source for all pages.
// ---------------------------------------------------------------------------

export type ItemTrendBucket = {
  /** 0-based inclusive start of the bucket in the merged device sequence. */
  start: number;
  /** 0-based exclusive end of the bucket. */
  end: number;
  min: number;
  max: number;
  mean: number;
  first: number;
  last: number;
  count: number;
};

export type ItemTrendSeriesBound = {
  dataset_version_id: string | null;
  start: number;
  end: number;
};

export type ItemTrendBlock = {
  status: "AVAILABLE" | "UNAVAILABLE";
  group_by: string;
  total_points?: number;
  stats?: { count: number; min: number | null; max: number | null; mean: number | null };
  series_bounds?: ItemTrendSeriesBound[];
  window?: { start: number; end: number };
  mode?: "raw" | "bucketed";
  points?: Array<{ group: string; value?: number | null; mean?: number | null; count?: number }>;
  buckets?: ItemTrendBucket[];
  reason_codes: string[];
};

export type ItemTrendLimits = {
  status: string;
  lsl: number | null;
  usl: number | null;
  unit: string | null;
};

/** Response of GET /level4/analysis/test-items/{id}/trend. */
export type ItemTrendTargetData = {
  item: { test_item_id: string };
  trend: ItemTrendBlock;
};

/** Response of POST /level4/analysis/test-items/{id}/batch-trend. */
export type BatchItemTrendTargetData = {
  context: {
    dataset_version_ids: string[];
    source_count: number;
    unavailable_sources: Array<{ dataset_version_id: string; reason: string | null }>;
  };
  item: { test_item_id: string; test_name: string | null; unit: string | null };
  trend: ItemTrendBlock;
  limits: ItemTrendLimits;
};

export type BatchItemPreviewRow = {
  dataset_version_id: string;
  source_asset_id?: string | null;
  lot_id: string | null;
  wafer_id: string | null;
  die_x: number | null;
  die_y: number | null;
  hbin: number | null;
  sbin: number | null;
  site: string | null;
  test_value: number | null;
  is_pass: boolean | null;
};

/** Response of POST /level4/analysis/test-items/{id}/batch-preview. */
export type BatchItemPreviewTargetData = {
  item: { test_item_id: string; test_name: string | null; unit: string | null };
  rows: BatchItemPreviewRow[];
  fields: string[];
  unavailable_sources: Array<{ dataset_version_id: string; reason: string | null }>;
};
