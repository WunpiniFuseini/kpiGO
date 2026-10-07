/**
 * The Executive dashboard (PRD EX-1, Scope §10): the one surface whose
 * composition the client controls. Admins place widgets; everyone granted
 * `executive.view` reads them here, on the periwinkle canvas, for a chosen
 * period. Each widget draws its own figures and drills on its own.
 *
 * The page says why it is empty: no data scope names the grant to ask for, and
 * a dashboard with nothing placed points an Admin at the Widgets admin.
 */
import { useState } from "react";

import type { Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { EmptyState } from "../../components/EmptyState";
import { ErrorPanel } from "../../components/ErrorPanel";
import { SelectField } from "../../components/Field";
import { CardSkeleton } from "../../components/Skeleton";
import { currentPeriod, formatPeriod, shiftPeriod } from "../../lib/format";
import { Page } from "../../shell/AppShell";
import { useMe } from "../../session/Session";
import { ViewBar, type DashboardState } from "./ViewBar";
import { Widget } from "./Widget";

const PAGE_KEY = "executive";

function recentPeriods(from: string, count = 12): { value: string; label: string }[] {
  return Array.from({ length: count }, (_, i) => {
    const key = shiftPeriod(from, -i);
    return { value: key, label: formatPeriod(key) };
  });
}

export function DashboardView({
  dashboard,
  periodKey,
  drill,
  onDrill,
}: {
  dashboard: Output<"widget.dashboard">;
  periodKey: string;
  drill: Record<string, string>;
  onDrill: (widgetKey: string, code: string | null) => void;
}) {
  if (dashboard.widgets.length === 0) {
    return (
      <EmptyState kind="none" title="No widgets on the dashboard yet">
        An Admin composes the Executive dashboard under Administer → Widgets: place a KPI card, a
        chart or a table, each reading a metric bound to Executive.
      </EmptyState>
    );
  }
  return (
    <div className="kg-exec-grid">
      {dashboard.widgets.map((w) => (
        <Widget
          key={w.widget_key}
          placed={w}
          periodKey={periodKey}
          drillTo={drill[w.widget_key] ?? null}
          onDrill={(code) => onDrill(w.widget_key, code)}
        />
      ))}
    </div>
  );
}

function PeriodPicker({
  periodKey,
  periods,
  onPeriod,
}: {
  periodKey: string;
  periods: { value: string; label: string }[];
  onPeriod: (period: string) => void;
}) {
  return (
    <SelectField
      label="Period"
      value={periodKey}
      options={periods}
      onChange={(e) => onPeriod(e.target.value)}
    />
  );
}

export function ExecutiveDashboardPage() {
  const me = useMe();
  const [periodKey, setPeriodKey] = useState(currentPeriod());
  const [drill, setDrill] = useState<Record<string, string>>({});
  const periods = recentPeriods(currentPeriod());
  const [dashboard, reload] = useQuery("widget.dashboard", {});

  const blocked = me.no_access.find((n) => n.page_key === PAGE_KEY);
  if (blocked) {
    return (
      <Page title="Executive" exec>
        <EmptyState kind="no-access" title="You cannot see Executive data yet" ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      </Page>
    );
  }

  function onDrill(widgetKey: string, code: string | null) {
    setDrill((prev) => {
      const next = { ...prev };
      if (code === null) delete next[widgetKey];
      else next[widgetKey] = code;
      return next;
    });
  }

  function applyView(state: DashboardState) {
    setPeriodKey(state.period_key);
    setDrill(state.drill);
  }

  return (
    <Page
      title="Executive"
      exec
      actions={
        <>
          <ViewBar state={{ period_key: periodKey, drill }} onApply={applyView} />
          <PeriodPicker periodKey={periodKey} periods={periods} onPeriod={setPeriodKey} />
        </>
      }
    >
      {dashboard.status === "loading" ? (
        <div className="kg-exec-grid">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : dashboard.status === "error" ? (
        <ErrorPanel error={dashboard.error} retry={reload} what="The dashboard" />
      ) : (
        <DashboardView
          dashboard={dashboard.data}
          periodKey={periodKey}
          drill={drill}
          onDrill={onDrill}
        />
      )}
    </Page>
  );
}
