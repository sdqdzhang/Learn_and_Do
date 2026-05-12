/** 高对比调色板（深色画布上易区分）。 */
export const TRACE_EDGE_PALETTE = [
  "#38bdf8",
  "#f472b6",
  "#4ade80",
  "#fbbf24",
  "#c084fc",
  "#2dd4bf",
  "#fb923c",
  "#a5b4fc",
] as const;

export type TraceEdgeData = {
  curvature: number;
};

/** 按已存在边数量轮换颜色，并用伪随机步进拉开贝塞尔曲率，减轻路径重叠。 */
export function edgePaint(edgeCount: number): { stroke: string; curvature: number } {
  const stroke = TRACE_EDGE_PALETTE[edgeCount % TRACE_EDGE_PALETTE.length];
  const curvature = 0.14 + ((edgeCount * 17) % 52) / 100;
  return { stroke, curvature };
}
