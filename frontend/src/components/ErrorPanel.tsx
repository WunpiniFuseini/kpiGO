import type { ApiError } from "../api/client";
import { EmptyState } from "./EmptyState";

/**
 * What a failed read shows (App Flow §9, Error): plain words, what to do next
 * and a reference to quote; never a stack trace.
 */
export function ErrorPanel({ error, retry, what }: { error: ApiError; retry?: () => void; what: string }) {
  const action = retry ? (
    <button type="button" className="kg-btn" onClick={retry}>
      Try again
    </button>
  ) : null;
  if (error.status === 403 && error.code === "licence_restricted") {
    return (
      <EmptyState kind="no-access" title={`${what} is not available under this licence`} ask="your kpiGo Admin, under Administer → Health → Licence">
        {error.message}
      </EmptyState>
    );
  }
  if (error.status === 403) {
    return (
      <EmptyState kind="no-access" title={`You do not have access to ${what.toLowerCase()}`} ask="an Admin, under Administer → Users & access">
        {error.message}
      </EmptyState>
    );
  }
  if (error.status === 503) {
    return (
      <EmptyState kind="awaiting" title="kpiGo is updating" action={action}>
        {error.message} You can still read figures; changes are paused until the update finishes.
      </EmptyState>
    );
  }
  if (error.status === 0 && error.code === "unreachable") {
    return (
      <EmptyState kind="error" title="kpiGo could not be reached" reference={error.reference} action={action}>
        Check your network connection. If others see the same, your IT team can check the kpiGo service.
      </EmptyState>
    );
  }
  return (
    <EmptyState kind="error" title={`${what} could not be loaded`} reference={error.reference} action={action}>
      {error.status >= 500 ? "Something went wrong on the server." : error.message}
    </EmptyState>
  );
}
