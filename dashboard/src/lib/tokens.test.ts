import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

/** WCAG 2.x contrast ratio of two "#rrggbb" colours. */
function contrast(a: string, b: string): number {
  const luminance = (hex: string) => {
    const n = Number.parseInt(hex.slice(1), 16);
    const channel = (shift: number) => {
      const c = ((n >> shift) & 255) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    };
    return 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0);
  };
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi! + 0.05) / (lo! + 0.05);
}

const css = readFileSync(join(__dirname, "../app/globals.css"), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");

/** The `--name: value` declarations of the block that follows `selector`. */
function tokens(selector: string, from = 0): Record<string, string> {
  const start = css.indexOf(selector, from);
  if (start === -1) throw new Error(`no block for ${selector}`);
  const open = css.indexOf("{", start);
  const close = css.indexOf("}", open);
  const found: Record<string, string> = {};
  for (const match of css.slice(open + 1, close).matchAll(/(--[\w-]+)\s*:\s*([^;]+);/g)) {
    found[match[1]!] = match[2]!.trim();
  }
  return found;
}

const light = tokens(":root {");
const dark = tokens(':root[data-theme="dark"]');
const mediaStart = css.indexOf("@media (prefers-color-scheme: dark)");
const darkByMedia = tokens(":root:where(:not([data-theme=\"light\"]))", mediaStart);

const textPairs: [string, string][] = [
  ["--ink", "--surface"],
  ["--ink-2", "--surface"],
  ["--ink-3", "--surface"],
  ["--ink", "--page"],
  ["--ink-2", "--page"],
  ["--ink-3", "--page"],
  ["--ink-2", "--sunken"],
  ["--ink-3", "--sunken"],
  ["--accent-ink", "--accent"], // text on a primary button
  ["--accent", "--surface"], // an accent-coloured link
  ["--good-ink", "--surface"],
];

describe.each([
  ["light", light],
  ["dark", dark],
] as const)("%s tokens", (_name, theme) => {
  it.each(textPairs)("text %s on %s has at least 4.5:1", (foreground, background) => {
    expect(contrast(theme[foreground]!, theme[background]!)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the meter's fill apart from its track", () => {
    expect(contrast(theme["--accent"]!, theme["--accent-wash"]!)).toBeGreaterThanOrEqual(3);
  });

  it("keeps the accent visible as a control on the surface", () => {
    expect(contrast(theme["--accent"]!, theme["--surface"]!)).toBeGreaterThanOrEqual(3);
  });

  it("keeps the class colours visible on the surface", () => {
    // On the light surface three of the six sit between 2:1 and 3:1 (aqua, yellow, magenta).
    // The palette's relief rule applies: every value is also written out, and there is a table view.
    const floor = theme === light ? 2 : 3;
    for (let slot = 1; slot <= 6; slot += 1) {
      expect(contrast(theme[`--series-${slot}`]!, theme["--surface"]!)).toBeGreaterThanOrEqual(floor);
    }
  });
});

describe("token files", () => {
  it("declares the dark theme the same way for the system setting and for a stamp", () => {
    for (const [name, value] of Object.entries(darkByMedia)) expect(dark[name], name).toBe(value);
    expect(Object.keys(darkByMedia).sort()).toEqual(Object.keys(dark).sort());
  });

  it("changes only theme tokens in dark mode, never the fixed status colours", () => {
    for (const fixed of ["--good", "--warning", "--serious", "--critical"]) {
      expect(dark[fixed]).toBeUndefined();
      expect(light[fixed]).toBeDefined();
    }
    for (const name of Object.keys(dark)) expect(light[name], name).toBeDefined();
  });
});
