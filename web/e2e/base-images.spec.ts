import { test, expect } from "@playwright/test";

const base = process.env.BASE_IMAGE_E2E_URL ?? "";
test.skip(!/^http:\/\/127\.0\.0\.1:\d+$/.test(base), "Requires tests.run_app_info_site and isolated Vite");
const address = "harbor.sh.mxcr.io/hpc-release/amgx-maca";
const digest = "sha256:f47fe6f2055e1d792689047debda38355a6dd2a7d1482bb3d21636152f361447";
const reference = `${address}@${digest}`;

test("RM saves a full base image per row, edits legacy rows and queries the parsed digest", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_rm", password: "test-password" } })).ok()).toBeTruthy();
  const state = await (await page.request.get(base + "/api/state")).json();
  const releaseId = state.releases[0].id;
  const otherId = state.releases[1].id;
  const legacyReference = `registry.example/hpc/legacy:v1@sha256:${"b".repeat(64)}`;
  expect((await page.request.post(base + "/api/releases/base-image", {
    data: { release_id: releaseId, image: { name: "Legacy name", image: "registry.example/hpc/legacy:v1", digest: `sha256:${"b".repeat(64)}`, note: "existing" } },
  })).ok()).toBeTruthy();
  await page.goto(base + "/init");
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(releaseId);
  const table = page.getByTestId("base-images-table");
  const panel = page.locator(".panel").filter({ has: page.getByRole("heading", { name: "基础镜像", exact: true }) });
  await expect(table.getByRole("columnheader")).toHaveText(["完整镜像引用", "说明", ""]);
  await expect(table).toContainText(legacyReference);
  await table.getByRole("button", { name: "编辑", exact: true }).click();
  await expect(table.getByRole("textbox", { name: "完整镜像引用", exact: true })).toHaveValue(legacyReference);
  await table.getByRole("button", { name: "取消", exact: true }).click();
  await table.getByRole("textbox", { name: "完整镜像引用", exact: true }).fill(`${address}@sha256:short`);
  await table.getByRole("textbox", { name: "说明", exact: true }).fill("新的基础镜像");
  await page.getByTestId("save-base-image-btn").click();
  await expect(panel.getByRole("alert")).toContainText("64 位");
  await expect(table.getByRole("textbox", { name: "完整镜像引用", exact: true })).toHaveValue(`${address}@sha256:short`);
  await table.getByRole("textbox", { name: "完整镜像引用", exact: true }).fill(`docker pull ${reference}`);
  await expect(table.getByRole("textbox", { name: "完整镜像引用", exact: true })).toHaveValue(reference);
  const saved = page.waitForResponse(response => response.url().endsWith("/api/releases/base-image") && response.request().method() === "POST");
  await page.getByTestId("save-base-image-btn").click();
  const response = await saved;
  expect(response.ok()).toBeTruthy();
  expect(response.request().postDataJSON()).toEqual({ release_id: releaseId, image: { reference, note: "新的基础镜像" } });
  const image = (await response.json()).base_images.at(-1);
  expect({ name: image.name, image: image.image, digest: image.digest }).toEqual({ name: "amgx-maca", image: address, digest });
  await expect(table).toContainText(reference);
  await expect(table.getByRole("textbox", { name: "完整镜像引用", exact: true })).toHaveValue("");
  const found = await (await page.request.get(base + `/api/lookup/digest?digest=${encodeURIComponent(digest)}`)).json();
  expect(found.base_images.some((row: { id: number }) => row.id === image.id)).toBe(true);
  await panel.screenshot({ path: "/tmp/base-images-merged.png" });

  const row = table.getByRole("row").filter({ hasText: reference });
  await row.getByRole("button", { name: "编辑", exact: true }).click();
  const updatedReference = `registry:5000/hpc/base:v2@${digest}`;
  await table.getByRole("textbox", { name: "完整镜像引用", exact: true }).fill(updatedReference);
  await table.getByRole("textbox", { name: "说明", exact: true }).fill("已修改");
  await page.getByTestId("save-base-image-btn").click();
  await expect(table).toContainText(updatedReference);
  await page.reload();
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(releaseId);
  await expect(table).toContainText(updatedReference);
  await expect(table).toContainText("已修改");
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(otherId);
  await expect(table.getByRole("row")).toHaveCount(2);
  await expect(table.getByRole("textbox", { name: "完整镜像引用", exact: true })).toHaveValue("");
});

test("published base images remain visible without editing controls", async ({ page }) => {
  expect((await page.request.post(base + "/api/login", { data: { username: "app_rm", password: "test-password" } })).ok()).toBeTruthy();
  const created = await page.request.post(base + "/api/releases/create", {
    data: { name: "base-image-locked", signup_deadline: "2000-01-01", qa_deadline: "2000-01-26", release_date: "2000-01-31" },
  });
  expect(created.ok()).toBeTruthy();
  const releaseId = (await created.json()).release_id;
  expect((await page.request.post(base + "/api/releases/base-image", { data: { release_id: releaseId, image: { reference, note: "locked" } } })).ok()).toBeTruthy();
  expect((await page.request.post(base + "/api/releases/publish", { data: { release_id: releaseId } })).ok()).toBeTruthy();
  await page.goto(base + "/init");
  await page.getByRole("combobox", { name: "选择版本", exact: true }).selectOption(releaseId);
  const table = page.getByTestId("base-images-table");
  await expect(table).toContainText(reference);
  await expect(table.getByRole("textbox")).toHaveCount(0);
  await expect(table.getByRole("button")).toHaveCount(0);
});
