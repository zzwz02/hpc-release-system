import { test, expect } from "@playwright/test";

const base = process.env.MCP_SETUP_E2E_URL ?? "";
const mcpBase = base.replace(/^https:/, "http:");
// Vite forwards /api only; set the isolated FastAPI URL for direct MCP RPC.
const rpcBase = process.env.MCP_SETUP_API_URL ?? base;
test.skip(!/^https?:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_mcp_setup_site and isolated Vite");
test.skip(!/^https?:\/\/127\.0\.0\.1:\d+$/.test(rpcBase), "MCP RPC must target the isolated local site");
test.use({ ignoreHTTPSErrors: true });

test("Guest MCP exposes only four tools and the MC3 project scope", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", {
    data: { username: "mcp_guest_test", password: "test-password" },
  })).ok()).toBeTruthy();
  await page.goto(base + "/mcp-console");
  const tools = page.locator(".panel").filter({ has: page.getByRole("heading", { name: "可用工具", exact: true }) });
  await expect(tools).toContainText("当前账号仅可读取 MC3 项目的 JIRA");
  const names = ["jira_search", "jira_pull_issue", "jira_read_attachment", "skill_load"];
  for (const name of names) await expect(tools.locator("tbody").getByText(name, { exact: true })).toBeVisible();
  await expect(tools.locator("tbody tr")).toHaveCount(4);
  await expect(tools.locator("tbody").getByText("jira_add_comment", { exact: true })).toHaveCount(0);
  await expect(tools.locator("tbody").getByText("jenkins_search_failures", { exact: true })).toHaveCount(0);
  await expect(tools).not.toContainText("把这个结论评论到");
  const info = await (await page.request.get(base + "/api/mcp/info")).json();
  expect(info.jira_read_projects).toEqual(["MC3"]);
  expect(info.tools.map((tool: { name: string }) => tool.name).sort()).toEqual([...names].sort());
  const created = await (await page.request.post(base + "/api/mcp/tokens", { data: { name: "browser-guest" } })).json();
  const response = await page.request.post(rpcBase + "/mcp", {
    headers: { Authorization: `Bearer ${created.token.token}` },
    data: { jsonrpc: "2.0", id: 1, method: "tools/call", params: { name: "jira_add_comment", arguments: { issue_key: "MC3-1", body: "forbidden" } } },
  });
  expect(response.status()).toBe(200);
  expect((await response.json()).error.code).toBe(-32602);
  await page.getByRole("button", { name: "使用统计", exact: true }).click();
  await expect(page.getByText("仅显示你本人的使用记录", { exact: true })).toBeVisible();
});

test("MCP client setup: collapsed sections and copyable single-line commands", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  expect((await page.request.post(base + "/api/login", {
    data: { username: "mcp_setup_test", password: "test-password" },
  })).ok()).toBeTruthy();
  await page.goto(base + "/mcp-console");
  const panel = page.locator(".panel").filter({ has: page.getByRole("heading", { name: "手动配置（CLI 与 VS Code 扩展通用）" }) });
  await expect(page.locator(".panel").filter({ has: page.getByRole("heading", { name: "一键配置（推荐，无需登录网站）" }) }).locator(".mcp-code pre"))
    .toHaveText(`bash <(curl -fsSL '${mcpBase}/api/mcp/setup.sh')`);
  const section = (name: string) => panel.locator("details").filter({ has: page.locator(":scope > summary").filter({ hasText: new RegExp(`^${name}$`) }) });
  for (const name of ["Claude Code", "Codex", "其他 harness"]) {
    await expect(section(name)).toHaveJSProperty("open", false);
  }
  for (const [name, client] of [["Claude Code", "claude"], ["Codex", "codex"]]) {
    const details = section(name);
    await details.locator(":scope > summary").click();
    const command = details.locator(":scope > .mcp-code pre");
    await expect(command).toContainText(`MCP_CLIENTS=${client}`);
    await expect(command).toContainText(" use-token");
    const text = await command.innerText();
    expect(text).not.toContain("\n");
    expect(text).toContain(`${mcpBase}/api/mcp/setup.sh`);
    expect(text).not.toContain("different.example");
    expect(text).not.toContain("MCP_TOKEN=");
    await details.locator(":scope > .mcp-code").getByRole("button", { name: "复制", exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  }
  await section("其他 harness").locator(":scope > summary").click();
  await expect(section("其他 harness")).toContainText("各客户端配置格式不同");
  const response = page.waitForResponse(r => r.url().endsWith("/api/mcp/tokens") && r.request().method() === "POST");
  await page.getByRole("button", { name: "生成 token", exact: true }).click();
  const token = (await (await response).json()).token.token;
  for (const name of ["Claude Code", "Codex"]) {
    await expect(section(name).locator(":scope > .mcp-code pre")).toContainText(`MCP_TOKEN='${token}'`);
  }
  for (const name of ["Claude Code", "Codex"]) {
    const config = section(name).locator("details").first();
    await config.locator("summary").click();
    await expect(config.locator("pre")).toContainText(`${mcpBase}/mcp`);
  }
  const permissions = section("Codex").locator(".mcp-codex-permissions");
  await permissions.locator("summary").click();
  await expect(permissions.locator("pre")).toContainText('sandbox_mode = "danger-full-access"');
  await expect(permissions.locator("pre")).toContainText('mcp_elicitations = true');
  await expect(permissions).toContainText("文件顶层");
  await permissions.getByRole("button", { name: "复制", exact: true }).click();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(await permissions.locator("pre").innerText());
  await expect(section("其他 harness").locator("pre")).toContainText(`URL: ${mcpBase}/mcp`);
  await expect(page.getByLabel("MCP 连接协议")).toHaveCount(0);
  await expect(page).toHaveURL(base + "/mcp-console");
  expect(errors).toEqual([]);
});
