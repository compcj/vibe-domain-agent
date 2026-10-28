import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Chat } from "./Chat";
import type { RunEvent } from "../lib/stream";
import type { Run } from "../types";

const { request, stream } = vi.hoisted(() => ({
  request: vi.fn(),
  stream: vi.fn(),
}));
vi.mock("../lib/api", () => ({
  api: request,
  errorMessage: (value: Error) => value.message,
}));
vi.mock("../lib/stream", () => ({ watchRun: stream }));

it("loads approvals after the gate becomes waiting and ignores late responses", async () => {
  const approval = {
    id: "pending", title: "Action at the gate", summary: "Review action",
    parameters: {}, expires_at: new Date(Date.now() + 600000).toISOString(),
  };
  const running = { id: "run", status: "running", approvals: [] } as Run;
  const waiting = { ...running, status: "awaiting_confirmation", approvals: [approval] };
  const pending: ((value: Run) => void)[] = [];
  let initial = true;
  request.mockImplementation((path: string) => {
    if (path !== "/runs/run") return Promise.resolve([]);
    if (initial) { initial = false; return Promise.resolve(running); }
    return new Promise<Run>((resolve) => pending.push(resolve));
  });
  let listener!: (event: RunEvent) => void;
  stream.mockImplementation((_id: string, onEvent: typeof listener) => {
    listener = onEvent;
    return new Promise(() => {});
  });
  render(<Chat conversation={{ id: "conversation", agent_id: "agent", title: "Test",
    active_run_id: "run", created_at: "" }} onChanged={() => {}} />);
  await waitFor(() => expect(listener).toBeTypeOf("function"));
  await act(async () => {
    listener({ id: 1, type: "approval", data: approval });
    pending.shift()!(running);
  });
  expect(screen.queryByText(approval.title)).not.toBeInTheDocument();
  await act(async () => {
    listener({ id: 2, type: "status", data: { status: "awaiting_confirmation" } });
  });
  expect(pending).toHaveLength(1);
  await act(async () => { pending.shift()!(waiting); });
  expect(screen.getByRole("button", { name: "确认执行" })).toBeInTheDocument();
  // A previous approval request cannot overwrite the later authoritative gate result.
  await act(async () => {
    listener({ id: 3, type: "approval", data: approval });
    listener({ id: 4, type: "status", data: { status: "awaiting_confirmation" } });
  });
  expect(pending).toHaveLength(2);
  await act(async () => { pending[1]({ ...waiting, approvals: [] }); });
  await act(async () => { pending[0](waiting); });
  expect(screen.queryByText(approval.title)).not.toBeInTheDocument();
  pending.length = 0;
  await act(async () => {
    listener({ id: 5, type: "status", data: { status: "awaiting_confirmation" } });
    listener({ id: 6, type: "done", data: { status: "completed" } });
  });
  await act(async () => { pending.shift()!(waiting); });
  expect(screen.queryByRole("button", { name: "确认执行" })).not.toBeInTheDocument();
});

it("shows only currently pending approvals when historical events are replayed", async () => {
  const approval = {
    id: "current",
    title: "Current pending action",
    summary: "Review current action",
    parameters: {},
    expires_at: new Date(Date.now() + 600000).toISOString(),
  };
  request.mockImplementation(async (path: string) =>
    path === "/runs/run"
      ? { id: "run", status: "awaiting_confirmation", approvals: [approval] }
      : [],
  );
  let listener!: (event: RunEvent) => void;
  stream.mockImplementation((_id: string, onEvent: typeof listener) => {
    listener = onEvent;
    return new Promise(() => {});
  });
  render(
    <Chat
      conversation={{
        id: "conversation",
        agent_id: "agent",
        title: "Test",
        active_run_id: "run",
        created_at: "",
      }}
      onChanged={() => {}}
    />,
  );
  await screen.findByText("Current pending action");
  await waitFor(() => expect(listener).toBeTypeOf("function"));
  await act(async () => {
    listener({
      id: 1,
      type: "approval",
      data: { ...approval, id: "old", title: "Previously approved action" },
    });
  });
  expect(
    screen.queryByText("Previously approved action"),
  ).not.toBeInTheDocument();
  expect(screen.getAllByRole("button", { name: "确认执行" })).toHaveLength(1);
});

it("checks citation access when opened and never falls back to a revoked cached excerpt", async () => {
  const citation = {
    id: "source",
    title: "Reference",
    locator: "Page 1",
    excerpt: "Cached private excerpt",
  };
  let revoked = false;
  request.mockImplementation(async (path: string) => {
    if (path.endsWith("/messages"))
      return [
        {
          id: "answer",
          role: "assistant",
          content: "Answer",
          citations: [citation],
          created_at: "",
          run_id: null,
        },
      ];
    if (path === "/citations/source") {
      if (revoked) throw new Error("来源已不可访问");
      return { ...citation, excerpt: "Authorized current excerpt" };
    }
    return [];
  });
  render(
    <Chat
      conversation={{
        id: "conversation",
        agent_id: "agent",
        title: "Test",
        created_at: "",
      }}
      onChanged={() => {}}
    />,
  );
  const summary = await screen.findByText("1. Reference");
  expect(screen.queryByText("Cached private excerpt")).not.toBeInTheDocument();
  const details = summary.closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await screen.findByText("Authorized current excerpt");
  details.open = false;
  fireEvent(details, new Event("toggle"));
  revoked = true;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await screen.findByText("来源已不可访问");
  expect(screen.queryByText("Cached private excerpt")).not.toBeInTheDocument();
  expect(
    screen.queryByText("Authorized current excerpt"),
  ).not.toBeInTheDocument();
});
