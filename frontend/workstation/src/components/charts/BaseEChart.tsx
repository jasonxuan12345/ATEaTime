import { t, localize, useLanguage } from "../../i18n";
import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";
import * as echarts from "echarts";

import { withChartTheme } from "./chartTheme";
import type { BaseChartProps, BaseEChartHandle } from "./chartTypes";

export type { BaseEChartHandle } from "./chartTypes";

export const BaseEChart = forwardRef<BaseEChartHandle, BaseChartProps>(function BaseEChart({
  ariaLabel,
  lazyUpdate = true,
  loading = false,
  notMerge = true,
  option,
  onClick,
  onDataZoom,
  theme,
}, ref) {
  const elementRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<echarts.EChartsType | null>(null);

  useImperativeHandle(ref, () => ({
    getInstance: () => chartRef.current,
    getDataURL: (options) => chartRef.current?.getDataURL({ type: "png", pixelRatio: 2, backgroundColor: "transparent", ...options }) ?? null,
    resize: () => chartRef.current?.resize(),
  }), []);

  useEffect(() => {
    const element = elementRef.current;
    if (!element) return;
    const chart = echarts.init(element, theme, { renderer: "canvas" });
    chartRef.current = chart;
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(() => chart.resize());
    observer?.observe(element);
    const resizeVisible = () => { if (!document.hidden) chart.resize(); };
    document.addEventListener("visibilitychange", resizeVisible);
    return () => {
      document.removeEventListener("visibilitychange", resizeVisible);
      observer?.disconnect();
      chart.dispose();
      chartRef.current = null;
    };
  }, [theme]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart) return;
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    chart.setOption(withChartTheme({ ...option, animation: reducedMotion ? false : option.animation ?? true }), { notMerge, lazyUpdate });
  }, [lazyUpdate, notMerge, option, theme]);

  useEffect(() => {
    if (loading) chartRef.current?.showLoading("default", { text: "Loading chart..." });
    else chartRef.current?.hideLoading();
  }, [loading]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onClick) return;
    chart.on("click", onClick);
    return () => { chart.off("click", onClick); };
  }, [onClick, theme]);

  useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !onDataZoom) return;
    chart.on("datazoom", onDataZoom);
    return () => { chart.off("datazoom", onDataZoom); };
  }, [onDataZoom, theme]);

  return <div aria-label={localize(ariaLabel)} className="base-echart" ref={elementRef} role="img" />;
});
