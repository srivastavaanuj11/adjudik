import { expect, test, type Page } from "@playwright/test";
import { login, logout, USERS } from "./helpers";

// SPEC §17 M4: seed, log in as reviewer, complete three reviews by keyboard;
// log in as auditor, open one of those evaluations, and see decided_by: human
// and its audit timeline. Requires the demo seed (gate tasks are blind, so each
// review is a `decide`; the examples declare reason codes, so one is chosen).

async function completeOneByKeyboard(page: Page): Promise<void> {
  await expect(page.getByRole("heading", { name: "Decision" })).toBeVisible();
  const url = page.url();

  // Focus outside any text field, then pick the first outcome with the keyboard.
  await page.getByRole("heading", { name: "Case data" }).click();
  await page.keyboard.press("1");

  // Reason codes are required by the example definitions; "Other" exists in both.
  const other = page.getByRole("checkbox", { name: "Other" });
  if (await other.count()) await other.first().check();

  // Ctrl/Cmd+Enter submits; auto-advance then moves us to the next task or queue.
  await page.keyboard.press("Control+Enter");
  await page.waitForFunction((prev) => window.location.href !== prev, url);
}

test("reviewer completes three reviews by keyboard; auditor sees the human decision", async ({
  page,
}) => {
  await login(page, USERS.reviewer);
  await expect(page).toHaveURL(/\/reviews$/);

  // Claim the first task, then complete three in a row via auto-advance.
  await page.getByRole("button", { name: /start reviewing/i }).click();
  await expect(page).toHaveURL(/\/reviews\/[0-9a-f-]+$/);
  for (let i = 0; i < 3; i++) {
    await completeOneByKeyboard(page);
  }
  await logout(page);

  // Auditor: find a human-decided evaluation and inspect it.
  await login(page, USERS.auditor);
  await expect(page).toHaveURL(/\/evaluations$/);
  await page.getByRole("combobox").nth(1).selectOption("human"); // "Decided by" filter
  const firstRow = page.locator("tbody tr").first();
  await expect(firstRow).toBeVisible();
  await firstRow.click();

  await expect(page).toHaveURL(/\/evaluations\/[0-9a-f-]+$/);
  await expect(page.getByText("Human")).toBeVisible(); // decided-by field
  await expect(page.getByRole("heading", { name: "Audit timeline" })).toBeVisible();
  // The timeline carries the decided event.
  await expect(page.getByText("evaluation.decided")).toBeVisible();
});
