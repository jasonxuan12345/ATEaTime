import { t, localize, useLanguage } from "../../../i18n";
import * as echarts from 'echarts';
import type { EChartsOption } from 'echarts';

export function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob), link = document.createElement('a');
  link.href = url; link.download = filename; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
export function wrapCanvasText(text: string, maxWidth: number, measure: (text: string) => number): string[] {
  const lines: string[] = [];
  for (const paragraph of text.split('\n')) {
    let line = '';
    for (const word of paragraph.match(/\S+\s*|\s+/g) ?? ['']) {
      if (line && measure(line + word.trimEnd()) > maxWidth) { lines.push(line.trimEnd()); line = ''; }
      for (const character of word) {
        if (line && measure(line + character) > maxWidth) { lines.push(line.trimEnd()); line = ''; }
        if (line || character.trim()) line += character;
      }
    }
    lines.push(line.trimEnd());
  }
  return lines;
}
export async function exportChartGroup(title: string, metadata: string[], options: EChartsOption[], images?: (string | null)[], filename = 'release-chart-group.png') {
  const urls: string[] = [];
  for (let index = 0; index < options.length; index++) {
    if (images?.[index]) { urls.push(images[index]!); continue; }
    const node = document.createElement('div');
    node.style.cssText = 'position:fixed;left:-10000px;width:1100px;height:460px'; document.body.appendChild(node);
    const chart = echarts.init(node, undefined, { renderer: 'canvas' });
    try { chart.setOption({ ...options[index], animation: false, dataZoom: [] }); urls.push(chart.getDataURL({ type: 'png', pixelRatio: 1, backgroundColor: '#ffffff' })); }
    finally { chart.dispose(); node.remove(); }
  }
  const imagesLoaded = await Promise.all(urls.map(url => new Promise<HTMLImageElement>((resolve, reject) => {
    const image = new Image(); image.onload = () => resolve(image); image.onerror = () => reject(new Error(t("图形导出失败，请重试"))); image.src = url;
  })));
  const canvas = document.createElement('canvas'); canvas.width = 1400;
  const ctx = canvas.getContext('2d'); if (!ctx) throw new Error(t("浏览器无法生成图组"));
  ctx.font = 'bold 23px Segoe UI, Microsoft YaHei, sans-serif';
  const titleLines = wrapCanvasText(title, 1330, text => ctx.measureText(text).width);
  ctx.font = '14px Segoe UI, Microsoft YaHei, sans-serif';
  const lines = metadata.flatMap(text => wrapCanvasText(text, 1330, value => ctx.measureText(value).width));
  const heights = imagesLoaded.map(image => image.height / image.width * 1350);
  canvas.height = 95 + (titleLines.length - 1) * 30 + lines.length * 22 + heights.reduce((sum, height) => sum + height + 25, 0);
  ctx.fillStyle = '#ffffff'; ctx.fillRect(0, 0, canvas.width, canvas.height);
  ctx.fillStyle = '#193844'; ctx.font = 'bold 23px Segoe UI, Microsoft YaHei, sans-serif';
  titleLines.forEach((line, index) => ctx.fillText(line, 30, 40 + index * 30));
  let y = 75 + (titleLines.length - 1) * 30; ctx.font = '14px Segoe UI, Microsoft YaHei, sans-serif';
  for (const line of lines) { ctx.fillText(line, 30, y); y += 22; }
  imagesLoaded.forEach((image, index) => { ctx.drawImage(image, 25, y, 1350, heights[index]); y += heights[index] + 25; });
  await new Promise<void>((resolve, reject) => canvas.toBlob(blob => { if (!blob) { reject(new Error(t("图片导出失败"))); return; }
    downloadBlob(blob, filename); resolve(); }, 'image/png'));
}
