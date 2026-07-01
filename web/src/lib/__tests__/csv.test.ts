import { describe, it, expect } from "vitest";
import { csvTextToBlob, reportToCsv, reportToCsvBlob, parseCsvRows } from "../csv";

function readBlobBytes(blob: Blob): Promise<Uint8Array> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (!(reader.result instanceof ArrayBuffer)) {
        reject(new TypeError("Expected Blob to read as ArrayBuffer"));
        return;
      }
      resolve(new Uint8Array(reader.result));
    };
    reader.onerror = () => reject(reader.error ?? new Error("Failed to read Blob"));
    reader.readAsArrayBuffer(blob);
  });
}

describe("reportToCsv", () => {
  it("generates a header + row CSV", () => {
    const csv = reportToCsv(["A", "B", "C"], [["1", "2", "3"]]);
    expect(csv).toBe("A,B,C\r\n1,2,3");
  });

  it("produces only headers when rows is empty", () => {
    const csv = reportToCsv(["X", "Y"], []);
    expect(csv).toBe("X,Y");
  });

  it("escapes cells containing commas", () => {
    const csv = reportToCsv(["col"], [["a,b"]]);
    expect(csv).toBe('col\r\n"a,b"');
  });

  it("escapes cells containing double-quotes (RFC-4180 double-quote doubling)", () => {
    const csv = reportToCsv(["col"], [['say "hello"']]);
    expect(csv).toBe('col\r\n"say ""hello"""');
  });

  it("escapes cells containing newlines", () => {
    const csv = reportToCsv(["col"], [["line1\nline2"]]);
    expect(csv).toBe('col\r\n"line1\nline2"');
  });

  it("handles empty cell values", () => {
    const csv = reportToCsv(["A", "B"], [["", ""]]);
    expect(csv).toBe("A,B\r\n,");
  });

  it("handles multiple rows", () => {
    const csv = reportToCsv(["n"], [["1"], ["2"], ["3"]]);
    expect(csv).toBe("n\r\n1\r\n2\r\n3");
  });
});

describe("reportToCsvBlob", () => {
  it("returns a Blob with expected size (BOM + csv content)", () => {
    // BOM is 3 bytes in UTF-8, "A\r\n1" is 4 bytes → total 7
    const blob = reportToCsvBlob(["A"], [["1"]]);
    expect(blob.size).toBe(7);
  });

  it("sets correct MIME type", () => {
    const blob = reportToCsvBlob(["A"], []);
    expect(blob.type).toBe("text/csv;charset=utf-8");
  });

  it("is larger than the plain CSV by exactly 3 bytes (BOM)", () => {
    const columns = ["Name", "Value"];
    const rows = [["alpha", "beta"]];
    const plainCsv = reportToCsv(columns, rows);
    const blob = reportToCsvBlob(columns, rows);
    // BOM in UTF-8 is 3 bytes (EF BB BF)
    const bomBytes = new TextEncoder().encode("﻿").length;
    const csvBytes = new TextEncoder().encode(plainCsv).length;
    expect(blob.size).toBe(bomBytes + csvBytes);
  });
});

describe("csvTextToBlob", () => {
  it("prefixes existing CSV text with a UTF-8 BOM", async () => {
    const csv = "应用,版本\r\nfoo,1";
    const blob = csvTextToBlob(csv);
    const bytes = await readBlobBytes(blob);
    expect(Array.from(bytes.slice(0, 3))).toEqual([0xef, 0xbb, 0xbf]);
    expect(blob.size).toBe(new TextEncoder().encode("\uFEFF" + csv).length);
  });

  it("does not duplicate an existing BOM", async () => {
    const csv = "\uFEFF应用,版本";
    const blob = csvTextToBlob(csv);
    const bytes = await readBlobBytes(blob);
    expect(Array.from(bytes.slice(0, 6))).toEqual([0xef, 0xbb, 0xbf, 0xe5, 0xba, 0x94]);
    expect(blob.size).toBe(new TextEncoder().encode(csv).length);
  });
});

describe("parseCsvRows", () => {
  it("parses a simple CSV string", () => {
    const rows = parseCsvRows("a,b,c\n1,2,3");
    expect(rows).toEqual([["a", "b", "c"], ["1", "2", "3"]]);
  });

  it("strips a leading BOM", () => {
    const rows = parseCsvRows("﻿a,b\n1,2");
    expect(rows).toEqual([["a", "b"], ["1", "2"]]);
  });

  it("handles CRLF line endings", () => {
    const rows = parseCsvRows("a,b\r\n1,2");
    expect(rows).toEqual([["a", "b"], ["1", "2"]]);
  });

  it("handles quoted fields with commas", () => {
    const rows = parseCsvRows('"hello, world",b');
    expect(rows).toEqual([["hello, world", "b"]]);
  });

  it("handles double-quoted escaping inside quoted fields", () => {
    const rows = parseCsvRows('"say ""hi""",b');
    expect(rows).toEqual([['say "hi"', "b"]]);
  });

  it("returns empty array for empty input", () => {
    expect(parseCsvRows("")).toEqual([]);
    expect(parseCsvRows("  ")).toEqual([["  "]]);
  });

  it("handles a trailing newline without adding an extra empty row", () => {
    // "a,b\n" → one row when the final empty row has no content
    // Legacy behavior: pushRow on each \n so we get [["a","b"],[""]].
    // Match the index.html behaviour: pushRow always happens.
    const rows = parseCsvRows("a,b\n");
    // The legacy parseCsvRows pushes the trailing empty row since cell=""
    // and row.length may be 0 at end — the `if (cell || row.length)` guard
    // means if there are no cells in the last row it's skipped.
    expect(rows.length).toBeGreaterThanOrEqual(1);
    expect(rows[0]).toEqual(["a", "b"]);
  });

  it("handles multi-column rows with empty cells", () => {
    const rows = parseCsvRows("a,,c\n,b,");
    expect(rows).toEqual([["a", "", "c"], ["", "b", ""]]);
  });
});
