/**
 * Vehicle classes and their colours. A class always wears the same categorical slot, whichever
 * classes happen to be present or visible, so a filter or a quiet hour never repaints the rest.
 * The slots and the order are the validated palette's (see globals.css). Classes the dashboard
 * does not know share the grey "other" colour.
 */
export const CLASS_ORDER = ["car", "truck", "bus", "motorcycle", "bicycle", "person"] as const;

const NAMES: Record<string, string> = {
  car: "Cars",
  truck: "Trucks",
  bus: "Buses",
  motorcycle: "Motorcycles",
  bicycle: "Bicycles",
  person: "People",
};

const SINGULAR: Record<string, string> = {
  car: "Car",
  truck: "Truck",
  bus: "Bus",
  motorcycle: "Motorcycle",
  bicycle: "Bicycle",
  person: "Person",
};

export function classSlot(name: string): number | null {
  const index = (CLASS_ORDER as readonly string[]).indexOf(name);
  return index === -1 ? null : index + 1;
}

/** The CSS colour of a class, for HTML and SVG. */
export function classColor(name: string): string {
  const slot = classSlot(name);
  return slot === null ? "var(--series-other)" : `var(--series-${slot})`;
}

/** "car" -> "Cars"; an unknown class gets its own name, capitalised. */
export function classLabel(name: string): string {
  return NAMES[name] ?? (name ? name.charAt(0).toUpperCase() + name.slice(1) : "Other");
}

/** "car" -> "Car": for one vehicle, as in a list of single counts. */
export function classSingular(name: string): string {
  return SINGULAR[name] ?? (name ? name.charAt(0).toUpperCase() + name.slice(1) : "Vehicle");
}

/** Known classes in their fixed order, then any others by name. */
export function orderedClasses(present: Iterable<string>): string[] {
  const names = new Set(present);
  const known = CLASS_ORDER.filter((name) => names.has(name));
  const others = [...names].filter((name) => classSlot(name) === null).sort();
  return [...known, ...others];
}
