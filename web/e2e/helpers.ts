import { expect, type Page } from "@playwright/test";

// The seed (`manage.py seed_demo --password $E2E_PASSWORD`) creates these users.
export const PASSWORD = process.env.E2E_PASSWORD ?? "adjudik-e2e-pw";
export const USERS = {
  admin: "demo-admin",
  admin2: "demo-admin2",
  author: "demo-author",
  reviewer: "demo-reviewer",
  auditor: "demo-auditor",
};

export async function login(page: Page, username: string): Promise<void> {
  await page.goto("/login");
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.getByRole("button", { name: /sign in/i }).click();
  // Land on a role home; the login form is gone.
  await expect(page.locator('input[name="username"]')).toHaveCount(0);
}

export async function logout(page: Page): Promise<void> {
  await page.getByRole("button", { name: /sign out/i }).click();
  await expect(page).toHaveURL(/\/login$/);
}
