"use client";

import { App } from "@/components/app";

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return <App>{children}</App>;
}
