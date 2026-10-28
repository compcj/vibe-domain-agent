import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { KnowledgePanel } from "./KnowledgePanel";
import { api } from "../../lib/api";

vi.mock("../../lib/api", () => ({
  api: vi.fn(),
  errorMessage: (failure: Error) => failure.message,
}));
HTMLDialogElement.prototype.showModal = function () {
  this.setAttribute("open", "");
};
HTMLDialogElement.prototype.close = function () {
  this.removeAttribute("open");
};

describe("partial document upload", () => {
  it.each(["rejected", "oversize"])(
    "refreshes accepted files and preserves a later %s error",
    async (failure) => {
      let accepted = false;
      vi.mocked(api).mockImplementation(async (path, options) => {
        if (path === "/admin/knowledge-bases")
          return [{ id: "kb", name: "手册" }];
        if (options?.method === "POST") {
          if (accepted) throw new Error("第二个文件上传失败");
          accepted = true;
          return {};
        }
        return accepted
          ? [{ id: "doc", name: "first.txt", status: "queued" }]
          : [];
      });
      const { container } = render(<KnowledgePanel />);
      fireEvent.click(
        await screen.findByRole("button", { name: "管理文档与检索" }),
      );
      await screen.findByText("还没有文档。");
      const second = new File(["second"], "second.txt");
      if (failure === "oversize")
        Object.defineProperty(second, "size", { value: 21 * 1024 * 1024 });
      fireEvent.change(container.querySelector('input[type="file"]')!, {
        target: { files: [new File(["first"], "first.txt"), second] },
      });
      await screen.findByText("first.txt");
      await waitFor(() =>
        expect(screen.getByRole("alert")).toHaveTextContent(
          failure === "oversize"
            ? "second.txt 超过 20 MB"
            : "第二个文件上传失败",
        ),
      );
    },
  );
});
