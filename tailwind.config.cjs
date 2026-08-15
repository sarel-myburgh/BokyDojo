/** @type {import('tailwindcss').Config} */

/*
 * The palette is the dojo: undyed cotton, sumi ink, one deep red seal.
 *
 * `gray` is deliberately *overridden* rather than supplemented. Every template
 * already reaches for gray-50/200/500/900, so retheming those stops restyles
 * the whole app from one place instead of rewriting twenty-eight files. The
 * stops keep their conventional meaning — 50 is the page, 200 is a hairline,
 * 500 is secondary text, 900 is ink — so existing markup stays correct.
 *
 * Default Tailwind grays are cool and blue-leaning. These are warm: cotton and
 * ink, not plastic and chrome.
 */
const ink = {
  50: "#faf9f6", // gi cotton — the page
  100: "#f3f1ea", // resting panel
  200: "#e4e0d5", // hairline rule
  300: "#cbc5b6", // input border
  400: "#9d9789",
  500: "#746e63", // secondary text
  600: "#565046",
  700: "#3c3833",
  800: "#252220",
  900: "#15130f", // sumi — primary text, obi black, primary action
  950: "#0a0907",
};

/*
 * State colours are warmed to sit in the same world as the ink. Bright default
 * red/green on warm cotton reads as an error in the design, not in the data.
 */
const brick = {
  50: "#fdf4f2",
  100: "#fae3de",
  200: "#f2c4bb",
  300: "#e39d90",
  400: "#cf6f5c",
  500: "#b8442c",
  600: "#9c2a17",
  700: "#8a2415",
  800: "#6f1d11",
  900: "#54160c",
};

const moss = {
  50: "#f4f7f1",
  100: "#e4ece0",
  200: "#c6d9be",
  300: "#a3c096",
  400: "#7ba36c",
  500: "#5a8450",
  600: "#456740",
  700: "#375234",
  800: "#2b402a",
  900: "#22321f",
};

const ochre = {
  50: "#fdf9ee",
  100: "#f9efd6",
  200: "#eeddad",
  300: "#dfc57c",
  400: "#cba748",
  500: "#ab8628",
  600: "#8a6a1d",
  700: "#6f5418",
  800: "#584214",
  900: "#43320f",
};

const indigo = {
  50: "#f2f4f9",
  100: "#e3e8f2",
  200: "#c5cfe3",
  300: "#9fafcd",
  400: "#7288b0",
  500: "#4f6795",
  600: "#3b5079",
  700: "#2f4063",
  800: "#26334e",
  900: "#1d2740",
};

/* The seal. Used sparingly — a mark of emphasis, never a surface. */
const crimson = {
  50: "#fdf3f2",
  100: "#f9dedb",
  200: "#f0b8b2",
  300: "#e08a80",
  400: "#c85445",
  500: "#a4211a",
  600: "#8c1c13",
  700: "#73160f",
  800: "#5c110c",
  900: "#420c08",
};

module.exports = {
  content: ["./templates/**/*.html", "./static/js/**/*.js"],
  theme: {
    extend: {
      colors: {
        gray: ink,
        ink,
        red: brick,
        green: moss,
        amber: ochre,
        blue: indigo,
        crimson,
      },
      fontFamily: {
        /*
         * Khmer faces come *after* the Latin ones so Latin glyphs render in the
         * system UI font and only Khmer falls through to a Khmer face. Putting
         * a Khmer font first makes English text render in it too, which looks
         * subtly wrong everywhere.
         *
         * "Noto Sans Khmer" is bundled and self-hosted — the @font-face lives in
         * static/css/dojo.css. Because the declared family name matches the one
         * already listed here, this stack needs no special casing: Latin resolves
         * to a system face before ever reaching it, and Khmer falls through to
         * the bundled file. The two names after it stay as fallbacks for a
         * browser that somehow fails to fetch the woff2.
         */
        sans: [
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "Noto Sans Khmer",
          "Khmer OS",
          "Leelawadee UI",
          "sans-serif",
        ],
      },
      /*
       * Karate is lines and angles, not pillows. Sharp enough to read as
       * deliberate, not so sharp it reads as unfinished under a thumb.
       */
      borderRadius: {
        none: "0",
        sm: "1px",
        DEFAULT: "2px",
        md: "2px",
        lg: "3px",
        xl: "4px",
        "2xl": "6px",
        full: "9999px",
      },
      /* Flat and structural: hairlines carry the layout, not drop shadows. */
      boxShadow: {
        sm: "0 1px 0 0 rgba(21, 19, 15, 0.04)",
        DEFAULT: "0 1px 2px 0 rgba(21, 19, 15, 0.06)",
      },
    },
  },
  plugins: [],
};
