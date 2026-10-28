import { expect, test } from "@playwright/test";

// Only the disposable tests.run_jira_testing_site peer exposes these fault routes.
const base = process.env.JIRA_COMMENT_E2E_URL || "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires the isolated local publication fixture");

test("uncertain publication is reconciled with one POST and its compact body remains visible", async ({ page }) => {
  expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  const initial = await (await page.request.post(`${base}/api/_testing/publication?accepted_timeout=true`)).json();
  const issueKey = `MC3-${Date.now()}`;
  const start = await page.request.post(`${base}/api/jira-agent/conversations`, {
    data: { issue_key: issueKey, note: "请检查尾部元素", post_comment: true,
      request_id: issueKey },
  });
  expect(start.ok()).toBeTruthy();
  const cid = (await start.json()).conversation.id;
  await expect.poll(async () => {
    const response = await page.request.get(`${base}/api/jira-agent/conversations/${cid}`);
    const data = await response.json();
    return data.turns.at(-1).comment_publication_state;
  }).toBe("uncertain");
  await page.goto(`${base}/jira-agent?issue=${issueKey}&conversation=${cid}`);
  await expect(page.getByText(/JIRA 评论发布结果待确认/)).toBeVisible();
  await expect(page.getByRole("button", { name: "重试发布", exact: true })).toHaveCount(0);
  const responsePromise = page.waitForResponse((response) => response.url().endsWith("/comment/retry") && response.request().method() === "POST");
  await page.getByRole("button", { name: "核对发布结果" }).click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const posted = (await response.json()).turn;
  expect(posted.comment_publication_state).toBe("posted");
  expect(posted.comment_body.length).toBeLessThan(600);
  expect(posted.comment_body).not.toContain("blocks = (n + 255)");
  await expect(page.getByText(/已在 JIRA 发布评论/)).toBeVisible();
  await expect(page.getByRole("button", { name: "核对发布结果" })).toHaveCount(0);
  const stats = await (await page.request.post(`${base}/api/_testing/publication`)).json();
  expect(stats.comments).toBe(initial.comments + 1);
  expect(stats.post_calls).toBe(initial.post_calls + 1);
  await page.getByText("评论内容", { exact: true }).click();
  await expect(page.getByText(/向上取整后原案例验证通过/, { exact: false }).last()).toBeVisible();
});
