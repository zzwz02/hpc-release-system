import { useMemo, useState } from "react";
import { downloadCsv } from "../lib/csv";
import { shortCommit } from "../lib/git";

export type CommandRow = Record<string, string | number | boolean | null>;

/** Share the QA command table's typography, controls, and column filtering. */
export function FlatCommandTable({ columns, rows, filename, label, renderCell, onRefresh }: {
  columns: string[];
  rows: CommandRow[];
  filename: string;
  label: string;
  renderCell?: (column: string, row: CommandRow) => React.ReactNode;
  onRefresh?: () => void;
}) {
  const [filter, setFilter] = useState("");
  const [columnFilters, setColumnFilters] = useState<Record<string, string>>({});
  const [sort, setSort] = useState<{ column: string; direction: 1 | -1 } | null>(null);
  const visible = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    const filters = columns.map(column => [column, (columnFilters[column] ?? "").trim().toLowerCase()])
      .filter(([, value]) => value);
    const result = rows.filter(row =>
      (!needle || columns.some(column => String(row[column] ?? "").toLowerCase().includes(needle))) &&
      filters.every(([column, value]) => String(row[column] ?? "").toLowerCase().includes(value)));
    if (sort && columns.includes(sort.column)) {
      result.sort((a, b) => String(a[sort.column] ?? "").localeCompare(String(b[sort.column] ?? ""), "zh-CN", { numeric: true }) * sort.direction);
    }
    return result;
  }, [columns, rows, filter, columnFilters, sort]);

  function sortBy(column: string) {
    setSort(previous => ({ column, direction: previous?.column === column && previous.direction === 1 ? -1 : 1 }));
  }

  return <>
    <div className="panel-head"><h2>{label}</h2></div>
    <div className="panel-body">
      <div className="qa-report-bar">
        {onRefresh && <button className="btn ghost sm" onClick={onRefresh}>↻ 刷新</button>}
        <button className="btn primary sm" disabled={!visible.length} onClick={() => downloadCsv(filename, columns, visible.map(row => columns.map(column => String(row[column] ?? ""))))}>导出 CSV</button>
        <input className="input qa-report-search" aria-label={`筛选${label}`} placeholder="全表关键词筛选…" value={filter} onChange={e => setFilter(e.target.value)} />
        <span className="muted small count">{visible.length === rows.length ? `${rows.length} 行` : `${visible.length} / ${rows.length} 行`}</span>
      </div>
      <div className="table report-table sticky-first-col">
        <table aria-label={label}>
          <thead>
            <tr>{columns.map(column => {
              const sorted = sort?.column === column;
              return <th key={column} className={`sortable${sorted ? " sorted" : ""}`} tabIndex={0}
                aria-sort={sorted ? sort.direction === 1 ? "ascending" : "descending" : "none"}
                onClick={() => sortBy(column)} onKeyDown={e => {
                  if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(column); }
                }}>
                <span className="th-text">{column}<span className="sort-ind" aria-hidden="true">{sorted && sort.direction < 0 ? "▼" : "▲"}</span></span>
              </th>;
            })}</tr>
            <tr className="colfilter-row">{columns.map(column => <th key={column}>
              <input aria-label={`${column} 列筛选`} placeholder="筛选…" autoComplete="off" value={columnFilters[column] ?? ""}
                onChange={e => setColumnFilters(previous => ({ ...previous, [column]: e.target.value }))} />
            </th>)}</tr>
          </thead>
          <tbody>{visible.map((row, index) => <tr key={index}>{columns.map(column => {
            const value = String(row[column] ?? "");
            return <td key={column} className={/command|docker_cmd|命令/.test(column) ? "cmd" : undefined}
              title={column === "commit_id" ? value : undefined}>
              {column === "commit_id" ? shortCommit(value) : renderCell ? renderCell(column, row) : value}
            </td>;
          })}</tr>)}
          {!visible.length && <tr><td colSpan={columns.length} className="muted">无匹配数据</td></tr>}</tbody>
        </table>
      </div>
    </div>
  </>;
}
