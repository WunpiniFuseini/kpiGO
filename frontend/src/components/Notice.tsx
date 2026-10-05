import type { ReactNode } from "react";

import { Icon } from "./icons";

/** A page-level message: licence, maintenance, approval routing. */
export function Notice({
  tone = "info",
  title,
  children,
  role,
}: {
  tone?: "info" | "warn" | "neg";
  title?: ReactNode;
  children?: ReactNode;
  role?: "status" | "alert";
}) {
  return (
    <div className={`kg-notice${tone === "info" ? "" : ` kg-notice--${tone}`}`} role={role}>
      <Icon name={tone === "info" ? "info" : "alert"} />
      <div className="kg-notice__body">
        {title ? <b>{title} </b> : null}
        {children}
      </div>
    </div>
  );
}
