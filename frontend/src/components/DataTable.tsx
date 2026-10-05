import type { ReactNode } from "react";

export interface Column<T> {
  key: string;
  header: string;
  numeric?: boolean;
  render: (row: T) => ReactNode;
}

/**
 * The core artefact is a data table (Design Brief §8): a caption, header cells
 * with scope, and numbers right-aligned in tabular figures. An empty table
 * renders the caller's empty state in its place, so it always says why.
 */
export function DataTable<T>({
  caption,
  captionHidden = false,
  columns,
  rows,
  rowKey,
  empty,
  footer,
}: {
  caption: string;
  captionHidden?: boolean;
  columns: Column<T>[];
  rows: T[];
  rowKey: (row: T) => string;
  empty: ReactNode;
  footer?: ReactNode;
}) {
  if (rows.length === 0) return <>{empty}</>;
  return (
    <div className="kg-table-wrap">
      <table className="kg-table">
        <caption className={captionHidden ? "sr-only" : "kg-section"}>{caption}</caption>
        <thead>
          <tr>
            {columns.map((c) => (
              <th key={c.key} scope="col" className={c.numeric ? "is-num" : undefined}>
                {c.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={rowKey(row)}>
              {columns.map((c, i) =>
                i === 0 ? (
                  <th key={c.key} scope="row">
                    {c.render(row)}
                  </th>
                ) : (
                  <td key={c.key} className={c.numeric ? "is-num" : undefined}>
                    {c.render(row)}
                  </td>
                ),
              )}
            </tr>
          ))}
        </tbody>
        {footer ? <tfoot>{footer}</tfoot> : null}
      </table>
    </div>
  );
}
