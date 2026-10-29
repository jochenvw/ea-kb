export default {
  content: ["./src/**/*.{ts,tsx}"],
  darkMode: ["selector", '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        background: "var(--cp-surface)",
        foreground: "var(--cp-text)",
        card: { DEFAULT: "var(--cp-surface)", foreground: "var(--cp-text)" },
        popover: { DEFAULT: "var(--cp-surface)", foreground: "var(--cp-text)" },
        primary: {
          DEFAULT: "var(--cp-accent)",
          foreground: "var(--cp-accent-fg)",
        },
        secondary: {
          DEFAULT: "var(--cp-surface-soft)",
          foreground: "var(--cp-text)",
        },
        muted: {
          DEFAULT: "var(--cp-surface-soft)",
          foreground: "var(--cp-text-muted)",
        },
        accent: {
          DEFAULT: "var(--cp-accent-soft)",
          foreground: "var(--cp-text)",
        },
        destructive: {
          DEFAULT: "var(--cp-danger)",
          foreground: "var(--cp-accent-fg)",
        },
        border: "var(--cp-border)",
        input: "var(--cp-border-strong)",
        ring: "var(--cp-accent)",
      },
      borderRadius: { lg: "1rem", md: "0.625rem", sm: "0.5rem" },
      boxShadow: { DEFAULT: "none", sm: "none", lg: "none" },
    },
  },
};
