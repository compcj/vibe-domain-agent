export interface RunEvent {
  id: number;
  type: string;
  data: Record<string, unknown>;
}

export class EventDecoder {
  private buffer = "";

  feed(chunk: string): RunEvent[] {
    this.buffer = (this.buffer + chunk).replace(/\r\n/g, "\n");
    const result: RunEvent[] = [];
    let boundary: number;
    while ((boundary = this.buffer.indexOf("\n\n")) >= 0) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      let id = 0;
      let type = "message";
      const data: string[] = [];
      for (const line of block.split("\n")) {
        if (line.startsWith("id:")) id = Number(line.slice(3).trim());
        else if (line.startsWith("event:")) type = line.slice(6).trim();
        else if (line.startsWith("data:"))
          data.push(line.slice(5).replace(/^ /, ""));
      }
      if (data.length) {
        const value: unknown = JSON.parse(data.join("\n"));
        if (
          !value ||
          typeof value !== "object" ||
          Array.isArray(value) ||
          !Number.isFinite(id)
        )
          throw new Error("无法解析任务进度");
        result.push({ id, type, data: value as Record<string, unknown> });
      }
    }
    return result;
  }
}

function pause(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    if (signal.aborted) {
      resolve();
      return;
    }
    const finish = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", finish);
      resolve();
    };
    const timer = setTimeout(finish, ms);
    signal.addEventListener("abort", finish, { once: true });
  });
}

export async function watchRun(
  runId: string,
  onEvent: (event: RunEvent) => void,
  signal: AbortSignal,
) {
  let after = 0;
  let retries = 0;
  while (!signal.aborted) {
    try {
      const response = await fetch(
        `/api/v1/runs/${encodeURIComponent(runId)}/events?after=${after}`,
        {
          signal,
          credentials: "same-origin",
          headers: { Accept: "text/event-stream" },
        },
      );
      if (
        response.status === 401 ||
        response.status === 403 ||
        response.status === 404
      )
        throw new Error("当前任务不可访问，请刷新页面");
      if (!response.ok || !response.body) throw new Error("暂时无法连接任务");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      const events = new EventDecoder();
      try {
        while (!signal.aborted) {
          const part = await reader.read();
          if (part.done) break;
          for (const event of events.feed(
            decoder.decode(part.value, { stream: true }),
          )) {
            if (event.id <= after) continue;
            after = event.id;
            retries = 0;
            onEvent(event);
            if (event.type === "done") return;
          }
        }
      } finally {
        await reader.cancel().catch(() => undefined);
      }
      retries = 0;
    } catch (error) {
      if (signal.aborted) return;
      if (error instanceof Error && error.message.includes("不可访问"))
        throw error;
      if (++retries > 5)
        throw new Error(
          "进度连接已中断，任务不会重复提交。请刷新页面查看结果。",
        );
    }
    await pause(Math.min(Math.max(retries, 1) * 1000, 5000), signal);
  }
}
