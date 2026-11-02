/** Real attachment selection and submission against the isolated test site. */
import { test, expect } from "@playwright/test";

const base = process.env.JIRA_TESTING_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires isolated testing site");

for (const followup of [false, true]) {
  test(`attachment validation and retry: ${followup ? "followup" : "handover"}`, async ({ page }, info) => {
    const errors: string[] = [];
    page.on("pageerror", error => errors.push(error.message));
    expect((await page.request.post(base + "/api/login", { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
    const key = followup ? "MC3-91002" : "MC3-91001";
    const initial = followup ? await page.request.post(base + "/api/jira-agent/conversations", {
      data: { issue_key: key, request_id: crypto.randomUUID(), post_comment: false },
    }) : null;
    if (initial) expect(initial.ok()).toBeTruthy();
    const id = initial ? (await initial.json()).conversation.id : "";
    if (id) await expect.poll(async () => {
      const response = await page.request.get(base + `/api/jira-agent/conversations/${id}`);
      return (await response.json()).conversation.state;
    }).toBe("waiting_review");
    await page.goto(base + `/jira-agent?issue=${key}${id ? "&conversation=" + id : ""}`);
    const composer = page.locator(".jira-agent-composer");
    const input = page.getByLabel("附加文件");
    const text = page.getByLabel(followup ? "补充信息" : "交单说明");
    const submit = page.getByRole("button", { name: followup ? "发送" : "交给 agent", exact: true });
    await text.fill("保留我的补充说明");
    await expect(submit).toBeEnabled();
    await expect(composer).toContainText("单个文件不超过 20 MB，每次最多 10 个");
    await input.setInputFiles([
      { name: "small.log", mimeType: "text/plain", buffer: Buffer.from("log") },
      { name: "large.log", mimeType: "text/plain", buffer: Buffer.alloc(20 * 1024 * 1024 + 1) },
    ]);
    await expect(composer.getByRole("alert")).toContainText("large.log");
    await expect(submit).toBeDisabled();
    await composer.screenshot({ path: info.outputPath("oversized.png") });
    await page.setViewportSize({ width: 390, height: 844 });
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBeTruthy();
    await composer.screenshot({ path: info.outputPath("oversized-mobile.png") });
    await page.setViewportSize({ width: 1440, height: 1000 });
    await input.setInputFiles(Array.from({ length: 11 }, (_, i) => ({ name: `${i}.log`, mimeType: "text/plain", buffer: Buffer.from("log") })));
    await expect(composer.getByRole("alert")).toContainText("已选择 11 个文件");
    await expect(submit).toBeDisabled();
    await page.getByRole("button", { name: "清除附件", exact: true }).click();
    await expect(submit).toBeEnabled();
    await expect(text).toHaveValue("保留我的补充说明");
    expect(await input.evaluate((element: HTMLInputElement) => element.files?.length)).toBe(0);
    await input.setInputFiles({ name: "notes.txt", mimeType: "text/plain", buffer: Buffer.from("keep this log") });
    const path = "/api/jira-agent/conversations" + (id ? `/${id}/messages` : "");
    await page.route("**" + path, async route => {
      if (route.request().method() !== "POST") return route.continue();
      await route.fulfill({ status: 400, contentType: "application/json", body: JSON.stringify({ detail: "测试请求失败" }) });
    });
    const failed = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(path));
    await submit.click();
    expect((await failed).status()).toBe(400);
    await expect(submit).toBeEnabled();
    await expect(text).toHaveValue("保留我的补充说明");
    expect(await input.evaluate((element: HTMLInputElement) => element.files?.[0].name)).toBe("notes.txt");
    await page.unroute("**" + path);
    const sent = page.waitForResponse(response => response.request().method() === "POST" && response.url().endsWith(path));
    await submit.click();
    const response = await sent;
    expect(response.ok()).toBeTruthy();
    expect(response.request().postDataJSON().files).toEqual([{ filename: "notes.txt", content_base64: Buffer.from("keep this log").toString("base64") }]);
    const saved = await response.json();
    const detail = await (await page.request.get(base + `/api/jira-agent/conversations/${saved.conversation.id}`)).json();
    expect(detail.files.filter((file: { source: string }) => file.source === "upload")).toEqual([
      expect.objectContaining({ name: "notes.txt", size: 13 }),
    ]);
    await expect(page.getByLabel("补充信息")).toHaveValue("");
    await expect(page.getByRole("button", { name: "清除附件", exact: true })).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}
