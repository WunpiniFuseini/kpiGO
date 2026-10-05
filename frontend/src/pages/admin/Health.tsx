import type { Output } from "../../api/actions";
import { useQuery } from "../../api/useAction";
import { CardSkeleton, Chip, DataTable, ErrorPanel, Loading, Notice, TableSkeleton } from "../../components";
import { formatBytes, formatDateTime } from "../../lib/format";
import { Page } from "../../shell/AppShell";

type Health = Output<"system.health">;

const STATUS_WORDS = { ok: "OK", degraded: "Degraded", down: "Down" } as const;
const STATUS_TONE = { ok: "up", degraded: "warn", down: "down" } as const;

function StatusChip({ status }: { status: keyof typeof STATUS_WORDS }) {
  return <Chip tone={STATUS_TONE[status]}>{STATUS_WORDS[status]}</Chip>;
}

/** Administer → Health (App Flow §1): services, database, feeds, queue, licence, version. */
export function HealthPage() {
  const [state, reload] = useQuery("system.health", { check_workers: true });
  const refresh = (
    <button type="button" className="kg-btn" onClick={reload} disabled={state.status === "loading"}>
      Check again
    </button>
  );
  return (
    <Page title="Health" actions={refresh}>
      {state.status === "loading" ? (
        <Loading label="Checking services">
          <div className="kg-grid">
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </div>
          <div className="kg-card" style={{ marginTop: 14 }}>
            <TableSkeleton rows={4} columns={3} />
          </div>
        </Loading>
      ) : state.status === "error" ? (
        <ErrorPanel error={state.error} retry={reload} what="Health" />
      ) : (
        <HealthView health={state.data} />
      )}
    </Page>
  );
}

export function HealthView({ health }: { health: Health }) {
  const feeds = health.feeds;
  return (
    <>
      <div className="kg-row" style={{ justifyContent: "flex-start" }}>
        <StatusChip status={health.status} />
        <span className="kg-cap">Checked {formatDateTime(health.checked_at)}</span>
      </div>
      {health.maintenance_mode ? (
        <Notice tone="warn" title="Maintenance mode is on.">
          Changes are paused for everyone; figures stay readable.
        </Notice>
      ) : null}
      {health.licence && health.licence.state !== "active" && health.licence.state !== "development" ? (
        <Notice tone={health.licence.mode === "full" ? "warn" : "neg"} title="Licence.">
          {health.licence.message}
          {health.licence.restart_required ? " Restart kpiGo to apply the new licence." : ""}
        </Notice>
      ) : null}
      <section className="kg-card">
        <DataTable
          caption="Services"
          columns={[
            { key: "name", header: "Service", render: (s) => s.name },
            { key: "status", header: "Status", render: (s) => <StatusChip status={s.status} /> },
            { key: "detail", header: "Detail", render: (s) => s.detail },
          ]}
          rows={health.services}
          rowKey={(s) => s.name}
          empty={<p className="kg-cap">No services reported.</p>}
        />
      </section>
      <div className="kg-grid">
        <article className="kg-card" aria-label="Feeds">
          <h2 className="kg-eyebrow">Feeds</h2>
          {feeds && feeds.total > 0 ? (
            <>
              <p className="kg-fig kg-fig--sm num">
                {feeds.fresh} of {feeds.total}
              </p>
              <p className="kg-cap">
                fresh · {feeds.stale} stale · {feeds.never_loaded} never loaded
              </p>
              {feeds.quarantined.length ? (
                <p className="kg-cap" style={{ color: "var(--warn-ink)", marginTop: 8 }}>
                  Last load rejected: {feeds.quarantined.join(", ")}
                </p>
              ) : null}
              {feeds.failed.length ? (
                <p className="kg-cap" style={{ color: "var(--neg-ink)", marginTop: 4 }}>
                  Failed: {feeds.failed.join(", ")}
                </p>
              ) : null}
            </>
          ) : (
            <p className="kg-cap" style={{ marginTop: 10 }}>
              No feeds are registered yet. A Data Steward adds them under Administer → Data integration.
            </p>
          )}
        </article>
        <article className="kg-card" aria-label="Queue">
          <h2 className="kg-eyebrow">Job queue</h2>
          <p className="kg-fig kg-fig--sm num">{health.queue_depth ?? "–"}</p>
          <p className="kg-cap">{health.queue_depth === null ? "The queue could not be read." : "jobs waiting"}</p>
        </article>
        <article className="kg-card" aria-label="Database">
          <h2 className="kg-eyebrow">Database</h2>
          <p className="kg-fig kg-fig--sm num">{health.database ? formatBytes(health.database.size_bytes) : "–"}</p>
          <p className="kg-cap">
            {health.database
              ? health.database.pending_migrations
                ? `${health.database.pending_migrations} migrations pending`
                : "All migrations applied"
              : "The database could not be read."}
          </p>
        </article>
        <article className="kg-card" aria-label="Version">
          <h2 className="kg-eyebrow">Version</h2>
          <p className="kg-fig kg-fig--sm num">{health.version.product_version}</p>
          <p className="kg-cap">Licence: {health.licence?.state ?? "unknown"}</p>
        </article>
      </div>
      {health.database && health.database.largest_tables.length ? (
        <section className="kg-card">
          <DataTable
            caption="Largest tables"
            columns={[
              { key: "t", header: "Table", render: (t) => t.table },
              { key: "s", header: "Size", numeric: true, render: (t) => formatBytes(t.bytes) },
              { key: "r", header: "Rows (estimate)", numeric: true, render: (t) => t.rows_estimate.toLocaleString("en-GB") },
            ]}
            rows={health.database.largest_tables}
            rowKey={(t) => t.table}
            empty={null}
          />
        </section>
      ) : null}
    </>
  );
}
