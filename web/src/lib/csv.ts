/**
 * CSV utilities: report serialization and BOM-prefixed download.
 *
 * Mirrors index.html:3447 (parseCsvRows) and index.html:2729 (reportToCsv /
 * reportDownloadCsv).
 *
 * All exported CSV files are UTF-8 with a BOM (U+FEFF) prepended so that
 * Excel on Windows auto-detects the encoding.
 */

/** UTF-8 BOM character */
const BOM = "﻿";

/**
 * Escape a single cell value for RFC-4180 CSV.
 *
 * Wraps in double-quotes if the value contains a comma, double-quote,
 * newline, or carriage-return.  Internal double-quotes are doubled.
 */
function escapeCell(value: string): string {
  const s = String(value ?? "");
  if (s.includes(",") || s.includes('"') || s.includes("\n") || s.includes("\r")) {
    return '"' + s.replace(/"/g, '""') + '"';
  }
  return s;
}

/**
 * Serialize a table (columns + rows) to a CSV string.
 *
 * Does NOT include a BOM — callers that download files should prepend BOM
 * themselves (or use `reportDownloadBlob`).
 *
 * Mirrors index.html:2729-style generation used for QA report CSV export.
 */
export function reportToCsv(columns: string[], rows: string[][]): string {
  const lines: string[] = [];
  lines.push(columns.map(escapeCell).join(","));
  for (const row of rows) {
    lines.push(row.map(escapeCell).join(","));
  }
  return lines.join("\r\n");
}

/**
 * Create a Blob for a BOM-prefixed UTF-8 CSV file.
 *
 * Mirrors index.html:2730: `new Blob(["﻿" + reportToCsv(...)])`.
 */
export function reportToCsvBlob(columns: string[], rows: string[][]): Blob {
  return new Blob([BOM + reportToCsv(columns, rows)], {
    type: "text/csv;charset=utf-8",
  });
}

/**
 * Trigger a browser download of a BOM-prefixed CSV file.
 *
 * Mirrors index.html:2729-2735 (reportDownloadCsv).
 */
export function downloadCsv(
  filename: string,
  columns: string[],
  rows: string[][],
): void {
  const blob = reportToCsvBlob(columns, rows);
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * Parse a CSV string into a 2D array of strings.
 *
 * Mirrors index.html:3444-3479 (parseCsvRows).
 *
 * Features:
 *   - Strips a leading BOM (U+FEFF) if present
 *   - Handles RFC-4180 quoted fields (double-quote escaping)
 *   - Handles CRLF and bare LF line endings
 */
export function parseCsvRows(text: string): string[][] {
  const input = String(text ?? "").replace(/^﻿/, "");
  const rows: string[][] = [];
  let row: string[] = [];
  let cell = "";
  let inQuotes = false;

  const pushCell = () => {
    row.push(cell);
    cell = "";
  };
  const pushRow = () => {
    pushCell();
    rows.push(row);
    row = [];
  };

  for (let i = 0; i < input.length; i++) {
    const ch = input[i];
    if (inQuotes) {
      if (ch === '"') {
        if (input[i + 1] === '"') {
          cell += '"';
          i += 1;
        } else {
          inQuotes = false;
        }
      } else {
        cell += ch;
      }
      continue;
    }
    if (ch === '"' && cell === "") {
      inQuotes = true;
      continue;
    }
    if (ch === ",") {
      pushCell();
      continue;
    }
    if (ch === "\r") {
      if (input[i + 1] === "\n") i += 1;
      pushRow();
      continue;
    }
    if (ch === "\n") {
      pushRow();
      continue;
    }
    cell += ch;
  }
  if (cell || row.length) pushRow();
  return rows;
}
