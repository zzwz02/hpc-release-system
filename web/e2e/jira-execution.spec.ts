/** Real HTTP/SQLite/protocol-peer checks; requires the isolated test site through Vite. */
import { test, expect } from "@playwright/test";

const base = process.env.JIRA_EXECUTION_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated execution site");

test("multi-host handover requires acknowledgement and records both machines", async ({ page }) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  const targets = ["tester@node-a", "tester@node-b"];
  await page.request.post(`${base}/api/_testing/execution`, { data: { targets, unreadable: [targets[1]] } });
  await page.goto(`${base}/jira-agent?issue=MC3-901`);
  await expect(page.getByLabel("模型", { exact: true })).toBeEnabled();
  await page.getByLabel("模型", { exact: true }).selectOption("other-model");
  await page.getByLabel("effort", { exact: true }).selectOption("high");
  await page.getByLabel(/自填 user@host/).check();
  await expect(page.getByText("批量粘贴机器", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("机器使用方式", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("共享数据依赖", { exact: true })).toHaveCount(0);
  await page.getByLabel("自填机器", { exact: true }).fill(targets[0]);
  await page.getByRole("button", { name: "添加机器", exact: true }).click();
  await page.getByLabel("自填机器 2", { exact: true }).fill(targets[1]);
  await page.getByLabel("机器类型 1").fill("C500");
  await page.getByLabel("机器类型 2").fill("C600");
  const submissions: object[] = [];
  page.on("request", request => { if (request.method() === "POST" && request.url().endsWith("/api/jira-agent/conversations")) submissions.push(request.postDataJSON()); });
  await page.getByRole("button", { name: "交给 agent", exact: true }).click();
  await page.getByRole("button", { name: "输入密码并检查", exact: true }).click();
  for (const target of targets) {
    const terminal = page.getByRole("dialog", { name: "机器登录授权", exact: true });
    await expect(terminal.getByRole("heading")).toHaveText(`登录授权：${target}`);
    await terminal.getByRole("checkbox").check();
    await terminal.getByRole("button", { name: "开始登录", exact: true }).click();
    await terminal.getByLabel("机器登录密码", { exact: true }).fill("s3cret");
    await terminal.getByRole("button", { name: "发送密码", exact: true }).click();
  }
  const preflight = page.getByRole("dialog", { name: "执行环境检查", exact: true });
  await expect(preflight.getByText("执行账号无法读取共享数据", { exact: true })).toBeVisible();
  expect(submissions).toHaveLength(0);
  await page.screenshot({ path: "/tmp/jira-execution-preflight.png", fullPage: true });
  const posted = page.waitForResponse(response => response.url().endsWith("/api/jira-agent/conversations") && response.request().method() === "POST");
  await preflight.getByRole("button", { name: "继续可执行工作", exact: true }).click();
  const response = await posted;
  expect(response.ok()).toBeTruthy();
  const conversation = (await response.json()).conversation;
  expect(conversation.model_provider).toBe("openai");
  expect(conversation.latest_turn).toMatchObject({ model: "other-model", effort: "high" });
  expect(conversation.execution.targets.map((item: { target: string }) => item.target)).toEqual(targets);
  expect(conversation.execution.selection_policy).toBe("as_needed");
  expect(conversation.execution.dataset_requirement).toBe("auto");
  expect(submissions).toHaveLength(1);
  expect(submissions[0]).toMatchObject({ acknowledged_issue_ids: ["tester@node-b:dataset"] });
  await expect.poll(async () => (await (await page.request.get(`${base}/api/jira-agent/conversations/${conversation.id}`)).json()).conversation.machines_used).toEqual(targets);
  expect(errors).toEqual([]);
});

test("auto test suite submits directly while custom machines still require authorization", async ({ page }) => {
  test.setTimeout(60000);
  await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } });
  await page.request.post(`${base}/api/_testing/execution`, { data: {} });
  const api = `${base}/api/jira-agent/testing`;
  const created = await page.request.post(`${api}/cases`, { data: { title: "执行环境浏览器验证", input: { issue: { key: "TEST-91", summary: "多机配置", description: "原始输入" }, note: "验证输入", files: [] } } });
  expect(created.ok()).toBeTruthy();
  const saved = await created.json();
  const raw = await page.request.post(`${api}/suites`, { data: { case_ids: [saved.id], agent_group: "HPC", model: "test-default", machine: "u@unapproved", request_id: "without-preflight" } });
  expect(raw.status()).toBe(409);
  await page.goto(`${base}/jira-agent?view=testing`);
  await expect(page.getByLabel("共享数据依赖", { exact: true })).toHaveCount(0);
  await page.getByLabel("选择 执行环境浏览器验证", { exact: true }).check();
  const posted = page.waitForResponse(response => response.url().endsWith("/suites") && response.request().method() === "POST");
  await page.getByRole("button", { name: "开始测试（1）", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "执行环境检查", exact: true })).toHaveCount(0);
  const response = await posted;
  expect(response.ok()).toBeTruthy();
  const suite = await response.json();
  expect(response.request().postDataJSON().preflight_id).toBe("");
  await expect.poll(async () => {
    const current = await (await page.request.get(`${api}/suites/${suite.id}`)).json();
    return current.entries[0].batch?.config.execution.mode;
  }).toBe("auto");
});
