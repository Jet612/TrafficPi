import { Bike, Bus, Car, CircleDot, Motorbike, PersonStanding, Truck, type LucideIcon } from "lucide-react";
import { classColor } from "@/lib/classes";
import { cn } from "@/lib/cn";

const ICONS: Record<string, LucideIcon> = {
  car: Car,
  truck: Truck,
  bus: Bus,
  motorcycle: Motorbike,
  bicycle: Bike,
  person: PersonStanding,
};

/** The icon of a kind of vehicle, in the kind's colour: a shape and a colour together, so that neither has to carry the meaning alone. */
export function ClassIcon({ name, className }: { name: string; className?: string }) {
  const Icon = ICONS[name] ?? CircleDot;
  return <Icon className={cn("size-4 shrink-0", className)} style={{ color: classColor(name) }} aria-hidden="true" />;
}
