// A smoke test of the whole dashboard in a real browser, against a running stack.
//
//   E2E_PASSWORD=... node e2e/smoke.mjs
//
// Environment: E2E_BASE_URL (default http://127.0.0.1:8000), E2E_USER (admin), E2E_PASSWORD,
// E2E_EXPECT_LIVE=1 to require a working camera, E2E_DEMO=1 for a stack running the made-up
// street (and to check that its vehicles get counted), E2E_SHOTS=<dir> to keep screenshots, CHROMIUM
// (default /usr/bin/chromium-headless-shell, from the apt package of that name).
//
// The test clears and rewrites the lines and zones of the database it runs against, so point it at
// a development stack, never at one that counts real traffic.
import assert from "node:assert/strict";
import { mkdirSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { chromium } from "playwright-core";

const base = process.env.E2E_BASE_URL ?? "http://127.0.0.1:8000";
const user = process.env.E2E_USER ?? "admin";
const password = process.env.E2E_PASSWORD;
const expectLive = process.env.E2E_EXPECT_LIVE === "1";
// Against `trafficpi-vision run --demo` the traffic is made up, runs left and right across the
// picture, and every vehicle can be counted; the test then checks that counts really arrive.
const demo = process.env.E2E_DEMO === "1";
const shots = process.env.E2E_SHOTS;
if (!password) throw new Error("set E2E_PASSWORD");
if (shots) mkdirSync(shots, { recursive: true });
const scratch = mkdtempSync(join(tmpdir(), "trafficpi-e2e-"));

const browser = await chromium.launch({
  executablePath: process.env.CHROMIUM ?? "/usr/bin/chromium-headless-shell",
  args: ["--no-sandbox", "--disable-gpu"],
});
const context = await browser.newContext({ viewport: { width: 1280, height: 900 }, acceptDownloads: true });
const page = await context.newPage();
page.setDefaultTimeout(20_000);

const problems = [];
page.on("pageerror", (error) => problems.push(`uncaught: ${error.message}`));
page.on("console", (message) => {
  if (message.type() === "error") problems.push(`console: ${message.text()}`);
});

let current = "";
async function step(name, run) {
  current = name;
  await run();
  console.log(`ok   ${name}`);
}
const shot = (name) => (shots ? page.screenshot({ path: `${shots}/${name}.png`, fullPage: true }) : undefined);

// --- the editor -----------------------------------------------------------------------------------

const canvas = () => page.getByTestId("zone-canvas");
/** The corners of the selected shape (the ends of a line), in order. */
const corners = () => canvas().locator('[data-handle="corner"]');
/** The small dots on a selected zone's edges, which add a corner when dragged. */
const edgeDots = () => canvas().locator('[data-handle="edge"]');
const saveButton = () => page.getByRole("button", { name: "Save", exact: true });
const listRows = () => page.getByRole("list", { name: "Lines and zones" }).getByRole("button");

/** The editor is folded away once there is something in it; open it if it is. */
async function openEditor() {
  // Wait for the card to have its profile, which is when it decides whether to open by itself.
  await page.locator("summary", { hasText: "Lines and zones" }).filter({ hasText: /version \d+/ }).waitFor();
  const add = page.getByRole("button", { name: "Add counting line" });
  if (!(await add.isVisible())) await page.getByText("Lines and zones", { exact: true }).click();
  await add.waitFor();
}

const centreOf = async (locator) => {
  const box = await locator.boundingBox();
  assert.ok(box, "the element is on the page");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
};

/** A point in the picture, as a page position: `fx` and `fy` are fractions of its width and height. */
async function inPicture(fx, fy) {
  await canvas().scrollIntoViewIfNeeded();
  const box = await canvas().boundingBox();
  assert.ok(box, "the picture is on the page");
  return { x: box.x + box.width * fx, y: box.y + box.height * fy };
}

async function drag(from, to) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(to.x, to.y, { steps: 12 });
  await page.mouse.up();
}

/** Drag corner `index` of the selected shape to a place in the picture. */
async function dragCorner(index, fx, fy) {
  await canvas().scrollIntoViewIfNeeded();
  await drag(await centreOf(corners().nth(index)), await inPicture(fx, fy));
}

const stored = () =>
  page.evaluate(async () => {
    const response = await fetch("/api/profile");
    return (await response.json()).profile;
  });

const near = (actual, wanted, tolerance, what) =>
  assert.ok(Math.abs(actual - wanted) <= tolerance, `${what}: ${actual} is not within ${tolerance} of ${wanted}`);

/** Wait for `more` further vehicles to be counted today (the made-up street counts about one every 2 s). */
async function waitForCounts(more) {
  const before = Number((await page.getByTestId("today-total").innerText()).replaceAll(",", ""));
  await page.waitForFunction(
    ([selector, wanted]) => Number(document.querySelector(selector)?.textContent?.replaceAll(",", "")) >= wanted,
    ['[data-testid="today-total"]', before + more],
    { timeout: 60_000 },
  );
}

try {
  await step("the login page appears for a visitor", async () => {
    await page.goto(base);
    await page.getByRole("heading", { name: "Sign in" }).waitFor();
    assert.equal(await page.getByLabel("Username").inputValue(), "admin");
  });

  await step("a wrong password is refused, with words and no clue which part was wrong", async () => {
    await page.getByLabel("Password").fill("definitely not the password");
    await page.getByRole("button", { name: "Sign in" }).click();
    // (Next.js keeps an empty alert of its own for route changes, so pick ours by its words.)
    const alert = page.getByRole("alert").filter({ hasText: /username or password/i });
    await alert.waitFor();
    assert.match(await alert.innerText(), /username or password is not right/i);
    assert.equal(await page.getByRole("heading", { name: "Live view" }).count(), 0);
  });

  await step("the right password opens the dashboard", async () => {
    await page.getByLabel("Username").fill(user);
    await page.getByLabel("Password").fill(password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    await page.getByRole("link", { name: "Skip to the dashboard" }).waitFor({ state: "attached" });
  });

  await step("the header reports what the camera is doing", async () => {
    const badge = page.locator("header").getByRole("status");
    await page.waitForFunction(() => !/Connecting|Waiting/.test(document.querySelector("header [role=status]")?.textContent ?? ""));
    const text = await badge.innerText();
    console.log(`     header says: ${text}`);
    if (expectLive) assert.match(text, /^Live/);
    else assert.match(text, /Live|offline|lost/i);
  });

  await step("today's total is a number", async () => {
    const total = await page.getByTestId("today-total").innerText();
    assert.match(total, /^[\d,]+$/);
  });

  await step("everything can be deleted, and the empty profile is saved", async () => {
    await openEditor();
    while ((await listRows().count()) > 0) {
      await listRows().first().click();
      await page.getByRole("button", { name: /^Delete this / }).click();
    }
    await page.getByText("Nothing is drawn yet").waitFor();
    if (await saveButton().isEnabled()) {
      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();
    }
    assert.deepEqual(await stored(), { schema_version: 1, lines: [], detection_zones: [], exclusion_zones: [] });
  });

  // The demo's vehicles drive sideways, so its line is an upright one across both lanes.
  const jitter = Number((Math.random() * 0.2).toFixed(3));
  const lineEnds = demo
    ? [[0.4 + jitter, 0.4], [0.4 + jitter, 0.9]]
    : [[0.05, 0.5 + jitter], [0.95, 0.5 + jitter]];

  await step("a counting line is added, dragged into place, named and saved", async () => {
    await page.getByRole("button", { name: "Add counting line" }).click();
    await corners().first().waitFor();
    assert.equal(await corners().count(), 2, "a line has two ends");
    assert.equal(await saveButton().isEnabled(), true, "a new line is something to save");
    await page.getByRole("button", { name: "Counting line 1", exact: false }).first().waitFor();

    await dragCorner(0, ...lineEnds[0]);
    await dragCorner(1, ...lineEnds[1]);
    await page.getByLabel("Name", { exact: true }).fill("Across the street");
    await page.getByRole("checkbox", { name: "Bicycles" }).check();

    await saveButton().click();
    await page.getByText(/Saved as version \d+/).waitFor();
    assert.equal(await saveButton().isEnabled(), false, "nothing is left to save");

    const profile = await stored();
    assert.equal(profile.lines.length, 1);
    const [line] = profile.lines;
    assert.equal(line.name, "Across the street");
    assert.deepEqual(line.classes, ["car", "truck", "bus", "motorcycle", "bicycle"]);
    // What was dragged is what was stored (the mouse works in whole pixels, so allow for that).
    near(line.a[0], lineEnds[0][0], 0.01, "line end A, x");
    near(line.a[1], lineEnds[0][1], 0.01, "line end A, y");
    near(line.b[0], lineEnds[1][0], 0.01, "line end B, x");
    near(line.b[1], lineEnds[1][1], 0.01, "line end B, y");
  });

  if (demo) {
    await step("vehicles driving across the line are counted, and show up everywhere", async () => {
      await waitForCounts(3);
      // A count has just happened: the list of latest counts and the table must have it too.
      await page.getByRole("list").filter({ has: page.locator("time") }).first().waitFor();
      await shot("counting");
    });
  }

  await step("the line is drawn on the picture", async () => {
    await page.waitForFunction(
      () => {
        const canvas = document.querySelector("canvas");
        if (!canvas) return false;
        const { data } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
        for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
        return false;
      },
      undefined,
      { timeout: expectLive ? 20_000 : 5_000 },
    ).catch((error) => {
      if (expectLive) throw error; // without a camera there is nothing to draw on
    });
  });

  await step("the line is listed under today's counts", async () => {
    await page.getByRole("row", { name: /Across the street/ }).waitFor();
  });

  await step("a detection zone is reshaped with the mouse: a corner is added by its dot and removed by two presses", async () => {
    await page.getByRole("button", { name: "Add detection zone" }).click();
    await edgeDots().first().waitFor();
    assert.equal(await corners().count(), 4, "a new zone is a rectangle");
    assert.equal(await edgeDots().count(), 4, "with a dot on each edge");

    // Pull the middle of the top edge up: that adds a corner there (the second one).
    const dot = await centreOf(edgeDots().first());
    await drag(dot, { x: dot.x, y: dot.y - 30 });
    assert.equal(await corners().count(), 5);
    assert.equal(await edgeDots().count(), 5);

    // Two quick presses on a corner remove it again.
    const added = await centreOf(corners().nth(1));
    await page.mouse.dblclick(added.x, added.y);
    await page.waitForFunction((n) => document.querySelectorAll('[data-handle="corner"]').length === n, 4);

    await page.getByLabel("Name", { exact: true }).fill("The road");
    await saveButton().click();
    await page.getByText(/Saved as version \d+/).waitFor();
    const profile = await stored();
    assert.equal(profile.detection_zones.length, 1);
    assert.equal(profile.detection_zones[0].name, "The road");
    assert.equal(profile.detection_zones[0].points.length, 4);
    assert.equal(profile.lines.length, 1, "the line is still there");
  });

  await step("a drag is one step to undo, and redo brings it back", async () => {
    const start = await centreOf(corners().nth(2));
    const target = { x: start.x - 70, y: start.y - 35 };
    await drag(start, target);
    const moved = await centreOf(corners().nth(2));
    near(moved.x, target.x, 2, "corner after the drag, x");
    assert.equal(await saveButton().isEnabled(), true);

    await page.keyboard.press("Control+z");
    const undone = await centreOf(corners().nth(2));
    near(undone.x, start.x, 2, "corner after undo, x");
    near(undone.y, start.y, 2, "corner after undo, y");
    assert.equal(await saveButton().isEnabled(), false, "back to what is saved");

    await page.keyboard.press("Control+Shift+z");
    const redone = await centreOf(corners().nth(2));
    near(redone.x, target.x, 2, "corner after redo, x");
    await page.keyboard.press("Control+z"); // leave the zone as it was saved
  });

  await step("an outline that crosses itself is flagged and cannot be saved, until it is put right", async () => {
    // Pull the bottom-right corner up over the top edge, which makes a bow tie.
    await dragCorner(2, 0.5, 0.1);
    await page.getByText(/Needs fixing: .*crosses itself/).waitFor();
    await page.getByText("Fix the marked problems to save").waitFor();
    assert.equal(await saveButton().isEnabled(), false);
    await shot("crossing");
    await page.keyboard.press("Control+z");
    await page.getByText(/Needs fixing/).waitFor({ state: "detached" });
  });

  await step("a corner can be moved from the keyboard", async () => {
    const handle = corners().nth(0);
    const before = await centreOf(handle);
    await handle.focus();
    for (let i = 0; i < 5; i += 1) await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Shift+ArrowDown");
    const after = await centreOf(handle);
    near(after.x - before.x, 5, 1.5, "five presses to the right");
    near(after.y - before.y, 10, 1.5, "one Shift press down");
    // Presses close together are one step to undo.
    await page.keyboard.press("Control+z");
    const undone = await centreOf(handle);
    near(undone.x, before.x, 1.5, "after undo, x");
    near(undone.y, before.y, 1.5, "after undo, y");
    assert.equal(await saveButton().isEnabled(), false, "back to what is saved");
  });

  await step("an exclusion zone is added, named and saved, and the live view shows the zones", async () => {
    await page.getByRole("button", { name: "Add exclusion zone" }).click();
    await edgeDots().first().waitFor();
    await page.getByLabel("Name", { exact: true }).fill("Parked cars");
    await saveButton().click();
    await page.getByText(/Saved as version \d+/).waitFor();
    const profile = await stored();
    assert.equal(profile.exclusion_zones.length, 1);

    // The live view draws the zones' outlines. Look along the bottom edge of the exclusion zone
    // (0.85 of the way down, from 0.1 to 0.3 across), where no vehicle's box ever has an edge, and
    // check that something is drawn there, and nothing once the Zones switch is off.
    if (!expectLive) {
      const drawnAlongEdge = () =>
        page.evaluate(() => {
          const canvas = document.querySelector("canvas");
          const row = Math.round(canvas.height * 0.85);
          const { data } = canvas
            .getContext("2d")
            .getImageData(Math.round(canvas.width * 0.1), row - 3, Math.round(canvas.width * 0.2), 7);
          for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
          return false;
        });
      await page.waitForFunction(() => {
        const canvas = document.querySelector("canvas");
        const row = Math.round(canvas.height * 0.85);
        const { data } = canvas
          .getContext("2d")
          .getImageData(Math.round(canvas.width * 0.1), row - 3, Math.round(canvas.width * 0.2), 7);
        for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
        return false;
      });
      const zones = page.getByRole("switch", { name: "Zones" });
      await zones.click();
      assert.equal(await drawnAlongEdge(), false, "with Zones off the outline is gone");
      await zones.click(); // put it back
    }
    await shot("zones");
  });

  if (demo) {
    await step("vehicles are still counted with the zones in place", async () => {
      await waitForCounts(3);
    });
  }

  await step("the picture can be kept, and the profile exported and imported", async () => {
    await page.getByRole("button", { name: /Keep this picture|Replace the saved picture/ }).click();
    await page.getByText(/Picture saved/).waitFor();

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export" }).click();
    const file = await download;
    assert.equal(file.suggestedFilename(), "trafficpi-profile.json");
    const exported = JSON.parse(readFileSync(await file.path(), "utf8"));
    assert.equal(exported.schema_version, 1);
    assert.equal(exported.lines.length, 1);
    assert.equal(exported.detection_zones.length, 1);
    assert.equal(exported.exclusion_zones.length, 1);

    // Import a copy with one thing renamed: it loads as an unsaved change.
    exported.lines[0].name = "Imported street";
    const renamed = join(scratch, "renamed.json");
    writeFileSync(renamed, JSON.stringify(exported));
    await page.locator('input[type="file"]').setInputFiles(renamed);
    await page.getByText(/Loaded renamed\.json\. It is not saved yet/).waitFor();
    await page.getByRole("button", { name: /Imported street/ }).waitFor();
    assert.equal(await saveButton().isEnabled(), true);
    await page.getByRole("button", { name: "Discard changes" }).click();
    await page.getByRole("button", { name: /Across the street/ }).waitFor();
    assert.equal(await page.getByRole("button", { name: /Imported street/ }).count(), 0);
  });

  await step("a file that is not a profile is refused with its reason, and changes nothing", async () => {
    const notJson = join(scratch, "notes.txt");
    writeFileSync(notJson, "{ nope");
    await page.locator('input[type="file"]').setInputFiles(notJson);
    const alert = page.getByRole("alert").filter({ hasText: "notes.txt" });
    await alert.waitFor();
    assert.match(await alert.innerText(), /not valid JSON/);

    const outside = join(scratch, "outside.json");
    writeFileSync(outside, JSON.stringify({ lines: [{ id: "x", name: "x", a: [0, 0], b: [3, 3] }] }));
    await page.locator('input[type="file"]').setInputFiles(outside);
    const refused = page.getByRole("alert").filter({ hasText: "outside.json" });
    await refused.waitFor();
    assert.match(await refused.innerText(), /cannot be used/);
    assert.match(await refused.innerText(), /inside the picture/);
    assert.equal(await saveButton().isEnabled(), false, "nothing was loaded");
  });

  await step("after a reload the editor shows what was saved", async () => {
    await page.reload();
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    await openEditor();
    await page.getByRole("button", { name: /Across the street/ }).waitFor();
    await page.getByRole("button", { name: /The road/ }).waitFor();
    await page.getByRole("button", { name: /Parked cars/ }).waitFor();
    assert.equal(await saveButton().isEnabled(), false);
    await page.getByText(/Picture saved/).waitFor();
    await canvas().scrollIntoViewIfNeeded();
    await shot("editor");
  });

  await step("the overlay switches work and are remembered", async () => {
    const lines = page.getByRole("switch", { name: "Lines" });
    assert.equal(await lines.getAttribute("aria-checked"), "true");
    await lines.click();
    assert.equal(await lines.getAttribute("aria-checked"), "false");
    await page.reload();
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    assert.equal(await page.getByRole("switch", { name: "Lines" }).getAttribute("aria-checked"), "false");
    assert.equal(await page.getByRole("switch", { name: "Zones" }).getAttribute("aria-checked"), "true");
    await page.getByRole("switch", { name: "Lines" }).click(); // put it back
  });

  await step("the chart shows a tooltip with the hour's counts", async () => {
    // Hovering needs the bars on screen, as for a person, who would scroll down to the chart.
    await page.locator(".recharts-wrapper").first().scrollIntoViewIfNeeded();
    // The bars exist a moment before they have a size, and the chart redraws as counts arrive, so
    // find the tallest bar in one go inside the page rather than asking element by element.
    const findTallest = () =>
      page.evaluate(() => {
        let best = null;
        for (const bar of document.querySelectorAll("g.recharts-bar-rectangle path")) {
          const box = bar.getBoundingClientRect();
          if (box.height > 0 && (best === null || box.height > best.height)) best = box;
        }
        return best && { x: best.x + best.width / 2, y: best.y + best.height / 2 };
      });
    let target = null;
    for (let tries = 0; tries < 20 && target === null; tries += 1) {
      target = await findTallest();
      if (target === null) await page.waitForTimeout(250);
    }
    if (target) {
      await page.mouse.move(target.x, target.y);
      await page.getByText(/\d+ counted$/).waitFor();
      await shot("tooltip");
    } else {
      console.log("     (no bars today: tooltip not checked)");
    }
  });

  await step("the same numbers are available as a table", async () => {
    await page.getByRole("button", { name: "Table", pressed: false }).click();
    const table = page.getByRole("table", { name: "Vehicles counted per hour today, by kind" });
    await table.waitFor();
    assert.ok((await table.getByRole("row").count()) >= 24, "a row for each hour, and the header");
    await page.getByRole("button", { name: "Chart", pressed: false }).click();
  });

  await shot("dashboard");

  await step("signing out returns to the login page, and a reload stays there", async () => {
    await page.getByRole("button", { name: "Sign out" }).click();
    await page.getByRole("heading", { name: "Sign in" }).waitFor();
    await page.reload();
    await page.getByRole("heading", { name: "Sign in" }).waitFor();
    const status = await page.evaluate(async () => (await fetch("/api/status")).status);
    assert.equal(status, 401);
  });

  // The browser logs every refused request; the ones above were provoked on purpose.
  const real = problems.filter((problem) => !/Failed to load resource: the server responded with a status of (401|403|422|503)/.test(problem));
  assert.deepEqual(real, [], `the page reported problems:\n${real.join("\n")}`);
  console.log("all steps passed");
} catch (error) {
  console.error(`FAILED at: ${current}`);
  await shot("failure");
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
