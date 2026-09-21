/**
 * Line diff for app_info values.
 *
 * app_info diffs arrive as old/new pairs of strings, string lists or
 * list-of-objects.  Rendering them side by side hides what actually moved, so
 * both the App workbench and the QA scope-confirmation dialog render them as a
 * unified diff instead: unchanged lines as context, the rest as -/+.
 */

export type DiffLineKind = "context" | "del" | "add";

export interface DiffLine {
  kind: DiffLineKind;
  text: string;
}

/** Above this many lines on either side the LCS table is not worth building. */
const LCS_LINE_LIMIT = 400;

/** Render one diff value as the lines a diff works on. */
export function valueLines(value: unknown): string[] {
  if (value === undefined || value === null) return [];
  if (typeof value === "string") {
    return value === "" ? [] : value.split("\n");
  }
  return (JSON.stringify(value, null, 2) ?? String(value)).split("\n");
}

function lcsLengths(a: string[], b: string[]): number[][] {
  const table: number[][] = Array.from({ length: a.length + 1 }, () =>
    new Array<number>(b.length + 1).fill(0),
  );
  for (let i = a.length - 1; i >= 0; i--) {
    for (let j = b.length - 1; j >= 0; j--) {
      table[i][j] = a[i] === b[j]
        ? table[i + 1][j + 1] + 1
        : Math.max(table[i + 1][j], table[i][j + 1]);
    }
  }
  return table;
}

/** Unified diff of two line lists: common lines stay as context. */
export function diffLines(oldLines: string[], newLines: string[]): DiffLine[] {
  if (oldLines.length > LCS_LINE_LIMIT || newLines.length > LCS_LINE_LIMIT) {
    return [
      ...oldLines.map((text): DiffLine => ({ kind: "del", text })),
      ...newLines.map((text): DiffLine => ({ kind: "add", text })),
    ];
  }
  const table = lcsLengths(oldLines, newLines);
  const lines: DiffLine[] = [];
  let i = 0;
  let j = 0;
  while (i < oldLines.length && j < newLines.length) {
    if (oldLines[i] === newLines[j]) {
      lines.push({ kind: "context", text: oldLines[i] });
      i++;
      j++;
    } else if (table[i + 1][j] >= table[i][j + 1]) {
      lines.push({ kind: "del", text: oldLines[i] });
      i++;
    } else {
      lines.push({ kind: "add", text: newLines[j] });
      j++;
    }
  }
  while (i < oldLines.length) lines.push({ kind: "del", text: oldLines[i++] });
  while (j < newLines.length) lines.push({ kind: "add", text: newLines[j++] });
  return lines;
}

/** Unified diff of one app_info change, with (空) standing in for nothing. */
export function diffValues(oldValue: unknown, newValue: unknown): DiffLine[] {
  const oldLines = valueLines(oldValue);
  const newLines = valueLines(newValue);
  if (!oldLines.length && !newLines.length) return [];
  if (!oldLines.length) {
    return [
      { kind: "del", text: "（空）" },
      ...newLines.map((text): DiffLine => ({ kind: "add", text })),
    ];
  }
  if (!newLines.length) {
    return [
      ...oldLines.map((text): DiffLine => ({ kind: "del", text })),
      { kind: "add", text: "（空）" },
    ];
  }
  return diffLines(oldLines, newLines);
}

const DIFF_PREFIX: Record<DiffLineKind, string> = {
  context: "  ",
  del: "- ",
  add: "+ ",
};

/** Plain-text rendering of a diff, for places that cannot render elements. */
export function diffLinesToText(lines: DiffLine[]): string {
  return lines.map((line) => `${DIFF_PREFIX[line.kind]}${line.text}`).join("\n");
}

export { DIFF_PREFIX };
