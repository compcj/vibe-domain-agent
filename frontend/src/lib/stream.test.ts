import { afterEach, describe, expect, it, vi } from "vitest";
import { EventDecoder, watchRun } from "./stream";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("SSE decoding and reconnect cursor", () => {
  it("buffers fragmented events and reads CRLF streams", () => {
    const decoder = new EventDecoder();
    expect(decoder.feed("id: 7\r\nevent: del")).toEqual([]);
    expect(decoder.feed('ta\r\ndata: {"text":"你好"}\r\n\r\n')).toEqual([
      { id: 7, type: "delta", data: { text: "你好" } },
    ]);
  });
  it("ignores keepalives and handles several events in one chunk", () => {
    const decoder = new EventDecoder();
    expect(
      decoder.feed(
        ': ping\n\nid: 1\nevent: status\ndata: {"status":"running"}\n\nid: 2\nevent: done\ndata: {"status":"completed"}\n\n',
      ),
    ).toEqual([
      { id: 1, type: "status", data: { status: "running" } },
      { id: 2, type: "done", data: { status: "completed" } },
    ]);
  });
  it("does not silently discard malformed data", () => {
    const decoder = new EventDecoder();
    expect(() =>
      decoder.feed("id: 1\nevent: delta\ndata: invalid-json\n\n"),
    ).toThrow();
  });
  it("keeps subscribing across healthy stream closures while awaiting approval", async () => {
    vi.useFakeTimers();
    let count = 0;
    const fetcher = vi.fn(
      async () =>
        new Response(
          ++count < 8
            ? ": keepalive\n\n"
            : 'id: 1\nevent: done\ndata: {"status":"completed"}\n\n',
          { headers: { "Content-Type": "text/event-stream" } },
        ),
    );
    vi.stubGlobal("fetch", fetcher);
    const received = vi.fn();
    const result = watchRun(
      "waiting",
      received,
      new AbortController().signal,
    ).then(
      () => "complete",
      (error: Error) => error.message,
    );
    await vi.runAllTimersAsync();
    expect(await result).toBe("complete");
    expect(fetcher).toHaveBeenCalledTimes(8);
    expect(received).toHaveBeenCalledWith({
      id: 1,
      type: "done",
      data: { status: "completed" },
    });
  });
  it("resumes from the last event and ignores replayed events", async () => {
    vi.useFakeTimers();
    const event = 'id: 7\nevent: delta\ndata: {"text":"回答"}\n\n';
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(new Response(event))
      .mockResolvedValueOnce(
        new Response(
          event + 'id: 8\nevent: done\ndata: {"status":"completed"}\n\n',
        ),
      );
    vi.stubGlobal("fetch", fetcher);
    const received = vi.fn();
    const result = watchRun("resume", received, new AbortController().signal);
    await vi.runAllTimersAsync();
    await result;
    expect(fetcher.mock.calls[1][0]).toContain("after=7");
    expect(received).toHaveBeenCalledTimes(2);
  });
});
