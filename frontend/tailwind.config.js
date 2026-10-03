/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "media",
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "ui-sans-serif", "system-ui", "-apple-system", "Segoe UI", "Roboto", "sans-serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      colors: {
        ink: { DEFAULT: "#0f172a", 2: "#334155", 3: "#64748b", 4: "#94a3b8" },
        line: "#e2e8f0",
        // NWS flood-category colors (used across NOAA products).
        cat: { action: "#e6c300", minor: "#ff9900", moderate: "#e60000", major: "#b833ff" },
        lvl: {
          0: "#15803d", 1: "#a16207", 2: "#b45309", 3: "#6d28d9", 4: "#c2410c", 5: "#b91c1c",
        },
      },
      boxShadow: { sheet: "0 -8px 30px -12px rgba(15,23,42,.25)", card: "0 1px 2px rgba(15,23,42,.06), 0 4px 16px -8px rgba(15,23,42,.12)" },
    },
  },
  plugins: [],
};
