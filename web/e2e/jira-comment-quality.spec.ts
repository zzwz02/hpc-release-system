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
  const comment = page.getByTestId("jira-agent-comment");
  const result = page.getByTestId("jira-agent-result");
  await expect(comment).toHaveCount(1);
  await expect(comment).toHaveAttribute("open");
  await expect(comment.locator(".jira-comment")).toBeVisible();
  await expect(comment.locator("summary .pill")).toHaveText(["修复已验证", "需要审核"]);
  await expect(comment.locator(".jira-agent-output")).toHaveCount(0);
  await expect(result).not.toHaveAttribute("open");
  await expect(result.locator("summary .pill")).toHaveCount(0);
  expect(await comment.evaluate(element => Boolean(
    element.compareDocumentPosition(document.querySelector('[data-testid="jira-agent-result"]')!) & Node.DOCUMENT_POSITION_FOLLOWING,
  ))).toBe(true);
  await comment.locator("summary").click();
  await expect(comment.locator(".jira-comment")).not.toBeVisible();
  await expect(comment.locator("summary").getByText("发布结果待确认")).toBeVisible();
  await expect(page.getByRole("button", { name: "重试发布", exact: true })).toHaveCount(0);
  const responsePromise = page.waitForResponse((response) => response.url().endsWith("/comment/retry") && response.request().method() === "POST");
  await page.getByRole("button", { name: "核对发布结果" }).click();
  const response = await responsePromise;
  expect(response.ok()).toBeTruthy();
  const posted = (await response.json()).turn;
  expect(posted.comment_publication_state).toBe("posted");
  expect(posted.comment_body.length).toBeLessThan(600);
  expect(posted.comment_body).not.toContain("blocks = (n + 255)");
  await expect(comment.locator("summary").getByText("已发布到 Jira")).toBeVisible();
  await expect(comment.getByRole("link", { name: `#${posted.comment_id}`, exact: true })).toHaveAttribute(
    "href", new RegExp(`\\?focusedCommentId=${posted.comment_id}#comment-${posted.comment_id}$`),
  );
  await expect(page.getByRole("button", { name: "核对发布结果" })).toHaveCount(0);
  const stats = await (await page.request.post(`${base}/api/_testing/publication`)).json();
  expect(stats.comments).toBe(initial.comments + 1);
  expect(stats.post_calls).toBe(initial.post_calls + 1);
  await expect(comment).not.toHaveAttribute("open");
  await comment.locator("summary").click();
  await expect(comment.getByText(/向上取整后原案例验证通过/, { exact: false })).toBeVisible();
});

test("a round that opts out of Jira publication shows its status in the comment heading", async ({ page }) => {
  expect((await page.request.post(`${base}/api/login`, { data: { username: "rm", password: "rm" } })).ok()).toBeTruthy();
  const initial = await (await page.request.post(`${base}/api/_testing/publication`)).json();
  const issue = `MC3-${Date.now()}`;
  const start = await page.request.post(`${base}/api/jira-agent/conversations`, {
    data: { issue_key: issue, note: "只在网站查看结论", post_comment: false, request_id: issue },
  });
  expect(start.ok()).toBeTruthy();
  const id = (await start.json()).conversation.id;
  await expect.poll(async () => {
    const detail = await (await page.request.get(`${base}/api/jira-agent/conversations/${id}`)).json();
    return detail.turns.at(-1).comment_body;
  }).not.toBe("");
  await page.goto(`${base}/jira-agent?issue=${issue}&conversation=${id}`);
  const card = page.getByTestId("jira-agent-comment");
  await expect(card).toHaveCount(1);
  await expect(card).toHaveAttribute("open");
  await expect(card.locator("summary").getByText("未发布 · 本轮仅在网站显示")).toBeVisible();
  await expect(card.locator("summary button, summary a")).toHaveCount(0);
  await card.locator("summary").click();
  await expect(card.locator(".jira-comment")).not.toBeVisible();
  await expect(card.locator("summary").getByText("未发布 · 本轮仅在网站显示")).toBeVisible();
  await expect(page.locator(".jira-agent-event.comment")).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(card.locator("summary").getByText("未发布 · 本轮仅在网站显示")).toBeVisible();
  expect(await card.evaluate(element => element.scrollWidth <= element.clientWidth)).toBe(true);
  const stats = await (await page.request.post(`${base}/api/_testing/publication`)).json();
  expect(stats.post_calls).toBe(initial.post_calls);
});
