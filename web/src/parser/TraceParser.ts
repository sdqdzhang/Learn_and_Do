import type { TraceEvent } from "../types/trace";

/** 将 JSONL 文本按行解析为 TraceEvent，并逐条交给回调（通常即 store.addEvent）。 */
export class TraceParser {
  constructor(private readonly onEvent: (event: TraceEvent) => void) {}

  parseJsonlString(raw: string): void {
    const lines = raw.split(/\r?\n/);
    for (const line of lines) {
      this.parseLine(line);
    }
  }

  parseLine(line: string): void {
    const trimmed = line.trim();
    if (!trimmed) return;
    const data = JSON.parse(trimmed) as TraceEvent;
    this.onEvent(data);
  }
}
