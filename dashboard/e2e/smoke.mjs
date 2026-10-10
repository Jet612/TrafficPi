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

// The live socket passes through untouched, except while a step sets `fakeDrift`: then every state
// message carries that camera-drift finding instead of the vision service's own, which is how the
// banner and the System page are looked at without moving a camera (the real check is tested in Python).
let fakeDrift = null;
// The newest state message that came through, which a step can read: what the vision service says about its tracks.
let newestState = null;
await page.routeWebSocket(/\/api\/live$/, (socket) => {
  const server = socket.connectToServer();
  socket.onMessage((message) => server.send(message));
  server.onMessage((message) => {
    if (typeof message === "string") {
      try {
        const data = JSON.parse(message);
        if (data.type === "state") {
          newestState = data;
          if (fakeDrift !== null) return socket.send(JSON.stringify({ ...data, drift: fakeDrift }));
        }
      } catch {
        // not JSON: passed on as it is
      }
    }
    return socket.send(message);
  });
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

const pagesNav = () => page.getByRole("navigation", { name: "Pages" });
const pagePath = () => new URL(page.url()).pathname;

/** Follow the header's link to a page (and do nothing when already there). */
async function goTo(label) {
  const link = pagesNav().getByRole("link", { name: label, exact: true });
  if ((await link.getAttribute("aria-current")) === "page") return;
  await link.click();
  await page.getByRole("heading", { level: 1, name: label }).waitFor({ state: "attached" });
  assert.equal(await pagesNav().getByRole("link", { name: label, exact: true }).getAttribute("aria-current"), "page");
}

/**
 * What explains an option: the (i) beside it and the bubble it opens. The words are not written under
 * the option any more, but they are always in the page (hidden while the bubble is shut), and the
 * option is described by them: `aria-describedby` names them, first.
 */
async function explanation(control) {
  const id = ((await control.getAttribute("aria-describedby")) ?? "").split(" ")[0];
  assert.ok(id, "the option is described by an explanation");
  return { icon: page.locator(`button[aria-label="More information"][aria-describedby="${id}"]`), bubble: page.locator(`[id="${id}"]`) };
}

/** Point at an (i), read what its bubble says, and take the pointer away again. */
async function explainedBy(icon) {
  const bubble = page.locator(`[id="${await icon.getAttribute("aria-describedby")}"]`);
  await icon.hover();
  await bubble.waitFor({ state: "visible" });
  const text = (await bubble.innerText()).trim();
  await page.mouse.move(0, 0);
  await bubble.waitFor({ state: "hidden" });
  return text;
}

/** What the (i) beside an option says (the option is a field or a switch). */
async function explained(control) {
  return explainedBy((await explanation(control)).icon);
}

/** The editor is on the Setup page; wait for it to have its profile. */
async function openEditor() {
  await goTo("Setup");
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

/**
 * Reload, and wait until the page is up: the header's Sign out button is there only once the session is known, which
 * is after the page has been taken over by its scripts. A click made before that finds a button that does nothing yet.
 */
async function reload() {
  await page.reload();
  await page.getByRole("button", { name: "Sign out" }).waitFor();
}

/** Where the keyboard focus is, in a few words: the element and what it is called. */
const focusNow = () =>
  page.evaluate(() => {
    const el = document.activeElement;
    if (!el) return "nothing";
    return `${el.tagName.toLowerCase()}${el.getAttribute("aria-label") ? ` "${el.getAttribute("aria-label")}"` : ""}${el.closest("[data-testid='zone-canvas']") ? " (in the picture)" : ""}`;
  });

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


// --- the Trails card -----------------------------------------------------------------------------

const trailCanvas = () => page.getByTestId("trail-canvas");
const trailHeadline = () => page.getByTestId("trails-headline");
const hourStrip = () => page.getByTestId("hour-strip");
const hourButtons = () => hourStrip().getByRole("button");
const pickedHours = () => hourStrip().locator('button[aria-pressed="true"]');
const dayInput = () => page.getByLabel("Day", { exact: true });
const linesSwitch = () => page.getByRole("switch", { name: "Lines and zones" });
const drawPaths = () => page.getByRole("button", { name: "Paths", exact: true });
const drawHeat = () => page.getByRole("button", { name: "Heat map", exact: true });

/** How many pixels of the drawing over the picture are not clear. */
const drawnPixels = () =>
  trailCanvas().evaluate((canvas) => {
    const { data } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
    let drawn = 0;
    for (let at = 3; at < data.length; at += 4) if (data[at] > 0) drawn += 1;
    return drawn;
  });

/** What the drawing has at a point: a fraction of its width and of its height. */
const pixelAt = (fx, fy) =>
  trailCanvas().evaluate(
    (canvas, [x, y]) => [...canvas.getContext("2d").getImageData(Math.round(canvas.width * x), Math.round(canvas.height * y), 1, 1).data],
    [fx, fy],
  );

/** Try a check until it holds: what the card shows follows a request that is still on its way. */
async function eventually(check, what, timeoutMs = 10_000) {
  const deadline = Date.now() + timeoutMs;
  for (;;) {
    try {
      return await check();
    } catch (error) {
      if (Date.now() > deadline) throw new Error(`${what}: ${error instanceof Error ? error.message : error}`);
      await page.waitForTimeout(100);
    }
  }
}

/** "Thursday 8 October 2026", as the card writes a day. */
const dayTitle = (day) =>
  new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(`${day}T00:00:00Z`))
    .replace(",", "");

/** The card has finished fetching: nothing is dimmed or on its way. */
const settled = () =>
  eventually(async () => assert.equal(await page.getByTestId("trail-view").getAttribute("aria-busy"), "false"), "the Trails card settles");

/** Show a day, wait for it, and pick all of it. */
async function showDay(day) {
  await dayInput().fill(day);
  await eventually(async () => assert.match(await trailHeadline().innerText(), new RegExp(dayTitle(day))), `the card shows ${day}`);
  await settled();
}

/** The number of trails in each hour, from the names of the hour buttons ("17:00–18:00: 512 vehicles"). */
async function hourCountsShown() {
  const labels = await hourButtons().evaluateAll((buttons) => buttons.map((button) => button.getAttribute("aria-label") ?? ""));
  return labels.map((label) => Number((/: (?:no|([\d,]+)) /.exec(label)?.[1] ?? "0").replaceAll(",", "")));
}

/** The hours an hour button is for, as it says so: "17:00–18:00". (Not worked out from its place in the row: a day can have 23 or 25.) */
const rangeOf = async (index) => ((await hourButtons().nth(index).getAttribute("aria-label")) ?? "").slice(0, 11);
/** The hours from one button to another: "02:00–06:00". */
const rangeBetween = async (first, last) => `${(await rangeOf(first)).slice(0, 5)}–${(await rangeOf(last)).slice(6)}`;

/** The count at the start of the headline: "4,799 vehicles (drawing ...) · Thursday ..." */
async function headlineCount() {
  return Number(/^([\d,]+) /.exec(await trailHeadline().innerText())?.[1].replaceAll(",", ""));
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
    await goTo("Live");
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
    assert.deepEqual(await stored(), { schema_version: 3, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [], calibration: null });
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
      await goTo("Live");
      await waitForCounts(3);
      // A count has just happened: the list of latest counts and the table must have it too.
      await page.getByRole("list").filter({ has: page.locator("time") }).first().waitFor();
      await shot("counting");
    });
  }

  await step("the line is drawn on the picture", async () => {
    await goTo("Live");
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
    await goTo("Live");
    await page.getByRole("row", { name: /Across the street/ }).waitFor();
  });

  await step("a detection zone is reshaped with the mouse: a corner is added by its dot and removed by two presses", async () => {
    await openEditor();
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
    // Undo works wherever the keyboard focus is, not only inside the picture (Chrome's own Ctrl+Z would
    // otherwise undo the last text typed in a field, such as the zone's name).
    await page.evaluate(() => document.activeElement?.blur?.());
    await page.keyboard.press("Control+z");
    const settled = await centreOf(corners().nth(2));
    near(settled.x, start.x, 2, "corner after undoing with the focus elsewhere, x");
    near(settled.y, start.y, 2, "corner after undoing with the focus elsewhere, y");
    assert.equal(await saveButton().isEnabled(), false, "back to what is saved");
    assert.equal(await page.getByLabel("Name", { exact: true }).inputValue(), "The road", "and the zone's name is untouched");
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

    await goTo("Live");
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
      await goTo("Live");
      await waitForCounts(3);
    });
  }

  if (demo) {
    // The made-up street's near lane (the lower one) drives left to right at 0.8 of the picture's
    // height, the far lane right to left at 0.65. A stop sign across both, going left to right,
    // judges the near lane's vehicles and leaves the far lane's alone.
    await step("a stop sign is added, reshaped, set up and saved", async () => {
      await openEditor();
      await page.getByRole("button", { name: "Add stop sign" }).click();
      await edgeDots().first().waitFor();
      assert.equal(await corners().count(), 6, "the four corners of the zone and the two ends of the line");
      assert.equal(await edgeDots().count(), 4, "a dot on each edge of the zone, and none on the line");
      await page.getByRole("button", { name: /^Stop sign 1/ }).first().waitFor();
      assert.equal(await saveButton().isEnabled(), true, "a new stop sign is something to save");

      const zone = [[0.15, 0.55], [0.5, 0.55], [0.5, 0.9], [0.15, 0.9]];
      for (const [index, [fx, fy]] of zone.entries()) await dragCorner(index, fx, fy);
      await dragCorner(4, 0.5, 0.94); // the ends of the stop line reach past the zone
      await dragCorner(5, 0.5, 0.5);
      await page.getByLabel("Name", { exact: true }).fill("Elm Street");
      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();

      const profile = await stored();
      assert.equal(profile.stop_signs.length, 1);
      const [sign] = profile.stop_signs;
      assert.equal(sign.name, "Elm Street");
      assert.equal(sign.direction, 1);
      assert.deepEqual(sign.classes, ["car", "truck", "bus", "motorcycle"], "bicycles and people are not asked to stop");
      assert.deepEqual([sign.hold_s, sign.still_radius, sign.roll_speed, sign.min_observed_s], [1, 0.08, 0.7, 0.8]);
      sign.approach.forEach((corner, index) => {
        near(corner[0], zone[index][0], 0.01, `corner ${index}, x`);
        near(corner[1], zone[index][1], 0.01, `corner ${index}, y`);
      });
      near(sign.a[1], 0.94, 0.01, "the line's end A");
      near(sign.b[1], 0.5, 0.01, "the line's end B");
      near(sign.a[0], 0.5, 0.01, "the line's x");
      await shot("stop-sign-editor");
    });

    await step("the live view draws the stop sign in red, and the made-up traffic is judged at it", async () => {
      await goTo("Live");
      const redAtTheLine = () =>
        page.evaluate(() => {
          const canvas = document.querySelector("canvas");
          if (!canvas) return false;
          // Along the stop line, but away from its middle, where the arrow is, and from the counting line beside it.
          const row = Math.round(canvas.height * 0.6);
          const { data } = canvas.getContext("2d").getImageData(Math.round(canvas.width * 0.5) - 2, row, 5, 1);
          for (let i = 0; i < data.length; i += 4) if (data[i] > 180 && data[i + 1] < 90 && data[i + 2] < 90 && data[i + 3] > 200) return true;
          return false;
        });
      await eventually(async () => assert.equal(await redAtTheLine(), true), "a red stop line is drawn", 15_000);
      const lines = page.getByRole("switch", { name: "Lines" });
      await lines.click();
      await eventually(async () => assert.equal(await redAtTheLine(), false), "and goes with the Lines switch");
      await lines.click();
      await eventually(async () => assert.equal(await redAtTheLine(), true));

      const sign = (await stored()).stop_signs[0];
      // (an earlier run of this test, on the same database, may have left events of signs that are gone)
      const stops = () => page.evaluate(async (id) => (await (await fetch("/api/events?type=stop&limit=200")).json()).filter((row) => row.line === id), sign.id);
      await eventually(async () => assert.ok((await stops()).length >= 2, "a vehicle has been judged"), "vehicles are judged at the stop sign", 90_000);
      const rows = await stops();
      assert.ok(rows.every((row) => row.type === "stop" && row.direction === null), "each event is a stop, which has no direction of its own");
      assert.ok(rows.every((row) => ["car", "truck", "bus", "motorcycle"].includes(row.label)), `only the kinds that must stop: ${[...new Set(rows.map((r) => r.label))]}`);
      await shot("stop-sign-live");
    });

    await step("a stop sign warns when its zone is on the wrong side, and its numbers can be set and reset", async () => {
      await openEditor();
      await page.getByRole("button", { name: /Elm Street/ }).first().click();
      const panel = page.getByRole("region", { name: "Elm Street: properties" });
      await panel.waitFor();
      assert.equal(await page.getByText(/Check: the approach zone is on the far side/).count(), 0, "as drawn, nothing to warn about");

      await panel.getByRole("button", { name: "Flip direction" }).click();
      await page.getByText(/Check: the approach zone is on the far side of the stop line/).waitFor();
      assert.equal(await saveButton().isEnabled(), true, "a warning is not an error: it can still be saved");
      await panel.getByRole("button", { name: "Flip direction" }).click();
      await page.getByText(/Check: the approach zone is on the far side/).waitFor({ state: "detached" });

      await panel.getByText("What counts as a stop").click();
      const hold = panel.getByLabel("Standing still for at least");
      await hold.fill("2.5");
      await panel.getByLabel("Moving less than").fill("12");
      await hold.focus(); // leaving the other box is one undo step
      assert.equal(await saveButton().isEnabled(), true);
      await hold.fill("50"); // more than the server takes: said so, and not applied
      await panel.getByText("From 0.2 to 10.").waitFor();
      await hold.blur();
      assert.equal(await hold.inputValue(), "2.5", "the box goes back to the number in use");
      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();
      const tuned = (await stored()).stop_signs[0];
      assert.deepEqual([tuned.hold_s, tuned.still_radius], [2.5, 0.12]);

      await panel.getByRole("button", { name: "Back to the usual" }).click();
      assert.equal(await hold.inputValue(), "1");
      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();
      const usual = (await stored()).stop_signs[0];
      assert.deepEqual([usual.hold_s, usual.still_radius, usual.roll_speed, usual.min_observed_s], [1, 0.08, 0.7, 0.8]);

      // the kinds that must stop: bicycles ticked, then cars unticked, and none left cannot be saved
      await panel.getByRole("checkbox", { name: "Bicycles" }).check();
      for (const kind of ["Cars", "Trucks", "Buses", "Motorcycles", "Bicycles"]) await panel.getByRole("checkbox", { name: kind }).uncheck();
      await page.getByText(/choose at least one kind of vehicle that has to stop/).first().waitFor();
      assert.equal(await saveButton().isEnabled(), false, "a stop sign that no kind must stop is refused");
      await page.getByRole("button", { name: "Discard changes" }).click();
      await page.getByText(/choose at least one kind/).first().waitFor({ state: "detached" });
    });

    /** Select the speed calibration in the list (a press on a selected row would let go of it). */
    const selectCalibration = async () => {
      if ((await corners().count()) === 0) await page.getByRole("button", { name: /^Speed calibration/ }).first().click();
      await corners().first().waitFor();
    };

    // The made-up street runs left and right across the picture, so its road is a horizontal band:
    // a rectangle laid along it has its length across the picture and its width up and down. The
    // street is flat, so the map is an even scale, and the speeds it gives are those of the drawing.
    await step("a speed calibration is added, laid on the road, sized and tested with a known distance", async () => {
      await openEditor();
      await page.getByRole("button", { name: "Add speed calibration" }).click();
      await corners().first().waitFor();
      assert.equal(await corners().count(), 4, "the four corners of the rectangle");
      assert.equal(await edgeDots().count(), 0, "a rectangle keeps its four corners: none can be added");
      assert.equal(await page.getByRole("button", { name: "Add speed calibration" }).isDisabled(), true, "a camera has one road");
      await page.getByRole("button", { name: /^Speed calibration/ }).first().waitFor();

      const rectangle = [[0.15, 0.5], [0.15, 0.88], [0.85, 0.88], [0.85, 0.5]]; // corner 1 to 2 is across the road, 2 to 3 along it
      for (const [index, [fx, fy]] of rectangle.entries()) await dragCorner(index, fx, fy);
      const panel = page.getByRole("region", { name: "Speed calibration: properties" });
      await panel.waitFor();
      assert.equal(await panel.getByLabel("Name", { exact: true }).count(), 0, "the calibration has no name of its own");
      await panel.getByLabel("Lengths in").selectOption("m");
      const width = panel.getByLabel("Width, across the road");
      const length = panel.getByLabel("Length, along the road");
      await width.fill("7");
      await length.fill("30");
      await length.blur();
      assert.match(await explained(width), /corner 1 to corner 2/);
      assert.match(await explained(length), /corner 2 to corner 3/);

      // the server says where speeds will be given, and the picture shades it
      await panel.getByText(/Speeds are given only where the calibration is expected to be right to within 10%/).waitFor();
      await canvas().getByTestId("believed-area").waitFor();
      const shaded = await canvas().getByTestId("believed-area").locator("rect").count();
      assert.ok(shaded > 20, `a good part of the picture is shaded (${shaded} cells)`);
      await panel.getByLabel("Shade where speeds are given").uncheck();
      await canvas().getByTestId("believed-area").waitFor({ state: "detached" });
      await panel.getByLabel("Shade where speeds are given").check();
      await canvas().getByTestId("believed-area").waitFor();

      // a known distance: along the middle of the road to begin with, and exactly 15 m on this even scale
      await panel.getByRole("button", { name: "Add a known distance" }).click();
      const check = page.getByRole("region", { name: "Known distance 1: properties" });
      await check.waitFor();
      assert.equal(await check.getByLabel("Its length").inputValue(), "15", "half the rectangle's length to begin with");
      await check.getByText(/Agrees: the map finds 15 m, the same as the 15 m you typed/).waitFor();
      await check.getByLabel("Its length").fill("25");
      await check.getByLabel("Its length").blur();
      await check.getByText(/Does not agree: the map finds 15 m, 40% shorter than the 25 m you typed/).waitFor();
      await check.getByLabel("Its length").fill("15");
      await check.getByLabel("Its length").blur();
      await check.getByText(/Agrees:/).waitFor();
      await shot("speed-calibration-editor");

      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();
      const { calibration } = await stored();
      assert.ok(calibration, "the calibration is stored");
      assert.deepEqual([calibration.width_m, calibration.length_m], [7, 30]);
      calibration.corners.forEach((corner, index) => {
        near(corner[0], rectangle[index][0], 0.01, `corner ${index + 1}, x`);
        near(corner[1], rectangle[index][1], 0.01, `corner ${index + 1}, y`);
      });
      assert.equal(calibration.checks.length, 1);
      assert.deepEqual([calibration.checks[0].name, calibration.checks[0].length_m], ["Known distance 1", 15]);
    });

    await step("corners dragged into a bow tie are refused in words, and the draft can be undone", async () => {
      await openEditor();
      await selectCalibration();
      await dragCorner(1, 0.85, 0.5); // corner 2 onto corner 4's place: the sides cross
      await page.getByText(/Needs fixing: the corners must go round the rectangle one after the other, without crossing/).waitFor();
      await page.getByText(/This calibration cannot be used/).waitFor();
      assert.equal(await saveButton().isEnabled(), false, "a calibration that cannot be a rectangle is refused");
      await page.getByRole("button", { name: "Discard changes" }).click();
      await page.getByText(/Needs fixing: the corners/).waitFor({ state: "detached" });
    });

    await step("the live view carries each vehicle's speed once the camera is calibrated", async () => {
      await goTo("Live");
      await eventually(
        async () => {
          assert.ok(newestState?.calibration, "the vision service says the camera is calibrated");
          assert.deepEqual([newestState.calibration.width_m, newestState.calibration.length_m], [7, 30]);
          const speeds = newestState.tracks.filter((track) => typeof track.speed_ms === "number");
          assert.ok(speeds.length > 0, "a vehicle has a speed");
          assert.ok(speeds.every((track) => track.speed_ms >= 0 && track.speed_ms < 20), `plausible speeds: ${speeds.map((t) => t.speed_ms)}`);
        },
        "vehicles are given speeds",
        45_000,
      );
    });

    await step("the Data page says how fast the counted vehicles went, and what that is worth", async () => {
      const speeds = () => page.evaluate(async () => (await (await fetch("/api/stats/speeds?days=1")).json()));
      await eventually(async () => assert.ok((await speeds()).figures?.count >= 3, "vehicles counted with a speed"), "counts get speeds", 90_000);
      const stats = await speeds();
      assert.equal(typeof stats.calibration, "string", "the calibration in force is named");
      assert.equal(stats.excluded, 0, "all from the one calibration");
      assert.ok(stats.figures.fastest < 40 && stats.figures.slowest > 0, `plausible speeds: ${stats.figures.slowest} to ${stats.figures.fastest} mph`);
      assert.ok(stats.counted >= stats.figures.count, "no more speeds than vehicles");

      await goTo("Data");
      await page.getByTestId("data-speeds").waitFor();
      assert.match(await page.getByTestId("data-speeds-p85").innerText(), /^\d+ (mph|km\/h)$/);
      await page.getByTestId("data-speeds-histogram").waitFor();
      assert.match(await page.getByTestId("data-speeds").innerText(), /has not yet been checked against a vehicle of known speed/);
      await page.getByTestId("data-speeds-by-line").waitFor();
      await shot("data-speeds");
    });

    await step("the speed calibration can be taken away again", async () => {
      await openEditor();
      await selectCalibration();
      await page.getByRole("region", { name: "Speed calibration: properties" }).getByRole("button", { name: "Delete this speed calibration" }).click();
      assert.equal(await corners().count(), 0, "the rectangle and its known distance are gone");
      await saveButton().click();
      await page.getByText(/Saved as version \d+/).waitFor();
      assert.equal((await stored()).calibration, null);
      assert.equal(await page.getByRole("button", { name: "Add speed calibration" }).isEnabled(), true);

      // with none in force, the speeds it measured are counted nowhere, and the Data page says how many it leaves out
      const after = await page.evaluate(async () => (await (await fetch("/api/stats/speeds?days=1")).json()));
      assert.equal(after.calibration, null);
      assert.equal(after.figures, null, "no calibration in force: no speed is counted");
      assert.ok(after.excluded >= 3, `the speeds of the calibration taken away are left out (${after.excluded})`);
      await goTo("Data");
      await page.getByTestId("data-speeds-none").waitFor();
      assert.match(await page.getByTestId("data-speeds-none").innerText(), /with a speed from an earlier calibration (is|are) left out/);
    });
  }

  await step("the picture can be kept, and the profile exported and imported", async () => {
    await openEditor();
    await page.getByRole("button", { name: /Keep this picture|Replace the saved picture/ }).click();
    await page.getByText(/Picture saved/).waitFor();

    const download = page.waitForEvent("download");
    await page.getByRole("button", { name: "Export" }).click();
    const file = await download;
    assert.equal(file.suggestedFilename(), "trafficpi-profile.json");
    const exported = JSON.parse(readFileSync(await file.path(), "utf8"));
    assert.equal(exported.schema_version, 3);
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
    await page.reload(); // on the Setup page: its address is its own
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });
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
    await goTo("Live");
    const lines = page.getByRole("switch", { name: "Lines" });
    assert.equal(await lines.getAttribute("aria-checked"), "true");
    await lines.click();
    assert.equal(await lines.getAttribute("aria-checked"), "false");
    await page.reload();
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    // what was kept is read from the browser's storage once the page is up, so it is waited for
    await eventually(async () => {
      assert.equal(await page.getByRole("switch", { name: "Lines" }).getAttribute("aria-checked"), "false");
      assert.equal(await page.getByRole("switch", { name: "Zones" }).getAttribute("aria-checked"), "true");
    }, "the switches are as they were left");
    await page.getByRole("switch", { name: "Lines" }).click(); // put it back
  });

  // The picture and what is drawn over it can fill the screen. Where the browser has no full screen for
  // a div (an iPhone's Safari), the page lays the picture over the whole window instead.
  const insideTheOverlayCanvas = () =>
    page.waitForFunction(
      () => {
        const canvas = document.querySelector("canvas");
        if (!canvas || canvas.width === 0) return false;
        const { data } = canvas.getContext("2d").getImageData(0, 0, canvas.width, canvas.height);
        for (let i = 3; i < data.length; i += 4) if (data[i] > 0) return true;
        return false;
      },
      undefined,
      { timeout: 8_000 },
    );

  await step("the video goes full screen by an overlay where the browser has none, and comes back", async () => {
    await page.evaluate(() => {
      Element.prototype.requestFullscreen = undefined;
      Element.prototype.webkitRequestFullscreen = undefined;
    });
    await goTo("Live");
    const stage = page.getByTestId("live-stage");
    const frame = page.getByTestId("live-frame");
    await stage.scrollIntoViewIfNeeded();
    const window = page.viewportSize();

    await page.getByRole("button", { name: "Full screen", exact: true }).click();
    await page.getByRole("dialog", { name: "Live view, full screen" }).waitFor();
    const covered = await stage.boundingBox();
    assert.deepEqual([covered.x, covered.y, covered.width, covered.height], [0, 0, window.width, window.height], "the stage fills the window");
    const picture = await frame.boundingBox();
    assert.ok(picture.width <= window.width + 1 && picture.height <= window.height + 1, "the whole picture is inside the window");
    assert.ok(Math.abs(picture.width - window.width) < 2 || Math.abs(picture.height - window.height) < 2, "and as large as it can be");
    assert.ok(Math.abs(picture.width / picture.height - 16 / 9) < 0.05, "with its own proportions");
    assert.equal(await page.evaluate(() => document.body.style.overflow), "hidden", "the page behind does not scroll");
    if (demo || expectLive) await insideTheOverlayCanvas(); // the boxes, lines and zones are still drawn
    await shot("fullscreen");

    await page.keyboard.press("Escape");
    await page.getByRole("dialog", { name: "Live view, full screen" }).waitFor({ state: "detached" });
    assert.equal(await page.evaluate(() => document.body.style.overflow), "", "scrolling is back");
    assert.equal(await page.getByRole("button", { name: "Full screen", exact: true }).count(), 1);

    await frame.dblclick({ position: { x: 200, y: 150 } }); // a double-click on the picture toggles it too
    await page.getByRole("dialog", { name: "Live view, full screen" }).waitFor();
    await page.getByRole("button", { name: "Exit full screen" }).click();
    await page.getByRole("dialog", { name: "Live view, full screen" }).waitFor({ state: "detached" });
  });

  await step("the video goes full screen by the browser where it can, and comes back", async () => {
    await reload(); // gives back the browser's own call
    await page.getByRole("heading", { name: "Live view" }).waitFor();
    const stage = page.getByTestId("live-stage");
    await stage.scrollIntoViewIfNeeded();
    await page.getByRole("button", { name: "Full screen", exact: true }).click();
    // Either the browser took it (the stage is the full screen element) or it refused and the overlay took over.
    await page.waitForFunction(() => document.fullscreenElement !== null || document.querySelector('[role="dialog"]') !== null);
    const native = await page.evaluate(() => document.fullscreenElement?.getAttribute("data-testid") === "live-stage");
    console.log(`     full screen by: ${native ? "the browser" : "the overlay (this browser refused)"}`);
    await page.getByRole("button", { name: "Exit full screen" }).waitFor();
    await page.getByRole("button", { name: "Exit full screen" }).click();
    await page.getByRole("button", { name: "Full screen", exact: true }).waitFor();
    assert.equal(await page.evaluate(() => document.fullscreenElement), null);
    assert.equal(await page.getByRole("dialog").count(), 0);
  });

  await step("what deleted lines counted is one note under the table, not a row for each", async () => {
    await goTo("Live");
    const today = (over) => ({
      date: "2026-10-09",
      timezone: "UTC",
      total: 15,
      by_class: { car: 12, truck: 3 },
      removed: 5,
      lines: [{ id: "north", name: "Northbound", forward: 8, reverse: 2, by_class: { car: 8, truck: 2 } }],
      hourly: [{ t: Date.UTC(2026, 9, 9), hour: 0, count: 15 }],
      ...over,
    });
    const route = "**/api/stats/today";
    const answer = (body) => (handler) => handler.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(body) });
    await page.route(route, answer(today()));
    await page.reload();
    await eventually(async () => assert.equal(await page.getByTestId("today-total").innerText(), "15"));
    const rows = page.getByRole("table", { name: "Crossings by counting line" }).locator("tbody tr");
    assert.equal(await rows.count(), 1, "one row, for the one line that exists");
    assert.match(await rows.first().innerText(), /Northbound\s+8\s+2/);
    assert.equal(await page.getByTestId("removed-lines-note").innerText(), "5 more were counted by lines that have since been removed.");

    await page.unroute(route);
    await page.route(route, answer(today({ removed: 1, total: 11 })));
    await page.reload();
    await eventually(async () => assert.equal(await page.getByTestId("removed-lines-note").innerText(), "1 more was counted by a line that has since been removed."));

    await page.unroute(route);
    await page.route(route, answer(today({ removed: 7, total: 7, by_class: { car: 7 }, lines: [] })));
    await page.reload();
    await page.getByText("All of these were counted by lines that have since been removed.").waitFor();
    assert.equal(await page.getByRole("table", { name: "Crossings by counting line" }).count(), 0, "no table when no line exists");
    await page.getByRole("link", { name: "Add a line on the Setup page" }).click();
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });

    await page.unroute(route);
    await goTo("Live");
    await page.reload();
    await page.getByTestId("today-total").waitFor();
  });

  await step("the chart shows a tooltip with the hour's counts", async () => {
    await goTo("History");
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

  // The made-up street records trails as its vehicles leave the picture, and with --history-days N
  // it has N earlier days of them, which hold still, so that numbers can be compared exactly.
  const oldestDay = async () => {
    const first = await dayInput().getAttribute("min");
    const last = await dayInput().getAttribute("max");
    assert.ok(first && last && first < last, "the made-up street needs --history-days 3, to have earlier days (see docs/dev-setup.md)");
    return first;
  };

  if (demo) {
    await step("the Stop signs card says what the vehicles did, lists the passes that need a look, and a review is kept", async () => {
      await goTo("History");
      const passes = page.getByRole("list", { name: "Passes at stop signs" });
      const rowsOf = () => passes.getByRole("listitem");
      await eventually(async () => assert.match(await page.getByTestId("stops-headline").innerText(), /judged: /), "the summary says what was seen");
      const tile = (outcome) => page.getByRole("list", { name: "Outcomes" }).getByRole("button", { name: new RegExp(`^${outcome}`) });
      assert.equal(await tile("Did not stop").getAttribute("aria-pressed"), "true", "what needs a look is shown to begin with");
      assert.equal(await tile("Stopped").getAttribute("aria-pressed"), "false", "and a clean stop is not");
      await eventually(async () => assert.ok((await rowsOf().count()) >= 1, "a pass is listed"));
      const kinds = await rowsOf().evaluateAll((items) => items.map((item) => item.getAttribute("data-outcome")));
      assert.ok(kinds.every((kind) => ["rolling", "did_not_stop", "indeterminate"].includes(kind)), `only those that need a look: ${kinds}`);

      // a tile that is pressed again takes its outcome off the list, and one pressed adds it
      await tile("Did not stop").click();
      await eventually(async () => {
        const now = await rowsOf().evaluateAll((items) => items.map((item) => item.getAttribute("data-outcome")));
        assert.ok(now.every((kind) => kind !== "did_not_stop"), `no passes that did not stop are left: ${now}`);
      });
      await tile("Did not stop").click();
      await eventually(async () => assert.ok((await rowsOf().evaluateAll((items) => items.some((i) => i.getAttribute("data-outcome") === "did_not_stop"))) === true));

      // the quick choice of outcome does the same in one press: only rolls, only passes that did not stop, both
      const focus = (name) => page.getByRole("group", { name: "Outcome" }).getByRole("button", { name, exact: true });
      const shown = async () => [...new Set(await rowsOf().evaluateAll((items) => items.map((item) => item.getAttribute("data-outcome"))))].sort();
      await focus("Did not stop").click();
      assert.equal(await focus("Did not stop").getAttribute("aria-pressed"), "true");
      assert.equal(await tile("Did not stop").getAttribute("aria-pressed"), "true", "the tile follows");
      assert.equal(await tile("Rolled through").getAttribute("aria-pressed"), "false");
      await eventually(async () => assert.deepEqual(await shown(), ["did_not_stop"]), "only passes that did not stop");
      await focus("Rolled through").click();
      await eventually(async () => assert.ok((await shown()).every((kind) => kind === "rolling"), `only rolls: ${await shown()}`));
      await focus("Both of those").click();
      assert.equal(await tile("Could not tell").getAttribute("aria-pressed"), "false", "both leaves out what could not be told");
      await eventually(async () => assert.ok((await shown()).every((kind) => kind === "rolling" || kind === "did_not_stop")));
      await focus("All that need a look").click();
      assert.equal(await tile("Could not tell").getAttribute("aria-pressed"), "true", "and the usual list is one press away");
      // the rows on show are those of the choice before until the answer for this one comes, and the made-up street has
      // passed another vehicle by then: wait for it, or the pass that is read below is not the one that is pressed
      await eventually(async () => assert.equal(await passes.getAttribute("aria-busy"), "false"), "the list has caught up with the choice");

      // the newest pass is called a false alarm: it is kept, found under "False alarms", and taken back
      const first = rowsOf().first();
      const when = await first.locator("time").innerText();
      const alarm = first.getByRole("button", { name: /^False alarm: / });
      await alarm.click();
      await eventually(async () => assert.equal(await alarm.getAttribute("aria-pressed"), "true"));
      await reload();
      await page.getByRole("button", { name: "False alarms", exact: true }).click();
      await eventually(async () => assert.ok((await passes.locator("time", { hasText: when }).count()) >= 1, "the reviewed pass is under False alarms, after a reload"));
      const marked = rowsOf().filter({ has: page.locator("time", { hasText: when }) }).first();
      assert.equal(await marked.getAttribute("data-review"), "false_positive");
      await marked.getByRole("button", { name: /^False alarm: / }).click(); // pressed again: taken back
      await eventually(async () => assert.equal(await passes.locator("time", { hasText: when }).count(), 0, "it is no longer a false alarm"));

      // and one is confirmed, which is a different button and a different state
      await page.getByRole("button", { name: "All", exact: true }).click();
      await eventually(async () => assert.ok((await rowsOf().count()) >= 1));
      // pinned by its time: a review asks for the list again, and a newer pass may be on top of it by then
      const confirmedWhen = await rowsOf().first().locator("time").innerText();
      const second = rowsOf().filter({ has: page.locator("time", { hasText: confirmedWhen }) }).first();
      await second.getByRole("button", { name: /^Confirm: / }).click();
      await eventually(async () => assert.equal(await second.getAttribute("data-review"), "confirmed"));
      assert.equal(await second.getByRole("button", { name: /^False alarm: / }).getAttribute("aria-pressed"), "false");
      await second.getByRole("button", { name: /^Confirm: / }).click(); // leave things as they were
      await eventually(async () => assert.equal(await second.getAttribute("data-review"), "none"));

      // the period is kept
      await page.getByRole("button", { name: "7 days", exact: true }).click();
      await page.reload();
      await eventually(async () => assert.equal(await page.getByRole("button", { name: "7 days", exact: true }).getAttribute("aria-pressed"), "true"), "the period is the one that was kept");
      await page.getByRole("button", { name: "24 hours", exact: true }).click();
      await shot("stop-signs-card");
    });

    await step("a pass that needs a look has its picture and clip, which open in a window where it can be reviewed", async () => {
      await goTo("History");
      const passes = page.getByRole("list", { name: "Passes at stop signs" });
      const clipButtons = () => passes.getByRole("button", { name: /^Watch the clip of the / });
      // The vision service writes the picture and the clip a few seconds after the pass, and the list is asked again every 15 s.
      await eventually(async () => assert.ok((await clipButtons().count()) >= 1, "a pass is listed with its clip"), "evidence is kept of a pass", 90_000);

      const name = await clipButtons().first().getAttribute("aria-label");
      assert.ok(name, "the thumbnail says which pass it is of");
      const opener = passes.getByRole("button", { name }); // by its name: the list may be asked again, and the newest change
      const picture = opener.locator("img");
      await eventually(async () => assert.ok((await picture.evaluate((img) => img.complete && img.naturalWidth)) >= 320, "the picture has loaded"));
      const snapshotUrl = await picture.getAttribute("src");
      assert.match(snapshotUrl ?? "", /^\/api\/stops\/\d+\/snapshot\.jpg$/);
      const clipUrl = (snapshotUrl ?? "").replace("snapshot.jpg", "clip.mp4");

      // what the server sends is a real JPEG and a real MP4, as small as the detector's own picture
      const fetched = await page.evaluate(
        async ([jpeg, mp4]) => {
          const look = async (url, headers = {}) => {
            const response = await fetch(url, { headers });
            const bytes = new Uint8Array(await response.arrayBuffer());
            return { status: response.status, type: response.headers.get("content-type"), range: response.headers.get("content-range"), size: bytes.length, head: [...bytes.slice(0, 12)] };
          };
          return { jpeg: await look(jpeg), mp4: await look(mp4), part: await look(mp4, { Range: "bytes=0-99" }) };
        },
        [snapshotUrl, clipUrl],
      );
      assert.equal(fetched.jpeg.status, 200);
      assert.equal(fetched.jpeg.type, "image/jpeg");
      assert.deepEqual(fetched.jpeg.head.slice(0, 2), [0xff, 0xd8], "a JPEG begins like one");
      assert.ok(fetched.jpeg.size > 5_000 && fetched.jpeg.size < 400_000, `a picture of a sensible size (${fetched.jpeg.size} bytes)`);
      assert.equal(fetched.mp4.type, "video/mp4");
      assert.equal(String.fromCharCode(...fetched.mp4.head.slice(4, 8)), "ftyp", "an MP4 begins like one");
      assert.ok(fetched.mp4.size > 10_000 && fetched.mp4.size < 5_000_000, `a clip of a sensible size (${fetched.mp4.size} bytes)`);
      assert.equal(fetched.part.status, 206, "a part of the clip can be asked for, which is how a browser seeks in it");
      assert.match(fetched.part.range ?? "", /^bytes 0-99\//);
      // asked outside the page, which would log the refusal as a problem
      assert.equal((await context.request.get(`${base}/api/stops/999999/clip.mp4`)).status(), 404, "and a pass with no evidence has none");

      // it opens in a window over the page, in which the clip is played
      await opener.click();
      const viewer = page.getByRole("dialog");
      await viewer.waitFor();
      const video = viewer.locator("video");
      assert.equal(await video.getAttribute("src"), clipUrl);
      assert.equal(await video.getAttribute("poster"), snapshotUrl, "the picture shows while the clip loads");
      assert.match((await viewer.getByRole("link", { name: "Save the clip" }).getAttribute("download")) ?? "", /^stop-\d+\.mp4$/);
      const canPlay = await page.evaluate(() => document.createElement("video").canPlayType('video/mp4; codecs="avc1.640028"'));
      if (canPlay !== "") {
        await eventually(async () => assert.ok((await video.evaluate((v) => v.readyState)) >= 1, "the browser has read the clip"), "the clip loads", 15_000);
        const { width, height, seconds } = await video.evaluate((v) => ({ width: v.videoWidth, height: v.videoHeight, seconds: v.duration }));
        assert.deepEqual([width, height], [640, 360]);
        assert.ok(seconds > 2 && seconds < 12, `a few seconds long (${seconds} s)`);
      } else {
        console.log("     (this browser cannot play H.264, so the clip was checked as a file only)");
      }
      await shot("stop-viewer");

      // the page behind is out of reach, and a pass can be reviewed from here
      const confirm = viewer.getByRole("button", { name: /^Confirm: / });
      await confirm.click();
      await eventually(async () => assert.equal(await confirm.getAttribute("aria-pressed"), "true"));
      await confirm.click(); // leave things as they were
      await eventually(async () => assert.equal(await confirm.getAttribute("aria-pressed"), "false"));

      // the other passes can be gone through; the newest has nothing newer
      const newer = viewer.getByRole("button", { name: "Newer" });
      const older = viewer.getByRole("button", { name: "Older" });
      const timeShown = () => viewer.locator("time").innerText();
      if (await older.isEnabled()) {
        const before = await timeShown();
        const wasNewest = await newer.isDisabled();
        await older.click();
        await eventually(async () => assert.equal(await newer.isEnabled(), true, "there is a newer one to go back to"));
        if (wasNewest) {
          await newer.click();
          await eventually(async () => assert.equal(await timeShown(), before, "back at the pass that was opened"));
          assert.equal(await newer.isDisabled(), true, "and the newest has nothing newer");
        }
      }

      // Escape closes it, and the keyboard is where it was
      await page.keyboard.press("Escape");
      await viewer.waitFor({ state: "hidden" });
      assert.equal(await opener.evaluate((el) => el === document.activeElement), true, "focus is back on the picture that was opened");

      // the close button does as much, and so does opening it again
      await opener.click();
      await page.getByRole("dialog").waitFor();
      await page.getByRole("dialog").getByRole("button", { name: "Close" }).click();
      await page.getByRole("dialog").waitFor({ state: "hidden" });
    });
  }

  if (demo) {
    await step("the Data page says what share of the vehicles stopped, rolled through and did not stop, and agrees with the server", async () => {
      // Freeze what the server says per request, so that the passes the made-up street keeps adding cannot move the numbers under the checks.
      const frozen = new Map();
      await page.route(/\/api\/stats\/(stops|traffic)\?/, async (route) => {
        const url = route.request().url();
        const response = await route.fetch();
        if (!frozen.has(url)) frozen.set(url, await response.json());
        await route.fulfill({ response, json: frozen.get(url) });
      });
      const answer = (kind, days) => [...frozen].find(([url]) => url.includes(`/api/stats/${kind}?days=${days}`))?.[1];
      const sum = (counts) => counts.stopped + counts.rolling + counts.did_not_stop + counts.indeterminate;
      const OUTCOMES = ["stopped", "rolling", "did_not_stop", "indeterminate"];

      await goTo("Data");
      await page.reload();
      await page.getByTestId("data-stops-headline").waitFor();
      await eventually(async () => assert.ok(answer("stops", 7), "the first request is for the 7 days"));
      assert.equal(await page.getByRole("button", { name: "7 days", exact: true }).getAttribute("aria-pressed"), "true", "7 days is the first choice");
      const stops = answer("stops", 7);
      const total = sum(stops.totals);
      assert.ok(total > 0, "the made-up street's passes at the stop sign are in the numbers");
      assert.match(await page.getByTestId("data-stops-headline").innerText(), new RegExp(`${total.toLocaleString("en-US")} vehicles? (was|were) judged`));

      // four tiles that add up: the counts are the server's, the percentages whole numbers that make 100
      let shares = 0;
      for (const outcome of OUTCOMES) {
        assert.equal(await page.getByTestId(`count-${outcome}`).innerText(), `${stops.totals[outcome].toLocaleString("en-US")} of ${total.toLocaleString("en-US")}`);
        const share = Number.parseInt(await page.getByTestId(`share-${outcome}`).innerText(), 10);
        near(share, (stops.totals[outcome] / total) * 100, 0.999, `the share that ${outcome}`);
        shares += share;
      }
      assert.equal(shares, 100, "the four percentages make 100");
      assert.equal(await page.getByRole("img", { name: /^(Stopped|Rolled through|Did not stop|Could not tell) \d+%/ }).count(), 1, "the share bar says the same in words");

      // nothing was judged in the days before, and the page says so instead of showing a change
      assert.equal(sum(stops.previous), 0);
      await page.getByText("Nothing to compare with: the 7 days before had no vehicle judged.").waitFor();
      assert.equal(await page.getByTestId("change-stopped").count(), 0);

      // the highlights, the kinds and the reviews
      await page.getByTestId("fact-busiest-hour").waitFor();
      // the fastest estimated speed: in the owner's units, with when and what, and said to be an estimate
      assert.ok(stops.fastest, "the made-up street's passes were given a fastest speed");
      const fastestFact = await page.getByTestId("fact-fastest").innerText();
      assert.match(fastestFact, /Fastest estimated speed/);
      assert.match(fastestFact, /about \d+ mph|almost still/);
      assert.match(fastestFact, /rough estimate/);
      assert.match(fastestFact, /a (car|truck|bus|bicycle|motorcycle|vehicle), \w{3} \d+ \w{3}, \d\d:\d\d/);
      const kinds = page.getByTestId("data-stops-by-kind");
      await kinds.getByRole("row", { name: /^Cars / }).waitFor();
      await page.getByTestId("data-stops-reviews").waitFor();
      await page.getByTestId("data-stops-timing").waitFor();

      // the hours: a chart with columns, the same numbers as a table, and the shares of each column
      const hours = page.getByTestId("data-stops-by-hour");
      await eventually(async () => assert.ok((await hours.locator(".recharts-bar-rectangle").count()) > 0, "the chart has columns"));
      await hours.getByRole("button", { name: "Share", exact: true }).click();
      await hours.getByText("100%", { exact: true }).first().waitFor();
      await hours.getByRole("button", { name: "Count", exact: true }).click();
      await hours.getByRole("button", { name: "Table", exact: true }).click();
      const rows = hours.getByRole("row");
      assert.equal(await rows.count(), 25, "a header and the 24 hours");
      const tableTotal = await hours.getByRole("row").evaluateAll((trs) =>
        trs.slice(1).reduce((sum, tr) => sum + Number(tr.querySelectorAll("td")[4].textContent.replaceAll(",", "")), 0),
      );
      assert.equal(tableTotal, total, "the hours add up to the vehicles judged");
      await hours.getByRole("button", { name: "Chart", exact: true }).click();
      assert.ok(answer("stops", 7).by_hour.length === 24);
      assert.equal(await page.getByTestId("data-stops-by-day").count(), 1, "7 days have a day-by-day chart");
      // the day-by-day table has a column for each day's fastest estimate, and today's is the server's
      const dayCard = page.getByTestId("data-stops-by-day");
      await dayCard.getByRole("button", { name: "Table", exact: true }).click();
      await dayCard.getByRole("columnheader", { name: "Fastest (est.)" }).waitFor();
      assert.ok(stops.by_day.at(-1).fastest, "today has a fastest estimate");
      const todayCells = await dayCard.getByRole("row").last().locator("td").allInnerTexts();
      assert.match(todayCells.at(-1), /^(\d+ (mph|km\/h)|almost still)$/, `today's fastest is a speed (${todayCells.at(-1)})`);
      assert.equal(await dayCard.getByRole("row").first().locator("th").count() > 3, true);
      await dayCard.getByRole("button", { name: "Chart", exact: true }).click();

      // the traffic, from the same period
      const traffic = answer("traffic", 7);
      await page.getByTestId("data-traffic-total").waitFor();
      assert.equal(await page.getByTestId("data-traffic-total").innerText(), traffic.total.toLocaleString("en-US"));
      await page.getByTestId("data-traffic-by-hour").waitFor();

      // a period is picked once for all of it, asked of the server in days, and remembered
      const picks = [["Today", 1], ["30 days", 30], ["90 days", 90], ["All time", 0]];
      for (const [label, days] of picks) {
        const asked = page.waitForResponse((response) => response.url().includes(`/api/stats/stops?days=${days}`));
        const askedTraffic = page.waitForResponse((response) => response.url().includes(`/api/stats/traffic?days=${days}`));
        await page.getByRole("button", { name: label, exact: true }).click();
        await asked;
        await askedTraffic;
        await eventually(async () => assert.equal(await page.getByRole("button", { name: label, exact: true }).getAttribute("aria-pressed"), "true"));
        await eventually(async () => assert.equal(answer("stops", days)?.days >= 1, true));
      }
      // the answer has arrived, but the page may not have drawn it yet (a slow machine, a busy board)
      await eventually(async () => assert.match(await page.getByTestId("data-stops-headline").innerText(), /since /), "all time says since when");
      assert.equal(answer("stops", 0).previous, null, "all time has nothing before it");
      assert.equal(await page.getByText(/^Nothing to compare with/).count(), 0, "and does not say that there is nothing to compare with");
      await reload();
      await page.getByTestId("data-stops-headline").waitFor();
      assert.equal(await page.getByRole("button", { name: "All time", exact: true }).getAttribute("aria-pressed"), "true", "the period is remembered");
      await page.getByRole("button", { name: "Today", exact: true }).click();
      await eventually(async () => assert.equal(await page.getByTestId("data-stops-by-day").count(), 0, "one day has no day-by-day chart"));
      await page.getByRole("button", { name: "7 days", exact: true }).click(); // leave things as they were
      await eventually(async () => assert.equal(await page.getByRole("button", { name: "7 days", exact: true }).getAttribute("aria-pressed"), "true"));
      await shot("data");

      // the link to the passes that need a look goes to the History page
      await page.getByRole("link", { name: "Review passes on the History page" }).click();
      await page.getByRole("heading", { level: 1, name: "History" }).waitFor({ state: "attached" });
      await page.unroute(/\/api\/stats\/(stops|traffic)\?/);
    });
  }

  await step("with no stop sign set up the Data page says how to add one, and a request that failed is said, with a way to try again", async () => {
    await goTo("Data");
    const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
    const zero = { stopped: 0, rolling: 0, did_not_stop: 0, indeterminate: 0 };
    const nothing = {
      timezone: "UTC", units: "mph", from_ms: 0, to_ms: 1, days: 7, all_time: false, sign: null, totals: zero, previous: zero,
      by_hour: Array.from({ length: 24 }, (_, hour) => ({ hour, ...zero })),
      by_day: [], fastest: null, by_weekday: [], by_kind: [], by_sign: [],
      timing: { stopped_hold_s: null, rolling_min_speed: null, did_not_stop_min_speed: null },
      review: { confirmed: 0, false_alarms: 0, waiting: 0 },
    };
    const profileRoute = "**/api/profile";
    const stopsRoute = "**/api/stats/stops**";
    const trafficRoute = "**/api/stats/traffic**";
    await page.route(profileRoute, (route) => route.fulfill(json({ version: 1, problem: null, reference_at: null, profile: { schema_version: 3, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [], calibration: null } })));
    await page.route(stopsRoute, (route) => route.fulfill(json(nothing)));
    await page.route(trafficRoute, (route) => route.fulfill(json({ detail: "broken" }, 503))); // one of the refusals that the end of the test expects
    await page.reload();
    await page.getByText("No stop sign is set up yet.").waitFor();
    await page.getByText("The traffic numbers could not be loaded.").waitFor();
    await page.unroute(trafficRoute);
    await page.getByRole("button", { name: "Try again" }).click();
    await page.getByTestId("data-traffic-counted").waitFor();
    assert.equal(await page.getByText("The traffic numbers could not be loaded.").count(), 0, "the failure is gone once it works");
    await page.unroute(profileRoute);
    await page.unroute(stopsRoute);
    await page.reload();
    await page.getByTestId("data-traffic-counted").waitFor();
  });

  await step("with no stop sign set up the card says how to add one, and a failed request is said", async () => {
    await goTo("History");
    const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });
    const none = { outcomes: ["stopped", "rolling", "did_not_stop", "indeterminate"].map((outcome) => ({ outcome, total: 0, confirmed: 0, false_positive: 0, unreviewed: 0 })), from_ms: 0, to_ms: 1 };
    const profileRoute = "**/api/profile";
    const summaryRoute = "**/api/stops/summary**";
    await page.route(summaryRoute, (route) => route.fulfill(json(none)));
    await page.route(profileRoute, (route) => route.fulfill(json({ version: 1, problem: null, reference_at: null, profile: { schema_version: 3, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [], calibration: null } })));
    await page.reload();
    await page.getByText("No stop sign is set up yet.").waitFor();
    await page.getByRole("link", { name: "Add one on the Setup page" }).click();
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });
    await page.unroute(profileRoute);
    await page.unroute(summaryRoute);
    await goTo("History");
    await page.reload();
    await page.getByTestId("stops-headline").waitFor();
  });

  await step("the Trails card draws a day's paths over the camera picture", async () => {
    await goTo("History");
    await page.waitForFunction(() => Number(document.querySelector('[data-testid="trail-canvas"]')?.dataset.paths) > 0, undefined, { timeout: 60_000 });
    await trailCanvas().scrollIntoViewIfNeeded();
    await linesSwitch().click(); // the paths alone, to look at them
    assert.equal(await linesSwitch().getAttribute("aria-checked"), "false");
    await settled();
    assert.ok((await drawnPixels()) > 500, "the paths are drawn");
    assert.match(await trailHeadline().innerText(), /^[\d,]+ vehicles?\b.* · \w+ \d+ \w+ \d{4} · All day$/);
    assert.equal(await pickedHours().count(), await hourButtons().count(), "the whole day is picked");
    assert.equal(await hourStrip().locator('button[tabindex="0"]').count(), 1, "one stop for the Tab key in the row of hours");
    assert.match((await trailCanvas().getAttribute("aria-label")) ?? "", /^Paths of .* drawn on the camera picture\.$/);
    await shot("trails");
  });

  await step("an hour picked narrows the Trails to it; dragging, Shift and the keyboard pick several", async () => {
    await showDay(await oldestDay());
    const counts = await hourCountsShown();
    assert.ok(counts.length >= 23 && counts.length <= 25);
    const busiest = counts.indexOf(Math.max(...counts));

    await hourButtons().nth(busiest).click();
    await eventually(async () => assert.equal(await pickedHours().count(), 1));
    assert.equal(await hourButtons().nth(busiest).getAttribute("aria-pressed"), "true");
    await eventually(
      async () => assert.match(await trailHeadline().innerText(), new RegExp(`^${counts[busiest].toLocaleString("en-US")} vehicles.* · ${await rangeOf(busiest)}$`)),
      "the headline names the hour and counts its vehicles",
    );
    await settled();
    assert.equal(Number(await trailCanvas().getAttribute("data-paths")), Math.min(counts[busiest], 600), "a path for each, up to the most that can be told apart");
    assert.ok((await drawnPixels()) > 500);

    // Shift-click extends to another hour
    const far = Math.min(busiest + 2, counts.length - 1);
    await hourButtons().nth(far).click({ modifiers: ["Shift"] });
    await eventually(async () => assert.equal(await pickedHours().count(), far - busiest + 1));
    await eventually(
      async () => assert.equal(await headlineCount(), counts.slice(busiest, far + 1).reduce((a, b) => a + b, 0)),
      "the count is of all the hours picked",
    );

    // dragging across the row picks the hours it passes over
    const from = await centreOf(hourButtons().nth(2));
    const to = await centreOf(hourButtons().nth(5));
    await page.mouse.move(from.x, from.y);
    await page.mouse.down();
    await page.mouse.move(to.x, to.y, { steps: 8 });
    await page.mouse.up();
    await eventually(async () => assert.equal(await pickedHours().count(), 4));
    assert.equal(await hourButtons().nth(2).getAttribute("aria-pressed"), "true");
    assert.equal(await hourButtons().nth(5).getAttribute("aria-pressed"), "true");
    assert.equal(await hourButtons().nth(6).getAttribute("aria-pressed"), "false");
    const dragged = await rangeBetween(2, 5);
    await eventually(async () => assert.equal((await trailHeadline().innerText()).split(" · ").at(-1), dragged));

    // and so does the keyboard: Enter picks the hour in focus, Shift with an arrow key extends
    await hourButtons().nth(10).focus();
    await page.keyboard.press("Enter");
    await eventually(async () => assert.equal(await pickedHours().count(), 1));
    await page.keyboard.press("Shift+ArrowRight");
    await page.keyboard.press("Shift+ArrowRight");
    await eventually(async () => assert.equal(await pickedHours().count(), 3));
    assert.equal(await hourButtons().nth(12).getAttribute("aria-pressed"), "true");
    await page.keyboard.press("Home"); // moves on without picking
    assert.equal(await pickedHours().count(), 3);
    assert.equal(await hourStrip().locator('button[tabindex="0"]').count(), 1);
    assert.equal(await page.evaluate(() => document.activeElement?.getAttribute("data-hour")), "0");
    await page.keyboard.press("Enter");
    await eventually(async () => assert.equal(await pickedHours().count(), 1));

    await page.getByRole("button", { name: "All day", exact: true }).click();
    await eventually(async () => assert.equal(await pickedHours().count(), counts.length));
    await eventually(async () => assert.match(await trailHeadline().innerText(), / · All day$/));
  });

  await step("each kind of vehicle can be switched off, and the count and the picture follow", async () => {
    await settled();
    const kinds = page.getByRole("group", { name: "Kinds to show" }).getByRole("checkbox");
    const total = await headlineCount();
    const names = await kinds.evaluateAll((boxes) => boxes.map((box) => box.closest("label")?.textContent?.trim() ?? ""));
    assert.ok(names.length >= 2, `more than one kind of vehicle in the day: ${names}`);
    const carsLabel = names.find((name) => name.startsWith("Cars"));
    assert.ok(carsLabel, "cars are in the day");
    const cars = Number(carsLabel.replace(/\D+/, "").replaceAll(",", ""));
    assert.ok(cars > 0 && cars < total);

    await page.getByRole("checkbox", { name: /^Cars/ }).uncheck();
    await eventually(async () => assert.equal(await headlineCount(), total - cars), "without the cars, the others are counted");
    await settled();
    assert.ok((await hourCountsShown()).reduce((a, b) => a + b, 0) === total - cars, "and so are the hours");

    for (let index = 0; index < (await kinds.count()); index += 1) await kinds.nth(index).uncheck();
    await page.getByText("Every kind is switched off.").waitFor();
    await eventually(async () => assert.equal(Number(await trailCanvas().getAttribute("data-paths")), 0));
    assert.equal(await drawnPixels(), 0, "nothing is left on the picture");

    for (let index = 0; index < (await kinds.count()); index += 1) await kinds.nth(index).check();
    await eventually(async () => assert.equal(await headlineCount(), total));
    await settled();
    assert.ok((await drawnPixels()) > 500, "and everything is back");
  });

  await step("the heat map shows all the paths as density, and the choice is remembered", async () => {
    await drawHeat().click();
    await eventually(async () => assert.equal(await trailCanvas().getAttribute("data-view"), "heat"));
    await page.getByText("Fewer vehicles").waitFor();
    const paths = Number(await trailCanvas().getAttribute("data-paths"));
    assert.ok(paths > 600, `the heat map takes every path, not the few hundred that can be told apart (${paths})`);
    assert.ok((await drawnPixels()) > 20_000, "the heat map covers the roads in a glow");
    assert.match((await trailCanvas().getAttribute("aria-label")) ?? "", /^Heat map of /);
    await shot("trails-heat");
    await page.reload();
    await page.getByTestId("trail-canvas").waitFor();
    assert.equal(await drawHeat().getAttribute("aria-pressed"), "true", "still the heat map after a reload");
    await drawPaths().click();
    await eventually(async () => assert.equal(await trailCanvas().getAttribute("data-view"), "paths"));
    assert.equal(Number(await trailCanvas().getAttribute("data-paths")) <= 600, true);
  });

  await step("a day is chosen with the arrows or the date, and a day without trails says so", async () => {
    const first = await oldestDay();
    const last = await dayInput().getAttribute("max");
    await showDay(first);
    const previous = page.getByRole("button", { name: "Previous day" });
    const next = page.getByRole("button", { name: "Next day" });
    assert.equal(await previous.isDisabled(), true, "nothing before the first day with trails");
    await next.click();
    const second = new Date(Date.parse(`${first}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    await eventually(async () => assert.match(await trailHeadline().innerText(), new RegExp(dayTitle(second))));
    assert.equal(await dayInput().inputValue(), second);
    await page.getByRole("button", { name: "Previous day" }).click();
    await eventually(async () => assert.match(await trailHeadline().innerText(), new RegExp(dayTitle(first))));

    await dayInput().fill("2020-01-01");
    await page.getByText("No vehicles were recorded on this day.").waitFor();
    assert.equal(Number(await trailCanvas().getAttribute("data-paths")), 0);
    assert.ok((await hourCountsShown()).every((n) => n === 0));

    await page.getByRole("button", { name: "Latest day" }).click();
    await eventually(async () => assert.equal(await dayInput().inputValue(), last));
    assert.equal(await page.getByRole("button", { name: "Latest day" }).count(), 0, "the button goes once it has done its work");
    assert.equal(await next.isDisabled(), true, "nothing after the newest day");
  });

  await step("playing a day goes through the hours with trails, from the one picked, and ends where it began", async () => {
    await showDay(await oldestDay());
    const counts = await hourCountsShown();
    const start = counts.length - 3; // the last three hours of the day all have some traffic
    assert.ok(counts.slice(start).every((n) => n > 0), "the evening has traffic");
    await hourButtons().nth(start).click();
    await eventually(async () => assert.equal(await pickedHours().count(), 1));
    const play = page.getByRole("button", { name: /^(Play the day|Pause)$/ });
    await play.click();
    assert.equal(await play.innerText(), "Pause");
    const lastWord = async () => (await trailHeadline().innerText()).split(" · ").at(-1);
    await eventually(async () => assert.equal(await lastWord(), await rangeOf(start + 1)), "the next hour", 4_000);
    assert.equal(await hourButtons().nth(start + 1).getAttribute("aria-pressed"), "true");
    assert.equal(await pickedHours().count(), 1, "one hour at a time");
    await eventually(async () => assert.equal(await lastWord(), await rangeOf(start + 2)), "and the one after", 4_000);
    await eventually(async () => assert.equal(await play.innerText(), "Play the day"), "it stops at the end of the day", 5_000);
    await eventually(async () => assert.equal(await hourButtons().nth(start).getAttribute("aria-pressed"), "true"), "and goes back to the hour that was picked");
    assert.equal(await pickedHours().count(), 1);

    // from the whole day it begins with the first hour that has something, and Pause holds it there
    await page.getByRole("button", { name: "All day", exact: true }).click();
    await play.click();
    const firstBusy = counts.findIndex((n) => n > 0);
    await eventually(async () => assert.equal(await lastWord(), await rangeOf(firstBusy)));
    const secondBusy = counts.findIndex((n, index) => index > firstBusy && n > 0);
    await eventually(async () => assert.equal(await lastWord(), await rangeOf(secondBusy)), "the second hour", 4_000);
    await play.click(); // Pause
    assert.equal(await play.innerText(), "Play the day");
    const held = await trailHeadline().innerText();
    await page.waitForTimeout(2_300);
    assert.equal(await trailHeadline().innerText(), held, "it stays where it was paused");
    await page.getByRole("button", { name: "All day", exact: true }).click();
  });

  await step("the picture can be saved as a PNG that says what it shows", async () => {
    await settled();
    const day = await oldestDay();
    await showDay(day);
    const [download] = await Promise.all([page.waitForEvent("download"), page.getByRole("button", { name: "Save picture" }).click()]);
    assert.equal(download.suggestedFilename(), `trafficpi-paths-${day}.png`);
    const file = join(scratch, "trails.png");
    await download.saveAs(file);
    const png = readFileSync(file);
    assert.deepEqual([...png.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a], "a PNG");
    const [width, height] = [png.readUInt32BE(16), png.readUInt32BE(20)];
    assert.ok(width >= 640 && Math.abs(width / height - 16 / 9) < 0.05, `the camera picture's size and shape (${width} x ${height})`);
    assert.ok(png.length > 30_000, `a picture with paths on it, not a blank one (${png.length} bytes)`);
    if (shots) writeFileSync(`${shots}/trails-saved.png`, png);
    await page.getByRole("button", { name: "Save picture" }).waitFor();
  });

  await step("the lines and zones can be shown over the paths or left off", async () => {
    await settled();
    const without = await drawnPixels();
    await linesSwitch().click();
    assert.equal(await linesSwitch().getAttribute("aria-checked"), "true");
    await eventually(async () => assert.ok((await drawnPixels()) > without, "the lines and zones add to the drawing"));
    await shot("trails-with-lines");
  });

  await step("an empty history, a failed request and a day of 25 hours are all shown properly", async () => {
    const hour = 3_600_000;
    const dayRoute = "**/api/trails/day**";
    const json = (body, status = 200) => ({ status, contentType: "application/json", body: JSON.stringify(body) });

    // 1. no trail has ever been recorded
    await page.route(dayRoute, (route) =>
      route.fulfill(json({ day: "2026-10-09", timezone: "UTC", total: 0, first_day: null, last_day: null, hours: [] })),
    );
    await page.reload();
    await page.getByText("No trails have been recorded yet.").waitFor();
    assert.equal(await hourStrip().count(), 0);
    assert.equal(await page.getByRole("button", { name: "Save picture" }).count(), 0, "nothing to save");

    // 2. the request fails, and Try again asks once more
    await page.unroute(dayRoute);
    await page.route(dayRoute, (route) => route.fulfill(json({ detail: "the database is busy" }, 503)));
    await page.reload();
    await page.getByText("Could not load the trails: the database is busy.").waitFor();
    await page.unroute(dayRoute);
    await page.getByRole("button", { name: "Try again" }).click();
    await hourStrip().waitFor();

    // 3. the day the clocks go back has 25 hours, and the hour that happens twice is two buttons
    const midnight = Date.UTC(2026, 10, 1, 4); // 00:00 EDT on 1 November 2026
    const localHours = [0, 1, 1, ...Array.from({ length: 22 }, (_, index) => index + 2)];
    const hours = localHours.map((local, index) => ({
      t: midnight + index * hour,
      end: midnight + (index + 1) * hour,
      hour: local,
      count: index === 1 ? 3 : index === 2 ? 5 : 0,
      by_kind: index === 1 ? { car: 3 } : index === 2 ? { truck: 5 } : {},
    }));
    const asked = [];
    const pathsRoute = (url) => url.pathname === "/api/trails";
    await page.route(dayRoute, (route) =>
      route.fulfill(json({ day: "2026-11-01", timezone: "America/New_York", total: 8, first_day: "2026-10-30", last_day: "2026-11-01", hours })),
    );
    await page.route(
      pathsRoute,
      (route) => {
        const query = new URL(route.request().url()).searchParams;
        asked.push([Number(query.get("from_ms")), Number(query.get("to_ms"))]);
        const here = (x0, y, x1) => ({ x: [x0, x1].map((f) => Math.round(f * 65535)), y: [y, y].map((f) => Math.round(f * 65535)) });
        return route.fulfill(
          json({
            from_ms: asked.at(-1)[0],
            to_ms: asked.at(-1)[1],
            total: 5,
            returned: 5,
            scale: 65535,
            // the five trucks that began in that hour, three one way and two the other
            trails: [0.1, 0.1, 0.1, 0.9, 0.9].map((from, index) => ({
              id: index + 1,
              started_at: asked.at(-1)[0] + 1000 * (index + 1),
              ended_at: asked.at(-1)[0] + 9000 + 1000 * index,
              label: "truck",
              ...here(from, 0.5, 1 - from),
            })),
          }),
        );
      },
    );
    await page.reload();
    await hourStrip().waitFor();
    await eventually(async () => assert.equal(await hourButtons().count(), 25));
    assert.match((await hourButtons().nth(1).getAttribute("aria-label")) ?? "", /^01:00–02:00, the first time: 3 vehicles$/);
    assert.match((await hourButtons().nth(2).getAttribute("aria-label")) ?? "", /^01:00–02:00, the second time: 5 vehicles$/);
    await page.getByRole("switch", { name: "Lines and zones" }).waitFor();
    if ((await linesSwitch().getAttribute("aria-checked")) === "true") await linesSwitch().click();
    await hourButtons().nth(2).click(); // the second 1 o'clock
    await eventually(async () => assert.deepEqual(asked.at(-1), [midnight + 2 * hour, midnight + 3 * hour]), "its own hour is asked for");
    await eventually(async () => assert.equal(Number(await trailCanvas().getAttribute("data-paths")), 5));
    await eventually(async () => assert.match(await trailHeadline().innerText(), /^5 vehicles · .* · 01:00–02:00$/), "five trucks, all of them drawn");
    // the paths are trucks' (orange) along the middle of the picture: orange there, nothing above it
    const [r, , b, alpha] = await pixelAt(0.5, 0.5);
    assert.ok(alpha > 100 && r > b, `an orange path at the middle (rgba ${r}, ${b}, ${alpha})`);
    assert.equal((await pixelAt(0.5, 0.2))[3], 0, "and nothing above it");
    await page.unroute(dayRoute);
    await page.unroute(pathsRoute);
    await page.reload();
    await hourStrip().waitFor();
  });

  await step("the settings are changed and saved, and a time zone that does not exist is refused", async () => {
    await goTo("Setup");
    await page.getByRole("form", { name: "Settings" }).waitFor();
    const readSettings = () => page.evaluate(async () => (await fetch("/api/settings")).json());
    const original = await readSettings();
    const save = page.getByRole("button", { name: "Save settings" });
    const zone = page.getByLabel("Time zone");
    const days = page.getByLabel("Keep trails for (days)");
    const people = page.getByRole("switch", { name: "Include people" });
    const enabled = page.getByRole("switch", { name: "Save the path of each vehicle" });
    const evidence = page.getByRole("switch", { name: "Keep a picture and a clip of passes that need a look" });
    const evidenceDays = page.getByLabel("Keep pictures and clips for (days)");
    const evidenceMb = page.getByLabel("Keep at most (megabytes)");
    const rollingHours = page.getByLabel("Keep the clip of a rolled-through stop for (hours)");
    const didNotStopHours = page.getByLabel("Keep the clip of a vehicle that did not stop for (hours)");
    const keepRolling = page.getByRole("switch", { name: "Keep a picture and a clip of rolled-through stops", exact: true });
    const keepDidNotStop = page.getByRole("switch", { name: "Keep a picture and a clip of vehicles that did not stop", exact: true });
    try {
      assert.equal(await zone.inputValue(), original.timezone, "the form shows what is saved");
      assert.equal(await days.inputValue(), String(original.trails_keep_days));
      assert.equal(await enabled.getAttribute("aria-checked"), String(original.trails_enabled));
      assert.equal(await evidence.getAttribute("aria-checked"), String(original.evidence_enabled));
      assert.equal(await evidenceDays.inputValue(), String(original.evidence_keep_days));
      assert.equal(await evidenceMb.inputValue(), String(original.evidence_max_mb));
      assert.equal(await rollingHours.inputValue(), String(original.evidence_rolling_clip_hours));
      assert.equal(await didNotStopHours.inputValue(), String(original.evidence_did_not_stop_clip_hours));
      assert.equal(await keepRolling.getAttribute("aria-checked"), String(original.evidence_keep_rolling));
      assert.equal(await keepDidNotStop.getAttribute("aria-checked"), String(original.evidence_keep_did_not_stop));
      assert.equal(await save.isDisabled(), true, "nothing to save yet");

      await days.fill("0");
      await page.getByText("Enter a whole number of days from 1 to 3650 to keep trails.").first().waitFor();
      await page.getByText("A whole number of days, from 1 to 3650.").waitFor(); // what is wrong stays in sight, under the box
      assert.equal(await save.isDisabled(), true, "a number of days that makes no sense cannot be saved");
      assert.equal(await explained(days), "Older trails are deleted by themselves.", "with no number there is nothing to say it is kept for");
      await days.fill("30");
      assert.equal(await explained(days), "Kept for 30 days. Older trails are deleted by themselves.");
      await page.getByText("A whole number of days, from 1 to 3650.").waitFor({ state: "detached" });
      await page.getByText("Settings not saved yet").waitFor();
      assert.equal(await save.isEnabled(), true);

      // the same for the room that evidence may take
      await evidenceMb.fill("10");
      await page.getByText("Enter a whole number of megabytes from 50 to 1000000 for pictures and clips.").first().waitFor();
      assert.equal(await save.isDisabled(), true, "less room than a few clips need cannot be saved");
      await evidenceDays.fill("");
      await page.getByText("Enter a whole number of days from 1 to 3650 to keep pictures and clips.").first().waitFor();
      assert.equal(await save.isDisabled(), true);
      await evidenceDays.fill("7");
      assert.equal(await explained(evidenceDays), "Kept for 7 days. Older ones are deleted by themselves, also if none are being made.");
      await evidenceMb.fill("1500");
      assert.equal(await explained(evidenceMb), "Up to 1.5 GB on the disk. When there is more, the oldest are deleted first.");
      assert.equal(await save.isEnabled(), true);

      // and for how long the clip of a roll, and of a pass that did not stop, is kept
      await rollingHours.fill("0");
      await page.getByText("Enter a whole number of hours from 1 to 87600 to keep the clips of rolled-through stops.").first().waitFor();
      assert.equal(await save.isDisabled(), true, "no hours cannot be saved");
      await rollingHours.fill("6");
      assert.match(await explained(rollingHours), /^Clips of rolled-through stops are deleted after 6 hours/);
      await didNotStopHours.fill("72");
      assert.match(await explained(didNotStopHours), /^Clips of vehicles that did not stop are deleted after 3 days/);
      assert.equal(await save.isEnabled(), true);

      // changes that are not saved are not lost without asking
      const asked = [];
      page.once("dialog", async (dialog) => {
        asked.push(dialog.message());
        await dialog.dismiss();
      });
      await pagesNav().getByRole("link", { name: "Live", exact: true }).click();
      await page.waitForTimeout(300);
      assert.match(asked[0] ?? "", /not saved/);
      assert.equal(pagePath(), "/setup/");
      assert.equal(await days.inputValue(), "30", "and are still there");

      // a time zone that does not exist is refused by the server, in words, and nothing is changed
      await zone.fill("Mars/Olympus");
      await save.click();
      await page.getByRole("alert").filter({ hasText: "unknown time zone" }).waitFor();
      assert.deepEqual(await readSettings(), original, "a refused change changes nothing");

      // all of it, saved
      await zone.fill("Europe/Paris");
      await page.getByRole("button", { name: "km/h" }).click();
      if ((await people.getAttribute("aria-checked")) !== "true") await people.click();
      if ((await enabled.getAttribute("aria-checked")) === "true") await enabled.click();
      // each kind of pass has its own switch; the rolled-through one is turned off, the other left
      assert.equal(await keepRolling.isEnabled(), true, "while evidence is on, each outcome's switch can be used");
      if ((await keepRolling.getAttribute("aria-checked")) === "true") await keepRolling.click();
      if ((await evidence.getAttribute("aria-checked")) === "true") await evidence.click();
      assert.equal(await keepRolling.isDisabled(), true, "with evidence off, the outcomes' switches are greyed out");
      assert.equal(await keepDidNotStop.isDisabled(), true);
      await page.getByText("Nothing is kept while the switch above is off, whatever these say.").waitFor();
      await save.click();
      await page.getByRole("status").filter({ hasText: "Saved." }).waitFor();
      assert.deepEqual(await readSettings(), {
        timezone: "Europe/Paris",
        units: "kmh",
        trails_enabled: false,
        trails_people: true,
        trails_keep_days: 30,
        evidence_enabled: false,
        evidence_keep_days: 7,
        evidence_max_mb: 1500,
        evidence_rolling_clip_hours: 6,
        evidence_did_not_stop_clip_hours: 72,
        evidence_keep_rolling: false,
        evidence_keep_did_not_stop: original.evidence_keep_did_not_stop,
        remote_live_video: original.remote_live_video,
        detect_people: original.detect_people,
      });
      assert.equal(await save.isDisabled(), true, "saved, so nothing left to save");
      await page.reload();
      await page.getByRole("form", { name: "Settings" }).waitFor();
      assert.equal(await zone.inputValue(), "Europe/Paris");
      assert.equal(await days.inputValue(), "30");
      assert.equal(await page.getByRole("button", { name: "km/h" }).getAttribute("aria-pressed"), "true");
      assert.equal(await people.getAttribute("aria-checked"), "true");
      assert.equal(await enabled.getAttribute("aria-checked"), "false");
      assert.equal(await evidence.getAttribute("aria-checked"), "false");
      assert.equal(await evidenceDays.inputValue(), "7");
      assert.equal(await evidenceMb.inputValue(), "1500");
      assert.equal(await rollingHours.inputValue(), "6");
      assert.equal(await didNotStopHours.inputValue(), "72");
      assert.equal(await keepRolling.getAttribute("aria-checked"), "false", "the switch of the rolled-through stops stays off after a reload");
      assert.equal(await keepDidNotStop.getAttribute("aria-checked"), String(original.evidence_keep_did_not_stop), "and the other is as it was");

      // people can be left out altogether, which makes keeping their paths moot
      const lookForPeople = page.getByRole("switch", { name: "Look for people" });
      assert.equal(await lookForPeople.getAttribute("aria-checked"), String(original.detect_people), "the form shows whether people are looked for");
      if ((await lookForPeople.getAttribute("aria-checked")) !== "false") await lookForPeople.click();
      assert.equal(await people.isDisabled(), true, "with people not looked for, their paths are not an option");
      await page.getByText("People are not being looked for, so there are no paths of people to save.").waitFor();
      await save.click();
      await page.getByRole("status").filter({ hasText: "Saved." }).waitFor();
      assert.equal((await readSettings()).detect_people, false, "the server has it");
      await page.reload();
      await page.getByRole("form", { name: "Settings" }).waitFor();
      assert.equal(await lookForPeople.getAttribute("aria-checked"), "false", "and it is still off after a reload");
      assert.equal(await people.isDisabled(), true);
      await lookForPeople.click();
      assert.equal(await people.isDisabled(), false, "switched on again, the option is back");
      await shot("settings");
    } finally {
      // the next steps (and the next run) expect the settings that were there
      await page.evaluate(async (settings) => {
        await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      }, original);
    }
    assert.deepEqual(await readSettings(), original);
  });

  await step("an info icon explains an option: on hover, on the keyboard and on a press, one at a time, always inside the window", async () => {
    await goTo("Setup");
    const settings = page.getByRole("form", { name: "Settings" });
    await settings.waitFor();
    const icons = settings.getByRole("button", { name: "More information" });
    const zone = settings.getByLabel("Time zone");
    const { icon, bubble } = await explanation(zone);
    const inside = async (shown, width, height) => {
      const box = await shown.boundingBox();
      assert.ok(box, "the bubble is shown");
      assert.ok(box.x >= 0 && box.y >= 0 && box.x + box.width <= width && box.y + box.height <= height, `the bubble is inside the window: ${JSON.stringify(box)}`);
      return box;
    };

    // the words are in the page, hidden, for whoever reads it aloud; nothing is written under the option
    assert.equal(await icon.getAttribute("aria-label"), "More information");
    assert.equal(await bubble.getAttribute("role"), "tooltip");
    assert.equal(await bubble.isVisible(), false, "shut to start with");
    assert.match(await bubble.textContent(), /^The zone of the camera, like America\/New_York\./);
    assert.equal(await settings.getByText("The zone of the camera").filter({ visible: true }).count(), 0, "not written under the field");

    // the pointer opens it, under the icon, and can go onto it to rest there; going away shuts it
    await icon.hover();
    await bubble.waitFor({ state: "visible" });
    const iconBox = await icon.boundingBox();
    const box = await inside(bubble, 1280, 900);
    assert.ok(box.y >= iconBox.y + iconBox.height, "under the icon, where there is room");
    await page.mouse.move(box.x + box.width / 2, box.y + box.height / 2, { steps: 6 });
    await page.waitForTimeout(400);
    assert.equal(await bubble.isVisible(), true, "the pointer can cross onto the bubble and rest there");
    await page.mouse.move(0, 0);
    await bubble.waitFor({ state: "hidden" });

    // Escape puts it away while the pointer stays, and it does not come back until the pointer has been away
    await icon.hover();
    await bubble.waitFor({ state: "visible" });
    await page.keyboard.press("Escape");
    await bubble.waitFor({ state: "hidden" });
    await page.mouse.move(iconBox.x + 6, iconBox.y + 6);
    await page.waitForTimeout(300);
    assert.equal(await bubble.isVisible(), false, "it stays shut while the pointer stays");
    await page.mouse.move(0, 0);

    // the keyboard: coming to the icon opens it, going on shuts it, and the field is next
    await zone.focus();
    await page.keyboard.press("Shift+Tab");
    assert.equal(await icon.evaluate((element) => element === document.activeElement), true, "the icon comes just before its field");
    await bubble.waitFor({ state: "visible" });
    await page.keyboard.press("Tab");
    await bubble.waitFor({ state: "hidden" });
    assert.equal(await zone.evaluate((element) => element === document.activeElement), true);

    // a press opens it and keeps it open when the pointer goes; a press elsewhere, or Escape, lets go
    await icon.click();
    await page.mouse.move(0, 0);
    await page.waitForTimeout(400);
    assert.equal(await bubble.isVisible(), true, "pressed, it stays");
    await page.getByRole("heading", { name: "Settings", exact: true }).click();
    await bubble.waitFor({ state: "hidden" });
    await icon.click();
    await page.mouse.move(0, 0);
    await page.keyboard.press("Escape");
    await bubble.waitFor({ state: "hidden" });

    // one at a time
    const second = icons.nth(1);
    const secondBubble = page.locator(`[id="${await second.getAttribute("aria-describedby")}"]`);
    await icon.click();
    await bubble.waitFor({ state: "visible" });
    await second.hover();
    await secondBubble.waitFor({ state: "visible" });
    await bubble.waitFor({ state: "hidden" });
    await page.mouse.move(0, 0);
    await secondBubble.waitFor({ state: "hidden" });

    // a switch and a title can be explained too, and are described by it
    const people = settings.getByRole("switch", { name: "Look for people", exact: true });
    assert.match(await explained(people), /^Vehicles are always looked for\./);
    const trails = settings.getByRole("group", { name: "Trails", exact: true });
    assert.match(await explainedBy(trails.getByRole("button", { name: "More information" }).first()), /^A trail is the path a vehicle took/); // the title's, before the box's

    // in a narrow window the first and the last bubbles still fit inside it
    await page.setViewportSize({ width: 360, height: 640 });
    try {
      for (const target of [icons.first(), icons.last()]) {
        await target.scrollIntoViewIfNeeded();
        await target.click();
        const shown = page.locator(`[id="${await target.getAttribute("aria-describedby")}"]`);
        await shown.waitFor({ state: "visible" });
        await inside(shown, 360, 640);
        await page.keyboard.press("Escape");
        await shown.waitFor({ state: "hidden" });
      }
    } finally {
      await page.setViewportSize({ width: 1280, height: 900 });
    }
    await shot("info-icons");

    // a finger has no hover: a tap opens it, a second tap or a tap elsewhere shuts it
    const touchContext = await browser.newContext({ viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true });
    try {
      const touch = await touchContext.newPage();
      touch.setDefaultTimeout(20_000);
      touch.on("pageerror", (error) => problems.push(`uncaught (touch): ${error.message}`));
      touch.on("console", (message) => {
        if (message.type() === "error") problems.push(`console (touch): ${message.text()}`);
      });
      await touch.goto(`${base}/setup/`);
      await touch.getByLabel("Username").fill(user);
      await touch.getByLabel("Password").fill(password);
      await touch.getByRole("button", { name: "Sign in" }).click();
      const form = touch.getByRole("form", { name: "Settings" });
      await form.waitFor();
      const tapped = form.getByRole("button", { name: "More information" }).first();
      const tappedBubble = touch.locator(`[id="${await tapped.getAttribute("aria-describedby")}"]`);
      await tapped.scrollIntoViewIfNeeded();
      await tapped.tap();
      await tappedBubble.waitFor({ state: "visible" });
      await touch.waitForTimeout(400);
      assert.equal(await tappedBubble.isVisible(), true, "a tap keeps it open");
      await tapped.tap();
      await tappedBubble.waitFor({ state: "hidden" });
      await tapped.tap();
      await tappedBubble.waitFor({ state: "visible" });
      await touch.getByRole("heading", { name: "Settings", exact: true }).tap();
      await tappedBubble.waitFor({ state: "hidden" });
    } finally {
      await touchContext.close();
    }
  });

  await step("the daily email card sets up recipients and a schedule, tests a send, and previews the mail", async () => {
    await goTo("Setup");
    const mailCard = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Daily email", exact: true }) });
    await mailCard.waitFor();
    const readMail = () => page.evaluate(async () => (await fetch("/api/mail")).json());
    const recipients = mailCard.getByRole("region", { name: "Recipients" });
    const addInput = mailCard.getByLabel("Add an address");
    const addButton = mailCard.getByRole("button", { name: "Add", exact: true });
    const saveSchedule = mailCard.getByRole("button", { name: "Save the schedule" });
    const original = await readMail();
    // Twelve hours from now, so that no scheduled time can come while the test runs and make a real mail.
    const sendAt = `${String((new Date().getHours() + 12) % 24).padStart(2, "0")}:30`;
    try {
      assert.equal(original.transport, "file", "the demo sends nothing: it writes files");
      await mailCard.getByText("Demo mode:").waitFor();
      assert.equal(await mailCard.getByRole("region", { name: "Resend API key" }).count(), 0, "no key is asked for where none is needed");
      await mailCard.getByText("The daily email is off.", { exact: false }).first().waitFor();
      await recipients.getByText("Nobody yet.").waitFor();

      // addresses: a wrong one is refused in words, a right one is listed in lower case, and a repeat is one
      await addInput.fill("not an address");
      await addButton.click();
      await recipients.getByRole("alert").filter({ hasText: "email address" }).waitFor();
      await addInput.fill("  Owner@Example.COM ");
      await addButton.click();
      await recipients.getByText("owner@example.com", { exact: true }).waitFor();
      assert.equal(await addInput.inputValue(), "", "the box is emptied once the address is in");
      await addInput.fill("OWNER@example.com");
      await addButton.click();
      await recipients.getByText("1 of 20.").waitFor();
      assert.equal(await recipients.getByRole("listitem").count(), 1, "the same address twice is one recipient");
      await addInput.fill("fail@example.com");
      await addButton.click();
      await recipients.getByText("2 of 20.").waitFor();
      await recipients.getByText("Nothing has been sent to this address yet.").first().waitFor();

      // a test needs the sender address to have been saved first
      await recipients.getByRole("button", { name: "Send a test to owner@example.com" }).click();
      await recipients.getByRole("alert").filter({ hasText: "sender address" }).waitFor();

      // the schedule: a wrong time cannot be saved; a right one is, and survives a reload
      const time = mailCard.getByLabel("Send it at");
      await mailCard.getByRole("switch", { name: "Send the daily email" }).click();
      await time.fill("");
      await mailCard.getByText("Enter the time as HH:MM, like 20:00.").first().waitFor();
      assert.equal(await saveSchedule.isDisabled(), true, "a time that is not one cannot be saved");
      await time.fill(sendAt);
      await mailCard.getByText("Enter the sender address before switching the email on.").first().waitFor();
      await mailCard.getByLabel("Sender name").fill("Elm Street cam");
      await mailCard.getByLabel("Sender address").fill("report@mail.example.com");
      await mailCard.getByLabel("Link to this dashboard (optional)").fill("javascript:alert(1)");
      await mailCard.getByText("The link must be a web address", { exact: false }).first().waitFor();
      await mailCard.getByLabel("Link to this dashboard (optional)").fill("https://traffic.example.com");
      for (const day of ["Saturday", "Sunday"]) await mailCard.getByRole("button", { name: day, exact: true }).click();
      assert.equal(
        await explainedBy(mailCard.getByRole("group", { name: "On these days" }).getByRole("button", { name: "More information" })),
        "Goes out weekdays. The first mail after a weekend covers the weekend too.",
        "the days picked are told in words by the bubble of the (i) beside them",
      );
      await mailCard.getByText("Not saved yet").waitFor();
      await saveSchedule.click();
      await mailCard.getByRole("status").filter({ hasText: "Saved." }).waitFor();
      assert.deepEqual((await readMail()).schedule, {
        enabled: true,
        time: sendAt,
        days: [0, 1, 2, 3, 4],
        sender_name: "Elm Street cam",
        sender_address: "report@mail.example.com",
        link_url: "https://traffic.example.com",
      });
      await mailCard.getByText("On. The next one goes out").waitFor();
      await reload();
      await mailCard.waitFor();
      assert.equal(await mailCard.getByLabel("Send it at").inputValue(), sendAt);
      assert.equal(await mailCard.getByRole("button", { name: "Saturday", exact: true }).getAttribute("aria-pressed"), "false");
      assert.equal(await mailCard.getByRole("button", { name: "Monday", exact: true }).getAttribute("aria-pressed"), "true");

      // a test goes out (to a file here) and the result is told next to the address; one that is refused says why
      await recipients.getByRole("button", { name: "Send a test to owner@example.com" }).click();
      await recipients.getByText("Sent. Look in that inbox").waitFor();
      await recipients.getByText("The test mail was sent at").waitFor();
      await page.waitForTimeout(5_200); // tests are limited to one every five seconds
      await recipients.getByRole("button", { name: "Send a test to fail@example.com" }).click();
      await recipients.getByRole("alert").filter({ hasText: "The test did not get through" }).waitFor();
      await recipients.getByText("The test mail did not get through at").waitFor();
      const afterTests = await readMail();
      const byAddress = Object.fromEntries(afterTests.recipients.map((r) => [r.address, r]));
      assert.equal(byAddress["owner@example.com"].last_status, "sent");
      assert.equal(byAddress["fail@example.com"].last_status, "failed");
      assert.match(byAddress["fail@example.com"].last_detail, /fail@/);

      // the preview is the mail as it would be made now, in a sandbox that can touch nothing
      await mailCard.getByRole("button", { name: "Preview the mail" }).click();
      await mailCard.getByText("[Test] TrafficPi").first().waitFor();
      const frame = mailCard.locator('iframe[title="Preview of the daily email"]');
      await frame.waitFor();
      assert.equal(await frame.getAttribute("sandbox"), "", "every sandbox restriction is on");
      await page.frameLocator('iframe[title="Preview of the daily email"]').getByText("TrafficPi daily report").first().waitFor();
      await mailCard.getByRole("button", { name: "Plain text" }).click();
      const plain = mailCard.locator("pre").filter({ hasText: "TrafficPi daily report" });
      await plain.waitFor();
      // every outcome has its count and its share, and there is a fastest estimate
      const mailText = await plain.innerText();
      for (const label of ["Stopped:", "Rolled through:", "Did not stop:", "Could not tell:"]) {
        assert.match(mailText, new RegExp(`${label}\\s+\\d+( \\(\\d+%\\))?\\n`), `${label} has a count and a share`);
      }
      assert.match(mailText, /Fastest estimate:\s+(about \d+ (mph|km\/h)|almost still|–)/);
      await mailCard.getByRole("button", { name: "Close the preview" }).click();
      assert.equal(await mailCard.locator("iframe").count(), 0);
      await shot("mail");

      // the System page says it in a line, and a removed address is gone
      await goTo("System");
      const summary = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Daily email", exact: true }) });
      await summary.getByText("On. The next one goes out").waitFor();
      await summary.getByTestId("last-mail").getByText("No daily email has been due yet.").waitFor();
      await summary.getByText("Some addresses did not get their last mail").waitFor();
      await summary.getByRole("link", { name: "Daily email settings" }).click();
      await mailCard.waitFor();
      await recipients.getByRole("button", { name: "Remove fail@example.com" }).click();
      await recipients.getByText("fail@example.com").waitFor({ state: "detached" });
      assert.equal((await readMail()).recipients.length, 1);
    } finally {
      // the next steps (and the next run) expect an email that is off with nobody on the list
      await page.evaluate(async (settings) => {
        const mail = await (await fetch("/api/mail")).json();
        for (const r of mail.recipients) await fetch(`/api/mail/recipients/${r.id}`, { method: "DELETE" });
        await fetch("/api/mail/schedule", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      }, original.schedule);
    }
    const restored = await readMail();
    assert.deepEqual(restored.schedule, original.schedule);
    assert.equal(restored.recipients.length, 0);
  });

  await step("the stop-sign hours are set by the sun and by the clock, shown as they are typed, saved, noted where the passes are, and kept by the vision service", async () => {
    await goTo("Setup");
    const readHours = () => page.evaluate(async () => (await fetch("/api/stops/hours")).json());
    const putJson = (path, body) =>
      page.evaluate(async ([where, what]) => (await fetch(where, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(what) })).status, [path, body]);
    const originalSettings = await page.evaluate(async () => (await fetch("/api/settings")).json());
    const original = (await readHours()).config;
    // The sun's times are told by the camera's clock, so the camera's zone has to be the place's: the
    // step sets it (and puts it back), which also makes it the same on a machine that keeps UTC.
    assert.equal(await putJson("/api/settings", { ...originalSettings, timezone: "America/New_York" }), 200);
    try {
      await page.reload();
      const card = page.locator("section").filter({ has: page.getByRole("heading", { level: 2, name: "Stop-sign hours", exact: true }) });
      const form = card.getByRole("form", { name: "Stop-sign hours" });
      await form.waitFor();
      const daylight = form.getByRole("switch", { name: "Judge stop signs only in daylight" });
      const timed = form.getByRole("switch", { name: "Judge stop signs only at set times" });
      const latitude = form.getByLabel("Latitude");
      const longitude = form.getByLabel("Longitude");
      const before = form.getByLabel("Start (minutes before sunrise)");
      const after = form.getByLabel("End (minutes after sunset)");
      const save = form.getByRole("button", { name: "Save the hours" });
      const meaning = form.getByRole("region", { name: "What these hours mean" });
      const saved = form.getByRole("status").filter({ hasText: "Saved." });
      const rangeFrom = (n) => form.getByLabel(`Range ${n} from`);
      const rangeTo = (n) => form.getByLabel(`Range ${n} to`);
      const clockIn = (ms) => new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).format(new Date(ms));
      const place = { latitude: 40.7128, longitude: -74.006 };

      // to begin with nothing is limited, and there is nothing to save
      assert.deepEqual([original.daylight, original.clock], [false, false]);
      await card.getByText("Every hour", { exact: true }).waitFor();
      await meaning.getByText("Every pass is judged, at every hour.").waitFor();
      assert.equal(await save.isDisabled(), true, "nothing to save yet");

      // the daylight limit needs a place, and says so
      await daylight.click();
      await form.getByText("Enter the camera's latitude and longitude to judge only in daylight.").waitFor();
      assert.equal(await save.isDisabled(), true, "no place, no daylight limit");

      // a longitude with the wrong sign puts the sun hours from midday: the card says so, and what to do
      await latitude.fill("40.7128");
      await longitude.fill("74.006");
      await meaning.getByText("far from midday", { exact: false }).waitFor();
      await meaning.getByText("negative longitude", { exact: false }).waitFor();

      // the right sign: the warning goes, and the sun's times for today come in the camera's clock
      await longitude.fill("-74.006");
      await meaning.getByText("far from midday", { exact: false }).waitFor({ state: "detached" });
      const sunLine = meaning.getByText(/the sun rises at \d\d:\d\d and sets at \d\d:\d\d, so the daylight window is \d\d:\d\d to \d\d:\d\d\./);
      await sunLine.waitFor();
      const words = (await sunLine.textContent()) ?? "";
      const [, rise, set, from, to] = words.match(/rises at (\d\d:\d\d) and sets at (\d\d:\d\d), so the daylight window is (\d\d:\d\d) to (\d\d:\d\d)/) ?? [];
      assert.ok(rise && set && rise < "12:00" && set > "12:00", `the sun rises before midday and sets after it: ${words}`);
      const asked = await page.evaluate(
        async (config) => (await fetch("/api/stops/hours/check", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(config) })).json(),
        { ...original, daylight: true, ...place },
      );
      assert.deepEqual([rise, set, from, to], [asked.day.sunrise, asked.day.sunset, asked.day.start, asked.day.end].map(clockIn), "the card tells the server's times, by the camera's clock");
      assert.equal(asked.day.sunrise - asked.day.start, 30 * 60_000, "the window starts half an hour before sunrise");
      assert.equal(asked.day.end - asked.day.sunset, 30 * 60_000, "and ends half an hour after sunset");
      assert.equal(asked.day.noon_offset_min > -180 && asked.day.noon_offset_min < 180, true, "the sun is highest within a few hours of noon");

      // the margins: none is the sun itself; a typo is refused in words; 30 and 30 are the owner's own
      await before.fill("0");
      await after.fill("0");
      await meaning.getByText(`the daylight window is ${rise} to ${set}.`).waitFor();
      await before.fill("abc");
      await form.getByText("The start must be a whole number of minutes from -120 to 180 before sunrise", { exact: false }).waitFor();
      assert.equal(await save.isDisabled(), true, "a start that is not a number cannot be saved");
      await before.fill("30");
      await after.fill("30");
      await meaning.getByText(`the daylight window is ${from} to ${to}.`).waitFor();
      await shot("stop-hours");

      // saved: the server has it, the card says whether stop signs are being judged now, and it survives a reload
      await save.click();
      await saved.waitFor();
      const daylightOnly = { ...original, daylight: true, ...place };
      const afterSave = await readHours();
      assert.deepEqual(afterSave.config, daylightOnly);
      assert.equal(afterSave.status.limited, true);
      await card.getByText(afterSave.status.judging ? "Judging now" : "Paused now", { exact: true }).waitFor();
      assert.equal(await save.isDisabled(), true, "saved, so nothing left to save");
      await page.reload();
      await form.waitFor();
      assert.equal(await daylight.getAttribute("aria-checked"), "true");
      assert.equal(await timed.getAttribute("aria-checked"), "false");
      assert.equal(await latitude.inputValue(), "40.7128");
      assert.equal(await longitude.inputValue(), "-74.006");
      assert.equal(await before.inputValue(), "30");

      // the pages that show the passes say that they are judged only at some hours
      const only = "Stop signs are judged only from 30 minutes before sunrise to 30 minutes after sunset";
      await goTo("History");
      await page.getByTestId("stop-hours-note").getByText(only, { exact: false }).waitFor();
      await goTo("Data");
      await page.getByTestId("stop-hours-note").getByText(only, { exact: false }).waitFor();

      // set times: ranges (one through midnight), and the days they apply on
      await goTo("Setup");
      await form.waitFor();
      await timed.click();
      await rangeFrom(1).fill("07:30");
      await rangeTo(1).fill("09:00");
      await form.getByRole("button", { name: "Add another range" }).click();
      assert.equal(await rangeFrom(2).inputValue(), "09:00", "the next range begins where the last one ended");
      assert.equal(await rangeTo(2).inputValue(), "10:00");
      await rangeFrom(2).fill("22:00");
      await rangeTo(2).fill("06:00");
      await form.getByText("through midnight", { exact: true }).waitFor();
      for (const name of ["Saturday", "Sunday"]) await form.getByRole("button", { name, exact: true }).click();
      for (const [name, picked] of [["Monday", "true"], ["Friday", "true"], ["Saturday", "false"], ["Sunday", "false"]]) {
        assert.equal(await form.getByRole("button", { name, exact: true }).getAttribute("aria-pressed"), picked, `${name} is ${picked === "true" ? "" : "not "}picked`);
      }

      // a range that starts and ends together is refused in words
      await rangeTo(2).fill("22:00");
      await form.getByText("Range 2 starts and ends at the same time.").waitFor();
      assert.equal(await save.isDisabled(), true);
      await rangeTo(2).fill("06:00");

      // a range can be taken out but never the last one
      await form.getByRole("button", { name: "Remove range 2" }).click();
      assert.equal(await rangeFrom(2).count(), 0);
      assert.equal(await form.getByRole("button", { name: "Remove range 1" }).isDisabled(), true, "one range is always left");
      await form.getByRole("button", { name: "Add another range" }).click();
      await rangeFrom(2).fill("22:00");
      await rangeTo(2).fill("06:00");

      // both limits are told in one sentence, and saved
      const both = `${only}, and from 07:30 to 09:00 and from 22:00 to 06:00 on weekdays.`;
      await meaning.getByText(both).waitFor();
      await save.click();
      await saved.waitFor();
      const ranges = [{ start: "07:30", end: "09:00" }, { start: "22:00", end: "06:00" }];
      assert.deepEqual((await readHours()).config, { ...daylightOnly, clock: true, ranges, days: [0, 1, 2, 3, 4] });
      await goTo("History");
      await page.getByTestId("stop-hours-note").getByText(both, { exact: false }).waitFor();

      // both off again: every pass is judged, and the pages say nothing about hours
      await goTo("Setup");
      await form.waitFor();
      await daylight.click();
      await timed.click();
      await save.click();
      await saved.waitFor();
      await card.getByText("Every hour", { exact: true }).waitFor();
      const answered = page.waitForResponse((response) => new URL(response.url()).pathname === "/api/stops/hours");
      await goTo("History");
      await answered;
      await page.getByTestId("stops-headline").waitFor();
      await page.waitForTimeout(200);
      assert.equal(await page.getByTestId("stop-hours-note").count(), 0, "with no limit there is nothing to note");

      // and the vision service obeys: with hours that do not hold the present no pass is recorded,
      // and with the limit off they come again (the made-up street's vehicles do not stop, every few seconds)
      const newestStop = () => page.evaluate(async () => (await (await fetch("/api/events?type=stop&limit=1")).json())[0]?.id ?? 0);
      const hourNow = Number(new Intl.DateTimeFormat("en-GB", { timeZone: "America/New_York", hour: "2-digit", hourCycle: "h23" }).format(new Date()));
      const oClock = (hour) => `${String(hour % 24).padStart(2, "0")}:00`;
      let mark = await newestStop();
      await eventually(async () => assert.ok((await newestStop()) > mark), "vehicles are judged while no limit is on", 90_000);
      assert.equal(await putJson("/api/stops/hours", { ...original, clock: true, ranges: [{ start: oClock(hourNow + 3), end: oClock(hourNow + 4) }] }), 200);
      await page.waitForTimeout(3000); // a pass that was on its way when the limit came may still be recorded
      mark = await newestStop();
      await page.waitForTimeout(12_000);
      assert.equal(await newestStop(), mark, "no pass is judged outside the hours");
      assert.equal(await putJson("/api/stops/hours", original), 200);
      await eventually(async () => assert.ok((await newestStop()) > mark), "vehicles are judged again once the limit is off", 90_000);
    } finally {
      // the next steps (and the next run) expect what was there
      await putJson("/api/stops/hours", original);
      await putJson("/api/settings", originalSettings);
    }
    assert.deepEqual((await readHours()).config, original);
  });

  await step("a viewer who comes through Cloudflare gets a still picture and not the stream, until the owner allows it", async () => {
    // A second browser whose every request carries what Cloudflare adds to those it passes on.
    const remoteContext = await browser.newContext({
      viewport: { width: 1280, height: 900 },
      extraHTTPHeaders: { "CF-Ray": "e2e0123456789abc-EWR", "CF-Connecting-IP": "203.0.113.9" },
    });
    const remote = await remoteContext.newPage();
    remote.setDefaultTimeout(20_000);
    const asked = []; // the picture requests this viewer made, and how they were answered
    remote.on("response", (response) => {
      const path = new URL(response.url()).pathname;
      if (path === "/api/preview.mjpg" || path === "/api/snapshot.jpg") asked.push(`${path} ${response.status()}`);
    });
    remote.on("pageerror", (error) => problems.push(`uncaught (remote viewer): ${error.message}`));
    remote.on("console", (message) => {
      if (message.type() === "error") problems.push(`console (remote viewer): ${message.text()}`);
    });
    const until = async (condition, what) => {
      for (let i = 0; i < 150; i++) {
        if (condition()) return;
        await remote.waitForTimeout(100);
      }
      assert.fail(`never happened: ${what}; the viewer's picture requests were ${JSON.stringify(asked)}`);
    };
    const readSettings = () => page.evaluate(async () => (await fetch("/api/settings")).json());
    const statusOfStream = (where) =>
      where.evaluate(async () => {
        const controller = new AbortController();
        const response = await fetch("/api/preview.mjpg", { signal: controller.signal });
        controller.abort(); // the stream never ends; its status is what is wanted
        return response.status;
      });
    const original = await readSettings();
    assert.equal(original.remote_live_video, false, "live video for remote viewers starts off");
    try {
      await remote.goto(`${base}/`);
      await remote.getByLabel("Username").fill(user);
      await remote.getByLabel("Password").fill(password);
      await remote.getByRole("button", { name: "Sign in" }).click();
      await remote.getByRole("heading", { name: "Live view" }).waitFor();

      // a still picture, with the reason; the stream is never even asked for
      const notice = remote.getByTestId("still-notice");
      await notice.waitFor();
      assert.match(await notice.innerText(), /Cloudflare/);
      await remote.getByRole("img", { name: /Still picture of the street/ }).waitFor();
      await until(() => asked.includes("/api/snapshot.jpg 200"), "a still picture arrives");
      assert.deepEqual(asked.filter((entry) => entry.startsWith("/api/preview")), [], "the stream was not asked for");
      await remote.getByRole("switch", { name: "Picture" }).waitFor(); // the switch says what it switches
      if (shots) await remote.screenshot({ path: `${shots}/remote-still.png`, fullPage: true });

      // the same viewer asking for the stream by hand is refused, and the person at home is not
      assert.equal(await statusOfStream(remote), 403);
      assert.equal(await statusOfStream(page), 200);

      // the viewer is told on the Setup page that this is through Cloudflare, and where the switch is
      await remote.getByRole("link", { name: "Setup", exact: true }).click();
      await remote.getByRole("form", { name: "Settings" }).waitFor();
      await remote.getByText("You are looking at this page through Cloudflare now.").waitFor();
      await remote.getByRole("switch", { name: "Allow live video for viewers who come through Cloudflare" }).waitFor();

      // the owner allows it (from home, here), and the viewer's next page has the stream
      await goTo("Setup");
      const allow = page.getByRole("switch", { name: "Allow live video for viewers who come through Cloudflare" });
      assert.equal(await allow.getAttribute("aria-checked"), "false");
      await allow.click();
      await page.getByText("Settings not saved yet").waitFor();
      await page.getByRole("button", { name: "Save settings" }).click();
      await page.getByRole("status").filter({ hasText: "Saved." }).waitFor();
      assert.equal((await readSettings()).remote_live_video, true);
      assert.equal(await statusOfStream(remote), 200, "the stream is open to the remote viewer now");
      asked.length = 0;
      await remote.goto(`${base}/`);
      await remote.getByRole("heading", { name: "Live view" }).waitFor();
      await until(() => asked.includes("/api/preview.mjpg 200"), "the stream arrives");
      assert.equal(await remote.getByTestId("still-notice").count(), 0, "no more notice of a still");
      assert.deepEqual(asked.filter((entry) => entry.startsWith("/api/snapshot")), [], "and no stills");
      await remote.getByRole("switch", { name: "Video" }).waitFor();
    } finally {
      await page.evaluate(async (settings) => {
        await fetch("/api/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(settings) });
      }, original);
      await remoteContext.close();
    }
    assert.deepEqual(await readSettings(), original);
  });

  await step("the System page tells about the accelerator, the data on disk, the last backup and the memory test", async () => {
    await goTo("System");
    const tile = (label) => page.locator("dt", { hasText: label }).locator("xpath=..");
    await tile("Accelerator").waitFor();
    // the made-up street has no chip, and says so; the model it uses is named
    assert.match(await tile("Accelerator").innerText(), /simulated/);
    assert.match(await tile("Accelerator").innerText(), /model demo/);
    assert.match(await tile("Data on disk").innerText(), /database .* MB · pictures and clips .* \(\d+ files?\) · backups .* \(\d+\)/);
    // a fresh stack has no daily backup and no memory test on record, and says what to do or what that means
    assert.match(await tile("Last backup").innerText(), /none yet/);
    assert.match(await tile("Last backup").innerText(), /not backed up/);
    assert.match(await tile("Memory test").innerText(), /not run/);
    assert.match(await tile("Uptime").innerText(), /dashboard server/);
    await shot("system-machine");
  });

  await step("the System page tells how the machine has been: numbers, charts, what happened, and the same as a table", async () => {
    await goTo("System");
    const card = page.getByTestId("system-history");
    await card.waitFor();
    const tile = (label) => card.locator("dt", { hasText: label }).locator("xpath=..");
    // the demo made up three days (a slowdown, a weak supply, a camera that went away, a hole in the record) and a day shows all of them
    await tile("Hottest").waitFor();
    assert.match(await tile("Hottest").innerText(), /\d+\.\d °C/);
    assert.match(await tile("Hottest").innerText(), /past 80 °C/, "the hot afternoon is told in words, not only by colour");
    assert.match(await tile("Slowed down").innerText(), /\d+ min/);
    assert.match(await tile("Camera working").innerText(), /\d+\.\d%/);
    const episodes = card.getByTestId("system-history-episodes");
    for (const title of ["Slowed itself down", "Power supply too weak", "Camera not working", "Nothing recorded"]) {
      await episodes.getByText(title).first().waitFor();
    }
    assert.match(await episodes.innerText(), /Up to 8\d\.\d °C/, "the slowdown says how hot it got");
    assert.equal(await card.locator(".recharts-wrapper").count(), 2, "a chart of the temperature and one of the frame rate");
    assert.ok((await card.locator(".recharts-line-curve").count()) >= 2, "lines are drawn");
    await shot("system-history");

    // the same numbers as a table, newest first
    await card.getByRole("button", { name: "Table", exact: true }).click();
    const table = card.getByRole("table");
    await table.waitFor();
    assert.ok((await table.locator("tbody tr").count()) > 100, "a row for each stretch that was recorded");
    assert.match(await table.locator("thead").innerText(), /The Pi says/);
    assert.match(await table.locator("tbody").innerText(), /the CPU is being slowed down/, "the Pi's flags are in words");
    await card.getByRole("button", { name: "Chart", exact: true }).click();

    // a shorter period has less to tell: the made-up troubles are hours back (a real slowdown of this very board may be in the last hour, since the demo
    // records the machine it runs on, so "nothing to report" cannot be asked for), and the choice is kept
    await card.getByRole("button", { name: "1 hour", exact: true }).click();
    await episodes.getByText("Power supply too weak").waitFor({ state: "detached" });
    for (const title of ["Camera not working", "Nothing recorded"]) assert.equal(await episodes.getByText(title).count(), 0, `${title} was hours ago`);
    assert.equal(await card.getByRole("button", { name: "1 hour", exact: true }).getAttribute("aria-pressed"), "true");
    await page.reload();
    await card.waitFor();
    assert.equal(await card.getByRole("button", { name: "1 hour", exact: true }).getAttribute("aria-pressed"), "true", "the period survives a reload");
    await card.getByRole("button", { name: "24 hours", exact: true }).click(); // as the next runs expect
    await episodes.getByText("Slowed itself down").first().waitFor();
  });

  await step("the pages are real addresses: links, deep links, a missing slash, reload and back all work", async () => {
    await goTo("Live");
    assert.equal(pagePath(), "/");
    const status = () => page.locator("header").getByRole("status").innerText();
    const before = await status();
    await goTo("System");
    assert.equal(pagePath(), "/system/");
    assert.equal(await pagesNav().getByRole("link", { name: "Live", exact: true }).getAttribute("aria-current"), null);
    assert.equal(await page.getByRole("heading", { name: "System", level: 2 }).count(), 1, "the system card");
    assert.match(await status(), /Live|offline|lost/i, `the header still reports the camera (it said: ${before})`);

    await page.reload();
    await page.getByRole("heading", { level: 1, name: "System" }).waitFor({ state: "attached" });
    await page.goBack(); // the browser's back button returns to the page before
    await page.getByRole("heading", { level: 1, name: "Live" }).waitFor({ state: "attached" });
    assert.equal(pagePath(), "/");

    await page.goto(`${base}/history`); // no trailing slash: the server sends it to the page
    await page.getByRole("heading", { level: 1, name: "History" }).waitFor({ state: "attached" });
    assert.equal(pagePath(), "/history/");
    await page.goto(`${base}/data`);
    await page.getByRole("heading", { level: 1, name: "Data" }).waitFor({ state: "attached" });
    assert.equal(pagePath(), "/data/");
    assert.equal(await page.title(), "Data · TrafficPi");
    await page.goto(`${base}/setup/`); // a page opened directly, as from a bookmark
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });
    assert.equal(await page.title(), "Setup · TrafficPi");
  });

  await step("a camera view that has moved is told on every page, and the System page says how far", async () => {
    const now = Date.now();
    const finding = (state, over = {}) => ({
      state,
      message: "The camera sees what it saw when the reference picture was kept.",
      shift_pct: 0.4,
      dx_pct: -0.3,
      dy_pct: -0.2,
      rotation_deg: 0.02,
      scale: 1.001,
      alignment: 0.7,
      checked_at: now,
      sure_at: now,
      unsure_because: null,
      ...over,
    });
    const banner = page.getByTestId("drift-banner");

    await goTo("Live");
    assert.equal(await banner.count(), 0, "no notice while nothing is wrong");

    fakeDrift = finding("drifted", {
      message:
        "The camera's view has changed since the reference picture was kept: the picture has moved 2.4% of the width to the left. The lines and zones are off by up to 2.4% of the picture's width.",
      shift_pct: 2.4,
      dx_pct: -2.4,
      dy_pct: 0.3,
    });
    await banner.waitFor();
    assert.equal(await banner.getAttribute("role"), "alert");
    assert.match(await banner.innerText(), /moved 2\.4% of the width to the left/);
    assert.match(await banner.innerText(), /put the camera back/);
    await shot("drift-banner");

    await goTo("System"); // the notice follows to the other pages
    await banner.waitFor();
    await page.getByRole("heading", { name: "Camera view", level: 2 }).waitFor();
    const text = await page.getByTestId("drift-message").innerText();
    assert.match(text, /off by up to 2\.4%/);
    assert.ok((await page.getByText("Has moved", { exact: true }).count()) >= 1, "the badge says it has moved");
    assert.ok((await page.getByText("2.4%", { exact: true }).count()) >= 1, "and the tile says how far");
    assert.ok((await page.getByText("left 2.4%, down 0.3%").count()) >= 1, "and in which direction");
    await shot("drift-card");

    await page.getByRole("link", { name: "Setup page" }).click(); // the notice leads to where it is put right
    await page.getByRole("heading", { level: 1, name: "Setup" }).waitFor({ state: "attached" });
    assert.equal(pagePath(), "/setup/");

    fakeDrift = finding("ok"); // the view is back
    await banner.waitFor({ state: "detached" });
    await goTo("System");
    await page.getByText("In place", { exact: true }).waitFor();

    fakeDrift = finding("unknown", { shift_pct: null, dx_pct: null, dy_pct: null, rotation_deg: null, scale: null, alignment: null, sure_at: null, unsure_because: "too few features to compare: the view is too dark, plain or blurred", message: "Checking against the new reference picture." });
    await page.getByText("Not sure yet", { exact: true }).waitFor();
    assert.match(await page.locator("main").innerText(), /could not tell: too few features/);
    assert.equal(await banner.count(), 0, "not being sure is not a warning");

    fakeDrift = null; // what the vision service says itself, from here on
  });

  await step("leaving the Setup page with unsaved changes asks first, and nothing is lost if you stay", async () => {
    await openEditor();
    await page.getByRole("button", { name: "Add counting line" }).click(); // an unsaved line
    const unsaved = page.getByRole("list", { name: "Lines and zones" }).getByRole("button");
    const before = await unsaved.count();
    const asked = [];
    page.once("dialog", async (dialog) => {
      asked.push(dialog.message());
      await dialog.dismiss(); // stay
    });
    await pagesNav().getByRole("link", { name: "Live", exact: true }).click();
    await page.waitForTimeout(300);
    assert.match(asked[0] ?? "", /not saved/);
    assert.equal(pagePath(), "/setup/", "still on the page");
    assert.equal(await unsaved.count(), before, "and the draft is intact");

    page.once("dialog", (dialog) => void dialog.accept()); // now leave
    await goTo("Live");
    await openEditor();
    assert.equal(await unsaved.count(), before - 1, "the unsaved line did not survive leaving");
    assert.equal(await saveButton().isEnabled(), false);
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

  // The browser logs every refused request; the ones above were provoked on purpose (a 409 is the daily
  // email step asking for a test before the sender address was saved).
  const real = problems.filter((problem) => !/Failed to load resource: the server responded with a status of (401|403|409|422|503)/.test(problem));
  assert.deepEqual(real, [], `the page reported problems:\n${real.join("\n")}`);
  console.log("all steps passed");
} catch (error) {
  console.error(`FAILED at: ${current}`);
  console.error(`the focus was in: ${await focusNow().catch(() => "unknown")}`);
  await shot("failure");
  console.error(error);
  process.exitCode = 1;
} finally {
  await browser.close();
}
