import { fireEvent, render, screen } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { Sources } from "./Sources";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("../lib/api", () => ({
  api: request,
  errorMessage: (value: Error) => value.message,
}));

it("keeps same-file chunks in one collapsed list and checks each opened citation", async () => {
  const citations = Array.from({ length: 12 }, (_, index) => ({
    id: `source-${index}`,
    title: "reference.md",
    locator: "lines 1-1000",
    excerpt: `Cached chunk ${index}`,
  }));
  let revoked = false;
  request.mockImplementation(async (path: string) => {
    if (revoked) throw new Error("来源已不可访问");
    return {
      ...citations.find((citation) => path.endsWith(`/${citation.id}`)),
      excerpt: "Currently authorized chunk",
    };
  });
  render(<Sources citations={citations} />);
  const group = screen.getByText("参考资料（12 个片段）").closest("details")!;
  expect(group.open).toBe(false);
  expect(screen.queryByText("1. reference.md")).not.toBeInTheDocument();
  expect(request).not.toHaveBeenCalled();
  group.open = true;
  fireEvent(group, new Event("toggle"));
  const reference = screen.getByText("2. reference.md");
  expect(reference).toBeVisible();
  const details = reference.closest("details")!;
  details.open = true;
  fireEvent(details, new Event("toggle"));
  await screen.findByText("Currently authorized chunk");
  expect(request).toHaveBeenCalledWith("/citations/source-1", expect.anything());
  expect(screen.queryByText("Cached chunk 1")).not.toBeInTheDocument();
  group.open = false;
  fireEvent(group, new Event("toggle"));
  expect(screen.queryByText("Currently authorized chunk")).not.toBeInTheDocument();
  revoked = true;
  group.open = true;
  fireEvent(group, new Event("toggle"));
  const reopened = screen.getByText("2. reference.md").closest("details")!;
  reopened.open = true;
  fireEvent(reopened, new Event("toggle"));
  await screen.findByText("来源已不可访问");
  expect(screen.queryByText("Currently authorized chunk")).not.toBeInTheDocument();
});
