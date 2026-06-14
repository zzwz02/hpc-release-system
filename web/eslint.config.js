import js from "@eslint/js";
import globals from "globals";
import tsParser from "@typescript-eslint/parser";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";

export default [
  { ignores: ["dist/**", "../web_dist/**", "node_modules/**"] },
  js.configs.recommended,
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2020,
      globals: {
        ...globals.browser,
        // React is in scope via JSX transform, not an explicit import in some files
        React: "readonly",
        RequestInit: "readonly",
        RequestCredentials: "readonly",
        HeadersInit: "readonly",
        Response: "readonly",
        Blob: "readonly",
        URL: "readonly",
      },
      parser: tsParser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // TypeScript recommended rules
      "@typescript-eslint/no-explicit-any": "warn",
      "@typescript-eslint/no-unused-vars": ["error", { argsIgnorePattern: "^_" }],
      // React hooks rules
      ...reactHooks.configs.recommended.rules,
      // React refresh
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      // Disable base rules superseded by TypeScript variants
      "no-unused-vars": "off",
      // Allow irregular whitespace in source (BOM constants in csv.ts etc.)
      "no-irregular-whitespace": "off",
      // no-undef is unreliable with TypeScript; TS itself handles this
      "no-undef": "off",
    },
  },
];
