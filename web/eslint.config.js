import js from "@eslint/js";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "src/api/schema.d.ts", "playwright-report", "test-results"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: { ecmaVersion: 2022, sourceType: "module" },
  },
);
