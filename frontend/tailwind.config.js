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
      keyframes: {
        shake: {
          "0%,100%": { transform: "translateX(0)" },
          "20%,60%": { transform: "translateX(-6px)" },
          "40%,80%": { transform: "translateX(6px)" },
        },
        "toast-in": {
          "0%": { opacity: "0", transform: "translateX(-50%) translateY(-12px) scale(0.95)" },
          "100%": { opacity: "1", transform: "translateX(-50%) translateY(0) scale(1)" },
        },
      },
      animation: {
        shake: "shake 0.4s ease-in-out",
        "toast-in": "toast-in 0.25s ease-out",
      },
    },
  },
  plugins: [],
};
