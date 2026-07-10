/**
 * DataTable — reusable generic table component.
 *
 * Renders a headed table from column definitions + row data.
 * Used throughout the app wherever a simple tabular view is needed.
 *
 * Mirrors the legacy `table()` helper (index.html:1534-1560) with typed
 * column definitions and slot-based cell rendering.
 */

import React from "react";

export interface DataTableColumn<T> {
  /** Column header label. */
  label: string;
  /** Key accessor or render function for the cell value. */
  render: (row: T, index: number) => React.ReactNode;
  /** Optional CSS class for the <th>/<td>. */
  className?: string;
  /** Optional inline style for the <th>/<td>. */
  style?: React.CSSProperties;
}

interface DataTableProps<T> {
  columns: DataTableColumn<T>[];
  rows: T[];
  /** Shown when rows is empty. */
  emptyText?: string;
  /** Row key extractor; defaults to row index. */
  rowKey?: (row: T, index: number) => React.Key;
  /** Optional click handler per row. */
  onRowClick?: (row: T, index: number) => void;
  /** Additional class for the <table>. */
  className?: string;
}

export function DataTable<T>({
  columns,
  rows,
  emptyText = "暂无数据",
  rowKey,
  onRowClick,
  className,
}: DataTableProps<T>) {
  return (
    <table className={["data-table", className].filter(Boolean).join(" ")}>
      <thead>
        <tr>
          {columns.map((col, i) => (
            <th key={i} className={col.className} style={col.style}>
              {col.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.length === 0 ? (
          <tr className="empty-row">
            <td colSpan={columns.length} className="muted ta-c p-16">
              {emptyText}
            </td>
          </tr>
        ) : (
          rows.map((row, i) => (
            <tr
              key={rowKey ? rowKey(row, i) : i}
              onClick={onRowClick ? () => onRowClick(row, i) : undefined}
              className={onRowClick ? "pointer" : undefined}
            >
              {columns.map((col, j) => (
                <td key={j} className={col.className} style={col.style}>
                  {col.render(row, i)}
                </td>
              ))}
            </tr>
          ))
        )}
      </tbody>
    </table>
  );
}
