import { expect, test } from "@playwright/test";

test.describe("Visual Workbench (read-only, mock backend)", () => {
  test("renders the URDF model, camera warnings, and saves a metadata reference", async ({
    page,
  }) => {
    await page.goto("/");
    await page.getByRole("link", { name: "可视化工作台" }).click();
    await expect(page.getByRole("heading", { name: "可视化工作台", level: 1 })).toBeVisible();

    await expect(page.getByText("只读可视化", { exact: true })).toBeVisible();
    await expect(page.getByLabel("URDF 运动学模型")).toBeVisible();
    await expect(page.getByText("URDF 模型为暂定", { exact: false })).toBeVisible();
    await expect(page.getByText("网关没有实时 tf2 订阅", { exact: false })).toBeVisible();

    const camera = page.locator("section", {
      has: page.getByRole("heading", { name: "相机 · RGB" }),
    });
    await expect(camera.locator('img[alt="ArmPi 相机"]')).toBeVisible();
    await expect(camera).toContainText("640×400");
    await expect(camera).toContainText("rgb_camera_link");
    await expect(camera).toContainText("camera_optical_calibrated");

    const joints = page.locator("section", {
      has: page.getByRole("heading", { name: "关节与限位" }),
    });
    await expect(joints.locator("tbody tr")).toHaveCount(11);

    const before = await page.request.get("/api/events?limit=1");
    const beforeBody = (await before.json()) as { items: Array<{ seq: number }> };
    const lastSeq = beforeBody.items[0]?.seq ?? 0;

    await page
      .getByPlaceholder("引用备注（如：运行前采样）")
      .fill("playwright visual reference");
    await page.getByRole("button", { name: "保存引用" }).click();
    await expect(page.getByText(/已保存引用 ref_/)).toBeVisible();

    const references = page.locator("section", {
      has: page.getByRole("heading", { name: "已保存引用" }),
    });
    await expect(references).toContainText("playwright visual reference");

    const after = await page.request.get(`/api/events?after_seq=${lastSeq}`);
    const afterBody = (await after.json()) as { items: Array<{ stage: string }> };
    const stages = afterBody.items.map((event) => event.stage);
    expect(stages).toContain("CameraReferenceSaved");
    expect(stages).not.toContain("Dispatched");
    expect(stages).not.toContain("Requested");
  });
});
