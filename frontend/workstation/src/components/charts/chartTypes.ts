import type { EChartsType, EChartsOption } from "echarts";

export type ChartThemeName = "light" | "dark";

export type BaseChartProps = {
  ariaLabel: string;
  loading?: boolean;
  notMerge?: boolean;
  lazyUpdate?: boolean;
  option: EChartsOption;
  theme: ChartThemeName;
  onClick?: (params: unknown) => void;
  onDataZoom?: (params: unknown) => void;
};

export type BaseEChartHandle = {
  getInstance(): EChartsType | null;
  getDataURL(options?: Parameters<EChartsType["getDataURL"]>[0]): string | null;
  resize(): void;
};
