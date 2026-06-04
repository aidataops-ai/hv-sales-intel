import type { Config } from "tailwindcss"

const config: Config = {
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        cream: "#faf8f4",
        ivory: {
          50: "#fefdfb",
          100: "#faf8f4",
          200: "#f5f0e8",
          300: "#ebe4d6",
        },
        // Brand primary — palette #3c6e71; darkest shade is the palette navy.
        teal: {
          DEFAULT: "#3c6e71",
          50: "#eef4f4",
          100: "#d6e6e6",
          400: "#6aa0a3",
          500: "#4f8a8d",
          600: "#3c6e71",
          700: "#335d60",
          800: "#284b63",
        },
        navy: "#284b63",
        mist: "#d9d9d9",
        // Dark-mode surfaces (palette #353535).
        night: {
          DEFAULT: "#353535",
          800: "#3d3d3d",
          900: "#2c2c2c",
        },
        rose: {
          DEFAULT: "#e11d48",
          500: "#f43f5e",
          600: "#e11d48",
        },
        amber: {
          400: "#fbbf24",
          500: "#f59e0b",
        },
      },
      fontFamily: {
        serif: ["var(--font-fraunces)", "Georgia", "serif"],
        sans: ["var(--font-jakarta)", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
}
export default config
