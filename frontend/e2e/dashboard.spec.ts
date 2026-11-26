import { expect, test } from "@playwright/test";

test.describe("Dashboard (mock mode)", () => {
  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "总览", level: 1 })).toBeVisible();
  });

  test("renders the simulation source, camera, battery and five joints", async ({ page }) => {
    // Source must be explicitly 模拟 in mock mode.
    await expect(page.locator(".topbar .source-badge")).toHaveText("模拟");

    const camera = page.locator("section", {
      has: page.getByRole("heading", { name: "相机 · RGB" }),
    });
    await expect(camera).toBeVisible();
    await expect(camera.locator('img[alt="ArmPi 相机"]')).toBeVisible();
    await expect(camera).toContainText("640×400");
    await expect(camera).toContainText("模拟");

    const auxCamera = page.locator("section", {
      has: page.getByRole("heading", { name: "相机 · 辅助" }),
    });
    await expect(auxCamera).toBeVisible();
    await expect(auxCamera.locator('img[alt="ArmPi 辅助相机"]')).toBeVisible();
    await expect(auxCamera).toContainText("mock://aux-camera");
    await expect(auxCamera).toContainText("640×480");
    await expect(auxCamera).toContainText("模拟");

    const battery = page.locator("section", {
      has: page.getByRole("heading", { name: "电池 · 2S" }),
    });
    await expect(battery).toBeVisible();
    await expect(battery).toContainText("2S");
    await expect(battery).toContainText(/%/);
    await expect(battery).toContainText("模拟");

    const joints = page.locator("section", {
      has: page.getByRole("heading", { name: "关节快照" }),
    });
    await expect(joints.locator("tbody tr")).toHaveCount(5);

    await expect(
      page.locator("section", { has: page.getByRole("heading", { name: "安全门禁" }) }),
    ).toContainText("0 个阻塞");
  });

  test("navigates the eight console pages", async ({ page }) => {
    for (const [link, title] of [
      ["关节控制", "关节控制"],
      ["诊断", "诊断"],
      ["轨迹检查", "轨迹检查"],
      ["实验台", "实验台"],
      ["可视化工作台", "可视化工作台"],
      ["事件时间线", "事件时间线"],
      ["设置", "设置"],
      ["总览", "总览"],
    ] as const) {
      await page.getByRole("link", { name: link }).click();
      await expect(page.getByRole("heading", { name: title, level: 1 })).toBeVisible();
    }
  });

  test("settings page reports mock mode and the verified mapping", async ({ page }) => {
    await page.getByRole("link", { name: "设置" }).click();
    await expect(page.getByRole("heading", { name: "设置", level: 1 })).toBeVisible();
    await expect(page.getByText("模拟（Mock）").first()).toBeVisible();
    // The mapping was adopted from the 2026-11-19 stop survey; its version and
    // verified status are the contract the Settings page must show.
    await expect(
      page.getByText(/measured-2026-11-19/).first(),
    ).toBeVisible();
    await expect(page.getByText("已验证", { exact: true }).first()).toBeVisible();
  });
});
