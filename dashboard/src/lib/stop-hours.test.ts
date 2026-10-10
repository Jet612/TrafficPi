import { describe, expect, it } from "vitest";
import {
  MAX_MARGIN,
  MAX_RANGES,
  MIN_MARGIN,
  addRange,
  dayText,
  fromDraft,
  isChanged,
  noonWarning,
  noteText,
  parseCoordinate,
  parseMargin,
  removeRange,
  savedText,
  statusText,
  toDraft,
  whenText,
  type StopHoursDraft,
} from "./stop-hours";
import type { StopHoursConfig, StopHoursDay, StopHoursStatus } from "./types";

const ZONE = "America/New_York";
const at = (iso: string) => new Date(iso).getTime();
// 20:41 on Friday 9 October 2026 in New York (EDT, UTC-4)
const NIGHT = at("2026-10-10T00:41:00Z");

const none: StopHoursConfig = {
  daylight: false,
  latitude: null,
  longitude: null,
  before_sunrise_min: 30,
  after_sunset_min: 30,
  clock: false,
  ranges: [{ start: "07:00", end: "19:00" }],
  days: [0, 1, 2, 3, 4, 5, 6],
};

const sunny: StopHoursConfig = { ...none, daylight: true, latitude: 40.7128, longitude: -74.006 };

function draft(over: Partial<StopHoursDraft> = {}): StopHoursDraft {
  return { ...toDraft(none), ...over };
}

function status(over: Partial<StopHoursStatus> = {}): StopHoursStatus {
  return {
    now: NIGHT,
    timezone: ZONE,
    limited: true,
    judging: false,
    next_change: at("2026-10-10T10:32:00Z"), // 06:32 the next morning in New York
    description: "from 30 minutes before sunrise to 30 minutes after sunset",
    day: null,
    ...over,
  };
}

function day(over: Partial<StopHoursDay> = {}): StopHoursDay {
  return {
    date: "2026-10-09",
    sunrise: at("2026-10-09T11:01:00Z"), // 07:01
    sunset: at("2026-10-09T22:25:00Z"), // 18:25
    start: at("2026-10-09T10:31:00Z"), // 06:31
    end: at("2026-10-09T22:55:00Z"), // 18:55
    polar: null,
    noon_offset_min: 43,
    ...over,
  };
}

describe("reading what was typed", () => {
  it("takes a coordinate with a sign and decimals, and refuses what is not a number in range", () => {
    expect(parseCoordinate("40.71", 90)).toBe(40.71);
    expect(parseCoordinate("-74.0060", 180)).toBe(-74.006);
    expect(parseCoordinate(" +12 ", 90)).toBe(12);
    expect(parseCoordinate("90", 90)).toBe(90);
    expect(parseCoordinate("-180", 180)).toBe(-180);
    for (const bad of ["", "abc", "40,71", "40.", ".5", "91", "-90.1", "1e2", "40.71.1", "40°", "--4", "0x10", "Infinity", "NaN"]) {
      expect(parseCoordinate(bad, 90), bad).toBeNull();
    }
  });

  it("takes a margin as a whole number of minutes, negative to start later", () => {
    expect(parseMargin("30")).toBe(30);
    expect(parseMargin("-15")).toBe(-15);
    expect(parseMargin(" 0 ")).toBe(0);
    expect(parseMargin(String(MIN_MARGIN))).toBe(MIN_MARGIN);
    expect(parseMargin(String(MAX_MARGIN))).toBe(MAX_MARGIN);
    for (const bad of ["", "abc", "30.5", String(MIN_MARGIN - 1), String(MAX_MARGIN + 1), "1e1", "--3", "3 0"]) {
      expect(parseMargin(bad), bad).toBeNull();
    }
  });
});

describe("the form and the setting", () => {
  it("starts from what is saved, the numbers as text", () => {
    expect(toDraft(sunny)).toMatchObject({ daylight: true, latitude: "40.7128", longitude: "-74.006", before: "30", after: "30", clock: false });
    expect(toDraft(none)).toMatchObject({ latitude: "", longitude: "" });
    expect(toDraft(none).ranges).toEqual([{ start: "07:00", end: "19:00" }]);
  });

  it("gives back what it was given, when nothing was changed", () => {
    for (const config of [none, sunny, { ...none, clock: true, days: [0, 4], ranges: [{ start: "22:00", end: "06:00" }] }]) {
      const result = fromDraft(toDraft(config));
      expect(result).toEqual({ ok: true, config });
    }
  });

  it("is not changed by spaces, and is changed by anything else", () => {
    expect(isChanged(toDraft(sunny), sunny)).toBe(false);
    expect(isChanged(draft({ ...toDraft(sunny), latitude: " 40.7128 " }), sunny)).toBe(false);
    expect(isChanged(draft({ ...toDraft(sunny), latitude: "40.72" }), sunny)).toBe(true);
    expect(isChanged({ ...toDraft(sunny), daylight: false }, sunny)).toBe(true);
    expect(isChanged({ ...toDraft(sunny), days: [0, 1] }, sunny)).toBe(true);
    expect(isChanged({ ...toDraft(sunny), ranges: [{ start: "07:00", end: "19:01" }] }, sunny)).toBe(true);
    expect(isChanged({ ...toDraft(sunny), latitude: "oops" }, sunny)).toBe(true); // it cannot be saved as it is
  });

  it("sorts the days it sends", () => {
    const result = fromDraft(draft({ clock: true, days: [4, 0, 2] }));
    expect(result.ok && result.config.days).toEqual([0, 2, 4]);
  });

  it("keeps a place while the daylight limit is off, and sends none when both are empty", () => {
    const result = fromDraft(draft({ latitude: "40.71", longitude: "-74.01" }));
    expect(result).toMatchObject({ ok: true, config: { daylight: false, latitude: 40.71, longitude: -74.01 } });
    expect(fromDraft(draft())).toMatchObject({ ok: true, config: { latitude: null, longitude: null } });
  });

  it("says in words what is wrong", () => {
    const problem = (over: Partial<StopHoursDraft>) => {
      const result = fromDraft(draft(over));
      return result.ok ? null : result.problem;
    };
    expect(problem({ daylight: true })).toMatch(/latitude and longitude/);
    expect(problem({ latitude: "40.71" })).toMatch(/both/);
    expect(problem({ longitude: "-74" })).toMatch(/both/);
    expect(problem({ latitude: "91", longitude: "0" })).toMatch(/latitude.*-90 to 90/);
    expect(problem({ latitude: "0", longitude: "181" })).toMatch(/longitude.*-180 to 180/);
    expect(problem({ before: "x" })).toMatch(/before sunrise/);
    expect(problem({ after: "999" })).toMatch(/after sunset/);
    expect(problem({ days: [] })).toMatch(/at least one day/);
    expect(problem({ ranges: [{ start: "", end: "19:00" }] })).toMatch(/both times/);
    expect(problem({ ranges: [{ start: "07:00", end: "07:00" }] })).toMatch(/same time/);
    expect(problem({ ranges: [] })).toMatch(/at least one/);
    expect(problem({ ranges: Array.from({ length: MAX_RANGES + 1 }, () => ({ start: "07:00", end: "19:00" })) })).toMatch(new RegExp(String(MAX_RANGES)));
  });

  it("names the range when there are several", () => {
    const result = fromDraft(draft({ ranges: [{ start: "07:00", end: "09:00" }, { start: "14:00", end: "14:00" }] }));
    expect(!result.ok && result.problem).toMatch(/range 2/i);
  });

  it("checks the hours and the place even while their limits are off, because they are sent", () => {
    expect(fromDraft(draft({ clock: false, ranges: [{ start: "07:00", end: "07:00" }] })).ok).toBe(false);
    expect(fromDraft(draft({ daylight: false, latitude: "abc", longitude: "0" })).ok).toBe(false);
  });
});

describe("ranges", () => {
  it("adds one after the last, an hour long, and stops at the most", () => {
    const one = [{ start: "07:00", end: "19:00" }];
    expect(addRange(one)).toEqual([...one, { start: "19:00", end: "20:00" }]);
    expect(addRange([{ start: "22:30", end: "23:30" }])[1]).toEqual({ start: "23:30", end: "00:30" }); // through midnight, it is allowed
    let ranges = one;
    for (let i = 0; i < 20; i += 1) ranges = addRange(ranges);
    expect(ranges).toHaveLength(MAX_RANGES);
  });

  it("removes one but never the last", () => {
    const two = [{ start: "07:00", end: "09:00" }, { start: "14:00", end: "16:00" }];
    expect(removeRange(two, 0)).toEqual([{ start: "14:00", end: "16:00" }]);
    expect(removeRange(removeRange(two, 0), 0)).toEqual([{ start: "14:00", end: "16:00" }]);
    expect(removeRange(two, 5)).toEqual(two);
  });
});

describe("saying when", () => {
  it("says today, tomorrow, or the day, by the clock of the camera", () => {
    expect(whenText(at("2026-10-10T22:55:00Z"), NIGHT, ZONE)).toBe("tomorrow at 18:55"); // the 10th, 18:55 EDT
    expect(whenText(at("2026-10-10T03:30:00Z"), NIGHT, ZONE)).toBe("today at 23:30");
    expect(whenText(at("2026-10-12T11:00:00Z"), NIGHT, ZONE)).toBe("Mon 12 Oct at 07:00");
  });

  it("goes by the camera's date, not Greenwich's", () => {
    // 20:41 in New York on the 9th is already the 10th in Greenwich; 06:32 the next morning there is "tomorrow"
    expect(whenText(at("2026-10-10T10:32:00Z"), NIGHT, ZONE)).toBe("tomorrow at 06:32");
    expect(whenText(at("2026-10-10T10:32:00Z"), NIGHT, "UTC")).toBe("today at 10:32");
  });

  it("is right across the end of a month and a year", () => {
    const evening = at("2026-12-31T23:30:00Z"); // 18:30 on the 31st in New York
    expect(whenText(at("2027-01-01T12:00:00Z"), evening, ZONE)).toBe("tomorrow at 07:00");
    const eom = at("2026-10-31T20:00:00Z");
    expect(whenText(at("2026-11-01T14:00:00Z"), eom, ZONE)).toBe("tomorrow at 09:00"); // EST by then
  });
});

describe("telling the day's sun", () => {
  it("gives sunrise, sunset and the window, by the camera's clock", () => {
    expect(dayText(day(), ZONE)).toBe("Today (Fri 9 Oct) the sun rises at 07:01 and sets at 18:25, so the daylight window is 06:31 to 18:55.");
  });

  it("tells the midnight sun and the polar night", () => {
    expect(dayText(day({ polar: "day", sunrise: null, sunset: null, start: null, end: null }), ZONE)).toMatch(/does not set.*all of it is daylight/);
    expect(dayText(day({ polar: "night", sunrise: null, sunset: null, start: null, end: null }), ZONE)).toMatch(/does not rise.*no daylight/);
  });

  it("says so when the margins leave no daylight", () => {
    expect(dayText(day({ start: null, end: null }), ZONE)).toMatch(/margins leave no daylight/);
  });

  it("warns, with the way to put it right, when the sun is highest hours from midday", () => {
    expect(noonWarning(day())).toBeNull();
    expect(noonWarning(day({ noon_offset_min: 117 }))).toBeNull(); // Urumqi keeps Beijing time
    expect(noonWarning(day({ noon_offset_min: -180 }))).toBeNull();
    const wrong = noonWarning(day({ noon_offset_min: -549 }));
    expect(wrong).toMatch(/02:51/);
    expect(wrong).toMatch(/negative longitude/);
    expect(noonWarning(day({ noon_offset_min: 540 }))).toMatch(/21:00/);
  });
});

describe("the status", () => {
  it("says nothing is limited when nothing is", () => {
    const line = statusText(status({ limited: false, judging: true, next_change: null, description: "at all hours" }));
    expect(line).toMatchObject({ level: "neutral" });
    expect(line.text).toMatch(/every pass is judged/i);
  });

  it("says that stop signs are being judged, and when that stops", () => {
    const line = statusText(status({ judging: true, next_change: at("2026-10-10T22:55:00Z") }));
    expect(line).toMatchObject({ level: "good" });
    expect(line.text).toBe("Stop signs are being judged now. Judging pauses tomorrow at 18:55.");
  });

  it("says that they are not, and when they will be", () => {
    const line = statusText(status());
    expect(line).toMatchObject({ level: "neutral" });
    expect(line.text).toBe("Stop signs are not being judged now. Judging resumes tomorrow at 06:32.");
  });

  it("says so when no change is in sight", () => {
    const line = statusText(status({ next_change: null }));
    expect(line.text).toMatch(/not being judged now/);
    expect(line.text).toMatch(/does not resume in the next/);
    expect(statusText(status({ judging: true, next_change: null })).text).toBe("Stop signs are being judged now.");
  });
});

describe("the note on other pages", () => {
  it("is empty when nothing is limited", () => {
    expect(noteText(status({ limited: false }))).toBeNull();
  });

  it("says what is judged, and where it stands now", () => {
    expect(noteText(status())).toBe(
      "Stop signs are judged only from 30 minutes before sunrise to 30 minutes after sunset. They are paused now and resume tomorrow at 06:32.",
    );
    expect(noteText(status({ judging: true, next_change: at("2026-10-10T22:55:00Z") }))).toBe(
      "Stop signs are judged only from 30 minutes before sunrise to 30 minutes after sunset. Judging pauses tomorrow at 18:55.",
    );
  });
});

describe("what a save says", () => {
  it("says the vision service uses it at once, or that it could not be told", () => {
    expect(savedText(true)).toBe("Saved. The vision service uses it at once.");
    expect(savedText(false)).toMatch(/could not be reached.*when it starts/);
    expect(savedText(false)).toMatch(/^Saved\./);
  });
});
