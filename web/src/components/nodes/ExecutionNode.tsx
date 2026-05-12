import { FileJson, Terminal } from "lucide-react";
import { Handle, Position } from "reactflow";
import type { ExecutionNodeData } from "../../types/trace";

type Props = { data: ExecutionNodeData };

function StatusBadge({ status }: { status: ExecutionNodeData["status"] }) {
  const map = {
    PENDING: "bg-amber-500/20 text-amber-200 ring-1 ring-amber-400/50",
    SUCCESS: "bg-emerald-500/20 text-emerald-200 ring-1 ring-emerald-400/50",
    FAILED: "bg-rose-500/20 text-rose-200 ring-1 ring-rose-400/50",
  } as const;
  return (
    <span
      className={`rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${map[status]}`}
    >
      {status}
    </span>
  );
}

export default function ExecutionNode({ data }: Props) {
  const { toolName, args, status } = data;
  const snapshot = JSON.stringify(args, null, 0);
  const short = snapshot.length > 180 ? `${snapshot.slice(0, 180)}…` : snapshot;

  return (
    <div className="w-[320px] rounded-none border border-emerald-700/40 bg-slate-950/55 px-3 py-2.5 font-mono text-slate-100 shadow-[0_0_0_1px_rgba(16,185,129,0.12)] backdrop-blur-sm">
      <Handle type="target" position={Position.Left} id="in-l" className="!h-2 !w-2 !bg-emerald-400" />
      <Handle type="target" position={Position.Right} id="in-r" className="!h-2 !w-2 !bg-emerald-400" />
      <Handle type="source" position={Position.Left} id="out-l" className="!h-2 !w-2 !bg-teal-300" />
      <Handle type="source" position={Position.Right} id="out-r" className="!h-2 !w-2 !bg-teal-300" />

      <div className="mb-2 flex items-center justify-between gap-2 border-b border-slate-800 pb-1.5">
        <div className="flex items-center gap-1.5 text-[11px] text-slate-300">
          <Terminal className="h-3.5 w-3.5 shrink-0 text-emerald-400/90" aria-hidden />
          <span className="truncate font-semibold text-emerald-100/95">{toolName}</span>
        </div>
        <StatusBadge status={status} />
      </div>

      <div className="flex gap-2 rounded border border-slate-800/90 bg-black/35 p-2">
        <div className="flex shrink-0 flex-col items-center gap-1 text-[9px] text-slate-500">
          <FileJson className="h-8 w-8 text-sky-400/80" aria-hidden />
          <span>args</span>
        </div>
        <pre className="max-h-[100px] flex-1 overflow-auto whitespace-pre-wrap break-all text-[10px] leading-tight text-slate-400">
          {short || "{}"}
        </pre>
      </div>
    </div>
  );
}
