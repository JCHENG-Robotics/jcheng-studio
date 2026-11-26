import { expect, test } from "@playwright/test";

test.describe("Trajectory Inspector (offline, mock backend)", () => {
  test("loads the simulated demo, shows metrics, and replays without backend writes", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "轨迹检查" }).click();
    await expect(page.getByRole("heading", { name: "轨迹检查", level: 1 })).toBeVisible();

    await page.getByRole("button", { name: "加载示例数据" }).click();
    await expect(page.getByText("G2 示例 - 模拟五关节运动").first()).toBeVisible({
      timeout: 15_000,
    });

    // The demo is explicitly simulated and its feedback truth is visible.
    await expect(page.getByRole("heading", { name: "跟踪指标" })).toBeVisible();
    await expect(page.locator(".metric-row").first()).toContainText("RMSE");
    await expect(page.getByRole("cell", { name: /反馈/ }).first()).toContainText("模拟");
    await expect(page.getByText("个对齐样本").first()).toBeVisible();

    // Two charts: joint position curves + tracking error (feedback is usable).
    await expect(page.getByLabel("轨迹曲线图")).toHaveCount(2);

    const sourceBadge = page.locator(".topbar .source-badge");
    await expect(sourceBadge).toHaveText("模拟");

    const before = await page.request.get("/api/events?limit=1");
    const beforeBody = (await before.json()) as { items: Array<{ seq: number }> };
    const lastSeq = beforeBody.items[0]?.seq ?? 0;

    await page.getByRole("button", { name: "播放回放" }).click();
    await expect(sourceBadge).toHaveText("回放");
    await expect(page.getByText("不会向硬件发送任何命令")).toBeVisible();
    await page.getByRole("button", { name: "暂停回放" }).click();
    await expect(sourceBadge).toHaveText("模拟");

    // Replay is client-side: no audit event, motion command or state change.
    const after = await page.request.get(`/api/events?after_seq=${lastSeq}`);
    const afterBody = (await after.json()) as { items: unknown[] };
    expect(afterBody.items).toEqual([]);
  });
});
