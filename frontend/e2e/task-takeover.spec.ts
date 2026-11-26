import { expect, test } from "@playwright/test";

// Dedicated pose-scope mock backend:
//   E2E_POSE=1 npm run test:e2e
// The task uses the simulated locate point; no camera and no robot.
test.describe("Task Takeover (mock pose scope)", () => {
  test("prepare → locate/plan → run → verify → put-back", async ({ page }) => {
    test.setTimeout(120_000);
    await page.goto("/#/task");
    await expect(
      page.getByRole("heading", { name: "任务接管", level: 1 }),
    ).toBeVisible();

    await page.getByRole("button", { name: "准备（移动到观察位）" }).click();
    await expect(page.getByText(/已到观察位/)).toBeVisible({ timeout: 25_000 });

    await page.getByRole("button", { name: "定位并规划" }).click();
    const planning = page.locator("section", {
      has: page.getByRole("heading", { name: "准备与定位" }),
    });
    await expect(planning.getByText("规划有效", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    await expect(planning).toContainText("目标点");
    await expect(planning).toContainText("拟合 xy 误差");

    const steps = page.locator("section", {
      has: page.getByRole("heading", { name: "阶段" }),
    });
    await expect(steps).toContainText("接近");
    await expect(steps).toContainText("抓取");
    await expect(steps).toContainText("闭爪");
    await expect(steps).toContainText("夹持验证");
    await expect(steps).toContainText("放回");
    await expect(steps).toContainText("返回观察位");

    await page.getByRole("button", { name: "运行任务（需确认）" }).click();
    const dialog = page.getByRole("dialog", { name: "确认执行任务" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: /确认执行/ }).click();

    const verifyCard = page.locator("section", {
      has: page.getByRole("heading", { name: "夹持验证" }),
    });
    await expect(verifyCard.getByText("已夹持", { exact: true })).toBeVisible({
      timeout: 90_000,
    });

    // The put-back continues after the verify; wait for the final step.
    const returnRow = steps.getByRole("row", { name: /返回观察位/ });
    await expect(returnRow.getByText("已完成", { exact: true })).toBeVisible({
      timeout: 60_000,
    });
    const statuses = await steps.getByText("已完成", { exact: true }).count();
    expect(statuses).toBeGreaterThanOrEqual(6);

    await page.getByRole("link", { name: "总览" }).click();
    await page.getByRole("button", { name: "释放控制权" }).click();
    await expect(page.getByText("当前没有控制权")).toBeVisible();
  });

  test("selects the measured lying block and plans its j4", async ({ page }) => {
    test.setTimeout(90_000);
    await page.goto("/#/task");
    await expect(
      page.getByRole("heading", { name: "任务接管", level: 1 }),
    ).toBeVisible();

    const forms = page.locator("section", {
      has: page.getByRole("heading", { name: "抓取形态" }),
    });
    await expect(forms).toBeVisible();
    await expect(forms.getByRole("button", { name: /39.4 mm 木块/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );

    await forms.getByRole("button", { name: /蓝块·平躺/ }).click();
    await expect(forms).toContainText("j4 -0.500 rad（实测）");
    await expect(forms).toContainText("闭合寄存器 482");
    await expect(forms).toContainText("闭合 -0.05");
    await expect(forms).toContainText("基准位窗口");
    await expect(forms).toContainText("(0.233, 0.001) ± 12 mm");

    await page.getByRole("button", { name: "准备（移动到观察位）" }).click();
    await expect(page.getByText(/已到观察位/)).toBeVisible({ timeout: 25_000 });

    await page.getByRole("button", { name: "定位并规划" }).click();
    const planning = page.locator("section", {
      has: page.getByRole("heading", { name: "准备与定位" }),
    });
    await expect(planning.getByText("规划有效", { exact: true })).toBeVisible({
      timeout: 20_000,
    });
    await expect(planning).toContainText("block_lying");
    await expect(planning).toContainText("j4 -0.500（实测）");
    await expect(planning).toContainText("闭合 -1.60 → -0.05");

    await page.getByRole("link", { name: "总览" }).click();
    await page.getByRole("button", { name: "释放控制权" }).click();
    await expect(page.getByText("当前没有控制权")).toBeVisible();
  });
});
