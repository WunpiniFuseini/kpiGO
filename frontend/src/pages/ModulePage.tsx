import { EmptyState } from "../components";
import { Page } from "../shell/AppShell";
import { useMe } from "../session/Session";

/**
 * A module the user may open whose screens this release does not include yet.
 * If the page is allowed but its data scope is absent, NO ACCESS comes first and
 * names the missing grant and who to ask (App Flow §9).
 */
export function ModulePage({ pageKey, title, exec = false }: { pageKey: string; title: string; exec?: boolean }) {
  const me = useMe();
  const blocked = me.no_access.find((n) => n.page_key === pageKey);
  return (
    <Page title={title} exec={exec}>
      {blocked ? (
        <EmptyState kind="no-access" title={`You cannot see ${title} data yet`} ask={blocked.ask}>
          {blocked.missing}
        </EmptyState>
      ) : (
        <EmptyState kind="not-built" title={`${title} is not in this version yet`}>
          This version of kpiGo sets up the foundations: users, metrics, data feeds and health. {title} screens arrive
          with the module&apos;s release. Your access is already in place.
        </EmptyState>
      )}
    </Page>
  );
}

export function AdminPlaceholder({ title, what }: { title: string; what: string }) {
  return (
    <Page title={title}>
      <EmptyState kind="not-built" title={`The ${title} screen is not in this version yet`}>
        {what} The actions behind it already exist and can be run from the command line by an Admin.
      </EmptyState>
    </Page>
  );
}
