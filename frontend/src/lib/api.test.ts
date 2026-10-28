import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, setCsrfToken, ApiError } from "./api";

describe("authenticated requests", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    setCsrfToken("csrf-example");
  });
  it("includes the CSRF token and session cookie for a write", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response('{"id":"run-1"}'));
    await api("/conversations/one/runs", {
      method: "POST",
      body: { message: "hello", attachment_ids: [] },
    });
    const request = fetch.mock.calls[0][1]!;
    expect(new Headers(request.headers).get("X-CSRF-Token")).toBe(
      "csrf-example",
    );
    expect(request.credentials).toBe("same-origin");
    expect(JSON.parse(request.body as string)).toEqual({
      message: "hello",
      attachment_ids: [],
    });
  });
  it("preserves multipart boundaries for uploads", async () => {
    const fetch = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(new Response("{}"));
    const data = new FormData();
    data.set("file", new File(["hello"], "note.txt"));
    await api("/files", { method: "POST", body: data });
    expect(
      new Headers(fetch.mock.calls[0][1]!.headers).has("Content-Type"),
    ).toBe(false);
    expect(fetch.mock.calls[0][1]!.body).toBe(data);
  });
  it("turns a server error into a usable typed error", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response('{"detail":"权限不足"}', { status: 403 }),
    );
    await expect(api("/admin/users")).rejects.toMatchObject({
      message: "权限不足",
      status: 403,
    });
    expect(new ApiError(401, "请登录").status).toBe(401);
  });
  it("handles an empty 204 response", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      new Response(null, { status: 204 }),
    );
    expect(await api("/auth/logout", { method: "POST" })).toBeUndefined();
  });
});
