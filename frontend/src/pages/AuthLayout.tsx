import { useEffect, type ReactNode } from "react";

/** The frame for signing in and first run: one card on the rail tint. */
export function AuthLayout({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    document.title = `${title} · kpiGo`;
  }, [title]);
  return (
    <main className="kg-auth" id="main">
      <div className="kg-card kg-auth__card">
        <div className="kg-auth__brand">
          <span className="kg-mark" aria-hidden="true">
            k
          </span>
          <b style={{ fontSize: 15, fontWeight: 600 }}>kpiGo</b>
        </div>
        <h1 className="kg-title" style={{ marginBottom: 16 }}>
          {title}
        </h1>
        {children}
      </div>
    </main>
  );
}
