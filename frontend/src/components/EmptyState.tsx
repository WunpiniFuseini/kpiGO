import type { ReactNode } from "react";

import { Icon } from "./icons";

/**
 * Every empty screen says why it is empty (Design Brief §2.4, App Flow §9).
 *
 * - awaiting: valid scope, nothing loaded yet; name the feed and when it is due
 * - none: valid scope, genuinely zero results; offer to reset the filter
 * - no-access: the page is allowed but the data scope is absent; name the grant and who to ask
 * - good: nothing to do, and that is good news (a contributor with nothing due)
 * - error: an unexpected failure, with a reference the user can quote
 * - not-built: this surface arrives in a later release
 */
export type EmptyKind = "awaiting" | "none" | "no-access" | "good" | "error" | "not-built";

const ICONS: Record<EmptyKind, string> = {
  awaiting: "clock",
  none: "search",
  "no-access": "lock",
  good: "check",
  error: "alert",
  "not-built": "info",
};

const VARIANT: Partial<Record<EmptyKind, string>> = {
  good: "kg-empty--good",
  "no-access": "kg-empty--blocked",
  error: "kg-empty--error",
};

export function EmptyState({
  kind,
  title,
  children,
  ask,
  reference,
  action,
  headingLevel = 2,
}: {
  kind: EmptyKind;
  title: string;
  children?: ReactNode;
  /** Who can fix it, e.g. "an Admin, under Administer → Users & access". */
  ask?: string;
  /** Error reference the user quotes to support. */
  reference?: string;
  action?: ReactNode;
  headingLevel?: 1 | 2 | 3;
}) {
  const Heading = `h${headingLevel}` as "h2";
  return (
    <div className={`kg-empty ${VARIANT[kind] ?? ""}`} role={kind === "error" ? "alert" : undefined}>
      <span className="kg-empty__icon">
        <Icon name={ICONS[kind]} />
      </span>
      <Heading>{title}</Heading>
      {children ? <p>{children}</p> : null}
      {ask ? <p className="kg-empty__ask">To change this, ask {ask}.</p> : null}
      {reference ? (
        <p className="kg-empty__ask">
          Reference <code>{reference}</code>. Quote it to your support team.
        </p>
      ) : null}
      {action}
    </div>
  );
}
