import { Sparkles } from "lucide-react";
import { Handle, Position } from "reactflow";
import type { ReflectionNodeData } from "../../types/trace";

type Props = { data: ReflectionNodeData };

export default function ReflectionNode({ data }: Props) {
  const { summary } = data;

  return (
    <div className="w-[300px] rounded-lg border-2 border-amber-700/40 bg-gradient-to-br from-slate-900/92 via-amber-950/18 to-slate-900/90 px-3 py-2.5 text-slate-100 shadow-lg shadow-slate-950/35">
      <Handle type="target" position={Position.Left} id="in-l" className="!h-2 !w-2 !bg-amber-500/70" />
      <Handle type="target" position={Position.Right} id="in-r" className="!h-2 !w-2 !bg-amber-500/70" />
      <Handle type="source" position={Position.Left} id="out-l" className="!h-2 !w-2 !bg-amber-400/80" />
      <Handle type="source" position={Position.Right} id="out-r" className="!h-2 !w-2 !bg-amber-300/75" />

      <div className="mb-1 flex items-center gap-1 text-[10px] font-semibold uppercase tracking-wider text-amber-200/85">
        <Sparkles className="h-3 w-3 shrink-0 text-amber-400/80" aria-hidden />
        反思 · Reflect
      </div>
      <div className="max-h-[160px] overflow-y-auto rounded border border-amber-900/35 bg-slate-950/35 px-2 py-1.5 text-[11px] leading-snug text-amber-50/95 whitespace-pre-wrap">
        {summary || "（无摘要）"}
      </div>
    </div>
  );
}
