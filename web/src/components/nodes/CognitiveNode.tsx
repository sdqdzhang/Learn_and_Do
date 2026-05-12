import { Handle, Position } from "reactflow";
import type { CognitiveNodeData } from "../../types/trace";
import { formatUsageLine } from "../../utils/thoughtAdapter";

type Props = { data: CognitiveNodeData };

export default function CognitiveNode({ data }: Props) {
  const { thoughts, usage } = data;
  const lines = thoughts.length > 0 ? thoughts : ["（无结构化 thought，已回退展示）"];

  return (
    <div className="w-[300px] rounded-lg border-2 border-slate-600 bg-gradient-to-br from-slate-900/95 via-slate-800/95 to-slate-900/90 px-3 py-2.5 text-slate-100 shadow-lg shadow-slate-950/40">
      <Handle type="target" position={Position.Left} id="in-l" className="!h-2 !w-2 !bg-slate-400" />
      <Handle type="target" position={Position.Right} id="in-r" className="!h-2 !w-2 !bg-slate-400" />
      <Handle type="source" position={Position.Left} id="out-l" className="!h-2 !w-2 !bg-emerald-400" />
      <Handle type="source" position={Position.Right} id="out-r" className="!h-2 !w-2 !bg-sky-400" />

      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wider text-sky-300/90">
        认知 · Thought
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
