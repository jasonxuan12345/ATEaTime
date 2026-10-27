const paths = {
  upload: "M12 16V3m-5 5 5-5 5 5M4 15v5h16v-5",
  file: "M14 2H5v20h14V7l-5-5ZM14 2v6h5M8 12h8m-8 4h5",
  search: "M21 21l-5-5M18 10a8 8 0 1 1-16 0 8 8 0 0 1 16 0Z",
  check: "m5 12 4 4L19 6",
  download: "M12 3v13m-5-5 5 5 5-5M4 17v4h16v-4",
  layers: "m12 3 10 5-10 5L2 8l10-5ZM2 12l10 5 10-5M2 16l10 5 10-5",
  chart: "M4 3v18h18M8 16v-5m5 5V7m5 9v-9",
  clock: "M12 7v5l3 2M22 12a10 10 0 1 1-20 0 10 10 0 0 1 20 0Z",
  arrow: "M4 12h16m-6-6 6 6-6 6",
} as const;
export function Icon({ name, size = 18 }: { name: keyof typeof paths; size?: number }) {
  return <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={paths[name]} /></svg>;
}
