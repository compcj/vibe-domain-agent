import { expect, test, type Page } from "@playwright/test";

const agents = [
  {
    id: "knowledge",
    name: "领域知识助手",
    description:
      "在组织知识中找到线索，理解制度、流程与业务背景，让每个问题有据可循。",
    examples: ["新项目立项需要准备哪些材料？"],
  },
  {
    id: "documents",
    name: "文档分析助手",
    description: "梳理长文档、比较不同版本，提炼资料中的关键结论与下一步行动。",
    examples: ["帮我对比两份方案的关键差异"],
  },
  {
    id: "business",
    name: "业务操作助手",
    description: "查询业务进展，协助完成日常工作。需要确认的操作由你决定。",
    examples: ["查询本周需要跟进的事项"],
  },
];

async function fixture(page: Page, role = "member") {
  let loggedIn = false;
  let created = false;
  let approved = false;
  const session = {
    user: {
      id: "user",
      email: "lin@example.test",
      display_name: "林悦",
      role,
      group_ids: ["group"],
      active: true,
    },
    csrf_token: "csrf-fixture",
  };
  const conversation = {
    id: "conversation",
    agent_id: "knowledge",
    title: "领域知识助手",
    created_at: new Date().toISOString(),
  };
  await page.route("**/api/v1/**", async (route) => {
    const request = route.request();
    const path = new URL(request.url()).pathname.slice("/api/v1".length);
    let data: unknown = [];
    let status = 200;
    if (path === "/auth/me") {
      status = loggedIn ? 200 : 401;
      data = session;
    } else if (path === "/auth/login") {
      loggedIn = true;
      data = session;
    } else if (path === "/agents") data = agents;
    else if (path === "/conversations") {
      if (request.method() === "POST") {
        created = true;
        data = conversation;
      } else data = created ? [conversation] : [];
    } else if (path.endsWith("/runs")) {
      data = { id: "run", status: "queued" };
      status = 202;
    } else if (path === "/runs/run") {
      data = {
        id: "run",
        status: approved ? "completed" : "awaiting_confirmation",
        approvals: approved
          ? []
          : [
              {
                id: "approval",
                title: "更新项目状态",
                summary: "即将更新项目 PRJ-014",
                parameters: { project: "PRJ-014", status: "已完成" },
                expires_at: new Date(Date.now() + 600000).toISOString(),
              },
            ],
      };
    } else if (path === "/approvals/approval/decision") {
      approved = true;
      data = { status: "approved" };
    } else if (path.endsWith("/events")) {
      const after = Number(new URL(request.url()).searchParams.get("after"));
      const events = approved
        ? 'id: 3\nevent: delta\ndata: {"text":"业务记录已更新。"}\n\nid: 4\nevent: done\ndata: {"status":"completed"}\n\n'
        : after
          ? ": keepalive\n\n"
          : `id: 1\nevent: status\ndata: {"status":"awaiting_confirmation","message":"请确认业务操作"}\n\nid: 2\nevent: approval\ndata: ${JSON.stringify({ id: "approval", title: "更新项目状态", summary: "即将更新项目 PRJ-014", parameters: { project: "PRJ-014", status: "已完成" }, expires_at: new Date(Date.now() + 600000).toISOString() })}\n\n`;
      await route.fulfill({
        status: 200,
        contentType: "text/event-stream",
        body: events,
      });
      return;
    } else if (path.endsWith("/messages"))
      data = approved
        ? [
            {
              id: "message",
              role: "assistant",
              content: "业务记录已更新。",
              citations: [],
              run_id: "run",
              created_at: new Date().toISOString(),
            },
          ]
        : [];
    await route.fulfill({ status, json: data });
  });
  await page.goto("/");
  await page.getByLabel("邮箱", { exact: true }).fill("lin@example.test");
  await page.getByLabel("密码", { exact: true }).fill("example-password");
  await page.getByRole("button", { name: "进入工作台" }).click();
  await expect(page.getByRole("heading", { name: /你好，林悦/ })).toBeVisible();
}

test("member finds an authorized assistant and confirms a specific business operation", async ({
  page,
}) => {
  await fixture(page);
  await expect(page.getByRole("button", { name: /组织管理/ })).toHaveCount(0);
  await page.getByLabel("搜索助手").fill("领域知识");
  await expect(page.getByRole("button", { name: /文档分析助手/ })).toHaveCount(
    0,
  );
  await page.getByRole("button", { name: /领域知识助手/ }).click();
  const requestPromise = page.waitForRequest(
    (request) =>
      request.url().endsWith("/conversations/conversation/runs") &&
      request.method() === "POST",
  );
  await page.getByLabel("输入消息").fill("将项目标记完成");
  await page.getByRole("button", { name: "发送消息" }).click();
  expect((await requestPromise).postDataJSON()).toEqual({
    message: "将项目标记完成",
    attachment_ids: [],
  });
  await expect(page.getByText("PRJ-014", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /确认执行/ }).click();
  await expect(
    page.getByText("业务记录已更新。", { exact: true }),
  ).toBeVisible();
});

test("portal is usable at desktop and mobile widths", async ({ page }) => {
  await fixture(page);
  await page.screenshot({
    path: "test-results/catalog-desktop.png",
    fullPage: true,
    animations: "disabled",
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole("button", { name: "打开导航" })).toBeVisible();
  await page.getByRole("button", { name: "打开导航" }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.getByRole("button", { name: "关闭导航", exact: true }).click();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(
    390,
  );
  await page.screenshot({
    path: "test-results/catalog-mobile.png",
    fullPage: true,
    animations: "disabled",
  });
});
