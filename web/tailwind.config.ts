import type { Config } from "tailwindcss"

// Strict 5-color palette: #353535, #3c6e71, #ffffff, #d9d9d9, #284b63.
// Every other Tailwind colour name is remapped onto one of these families so
// the whole app stays on-palette without touching each className.

// Neutral ramp, anchored on the palette's #d9d9d9 (200) and #353535 (900).
const neutral = {
  50: "#f6f6f6",
  100: "#ececec",
  200: "#d9d9d9",
  300: "#c2c2c2",
  400: "#9b9b9b",
  500: "#6f6f6f",
  600: "#565656",
  700: "#474747",
  800: "#3b3b3b",
  900: "#353535",
}

// Teal ramp around the brand #3c6e71 (darkest = palette navy #284b63).
const tealRamp = {
  DEFAULT: "#3c6e71",
  50: "#eef4f4",
  100: "#d6e6e6",
  200: "#b6d2d3",
  300: "#8fb8ba",
  400: "#6aa0a3",
  500: "#4f8a8d",
  600: "#3c6e71",
  700: "#335d60",
  800: "#284b63",
  900: "#22404f",
}

// Navy ramp around the palette #284b63.
const navyRamp = {
  DEFAULT: "#284b63",
  50: "#eef2f5",
  100: "#d7e0e8",
  200: "#b3c4d2",
  300: "#8aa3b8",
  400: "#5d7f9c",
  500: "#3e6080",
  600: "#284b63",
  700: "#223f53",
  800: "#1d3445",
  900: "#172a37",
}

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
        // Light background = palette white.
        cream: "#ffffff",
        ivory: { 50: "#ffffff", 100: "#fafafa", 200: "#f0f0f0", 300: "#e4e4e4" },
        // Neutrals.
        gray: neutral,
        mist: "#d9d9d9",
        night: { DEFAULT: "#353535", 800: "#3d3d3d", 900: "#2c2c2c" },
        // Brand + secondary.
        teal: tealRamp,
        navy: navyRamp,
        // Off-palette accents folded into the palette so nothing escapes it:
        // warm/positive hues → teal, cool/info hues → navy, negatives → neutral.
        amber: tealRamp,
        emerald: tealRamp,
        green: tealRamp,
        blue: navyRamp,
        purple: navyRamp,
        rose: neutral,
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
