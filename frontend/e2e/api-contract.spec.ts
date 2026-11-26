import { expect, test } from "@playwright/test";

test.describe("Served API contract (mock mode)", () => {
  test("health, state and peripherals keep the frontend contract", async ({ request }) => {
    const healthResponse = await request.get("/api/health");
    expect(healthResponse.ok()).toBeTruthy();
    const health = await healthResponse.json();
    expect(health.status).toBe("ok");
    expect(health.mode).toBe("mock");
    expect(health.source_label).toBe("SIMULATION");
    expect(health.capabilities.hardware_motion).toBe(false);

    const stateResponse = await request.get("/api/state");
    expect(stateResponse.ok()).toBeTruthy();
    const state = await stateResponse.json();
    expect(state.joints).toHaveLength(5);
    expect(state.joints.every((joint: { truth: string }) => joint.truth === "simulated")).toBe(
      true,
    );
    expect(state.capabilities.hardware_motion).toBe(false);

    const peripheralsResponse = await request.get("/api/peripherals");
    expect(peripheralsResponse.ok()).toBeTruthy();
    const peripherals = await peripheralsResponse.json();
    expect(peripherals.battery.available).toBe(true);
    expect(peripherals.battery.truth).toBe("simulated");
    expect(peripherals.battery.voltage_v).toBeGreaterThan(0);
    expect(peripherals.camera.available).toBe(true);
    expect(peripherals.camera.width).toBe(640);
    expect(peripherals.camera.height).toBe(400);

    const snapshot = await request.get("/api/peripherals/camera/snapshot");
    expect(snapshot.ok()).toBeTruthy();
    expect(snapshot.headers()["content-type"]).toContain("image/");
    expect((await snapshot.body()).byteLength).toBeGreaterThan(0);
  });

  test("safety gate is ready and mock preview issues a token", async ({ request }) => {
    const safety = await (await request.get("/api/safety")).json();
    expect(safety.ready).toBe(true);
    expect(safety.blockers).toEqual([]);

    const preview = await (
      await request.post("/api/motion/preview", {
        data: {
          session_id: "e2e-api-contract",
          targets: [{ joint: "joint1", target_rad: 0.1, duration_s: 1.0 }],
        },
      })
    ).json();
    expect(preview.valid).toBe(true);
    expect(typeof preview.preview_token).toBe("string");
    expect(preview.preview_token.length).toBeGreaterThan(0);
  });
});
