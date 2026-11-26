import { expect, test } from "@playwright/test";

test.describe("Joint Control (mock mode)", () => {
  test("preview rejects a step beyond max_step_rad", async ({ page }) => {
    // HashRouter: the route lives after '#', and the backend's SPA fallback
    // still serves index.html for a bare /joints visit.
    await page.goto("/#/joints");
    await expect(page.getByRole("heading", { name: "关节控制", level: 1 })).toBeVisible();

    await page.getByLabel("目标 (rad)").fill("0.900");
    await page.getByRole("button", { name: "预览", exact: true }).click();

    const preview = page.locator("section", {
      has: page.getByRole("heading", { name: "预览结果" }),
    });
    await expect(preview).toBeVisible();
    await expect(preview.getByText("已拒绝", { exact: true })).toBeVisible();
    await expect(preview).toContainText("STEP_EXCEEDED");
  });

  test("lease → preview → confirm → execute completes", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "总览", level: 1 })).toBeVisible();

    await page.getByRole("button", { name: "申请控制权" }).click();
    await expect(page.getByText("本浏览器持有控制权")).toBeVisible();

    await page.getByRole("link", { name: "关节控制" }).click();
    await expect(page.getByRole("heading", { name: "关节控制", level: 1 })).toBeVisible();

    await page.getByLabel("目标 (rad)").fill("0.100");
    await page.getByRole("button", { name: "预览", exact: true }).click();

    const preview = page.locator("section", {
      has: page.getByRole("heading", { name: "预览结果" }),
    });
    await expect(preview.getByText("有效", { exact: true })).toBeVisible();

    await page.getByRole("button", { name: "执行（需确认）" }).click();
    const dialog = page.getByRole("dialog", { name: "确认执行" });
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: "确认执行" }).click();

    const command = page.locator("section", {
      has: page.getByRole("heading", { name: /命令状态/ }),
    });
    await expect(command).toBeVisible();
    await expect(command.getByText("已完成", { exact: true }).first()).toBeVisible({
      timeout: 10_000,
    });

    // Release the lease so the next run starts clean.
    await page.getByRole("link", { name: "总览" }).click();
    await page.getByRole("button", { name: "释放控制权" }).click();
    await expect(page.getByText("当前没有控制权")).toBeVisible();
  });
});
