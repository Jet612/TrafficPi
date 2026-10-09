import { describe, expect, it } from "vitest";
import {
  describeChecks,
  describeMove,
  describeSize,
  describeTurn,
  driftLabel,
  driftLevel,
  parseDrift,
  sameDrift,
  type DriftStatus,
} from "./drift";

const ok = {
  state: "ok",
  message: "The camera sees what it saw when the reference picture was kept.",
  shift_pct: 0.46,
  dx_pct: -0.33,
  dy_pct: -0.19,
  rotation_deg: 0.03,
  scale: 1.0015,
  alignment: 0.69,
  checked_at: 1_700_000_000_000,
  sure_at: 1_700_000_000_000,
  unsure_because: null,
};

const reading = (over: Partial<DriftStatus> = {}): DriftStatus => ({ ...(parseDrift(ok) as DriftStatus), ...over });

describe("parseDrift", () => {
  it("reads a whole status", () => {
    expect(parseDrift(ok)).toEqual(ok);
  });

  it("reads all four states", () => {
    for (const state of ["ok", "drifted", "unknown", "no_reference"]) {
      expect(parseDrift({ state })?.state).toBe(state);
    }
  });

  it("has nothing when the service keeps no check (an empty block) or sends nothing", () => {
    expect(parseDrift({})).toBeNull();
    expect(parseDrift(undefined)).toBeNull();
    expect(parseDrift(null)).toBeNull();
  });

  it("refuses what is not a status", () => {
    expect(parseDrift("ok")).toBeNull();
    expect(parseDrift([ok])).toBeNull();
    expect(parseDrift({ state: "fine" })).toBeNull();
    expect(parseDrift({ state: 3 })).toBeNull();
  });

  it("makes the numbers it cannot trust null, and keeps the rest", () => {
    const status = parseDrift({ state: "drifted", message: 7, shift_pct: "2", dx_pct: Number.NaN, dy_pct: 1.5, unsure_because: "" });
    expect(status).toMatchObject({ state: "drifted", message: "", shift_pct: null, dx_pct: null, dy_pct: 1.5, unsure_because: null });
  });

  it("fills in what an older vision service leaves out", () => {
    expect(parseDrift({ state: "unknown", message: "Waiting for the first check." })).toEqual({
      state: "unknown",
      message: "Waiting for the first check.",
      shift_pct: null,
      dx_pct: null,
      dy_pct: null,
      rotation_deg: null,
      scale: null,
      alignment: null,
      checked_at: null,
      sure_at: null,
      unsure_because: null,
    });
  });
});

describe("sameDrift", () => {
  it("is true for two readings that say the same, even if they are different objects", () => {
    expect(sameDrift(parseDrift(ok), parseDrift({ ...ok }))).toBe(true);
    expect(sameDrift(null, null)).toBe(true);
  });

  it("notices any change, including a new look that found the same thing", () => {
    expect(sameDrift(parseDrift(ok), parseDrift({ ...ok, state: "drifted" }))).toBe(false);
    expect(sameDrift(parseDrift(ok), parseDrift({ ...ok, checked_at: ok.checked_at + 60_000 }))).toBe(false);
    expect(sameDrift(parseDrift(ok), null)).toBe(false);
    expect(sameDrift(null, parseDrift(ok))).toBe(false);
  });
});

describe("the words for a state", () => {
  it("calls only a moved view a warning", () => {
    expect(driftLevel(reading({ state: "ok" }))).toBe("good");
    expect(driftLevel(reading({ state: "drifted" }))).toBe("warning");
    expect(driftLevel(reading({ state: "unknown" }))).toBe("neutral");
    expect(driftLevel(reading({ state: "no_reference" }))).toBe("neutral");
    expect(driftLevel(null)).toBe("neutral");
  });

  it("labels each state", () => {
    expect(["ok", "drifted", "unknown", "no_reference"].map((state) => driftLabel(reading({ state: state as DriftStatus["state"] })))).toEqual([
      "In place",
      "Has moved",
      "Not sure yet",
      "No reference picture",
    ]);
    expect(driftLabel(null)).toBe("Not checked");
  });
});

describe("how far it moved", () => {
  it("names the directions of the move, and leaves out a move too small to say", () => {
    expect(describeMove(reading({ dx_pct: 1.24, dy_pct: -0.6 }))).toBe("right 1.2%, up 0.6%");
    expect(describeMove(reading({ dx_pct: -2.0, dy_pct: 0.01 }))).toBe("left 2.0%");
    expect(describeMove(reading({ dx_pct: 0.02, dy_pct: -0.04 }))).toBe("");
    expect(describeMove(reading({ dx_pct: null, dy_pct: null }))).toBe("");
  });

  it("never writes a minus zero", () => {
    expect(describeMove(reading({ dx_pct: -0.06, dy_pct: 0 }))).toBe("left 0.1%");
  });

  it("names the turn with its direction", () => {
    expect(describeTurn(reading({ rotation_deg: 0.84 }))).toBe("0.8 degrees clockwise");
    expect(describeTurn(reading({ rotation_deg: -1.5 }))).toBe("1.5 degrees anticlockwise");
    expect(describeTurn(reading({ rotation_deg: 0.03 }))).toBe("");
    expect(describeTurn(reading({ rotation_deg: null }))).toBe("");
  });

  it("names a change of size", () => {
    expect(describeSize(reading({ scale: 1.021 }))).toBe("2.1% bigger");
    expect(describeSize(reading({ scale: 0.97 }))).toBe("3.0% smaller");
    expect(describeSize(reading({ scale: 1.0015 }))).toBe("");
    expect(describeSize(reading({ scale: null }))).toBe("");
  });
});

describe("describeChecks", () => {
  const now = ok.checked_at + 3 * 60_000;

  it("says when it last looked", () => {
    expect(describeChecks(reading(), now)).toBe("Last looked 3 min ago.");
  });

  it("says that the last look could not tell, and when one last could", () => {
    expect(describeChecks(reading({ sure_at: null }), now)).toBe("Last looked 3 min ago; it could not tell.");
    expect(describeChecks(reading({ sure_at: ok.checked_at - 2 * 3600_000 }), now)).toBe(
      "Last looked 3 min ago; the last look that could tell was 2 h ago.",
    );
  });

  it("copes with a service that has not looked, and with none at all", () => {
    expect(describeChecks(reading({ checked_at: null, sure_at: null }), now)).toBe("Not checked yet.");
    expect(describeChecks(null, now)).toContain("not checking");
  });
});
