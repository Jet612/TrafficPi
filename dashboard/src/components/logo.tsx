/** A road seen from above with a lane line, inside a rounded square: the TrafficPi mark. */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 32 32" className={className} role="img" aria-label="TrafficPi" fill="none">
      <rect x="1.5" y="1.5" width="29" height="29" rx="8" stroke="currentColor" strokeWidth="2.5" />
      <path d="M11 27 14.5 5M21 27 17.5 5" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
      <path d="M16 24v-3.5M16 16v-3.5M16 8.5V7" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
