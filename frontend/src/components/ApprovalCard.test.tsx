import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApprovalCard } from "./ApprovalCard";

describe("write confirmation", () => {
  it("shows the actual action and prevents duplicate submits while pending", async () => {
    let finish!: () => void;
    const decision = vi.fn(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    render(
      <ApprovalCard
        approval={{
          id: "a1",
          title: "更新工单",
          summary: "将工单 123 关闭",
          parameters: { ticket_id: "123", status: "closed" },
          expires_at: "2099-01-01T00:00:00Z",
        }}
        onDecide={decision}
      />,
    );
    expect(screen.getByText("将工单 123 关闭")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "确认执行" });
    fireEvent.click(button);
    fireEvent.click(button);
    expect(decision).toHaveBeenCalledTimes(1);
    expect(decision).toHaveBeenCalledWith("a1", true);
    expect(button).toBeDisabled();
    finish();
    await waitFor(() =>
      expect(screen.getByText("已确认，正在处理")).toBeInTheDocument(),
    );
  });
  it("cannot approve an expired operation", () => {
    render(
      <ApprovalCard
        approval={{
          id: "a2",
          title: "更新工单",
          summary: "关闭",
          parameters: {},
          expires_at: "2000-01-01T00:00:00Z",
        }}
        onDecide={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: "确认执行" })).toBeDisabled();
    expect(screen.getByText("确认已过期")).toBeInTheDocument();
  });
});
