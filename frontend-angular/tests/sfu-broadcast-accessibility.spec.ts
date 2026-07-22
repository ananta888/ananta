import AxeBuilder from "@axe-core/playwright";
import { expect, test } from "@playwright/test";

const LIVE = process.env["RUN_SFU_BROADCAST_A11Y_E2E"] === "1";

test.describe("real SFU broadcast accessibility lifecycle", () => {
  test.skip(!LIVE, "release evidence requires the real SFU browser stack");

  test("keyboard, semantics, responsive layout, axe and owned lifecycle", async ({
    browserName,
    page,
  }) => {
    expect(["chromium", "firefox"]).toContain(browserName);
    await page.setViewportSize({ width: 360, height: 720 });
    await page.goto("/voice");

    const control = page.getByTestId("sfu-broadcast-control");
    await expect(control).toBeVisible();
    await expect(control.getByRole("status")).toHaveCount(1);
    const start = control.getByRole("button", { name: /broadcast.*start/i });
    await start.focus();
    await page.keyboard.press("Enter");
    await expect(control.getByRole("button", { name: /broadcast.*stop/i })).toBeVisible();

    const snapshot = await control.ariaSnapshot();
    expect(snapshot).toContain("status");
    expect(snapshot).toContain("button");
    const touchTargets = await control.locator("button").evaluateAll((buttons) =>
      buttons.every((button) => {
        const box = button.getBoundingClientRect();
        return box.width >= 44 && box.height >= 44;
      }),
    );
    expect(touchTargets).toBe(true);
    const noOverflow = await page.evaluate(
      () => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1,
    );
    expect(noOverflow).toBe(true);

    const axe = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"])
      .analyze();
    expect(axe.violations).toEqual([]);

    const started = await page.evaluate(() => {
      const value = Reflect.get(window, "__ANANTA_SFU_BROADCAST_LIFECYCLE__");
      return typeof value === "object" && value !== null
        ? Reflect.get(value, "started")
        : null;
    });
    expect(started).toMatchObject({
      owned_track: expect.any(Number),
      livekit_room: expect.any(Number),
      quality_reporter: expect.any(Number),
      data_listener: expect.any(Number),
      remote_video_render: expect.any(Number),
      layer_controller: expect.any(Number),
    });

    const stop = control.getByRole("button", { name: /broadcast.*stop/i });
    await stop.focus();
    await page.keyboard.press("Enter");
    const cleanup = await page.evaluate(() => {
      const value = Reflect.get(window, "__ANANTA_SFU_BROADCAST_LIFECYCLE__");
      return typeof value === "object" && value !== null
        ? Reflect.get(value, "cleanup")
        : null;
    });
    expect(cleanup).toMatchObject({
      owned_tracks: 0,
      remote_video_attachments: 0,
      rooms: 0,
      requests: 0,
      quality_reporters: 0,
      data_listeners: 0,
      room_listeners: 0,
      layer_controllers: 0,
      subscriptions: 0,
      timers: 0,
    });
    await expect(start).toBeFocused();
  });
});
