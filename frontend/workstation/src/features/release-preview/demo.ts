// Design-preview fixtures only. Production statistics and rules stay in Python/YAML.
export type Stage = "import" | "cpk" | "cgm" | "site";
export type AnalysisStage = "cpk" | "cgm";
export type Verdict = "FAIL" | "RISK" | "PASS" | "NOT_CALCULABLE" | "REVIEW_REQUIRED" | "INSUFFICIENT_DATA";
export type Selection = { sites: number[]; tests: string[]; minimum: string };
export type DemoTest = {
  id: string; name: string; description: string; unit: string | null;
  low: number; high: number; mean: number; std: number;
  cpk: number | null; cg: (number | null)[]; issue?: string;
};
export type DemoRow = { test: DemoTest; value: number | null; status: Verdict; reason: string;
  n: number; override: boolean; supportingCpk: number | null };
export type DemoReport = { selection: Selection; rows: DemoRow[] };

export const demoTests: DemoTest[] = [
  { id: "1001", name: "VDD_IQ", description: "静态电流", unit: "mA", low: .5, high: 12,
    mean: 3.26, std: 1, cpk: .92, cg: [.85, .97, .9, 1.05] },
  { id: "1002", name: "ADC_OFFSET", description: "ADC 零点偏移", unit: "mV", low: -3, high: 3,
    mean: 0, std: .562, cpk: 1.78, cg: [1.16, 1.24, 1.08, 1.12] },
  { id: "1003", name: "CLK_FREQ", description: "时钟频率", unit: "MHz", low: 74, high: 86,
    mean: 80, std: .813, cpk: 2.46, cg: [.91, .94, 1.1, .88] },
  { id: "1004", name: "VDD_LEVEL", description: "供电电压", unit: "V", low: 1.7, high: 1.9,
    mean: 1.8, std: .01, cpk: 3.33, cg: [1.95, 2.11, 2.02, 1.84] },
  { id: "1005", name: "LNA_GAIN", description: "放大器增益", unit: "dB", low: 12, high: 18,
    mean: 15, std: .641, cpk: 1.56, cg: [1.08, 1.02, 1.04, .96] },
  { id: "1006", name: "REF_I", description: "参考电流", unit: "µA", low: 8, high: 12,
    mean: 10, std: .317, cpk: 2.1, cg: [1.5, 1.61, 1.44, 1.52] },
  { id: "1007", name: "TEMP_SENSOR", description: "温度传感器", unit: "°C", low: 20, high: 30,
    mean: 25, std: 0, cpk: null, cg: [null, null, null, null], issue: "零标准差" },
  { id: "1008", name: "LEAKAGE", description: "漏电流", unit: null, low: .1, high: 3,
    mean: 1.2, std: .12, cpk: 3.06, cg: [1.6, 1.72, 1.65, 1.59], issue: "单位缺失" },
];

export const defaultSelection = (): Selection => ({ sites: [1, 2, 3, 4], tests: demoTests.map(t => t.id), minimum: "" });
export const verdictLabels: Record<Verdict, string> = {
  FAIL: "未达标", RISK: "风险", PASS: "通过", NOT_CALCULABLE: "无法计算",
  REVIEW_REQUIRED: "需复核", INSUFFICIENT_DATA: "数据不足",
};
export const metric = (value: number | null) => value === null ? "—" : value.toFixed(2);
export const sameSelection = (a: Selection, b: Selection) => JSON.stringify(a) === JSON.stringify(b);
export function selectionError(selection: Selection) {
  if (!selection.sites.length || !selection.tests.length) return "至少选择一个 site 和一个测项。";
  if (selection.minimum && (!Number.isInteger(Number(selection.minimum)) || Number(selection.minimum) < 2)) {
    return "最小样本数需要是至少 2 的整数。";
  }
  return null;
}

export function buildDemoReport(stage: AnalysisStage, selection: Selection, supportingReport: DemoReport | null): DemoReport {
  // These illustrative values are not measurements from a selected/uploaded file.
  const rows = demoTests.filter(test => selection.tests.includes(test.id)).map(test => {
    const cgs = selection.sites.map(site => test.cg[site - 1]);
    const value = stage === "cpk" ? test.cpk : cgs.some(cg => cg === null) ? null : Math.min(...cgs as number[]);
    const n = selection.sites.length * (stage === "cpk" ? 61 : 64);
    const support = supportingReport?.rows.find(row => row.test.id === test.id);
    const supportingCpk = support && ["PASS", "RISK", "FAIL"].includes(support.status) ? support.value : null;
    let status: Verdict = "PASS", reason = "满足示例规则";
    let override = false;
    if (Number(selection.minimum) > (stage === "cpk" ? n : 64)) {
      status = "INSUFFICIENT_DATA"; reason = "参与计算的记录数少于所设最小样本数";
    } else if (value === null) {
      status = "NOT_CALCULABLE"; reason = test.issue ?? "没有可计算的指标";
    } else if (stage === "cgm" && supportingCpk !== null && supportingCpk >= 2) {
      reason = "bin1 CPK ≥ 2，按现有规则接受"; override = true;
    } else if (value < (stage === "cpk" ? 1.67 : 1)) {
      status = "FAIL"; reason = stage === "cpk" ? "CPK < 1.67" : "Cg < 1";
    } else if (value < (stage === "cpk" ? 2 : 1.33)) {
      status = "RISK"; reason = stage === "cpk" ? "1.67 ≤ CPK < 2" : "1 ≤ Cg < 1.33";
    }
    return { test, value, status, reason, n, override, supportingCpk };
  });
  return { selection: structuredClone(selection), rows };
}
