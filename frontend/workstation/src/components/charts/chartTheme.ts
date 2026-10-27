import type { EChartsOption } from "echarts";

const token = (name: string, fallback: string) => {
  if (typeof document === "undefined") return fallback;
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
};

export function chartTokens() {
  return {
    accent: token("--accent", "#5b6cff"),
    teal: token("--teal", "#14b8a6"),
    purple: token("--purple", "#8b5cf6"),
    warning: token("--warning", token("--orange", "#f59e0b")),
    danger: token("--danger", "#ef4444"),
    success: token("--success", "#10b981"),
    text: token("--text-primary", "#111827"),
    secondary: token("--text-secondary", "#4b5563"),
    muted: token("--text-muted", "#8a94a6"),
    border: token("--border", "rgba(17,24,39,.08)"),
    background: token("--bg-solid", "#ffffff"),
  };
}

export function withChartTheme(option: EChartsOption): EChartsOption {
  const colors = chartTokens();
  return {
    color: [colors.accent, colors.teal, colors.purple, colors.warning, colors.danger],
    textStyle: { color: colors.secondary, fontFamily: "Inter, ui-sans-serif, system-ui" },
    ...option,
  };
}
