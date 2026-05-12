import { ScrollText } from "lucide-react";
import { Handle, Position } from "reactflow";
import type { OutputNodeData } from "../../types/trace";

type Props = { data: OutputNodeData };

export default function OutputNode({ data }: Props) {
  const { toolName, stdout, stderr, summary } = data;
  const out = stdout.trim();
  const err = stderr.trim();

  return (
    <div className="w-[300px] rounded-lg border-2 border-sky-600/55 bg-gradient-to-br from-slate-950 via-slate-900/98 to-slate-950 px-3 py-2.5 text-slate-100 shadow-lg shadow-slate-950/45">
      <Handle type="target" position={Position.Left} id="in-l" className="!h-2 !w-2 !bg-sky-400" />
      <Handle type="target" position={Position.Right} id="in-r" className="!h-2 !w-2 !bg-sky-400" />
      <Handle type="source" position={Position.Left} id="out-l" className="!h-2 !w-2 !bg-indigo-400" />
      <Handle type="source" position={Position.Right} id="out-r" className="!h-2 !w-2 !bg-indigo-400" />

      <div className="mb-1 flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-wider text-sky-300/95">
        <ScrollText className="h-3 w-3 shrink-0 text-sky-400/90" aria-hidden />
        输出 · stdout/stderr
      </div>
      <div className="truncate text-[11px] font-semibold text-sky-100">{toolName}</div>
      {summary ? (
        <div className="mt-0.5 truncate text-[10px] text-slate-400">{summary}</div>
      ) : null}

      <pre
        className="mt-2 max-h-[130px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-slate-700/90 bg-black/50 p-2 font-mono text-[10px] leading-snug text-slate-100"
        tabIndex={0}
      >
        {out || "（无 stdout）"}
      </pre>

      {err ? (
        <pre
          className="mt-2 max-h-[72px] overflow-auto whitespace-pre-wrap break-words rounded-md border border-rose-800/60 bg-rose-950/45 p-2 font-mono text-[10px] leading-snug text-rose-50"
          tabIndex={0}
        >
          {err}
        </pre>
      ) : null}
    </div>
  );
}
