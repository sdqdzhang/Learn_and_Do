import type { CSSProperties } from "react";
import { BaseEdge, type EdgeProps, getBezierPath } from "reactflow";
import type { TraceEdgeData } from "../../utils/edgeAppearance";

/** 贝塞尔斜向连线 + 单色箭头；颜色与曲率由 store 按边序号注入，减少重叠与混淆。 */
export function DataFlowEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  data,
}: EdgeProps<TraceEdgeData>) {
  const curvature =
    typeof data?.curvature === "number" && Number.isFinite(data.curvature)
      ? data.curvature
      : 0.28;

  const [path] = getBezierPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    curvature,
  });

  const stroke =
    (typeof (style as CSSProperties | undefined)?.stroke === "string"
      ? (style as CSSProperties).stroke
      : null) ?? "#94a3b8";
  const mid = `trace-arr-${id.replace(/[^a-zA-Z0-9_-]/g, "")}`;

  return (
    <>
      <defs>
        <marker
          id={mid}
          markerWidth="12"
          markerHeight="12"
          refX="9"
          refY="6"
          orient="auto"
          markerUnits="userSpaceOnUse"
        >
          <path d="M0,0 L10,6 L0,12 Z" fill={stroke} />
        </marker>
      </defs>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={`url(#${mid})`}
        style={{
          ...style,
          fill: "none",
          strokeWidth: 2.35,
        }}
      />
    </>
  );
}
