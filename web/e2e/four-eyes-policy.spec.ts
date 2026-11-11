import { expect, test } from "@playwright/test";
import { login, logout, USERS } from "./helpers";

// SPEC §17 M4: an admin proposes a manual policy and, with four-eyes on, a second
// admin approves it. Support routing has no active policy in the seed, so it is a
// clean target.

test("admin proposes a policy; a second admin approves it under four-eyes", async ({ page }) => {
  // Admin 1: turn four-eyes on.
  await login(page, USERS.admin);
  await page.goto("/settings/workspace");
  const fourEyes = page.getByRole("checkbox", {
    name: /require four-eyes/i,
  });
  await expect(fourEyes).toBeVisible();
  if (!(await fourEyes.isChecked())) await fourEyes.check();
  await page.getByRole("button", { name: /save settings/i }).click();
  await expect(page.getByText(/saved/i)).toBeVisible();

  // Admin 1: propose a manual policy on support routing.
  await page.goto("/decisions/support_routing/policies");
  await page.getByRole("button", { name: /propose manual policy/i }).click();
  await page.getByLabel("billing threshold").fill("0.95");
  await page.getByLabel("Audit sample rate").fill("0.1");
  await page.getByRole("button", { name: /^propose$/i }).click();

  // A proposed (not yet active) policy appears.
  await expect(page.getByText("Proposed").first()).toBeVisible();
  await logout(page);

  // Admin 2: approve it.
  await login(page, USERS.admin2);
  await page.goto("/decisions/support_routing/policies");
  const proposedRow = page.locator("tbody tr", { hasText: "Proposed" }).first();
  await expect(proposedRow).toBeVisible();
  await proposedRow.getByRole("button", { name: /approve/i }).click();

  // It becomes active.
  await expect(page.locator("tbody tr", { hasText: "Active" }).first()).toBeVisible();
});
