import { test, expect } from "@playwright/test";

const base = process.env.MCP_SETUP_E2E_URL ?? "";
const mcpBase = base.replace(/^https:/, "http:");
test.skip(!/^https?:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_mcp_setup_site and isolated Vite");
test.use({ ignoreHTTPSErrors: true });

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
    const config = section(name).locator("details");
    await config.locator("summary").click();
    await expect(config.locator("pre")).toContainText(`${mcpBase}/mcp`);
  }
  await expect(section("其他 harness").locator("pre")).toContainText(`URL: ${mcpBase}/mcp`);
  await expect(page.getByLabel("MCP 连接协议")).toHaveCount(0);
  await expect(page).toHaveURL(base + "/mcp-console");
  expect(errors).toEqual([]);
});
