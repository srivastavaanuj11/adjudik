import { expect, test } from "@playwright/test";

// M0 smoke test; the full §17 flows (reviewer/auditor/admin) arrive in M4.
test("dashboard loads and shows the Adjudik heading", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("heading", { level: 1, name: "Adjudik" })).toBeVisible();
});
