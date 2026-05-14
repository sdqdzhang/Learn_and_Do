import { Handle, Position } from "reactflow";
import type { CognitiveNodeData } from "../../types/trace";
import { formatUsageLine } from "../../utils/thoughtAdapter";

type Props = { data: CognitiveNodeData };

const BARE_DONE = /^\[(完成|done)\]\s*$/i;

export default function CognitiveNode({ data }: Props) {
  const { thoughts, usage, contentFallback, traceHints } = data;
  const trimmed = contentFallback.trim();
  const emptyBodyFallback =
    traceHints.length > 0
      ? traceHints
      : [
          "（本轮 assistant 正文为空且无结构化 thought；常见于解析失败后重试瞬间的空 completion；完整载荷见侧栏）",
        ];
  const lines =
    thoughts.length > 0
      ? thoughts
      : trimmed
        ? [
            trimmed.length > 400 ? `${trimmed.slice(0, 400)}…` : trimmed,
            ...(BARE_DONE.test(trimmed)
              ? [
                  "（终止标记：模型声明本 session 可结束；若本轮未触发工具，workflow 会直接 DONE）",
                ]
              : []),
          ]
        : emptyBodyFallback;

  return (
    <div className="w-[300px] rounded-lg border-2 border-emerald-700/45 bg-gradient-to-br from-slate-900/95 via-emerald-950/12 to-slate-900/90 px-3 py-2.5 text-slate-100 shadow-lg shadow-slate-950/40">
      <Handle type="target" position={Position.Left} id="in-l" className="!h-2 !w-2 !bg-emerald-500/60" />
      <Handle type="target" position={Position.Right} id="in-r" className="!h-2 !w-2 !bg-emerald-500/60" />
      <Handle type="source" position={Position.Left} id="out-l" className="!h-2 !w-2 !bg-emerald-400/75" />
      <Handle type="source" position={Position.Right} id="out-r" className="!h-2 !w-2 !bg-sky-400/70" />

      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-emerald-200/85">
        思考 · Think
      </div>
      <ul className="max-h-[140px] space-y-1.5 overflow-y-auto text-xs leading-snug text-slate-200">
        {lines.map((t, i) => (
          <li
            key={i}
            className="rounded border border-slate-700/80 bg-slate-950/40 px-2 py-1.5 text-[11px]"
          >
            {t}
          </li>
        ))}
      </ul>
      <div className="mt-2 border-t border-slate-700/80 pt-1.5 text-[10px] text-slate-500">
        {formatUsageLine(usage)}
      </div>
    </div>
  );
}
