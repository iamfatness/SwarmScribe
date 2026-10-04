import js from "@eslint/js";
import jsxA11y from "eslint-plugin-jsx-a11y";
import reactHooks from "eslint-plugin-react-hooks";
import globals from "globals";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "playwright-report", "test-results", "e2e/harness"] },
  js.configs.recommended,
  ...tseslint.configs.strict,
  {
    files: ["src/**/*.{ts,tsx}"],
    ...jsxA11y.flatConfigs.strict,
    languageOptions: { globals: { ...globals.browser } },
    plugins: { "react-hooks": reactHooks, ...jsxA11y.flatConfigs.strict.plugins },
    rules: {
      ...jsxA11y.flatConfigs.strict.rules,
      ...reactHooks.configs.recommended.rules,
      // A scrolling table region must be focusable for keyboard users (axe
      // scrollable-region-focusable); tabpanel is the rule's own default.
      "jsx-a11y/no-noninteractive-tabindex": ["error", { tags: [], roles: ["region", "tabpanel"] }],
      "no-console": "error",
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='style']",
          message: "No style props: the console's CSP forbids inline styles. Use a class.",
        },
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Never set HTML: render text.",
        },
      ],
      "no-restricted-globals": [
        "error",
        { name: "localStorage", message: "Use src/app/theme.ts; nothing else is stored." },
        { name: "sessionStorage", message: "Nothing is stored in sessionStorage." },
      ],
    },
  },
  {
    files: ["e2e/**/*.ts", "*.config.ts", "scripts/**/*.mjs"],
    languageOptions: { globals: { ...globals.node } },
  },
);
