import { useEffect, useRef, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";

import { Icon } from "../components/icons";
import { Notice } from "../components/Notice";
import { initials } from "../lib/format";
import type { Me } from "../session/Session";
import { pathFor } from "./pages";

const QUIET_LICENCE = new Set(["active", "development"]);

/** Persistent left rail, topbar and view container (Design Brief §3). */
export function AppShell({
  me,
  onSignOut,
  children,
}: {
  me: Me;
  onSignOut: () => void;
  children: ReactNode;
}) {
  const modules = me.pages.filter((p) => p.group === "modules");
  const admin = me.pages.filter((p) => p.group === "administer");
  return (
    <div className="kg-shell">
      <a className="kg-skip" href="#main">
        Skip to content
      </a>
      <nav className="kg-rail" aria-label="Main">
        <NavLink to="/" className="kg-brand" aria-label="kpiGo home">
          <span className="kg-mark" aria-hidden="true">
            k
          </span>
          <b>kpiGo</b>
        </NavLink>
        <div className="kg-nav">
          {modules.length ? <NavGroup label="Modules" pages={modules} /> : null}
          {admin.length ? <NavGroup label="Administer" pages={admin} /> : null}
        </div>
        <div className="kg-rail-foot">
          <div className="kg-who">
            <span className="kg-av" aria-hidden="true">
              {initials(me.user.display_name)}
            </span>
            <span className="kg-who__text">
              <span>{me.user.display_name}</span>
              <small>{me.user.roles.join(", ") || "No role"}</small>
            </span>
          </div>
          <button type="button" className="kg-navitem kg-navitem--button" onClick={onSignOut}>
            <Icon name="signout" />
            <span className="kg-navitem__label">Sign out</span>
          </button>
        </div>
      </nav>
      <div className="kg-main">
        {QUIET_LICENCE.has(me.licence.state) ? null : (
          <div style={{ padding: "12px 22px 0" }}>
            <Notice tone={me.licence.mode === "full" ? "warn" : "neg"} title="Licence." role="status">
              {me.licence.message}
              {me.licence.days_left !== null ? ` ${me.licence.days_left} days left.` : ""}
            </Notice>
          </div>
        )}
        {children}
      </div>
    </div>
  );
}

function NavGroup({ label, pages }: { label: string; pages: Me["pages"] }) {
  return (
    <>
      <div className="kg-navlabel" aria-hidden="true">
        {label}
      </div>
      <ul aria-label={label}>
        {pages.map((p) => (
          <li key={p.page_key}>
            <NavLink to={pathFor(p.page_key)} className="kg-navitem" title={p.label}>
              <Icon name={p.page_key} />
              <span className="kg-navitem__label">{p.label}</span>
            </NavLink>
          </li>
        ))}
      </ul>
    </>
  );
}

/** A page: its topbar title and actions, then the view. Focus moves to the title on navigation. */
export function Page({
  title,
  actions,
  exec = false,
  children,
}: {
  title: string;
  actions?: ReactNode;
  exec?: boolean;
  children: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const location = useLocation();
  useEffect(() => {
    document.title = `${title} · kpiGo`;
    heading.current?.focus({ preventScroll: true });
  }, [title, location.pathname]);
  return (
    <>
      <header className="kg-topbar">
        <h1 ref={heading} tabIndex={-1} style={{ outline: "none" }}>
          {title}
        </h1>
        <span className="kg-spacer" />
        {actions}
      </header>
      <main id="main" className={`kg-view${exec ? " kg-view--exec" : ""}`}>
        {children}
      </main>
    </>
  );
}
