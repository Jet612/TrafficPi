import type { Metadata } from "next";
import { StopsCard } from "@/components/stops-card";

export const metadata: Metadata = { title: "Review" };

export default function ReviewPage() {
  return (
    <div className="grid grid-cols-1 gap-4">
      <h1 className="sr-only">Review</h1>
      <StopsCard />
    </div>
  );
}
