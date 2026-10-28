import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import App from "./App";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));
vi.mock("./lib/api", () => ({
  api: request,
  setCsrfToken: vi.fn(),
  errorMessage: (value: Error) => value.message,
}));
afterEach(() => vi.clearAllMocks());

it("clears account-scoped history immediately when another member logs in", async () => {
  let secondAccount = false;
  const session = (id: string) => ({
    user: {
      id,
      email: `${id}@example.test`,
      display_name: id,
      role: "member",
      group_ids: [],
      active: true,
    },
    csrf_token: id,
  });
  request.mockImplementation(async (path: string) => {
    if (path === "/auth/me") return session("first");
    if (path === "/auth/logout") return undefined;
    if (path === "/auth/login") {
      secondAccount = true;
      return session("second");
    }
    if (secondAccount) return new Promise(() => {});
    if (path === "/agents")
      return [
        {
          id: "private-agent",
          name: "First account private agent",
          description: "",
          examples: [],
        },
      ];
    if (path === "/conversations")
      return [
        {
          id: "private-conversation",
          title: "First account private case",
          agent_id: "private-agent",
          created_at: "",
        },
      ];
    return [];
  });
  render(<App />);
  await screen.findByText("First account private case");
  fireEvent.click(screen.getByRole("button", { name: "退出登录" }));
  await screen.findByRole("heading", { name: "登录工作台" });
  fireEvent.change(screen.getByLabelText("邮箱"), {
    target: { value: "second@example.test" },
  });
  fireEvent.change(screen.getByLabelText("密码"), {
    target: { value: "second-account-password" },
  });
  fireEvent.click(screen.getByRole("button", { name: "进入工作台" }));
  await waitFor(() =>
    expect(
      screen.getByRole("heading", { name: /你好，second/ }),
    ).toBeInTheDocument(),
  );
  expect(
    screen.queryByText("First account private case"),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByText("First account private agent"),
  ).not.toBeInTheDocument();
});
