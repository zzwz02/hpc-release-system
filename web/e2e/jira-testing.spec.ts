/** Runs only against the isolated SQLite/site/app-server peers, never a live GPU. */
import { test, expect, type Page } from "@playwright/test";
const base = process.env.JIRA_TESTING_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated testing site");
const api = `${base}/api/jira-agent/testing`;
const rows = (page: Page) => page.getByRole("table", { name: "测试记录" }).locator("tbody tr");
const dialog = (page: Page) => page.getByRole("dialog", { name: "测试设置", exact: true });
const detail = (page: Page) => page.getByRole("region", { name: "测试详情", exact: true });
async function login(page: Page, username = "rm") {
  expect((await page.request.post(`${base}/api/login`, { data: { username, password: username } })).ok()).toBeTruthy();
}
async function save(page: Page, title: string, criteria = "完成分析并交付验证记录") {
  const response = await page.request.post(`${api}/cases`, { data: {
    title, criteria, input: { issue: { key: "TEST-1", summary: title, description: "原始输入，不含答案" },
      note: "复现并定位问题，交付验证记录", files: [] } } });
  expect(response.ok()).toBeTruthy();
  return response.json();
}
async function launch(page: Page) {
  await page.getByRole("button", { name: /^开始测试（/ }).click();
  await expect(page.getByRole("region", { name: "批次详情", exact: true })).toBeVisible();
  await expect(page.getByRole("complementary", { name: "测试用例" })).toBeVisible();
}
async function openRun(page: Page, index = 0) {
  const row = rows(page).nth(index);
  await expect(row).toBeVisible();
  if (await row.getByRole("button", { name: "展开", exact: true }).isVisible()) {
    await row.getByRole("button", { name: "展开", exact: true }).click();
  }
}

test("built-ins carry standards, have no historical runs and can be exported", async ({ page }) => {
  test.skip(process.env.JIRA_TESTING_BUILTINS !== "1", "requires --builtin-cases");
  await login(page);
  const options = await (await page.request.get(`${api}/cases`)).json();
  const builtins = options.cases.filter((item: { builtin_key?: string }) => item.builtin_key);
  expect(builtins).toHaveLength(3);
  await page.goto(`${base}/jira-agent?view=testing`);
  await expect(page.getByRole("button", { name: "开始测试（0）" })).toBeDisabled();
  await page.getByRole("checkbox", { name: "选择当前列表全部用例", exact: true }).check();
  await expect(page.getByRole("button", { name: "开始测试（3）" })).toBeEnabled();
  await page.getByRole("button", { name: "清空选择", exact: true }).click();
  for (const item of builtins) {
    const value = await (await page.request.get(`${api}/cases/${item.id}`)).json();
    expect(value.batches).toEqual([]);
    expect(value.versions[0].criteria.split("\n")).toHaveLength(3);
  }
  await page.locator(".test-case-button").filter({ hasText: builtins[0].title }).click();
  const input = page.getByRole("region", { name: "用例详情", exact: true });
  await expect(input.locator(".test-input-section")).not.toHaveAttribute("open");
  await input.locator(".test-case-input summary").first().click();
  await expect(input.getByRole("heading", { name: "通过标准" })).toBeVisible();
  const downloading = page.waitForEvent("download");
  await input.getByRole("button", { name: "导出用例", exact: true }).click();
  const path = await (await downloading).path();
  await page.getByLabel("上传用例包").setInputFiles(path!);
  await expect(page.getByRole("button", { name: "打开已有用例" })).toBeVisible();
  await expect(page.getByLabel("通过标准", { exact: true })).not.toBeEmpty();
});

test("batch selection, AI verdict, optional review and foldable process share one surface", async ({ page }) => {
  test.setTimeout(60000);
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await login(page);
  const first = await save(page, "批量甲");
  await save(page, "批量乙");
  await page.goto(`${base}/jira-agent?view=testing`);
  await page.getByLabel("选择 批量甲", { exact: true }).check();
  await page.getByLabel("选择 批量乙", { exact: true }).check();
  await page.getByLabel("筛选用例").fill("批量甲");
  await expect(page.getByText(/有 1 个被筛选隐藏/)).toBeVisible();
  const posting = page.waitForRequest((request) => request.method() === "POST" && request.url().endsWith("/suites"));
  await launch(page);
  expect((await posting).postDataJSON().case_ids).toHaveLength(2);
  await expect(rows(page)).toHaveCount(2, { timeout: 20000 });
  await expect(page.locator(".test-run-review")).toHaveText(["通过 · AI", "通过 · AI"], { timeout: 20000 });
  await expect(page.locator('.test-suite-meta')).toContainText("2 个用例 · 已完成");
  await expect(page.getByRole('table', { name: '批次记录' })).toHaveCount(0);
  await expect(detail(page)).toHaveCount(0);
  const reviewedTitle = await rows(page).first().locator('td').first().innerText();
  await openRun(page);
  await expect(detail(page).getByTestId("jira-agent-result")).toHaveCount(1);
  await expect(detail(page).locator('.test-result-evidence')).not.toHaveAttribute('open');
  await detail(page).locator('.test-result-evidence > summary').click();
  await expect(detail(page).locator('.test-result-evidence')).toContainText('PASS exit=0');
  await detail(page).locator('.test-result-evidence > summary').click();
  await expect(detail(page).locator('.test-evaluation')).not.toHaveAttribute('open');
  await detail(page).locator('.test-evaluation > summary').click();
  await expect(detail(page).getByRole('cell', { name: '完成分析并交付验证记录', exact: true })).toBeVisible();
  await detail(page).locator('.test-evaluation > summary').click();
  const order = await detail(page).evaluate((element) => [".test-review", ".test-evaluation", "[data-testid=jira-agent-result]", ".test-process"].map((selector) => element.querySelector(selector)!.getBoundingClientRect().top));
  expect(order).toEqual([...order].sort((a,b) => a-b));
  await detail(page).getByRole("button", { name: "人工评价", exact: true }).click();
  await detail(page).getByRole("combobox", { name: "人工评价", exact: true }).selectOption("partial");
  await detail(page).getByLabel("评价说明").fill("缺少原输入验证");
  await rows(page).first().getByRole("button", { name: "收起", exact: true }).click();
  await openRun(page);
  await expect(detail(page).getByLabel("评价说明")).toHaveValue("缺少原输入验证");
  await detail(page).getByRole("button", { name: "保存评价", exact: true }).click();
  await expect(rows(page).first()).toContainText("部分符合 · 人工");
  await expect(page.locator(".test-counts")).toContainText("部分符合 1");
  await expect(page.locator('.test-suite-meta')).toContainText("人工评价 1 个");
  await detail(page).getByRole('button', { name: '修改人工评价' }).click();
  await detail(page).getByRole('combobox', { name: '人工评价', exact: true }).selectOption('fail');
  await detail(page).getByLabel('评价说明').fill('重新检查发现结论没有证据');
  await detail(page).getByRole('button', { name: '保存评价' }).click();
  await expect(rows(page).first()).toContainText('不符合 · 人工');
  await expect(detail(page).getByRole('button', { name: /撤销|确认 AI/ })).toHaveCount(0);
  await detail(page).locator(".test-process > summary").click();
  await expect(detail(page).getByTestId("jira-agent-timeline")).toBeVisible();
  await openRun(page, 1);
  await expect(detail(page)).toHaveCount(1);
  await page.goto(`${base}/jira-agent?view=testing&tab=records&case=${first.id}`);
  await expect(detail(page)).toHaveCount(0);
  await expect(rows(page)).toHaveCount(1);
  await expect(page.locator('.test-sidebar-case.selected')).toContainText('批量甲');
  await expect(page.getByRole('region', { name: '用例详情' }).getByRole('heading', { name: '批量甲', exact: true })).toBeVisible();
  await openRun(page);
  await expect(page.locator(".test-process")).not.toHaveAttribute("open");
  await page.getByRole('button', { name: '测试汇总', exact: true }).click();
  await expect(page.getByRole('table', { name: '批次记录' })).toBeVisible();
  await expect(detail(page)).toHaveCount(0);
  await expect(page.getByRole('complementary', { name: '测试用例' })).toBeVisible();
  await page.locator('.test-case-button').filter({ hasText: '批量乙' }).click();
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page)).toContainText(reviewedTitle === '批量乙' ? '不符合 · 人工' : '通过 · AI');
  await expect(detail(page)).toHaveCount(0);
  const sidebar = await page.locator('.test-sidebar').boundingBox();
  const main = await page.locator('.test-main').boundingBox();
  expect(sidebar!.x + sidebar!.width).toBeLessThan(main!.x);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
  await expect(page.getByRole('button', { name: '开始测试（0）' })).toBeVisible();
  expect(errors).toEqual([]);
});

test("criteria editor saves separate review data and import differences need confirmation", async ({ page }) => {
  await login(page);
  const saved = await save(page, "标准编辑", "原有标准");
  await page.goto(`${base}/jira-agent?view=testing&case=${saved.id}`);
  await page.getByRole("button", { name: "修改用例" }).click();
  await page.getByLabel("通过标准", { exact: true }).fill("新标准\n交付记录可验证");
  await page.getByText("参考结果（可选，仅供评价）", { exact: true }).click();
  await page.getByLabel("参考结果", { exact: true }).fill("只供评价的已核实结论");
  await page.getByRole("checkbox", { name: /已核对输入完整/ }).check();
  const savedVersion = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/versions"));
  await page.getByRole("button", { name: "保存用例", exact: true }).click();
  expect((await savedVersion).ok()).toBeTruthy();
  const updated = await (await page.request.get(`${api}/cases/${saved.id}`)).json();
  expect(updated.versions).toHaveLength(2);
  expect(updated.versions[0].input).toEqual(saved.versions[0].input);
  const old = await page.request.get(`${api}/cases/${saved.id}/versions/${saved.versions[0].id}/package`);
  await expect(page.getByRole("region", { name: "用例详情" })).toBeVisible();
  await page.getByLabel("上传用例包").setInputFiles({ name: "old.zip", mimeType: "application/zip", buffer: await old.body() });
  await expect(page.getByText("通过标准或参考结果不同。", { exact: true })).toBeVisible();
  await page.getByRole("checkbox", { name: /已核对输入完整/ }).check();
  await expect(page.getByRole("button", { name: "保存用例", exact: true })).toBeDisabled();
  await page.getByRole("checkbox", { name: "将导入内容保存为该用例的新版本" }).check();
  const imported = page.waitForResponse((response) => response.request().method() === "POST" && response.url().endsWith("/cases"));
  await page.getByRole("button", { name: "保存用例", exact: true }).click();
  expect((await imported).ok()).toBeTruthy();
  await expect(page.getByRole("region", { name: "用例详情" })).toBeVisible();
  expect((await (await page.request.get(`${api}/cases/${saved.id}`)).json()).versions).toHaveLength(3);
});

test("missing criteria skip AI grading but allow independent manual evaluation", async ({ page }) => {
  await login(page);
  await save(page, "未设置标准样例", "");
  await page.goto(`${base}/jira-agent?view=testing`);
  await page.getByLabel('选择 未设置标准样例', { exact: true }).check();
  await launch(page);
  await expect(page.locator(".test-run-review")).toHaveText("未设置通过标准", { timeout: 20000 });
  await openRun(page);
  await detail(page).getByRole("button", { name: "人工评价", exact: true }).click();
  await detail(page).getByRole('combobox', { name: '人工评价', exact: true }).selectOption('pass');
  await detail(page).getByLabel('评价说明').fill('人工检查测试输入、执行记录和交付，满足要求');
  await detail(page).getByRole('button', { name: '保存评价' }).click();
  await expect(rows(page).first()).toContainText('通过 · 人工');
});

test("stop remaining keeps the current run, supports cancellation and preserves collapsed state", async ({ page }) => {
  await login(page);
  await page.request.post(`${base}/api/_testing/plan?plan=hold`);
  await save(page, "停止甲");
  await save(page, "停止乙");
  await page.goto(`${base}/jira-agent?view=testing`);
  await page.getByLabel("选择 停止甲", { exact: true }).check();
  await page.getByLabel("选择 停止乙", { exact: true }).check();
  await launch(page);
  await expect(page.locator(".test-run-status")).toHaveText(["执行中", "排队中"], { timeout: 20000 });
  await page.getByRole("button", { name: "停止后续测试" }).click();
  await expect(page.locator(".test-run-status")).toHaveText(["执行中", "已取消"]);
  await openRun(page);
  await rows(page).first().getByRole("button", { name: "收起", exact: true }).click();
  await page.reload();
  await expect(detail(page)).toHaveCount(0);
  await openRun(page);
  await detail(page).getByRole("button", { name: "取消测试", exact: true }).click();
  await expect(page.locator(".test-run-status")).toHaveText(["已取消", "已取消"]);
  await expect(page.getByRole("button", { name: "再次运行本批", exact: true })).toBeVisible();
});

test("failed launch retains options and Owner cannot access tests", async ({ page }) => {
  await login(page);
  await save(page, "设置检查");
  await page.goto(`${base}/jira-agent?view=testing`);
  await page.getByLabel('选择 设置检查', { exact: true }).check();
  await page.getByRole('button', { name: '测试设置' }).click();
  await dialog(page).getByLabel("模型", { exact: true }).fill("x".repeat(101));
  await dialog(page).getByRole('button', { name: '完成', exact: true }).click();
  await page.getByRole('button', { name: '开始测试（1）' }).click();
  await expect(page.getByRole("alert")).toBeVisible();
  await page.getByRole('button', { name: '测试设置' }).click();
  await expect(dialog(page).getByLabel("模型", { exact: true })).toHaveValue("x".repeat(101));
  await page.keyboard.press("Escape");
  await login(page, "owner_test");
  expect((await page.request.get(`${api}/cases`)).status()).toBe(403);
  expect((await page.request.get(`${api}/suites`)).status()).toBe(403);
});
