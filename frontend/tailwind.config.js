/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["Inter", "-apple-system", "BlinkMacSystemFont", "system-ui", "sans-serif"],
        mono: ["JetBrains Mono", "SF Mono", "Menlo", "monospace"],
      },
      colors: {
        ink: {
          950: "#07090d",
          900: "#0b0e14",
          850: "#0f131b",
          800: "#141923",
          700: "#1c2230",
          600: "#262d3d",
          500: "#3a4254",
          300: "#9aa3b8",
          200: "#d6dae3",
          100: "#e6e9f0",
        },
        edge: "#2a3142",
        cn: { up: "#ef4444", dn: "#10b981" },
        gold: "#d4a857",
        sky2: "#7dd3fc",
      },
    },
  },
  plugins: [],
};
