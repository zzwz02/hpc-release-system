import { useMemo, useState } from "react";
import { downloadCsv } from "../lib/csv";
import { shortCommit } from "../lib/git";

export type CommandRow = Record<string, string | number | boolean | null>;
export interface CommandTableFilters {
  filter: string;
  columnFilters: Record<string, string>;
  sort: { column: string; direction: 1 | -1 } | null;
}

interface RemoteTable {
  filters: CommandTableFilters;
  onChange: (filters: CommandTableFilters) => void;
  total: number;
  busy: boolean;
  exporting: boolean;
  onExport: () => void;
}

/** Share the QA command table's typography, controls, and column filtering. */
export function FlatCommandTable({ columns, rows, filename, label, renderCell, onRefresh, remote }: {
  columns: string[];
  rows: CommandRow[];
  filename: string;
  label: string;
  renderCell?: (column: string, row: CommandRow) => React.ReactNode;
  onRefresh?: () => void;
  remote?: RemoteTable;
}) {
  const [localFilters, setLocalFilters] = useState<CommandTableFilters>({ filter: "", columnFilters: {}, sort: null });
  const { filter, columnFilters, sort } = remote?.filters ?? localFilters;
  const setFilters = remote?.onChange ?? setLocalFilters;
  const visible = useMemo(() => {
    if (remote) return remote.busy ? [] : rows;
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
  }, [columns, rows, filter, columnFilters, sort, remote]);

  function sortBy(column: string) {
    setFilters({ filter, columnFilters, sort: { column, direction: sort?.column === column && sort.direction === 1 ? -1 : 1 } });
  }

  return <>
    <div className="panel-head"><h2>{label}</h2></div>
    <div className="panel-body">
      <div className="qa-report-bar">
        {onRefresh && <button className="btn ghost sm" disabled={remote?.busy} onClick={onRefresh}>↻ 刷新</button>}
        <button className="btn primary sm" disabled={remote ? remote.busy || remote.exporting || !remote.total : !visible.length} onClick={() => remote ? remote.onExport() : downloadCsv(filename, columns, visible.map(row => columns.map(column => String(row[column] ?? ""))))}>{remote?.exporting ? "正在导出…" : "导出 CSV"}</button>
        <input className="input qa-report-search" aria-label={`筛选${label}`} placeholder="全表关键词筛选…" value={filter} onChange={e => setFilters({ filter: e.target.value, columnFilters, sort })} />
        <span className="muted small count">{remote ? remote.busy ? "正在筛选…" : `${remote.total} 行` : visible.length === rows.length ? `${rows.length} 行` : `${visible.length} / ${rows.length} 行`}</span>
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
                onChange={e => setFilters({ filter, columnFilters: { ...columnFilters, [column]: e.target.value }, sort })} />
            </th>)}</tr>
          </thead>
          <tbody>{visible.map((row, index) => <tr key={index}>{columns.map(column => {
            const value = String(row[column] ?? "");
            return <td key={column} className={/command|docker_cmd|命令/.test(column) ? "cmd" : undefined}
              title={column === "commit_id" ? value : undefined}>
              {column === "commit_id" ? shortCommit(value) : renderCell ? renderCell(column, row) : value}
            </td>;
          })}</tr>)}
          {!visible.length && <tr><td colSpan={columns.length} className="muted">{remote?.busy ? "正在查询全部匹配结果…" : "无匹配数据"}</td></tr>}</tbody>
        </table>
      </div>
    </div>
  </>;
}
