import { cn } from "@/lib/cn";

export function Card({ className, ...props }: React.ComponentProps<"section">) {
  return <section className={cn("h-full min-w-0 rounded-xl border border-edge bg-surface", className)} {...props} />;
}

export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex flex-wrap items-center justify-between gap-x-4 gap-y-2 px-5 pt-4", className)} {...props} />;
}

/** A card's heading: an h2, or an h3 where the card sits under a heading of its own. */
export function CardTitle({ className, as: Heading = "h2", ...props }: React.ComponentProps<"h2"> & { as?: "h2" | "h3" }) {
  return <Heading className={cn("text-sm font-semibold text-ink", className)} {...props} />;
}

export function CardBody({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 pb-5 pt-3", className)} {...props} />;
}
