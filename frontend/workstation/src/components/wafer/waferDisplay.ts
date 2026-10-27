import type { V2WaferMapDisplay } from "../../types/analysis";

export type WaferBounds = {
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
};

/** Screen placement of product (0,0) derived from device display config. */
export function resolveOriginCorner(
  display?: Pick<V2WaferMapDisplay, "origin" | "corner"> | null,
): "top_left" | "top_right" | "bottom_left" | "bottom_right" {
  if (!display || display.origin === "center") return "top_left";
  const corner = display.corner;
  if (corner === "top_right" || corner === "bottom_left" || corner === "bottom_right") {
    return corner;
  }
  return "top_left";
}

export function dieToGrid(
  x: number,
  y: number,
  bounds: WaferBounds,
  display?: Pick<V2WaferMapDisplay, "origin" | "corner"> | null,
): { col: number; row: number } {
  const corner = resolveOriginCorner(display);
  const col = corner === "top_right" || corner === "bottom_right"
    ? bounds.maxX - x
    : x - bounds.minX;
  const row = corner === "bottom_left" || corner === "bottom_right"
    ? bounds.maxY - y
    : y - bounds.minY;
  return { col, row };
}

export function gridToDie(
  col: number,
  row: number,
  bounds: WaferBounds,
  display?: Pick<V2WaferMapDisplay, "origin" | "corner"> | null,
): { x: number; y: number } {
  const corner = resolveOriginCorner(display);
  const x = corner === "top_right" || corner === "bottom_right"
    ? bounds.maxX - col
    : bounds.minX + col;
  const y = corner === "bottom_left" || corner === "bottom_right"
    ? bounds.maxY - row
    : bounds.minY + row;
  return { x, y };
}
