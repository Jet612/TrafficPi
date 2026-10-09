// Sign in and photograph every page of the dashboard (Live, History, Setup with a shape selected,
// System), in light and dark, on a desktop and on a phone. For the README and for looking at the
// layout.
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
    const shot = (name) => page.screenshot({ path: `${out}/${view.name}-${scheme}-${name}.png`, fullPage: true });
    await shot("live");

    // The other pages, by their own addresses (as a bookmark would open them).
    await page.goto(`${base}/history/`);
    await page.getByRole("heading", { level: 1, name: "History" }).waitFor({ state: "attached" });
    await page.waitForTimeout(1200);
    await shot("history");

    await page.goto(`${base}/setup/`);
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });
    await page.getByRole("button", { name: "Add counting line" }).waitFor();
    const shapes = page.getByRole("list", { name: "Lines and zones" }).getByRole("button");
    if ((await shapes.count()) > 0) await shapes.first().click();
    await page.getByTestId("zone-canvas").waitFor();
    await page.waitForTimeout(800); // the picture
    await shot("setup");

    await page.goto(`${base}/system/`);
    await page.getByRole("heading", { level: 1, name: "System" }).waitFor({ state: "attached" });
    await page.waitForTimeout(800);
    await shot("system");
    await context.close();
  }
}
await browser.close();
console.log(`wrote screenshots to ${out}`);
