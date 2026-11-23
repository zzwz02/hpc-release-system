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

test("RM grants and revokes JIRA writes for one Guest from MCP permissions", async ({ page, browser }) => {
  const username = "mcp_guest_test";
  const path = base + "/api/mcp/permissions";
  expect((await page.request.post(base + "/api/login", {
    data: { username: "mcp_rm_test", password: "test-password" },
  })).ok()).toBeTruthy();
  await page.goto(base + "/mcp-console");
  await page.getByRole("button", { name: "权限配置", exact: true }).click();
  const search = page.getByRole("searchbox", { name: "搜索 Guest 账号" });
  await search.fill("不存在的账号");
  await expect(page.getByText("没有匹配的 Guest 账号")).toBeVisible();
  await search.fill("MCP Guest test");
  const grant = page.getByRole("checkbox", { name: `允许 ${username} 写入 JIRA` });
  const grantAll = page.getByRole("checkbox", { name: `允许 ${username} 写入所有 JIRA` });
  await expect(grant).not.toBeChecked();
  await expect(grantAll).not.toBeChecked();
  const save = page.getByRole("button", { name: "保存权限" });
  await grant.check();
  await page.getByRole("button", { name: "放弃修改并重读" }).click();
  await expect(grant).not.toBeChecked();
  await expect(save).toBeDisabled();

  async function saveGrant(enabled: boolean, all = false) {
    await (all ? grantAll : grant).setChecked(enabled);
    const responsePromise = page.waitForResponse(response => response.url() === path && response.request().method() === "POST");
    await save.click();
    const response = await responsePromise;
    expect(response.ok()).toBeTruthy();
    const field = all ? "jira_write_all" : "jira_write";
    expect(response.request().postDataJSON()[`guest_${field}`]).toEqual({ [username]: enabled });
    await expect(save).toBeDisabled();
    expect(await (await page.request.get(path)).json()).toMatchObject({
      guests: expect.arrayContaining([expect.objectContaining({ username, [field]: enabled })]),
    });
  }

  await saveGrant(true);
  const guestPage = await browser.newPage();
  try {
    expect((await guestPage.request.post(base + "/api/login", {
      data: { username, password: "test-password" },
    })).ok()).toBeTruthy();
    await guestPage.goto(base + "/mcp-console");
    await expect(guestPage.getByRole("button", { name: "权限配置" })).toHaveCount(0);
    const tools = guestPage.locator("tbody");
    await expect(tools.getByText("jira_add_comment", { exact: true })).toBeVisible();
    await expect(tools.getByText("jira_upload_attachment", { exact: true })).toBeVisible();
    await saveGrant(true, true);
    await saveGrant(false);
    await guestPage.reload();
    await expect(guestPage.getByText("当前账号可给任意项目的 JIRA 工单评论和上传附件。")).toBeVisible();
    await expect(tools.getByText("jira_add_comment", { exact: true })).toBeVisible();
    await saveGrant(false, true);
    await guestPage.reload();
    await expect(tools.getByText("jira_pull_issue", { exact: true })).toBeVisible();
    await expect(tools.getByText("jira_add_comment", { exact: true })).toHaveCount(0);
    await expect(tools.getByText("jira_upload_attachment", { exact: true })).toHaveCount(0);
  } finally {
    await guestPage.close();
  }
});

test("MCP setup offers Bash and PowerShell installers plus direct manual configuration", async ({ page, context }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  expect((await page.request.post(base + "/api/login", {
    data: { username: "mcp_setup_test", password: "test-password" },
  })).ok()).toBeTruthy();
  await page.goto(base + "/mcp-console");
  const panel = page.locator(".panel").filter({ has: page.getByRole("heading", { name: "手动配置（CLI 与 VS Code 扩展通用）" }) });
  for (const [platform, command] of [
    ["Linux / macOS / WSL（Bash）", `bash <(curl -fsSL '${mcpBase}/api/mcp/setup.sh')`],
    ["Windows（PowerShell）", `irm '${mcpBase}/api/mcp/setup.ps1' | iex`],
  ]) {
    const installer = page.getByRole("group", { name: platform, exact: true });
    await expect(installer.locator("pre")).toHaveText(command);
    await installer.getByRole("button", { name: "复制", exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(command);
  }
  const powershellScript = await page.request.get(base + "/api/mcp/setup.ps1");
  expect(powershellScript.ok()).toBeTruthy();
  expect(await powershellScript.text()).toContain("#Requires -Version 5.1");
  const section = (name: string) => panel.locator("details").filter({ has: page.locator(":scope > summary").filter({ hasText: new RegExp(`^${name}$`) }) });
  const config = (name: string) => section(name).locator(":scope > .mcp-code");
  for (const name of ["Claude Code", "Codex", "其他 harness"]) {
    await expect(section(name)).toHaveJSProperty("open", false);
  }
  for (const name of ["Claude Code", "Codex"]) {
    const details = section(name);
    await details.locator(":scope > summary").click();
    const snippet = config(name).locator("pre");
    await expect(snippet).toBeVisible();
    const text = await snippet.innerText();
    expect(text).toContain(`${mcpBase}/mcp`);
    expect(text).toContain("Bearer <你的 token>");
    expect(text).not.toContain("different.example");
    await config(name).getByRole("button", { name: "复制", exact: true }).click();
    expect(await page.evaluate(() => navigator.clipboard.readText())).toBe(text);
  }
  expect(JSON.parse(await config("Claude Code").locator("pre").innerText())).toEqual({
    mcpServers: {
      "release-system": {
        type: "http",
        url: `${mcpBase}/mcp`,
        headers: { Authorization: "Bearer <你的 token>" },
      },
    },
  });
  await expect(config("Codex").locator("pre")).toContainText("[mcp_servers.release-system]");
  await expect(panel.locator("pre").filter({ hasText: /setup\.sh|setup\.ps1|MCP_CLIENTS=/ })).toHaveCount(0);
  await expect(panel.getByText("查看配置文件内容", { exact: true })).toHaveCount(0);
  await section("其他 harness").locator(":scope > summary").click();
  await expect(section("其他 harness")).toContainText("各客户端配置格式不同");
  const response = page.waitForResponse(r => r.url().endsWith("/api/mcp/tokens") && r.request().method() === "POST");
  await page.getByRole("button", { name: "生成 token", exact: true }).click();
  const token = (await (await response).json()).token.token;
  for (const name of ["Claude Code", "Codex"]) {
    await expect(config(name).locator("pre")).toContainText(`Bearer ${token}`);
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
