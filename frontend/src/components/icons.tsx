/** Line icons for the rail and states. Decorative: always paired with a text label. */
import type { ReactElement } from "react";

const paths: Record<string, ReactElement> = {
  scorecards: <path d="M3 3h10v10H3zM3 7h10M7 7v6" />,
  agent_performance: <path d="M2 13h12M4 13V8M8 13V4M12 13V6" />,
  campaign: <path d="M2 6v4l3 .5L12 13V3L5 5.5zM5 5.5v5" />,
  executive: <path d="M2 2h5v5H2zM9 2h5v3H9zM9 7h5v7H9zM2 9h5v5H2z" />,
  my_inputs: <path d="M3 2h7l3 3v9H3zM6 8h4M6 11h4M10 2v3h3" />,
  "admin.users": <path d="M6 7a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5zM1.5 14c0-2.5 2-4.5 4.5-4.5s4.5 2 4.5 4.5M11 2.5a2.5 2.5 0 0 1 0 4.5M12.5 9.8c1.2.6 2 1.9 2 3.7" />,
  "admin.metrics": <path d="M2 3h12M2 8h12M2 13h12M5 1.5v3M10 6.5v3M7 11.5v3" />,
  "admin.targets": <path d="M8 14A6 6 0 1 0 8 2a6 6 0 0 0 0 12zM8 11a3 3 0 1 0 0-6 3 3 0 0 0 0 6zM8 8h.01" />,
  "admin.scorecard_setup": <path d="M2 3h4v4H2zM10 9h4v4h-4zM6 5h3v6h1M4 7v6h6" />,
  "admin.calendar": <path d="M2 4h12v10H2zM2 7h12M5 2v3M11 2v3" />,
  "admin.product_lines": <path d="M2 2h5v5H2zM9 9h5v5H9zM9 4.5h5M2 11.5h5" />,
  "admin.data_integration": <path d="M8 1.5c3.3 0 6 1 6 2.5S11.3 6.5 8 6.5 2 5.5 2 4s2.7-2.5 6-2.5zM2 4v8c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5V4M2 8c0 1.4 2.7 2.5 6 2.5s6-1.1 6-2.5" />,
  "admin.widgets": <path d="M2 2h12v12H2zM2 6h12M6 6v8" />,
  "admin.health": <path d="M1.5 8h3l1.5-4 3 8 1.5-4h4" />,
  "admin.audit": <path d="M3 2h10v12H3zM5.5 5h5M5.5 8h5M5.5 11h3" />,
  signout: <path d="M6 14H3V2h3M10.5 11 14 8l-3.5-3M14 8H6" />,
  info: <path d="M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM8 7.5V11M8 5h.01" />,
  check: <path d="M3 8.5 6.5 12 13 4.5" />,
  lock: <path d="M3.5 7h9v7h-9zM5.5 7V5a2.5 2.5 0 0 1 5 0v2" />,
  alert: <path d="M8 2 15 14H1zM8 6.5v3.5M8 12h.01" />,
  clock: <path d="M8 14.5a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13zM8 4.5V8l2.5 1.5" />,
  search: <path d="M7 12A5 5 0 1 0 7 2a5 5 0 0 0 0 10zM14 14l-3.5-3.5" />,
};

export function Icon({ name, size = 16 }: { name: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.5}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {paths[name] ?? paths.info}
    </svg>
  );
}
