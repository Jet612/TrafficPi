// Sign in and photograph the dashboard, with the lines and zones editor open and a shape selected,
// in light and dark, on a desktop and on a phone. For the README and for looking at the layout.
//
//   E2E_PASSWORD=... node e2e/screenshots.mjs [outputDir]
//
// Needs a running stack (see docs/dev-setup.md) with some lines and zones drawn in it (the made-up
// street after the browser test has run is one), and a Chromium: the apt package
// chromium-headless-shell, or any Chrome set in CHROMIUM.
import { mkdirSync } from "node:fs";
import { chromium } from "playwright-core";

const base = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000";
const user = process.env.E2E_USER ?? "admin";
const password = process.env.E2E_PASSWORD;
const out = process.argv[2] ?? "screenshots";
if (!password) throw new Error("set E2E_PASSWORD");
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium-headless-shell",
  args: ["--no-sandbox", "--disable-gpu"],
});

const views = [
  { name: "desktop", viewport: { width: 1280, height: 900 }, deviceScaleFactor: 1 },
  { name: "phone", viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true },
];

for (const view of views) {
  for (const scheme of ["light", "dark"]) {
    const context = await browser.newContext({ ...view, colorScheme: scheme });
    const page = await context.newPage();
    await page.goto(base);
    await page.getByRole("heading", { name: "Sign in" }).waitFor();
    if (view.name === "desktop") await page.screenshot({ path: `${out}/login-${scheme}.png` });
    await page.getByLabel("Password").fill(password);
    await page.getByLabel("Username").fill(user);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    await page.getByRole("status").filter({ hasText: /Live|offline|Connecting/ }).first().waitFor();
    await page.waitForTimeout(3500); // let the first frames, boxes and statistics arrive
    await page.screenshot({ path: `${out}/${view.name}-${scheme}.png`, fullPage: true });

    // Open the editor (it is folded away once something is drawn) and select the first shape.
    const add = page.getByRole("button", { name: "Add counting line" });
    if (!(await add.isVisible())) await page.getByText("Lines and zones", { exact: true }).click();
    await add.waitFor();
    await page.getByRole("list", { name: "Lines and zones" }).getByRole("button").first().click();
    await page.getByTestId("zone-canvas").waitFor();
    await page.waitForTimeout(800); // the picture
    await page.screenshot({ path: `${out}/${view.name}-${scheme}-editor.png`, fullPage: true });
    await context.close();
  }
}
await browser.close();
console.log(`wrote screenshots to ${out}`);
