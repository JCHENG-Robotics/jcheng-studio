import { expect, test } from "@playwright/test";

/**
 * Private, explicitly gated hardware run.
 *
 *   ARMPI_E2E_HARDWARE=1 \
 *   ARMPI_GATEWAY_URL=http://10.42.0.218:8765 \
 *   ARMPI_GATEWAY_TOKEN_FILE=/tmp/armpi_gateway_token \
 *   npm run test:e2e:hardware
 *
 * The backend runs with hardware_motion=false. This spec reads live battery,
 * camera and command-echo state, checks that the G4 /tf path is live, and
 * verifies that a complete preview/execute attempt is rejected by the safety
 * gate. It never moves the arm and must not run in CI.
 */

const HARDWARE = process.env.ARMPI_E2E_HARDWARE === "1";

test.describe("Hardware mode (read-only gateway)", () => {
  test.skip(!HARDWARE, "set ARMPI_E2E_HARDWARE=1 and point at a private gateway");

  test.beforeEach(async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "总览", level: 1 })).toBeVisible();
  });

  test("shows LIVE measured battery/camera and command-cache joint state", async ({
    page,
    request,
  }) => {
    await expect(page.locator(".topbar .source-badge")).toHaveText("实机");
    await expect(page.getByText("模拟", { exact: true })).toHaveCount(0);

    const health = await (await request.get("/api/health")).json();
    expect(health.mode).toBe("hardware");
    expect(health.source_label).toBe("LIVE");
    expect(health.capabilities.hardware_motion).toBe(false);
    expect(health.capabilities.hardware_read).toBe(true);

    const peripherals = await (await request.get("/api/peripherals")).json();
    expect(peripherals.battery.available).toBe(true);
    expect(peripherals.battery.truth).toBe("measured");
    expect(peripherals.battery.voltage_v).toBeGreaterThan(0);
    expect(peripherals.camera.available).toBe(true);
    expect(peripherals.camera.truth).toBe("measured");
    expect(peripherals.camera.width).toBeGreaterThan(0);
    expect(peripherals.camera.height).toBeGreaterThan(0);

    const snapshot = await request.get("/api/peripherals/camera/snapshot");
    expect(snapshot.ok()).toBeTruthy();
    expect(snapshot.headers()["content-type"]).toContain("image/");
    expect((await snapshot.body()).byteLength).toBeGreaterThan(100);

    const camera = page.locator("section", {
      has: page.getByRole("heading", { name: "相机 · RGB" }),
    });
    await expect(camera.locator('img[alt="ArmPi 相机"]')).toBeVisible();
    await expect(camera).toContainText("实测");

    const battery = page.locator("section", {
      has: page.getByRole("heading", { name: "电池 · 2S" }),
    });
    await expect(battery).toContainText("实测");

    const state = await (await request.get("/api/state")).json();
    expect(state.joints).toHaveLength(5);
    // /controller_manager/servo_states is the vendor command cache, not a bus
    // read; the read-only console must label it `commanded` (2026-11-21).
    expect(
      state.joints.every((joint: { truth: string }) =>
        ["commanded", "measured_on_demand", "measured"].includes(joint.truth),
      ),
    ).toBe(true);
    expect(state.joints[0].source).toContain("servo_states");
    expect(state.capabilities.hardware_motion).toBe(false);
  });

  test("G4 workbench uses live gateway /tf, not model-only FK", async ({ request }) => {
    const visual = await (await request.get("/api/visual")).json();
    expect(visual.urdf.available).toBe(true);
    expect(visual.tf.source).toBe("tf2_topics");
    expect(visual.tf.live).toBe(true);
    expect(visual.tf.status).toBe("live");
    expect(visual.tf.dynamic_edges).toBeGreaterThan(0);
    expect(visual.tf.static_edges).toBeGreaterThan(0);
    expect(visual.camera.optical_frame).toBe("camera_optical_calibrated");
    expect(visual.camera.frame).toBe("rgb_camera_link");
  });

  test("a full preview/execute attempt is rejected without motion", async ({ request }) => {
    const sessionId = "e2e-hardware-readonly";
    const leaseResponse = await request.post("/api/control/acquire", {
      data: { session_id: sessionId, ttl_s: 30 },
    });
    expect(leaseResponse.ok()).toBeTruthy();
    const lease = await leaseResponse.json();

    const state = await (await request.get("/api/state")).json();
    const joint1 = state.joints.find((joint: { name: string }) => joint.name === "joint1");
    if (!joint1) throw new Error("joint1 missing from /api/state");
    // The arm's live pose is not fixed (it homes to joint1 = +0.8378 rad), so
    // derive the target from the measured position. A hard-coded target makes
    // the preview fail with STEP_EXCEEDED whenever the arm is not near zero.
    const currentRad: number = joint1.position_rad ?? joint1.target_rad ?? 0;
    const previewStep = 0.05;
    const targetRad = [currentRad + previewStep, currentRad - previewStep].find(
      (value: number) => value >= joint1.limit_min_rad && value <= joint1.limit_max_rad,
    );
    if (targetRad === undefined) throw new Error("no in-limit preview target for joint1");

    const targets = [{ joint: "joint1", target_rad: targetRad, duration_s: 1.0 }];
    const previewResponse = await request.post("/api/motion/preview", {
      data: { session_id: sessionId, targets },
    });
    expect(previewResponse.ok()).toBeTruthy();
    const preview = await previewResponse.json();
    expect(preview.valid).toBe(true);
    expect(typeof preview.command_id).toBe("string");

    const executeResponse = await request.post("/api/motion/execute", {
      headers: { "Idempotency-Key": "idem-e2e-hardware-readonly" },
      data: {
        session_id: sessionId,
        lease_id: lease.lease_id,
        preview_token: preview.preview_token,
        command_id: preview.command_id,
        targets,
      },
    });
    expect(executeResponse.status()).toBe(403);
    const error = await executeResponse.json();
    expect(error.error.code).toBe("GATE_REJECTED");
    expect(error.error.details.blockers.length).toBeGreaterThan(0);

    await request.post("/api/control/release", {
      data: { lease_id: lease.lease_id, session_id: sessionId },
    });

    const events = await (await request.get("/api/events")).json();
    const stages = (events.items ?? []).map((item: { stage: string }) => item.stage);
    expect(stages).toContain("Failed");
    expect(stages.filter((stage: string) => stage === "Dispatched")).toHaveLength(0);
    expect(stages.filter((stage: string) => stage === "Completed")).toHaveLength(0);
  });
});
