import { expect, test } from "@playwright/test";

import { loginFast } from "./utils";

const VIEWPORTS = [
  { label: "five-inch", width: 360, height: 640 },
  { label: "fifteen-inch", width: 1440, height: 900 },
] as const;

test.describe("semantic media status and consent accessibility", () => {
  test.skip(
    process.env["RUN_SEMANTIC_MEDIA_LIVE_E2E"] !== "1",
    "real Angular browser evidence is mandatory for release",
  );

  for (const viewport of VIEWPORTS) {
    test(`${viewport.label} viewport exposes bounded keyboard and screen-reader controls`, async ({
      page,
      request,
    }) => {
      await page.setViewportSize(viewport);
      await loginFast(page, request);
      await page.goto("/voice");

      const host = page.getByTestId("semantic-media-program");
      await expect(host).toBeVisible();
      await expect(
        host.getByRole("heading", { name: "Semantic Media und Speech" }),
      ).toBeVisible();
      await expect(
        host.getByRole("status").filter({ hasText: /Hub|Offline/ }),
      ).toHaveCount(1);

      const scopeTerms = [
        "Richtung",
        "Datenklasse",
        "Zweck",
        "Aufbewahrung",
        "Trainerstandort",
        "E2EE-Modus",
        "Ordinary-Fallback",
      ];
      const scope = host.getByLabel("Freigabeumfang");
      for (const term of scopeTerms) {
        await expect(scope.getByText(term, { exact: true })).toBeVisible();
      }
      await expect(
        host.locator('article[data-capability] [role="note"]').filter({
          hasText: "Separate ausdrückliche Freigabe erforderlich",
        }),
      ).toHaveCount(5);
      await expect(
        host.locator("article[data-capability]").getByRole("button", {
          name: "Einzeln freigeben",
        }),
      ).toHaveCount(5);
      await expect(host.getByText(/Sammelfreigabe/).first()).toBeVisible();

      const ariaTree = await host.ariaSnapshot();
      expect(ariaTree).toContain('heading "Semantic Media und Speech"');
      expect(ariaTree).toContain("status");
      expect(ariaTree).toContain('button "Einzeln freigeben"');

      await page.locator("body").focus();
      let reachedProgramControl = false;
      for (let attempt = 0; attempt < 80; attempt += 1) {
        await page.keyboard.press("Tab");
        reachedProgramControl = await page.evaluate(() => {
          const active = document.activeElement;
          return Boolean(
            active instanceof HTMLButtonElement &&
            active.closest('[data-testid="semantic-media-program"]'),
          );
        });
        if (reachedProgramControl) break;
      }
      expect(reachedProgramControl).toBe(true);

      const layout = await host.evaluate((element) => ({
        clientWidth: element.clientWidth,
        scrollWidth: element.scrollWidth,
        viewportWidth: document.documentElement.clientWidth,
        documentWidth: document.documentElement.scrollWidth,
      }));
      const overflowElements = await page.evaluate(() =>
        [...document.querySelectorAll<HTMLElement>("body *")]
          .map((element) => ({
            className: String(element.className || "").slice(0, 120),
            right: Math.ceil(element.getBoundingClientRect().right),
            tagName: element.tagName.toLowerCase(),
            width: Math.ceil(element.getBoundingClientRect().width),
          }))
          .filter((row) => row.right > document.documentElement.clientWidth + 1)
          .slice(0, 10),
      );
      expect(layout.scrollWidth).toBeLessThanOrEqual(layout.clientWidth + 1);
      expect(
        layout.documentWidth,
        `overflowing elements: ${JSON.stringify(overflowElements)}`,
      ).toBeLessThanOrEqual(layout.viewportWidth + 1);
    });
  }
});
