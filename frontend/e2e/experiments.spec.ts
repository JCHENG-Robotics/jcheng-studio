import { expect, test } from "@playwright/test";

test.describe("Experiment Lab (offline, mock backend)", () => {
  test("loads the simulated paired demo and reports honest analysis limits", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "实验台" }).click();
    await expect(page.getByRole("heading", { name: "实验台", level: 1 })).toBeVisible();

    const before = await page.request.get("/api/events?limit=1");
    const beforeBody = (await before.json()) as { items: Array<{ seq: number }> };
    const lastSeq = beforeBody.items[0]?.seq ?? 0;

    await page.getByRole("button", { name: "加载示例实验" }).click();
    await expect(page.getByText("G3 示例 - 模拟配对抓取").first()).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByRole("heading", { name: "配对分析" })).toBeVisible();

    const improvement = page
      .locator(".metric-row-4 .metric")
      .filter({ hasText: "平均改善" });
    await expect(improvement).toContainText("1.161");
    await expect(improvement).toContainText("mm");

    const trials = page.locator("section", {
      has: page.getByRole("heading", { name: "已记录试验" }),
    });
    await expect(trials.locator("tbody tr")).toHaveCount(32);

    // The demo is simulated and the binary endpoint is explicitly underpowered.
    await expect(page.getByText("模拟", { exact: true }).first()).toBeVisible();
    await expect(
      page.getByText("不要宣称成功率有显著改善", { exact: false }),
    ).toBeVisible();
    await expect(page.getByRole("link", { name: "Trials CSV" })).toBeVisible();
    await expect(page.getByRole("link", { name: "元数据 JSON" })).toBeVisible();

    const after = await page.request.get(`/api/events?after_seq=${lastSeq}`);
    const afterBody = (await after.json()) as { items: Array<{ stage: string }> };
    const stages = afterBody.items.map((event) => event.stage);
    expect(stages).not.toContain("Dispatched");
    expect(stages).not.toContain("Requested");
  });
});
