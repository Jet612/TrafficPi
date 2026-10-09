import { describe, expect, it } from "vitest";
import { classColor, classLabel, classSingular, classSlot, orderedClasses } from "./classes";

describe("classes", () => {
  it("gives each class its own fixed slot", () => {
    expect(classSlot("car")).toBe(1);
    expect(classSlot("truck")).toBe(2);
    expect(classSlot("person")).toBe(6);
    expect(classSlot("giraffe")).toBeNull();
    expect(classColor("bus")).toBe("var(--series-3)");
    expect(classColor("giraffe")).toBe("var(--series-other)");
  });

  it("does not repaint a class when others come and go", () => {
    // The colour is a function of the class alone, never of what else is on screen.
    expect(classColor("truck")).toBe(classColor("truck"));
    expect(orderedClasses(["truck"])).toEqual(["truck"]);
    expect(classColor(orderedClasses(["truck"])[0]!)).toBe("var(--series-2)");
  });

  it("orders known classes by slot and unknown ones by name after them", () => {
    expect(orderedClasses(["person", "zebra", "car", "bus", "ant"])).toEqual(["car", "bus", "person", "ant", "zebra"]);
    expect(orderedClasses([])).toEqual([]);
  });

  it("names one vehicle without chopping letters off the plural", () => {
    expect(classSingular("bus")).toBe("Bus");
    expect(classSingular("car")).toBe("Car");
    expect(classSingular("person")).toBe("Person");
    expect(classSingular("scooter")).toBe("Scooter");
    expect(classSingular("")).toBe("Vehicle");
  });

  it("names classes for people", () => {
    expect(classLabel("car")).toBe("Cars");
    expect(classLabel("scooter")).toBe("Scooter");
    expect(classLabel("")).toBe("Other");
  });
});
