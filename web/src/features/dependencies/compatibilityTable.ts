import { compatibilityTableMetadata, publicationManifestSourceLabel, type PublicationManifestSource } from "../../lib/domainMetadata";

export interface ReleaseRow {
  app_id: string;
  app_name: string;
  owners: string[];
  dependencies: string[];
  scope_pending: boolean;
  hpc_version: string;
  app_version: string;
  app_info_commit_id: string;
  manifest_source: PublicationManifestSource;
  os?: string;
  arch?: string;
  chips?: string[];
  GPU?: string;
  image_target?: string;
  python_label?: string;
  pytorch_label?: string;
  delivery_reference?: string;
  path?: string;
  command?: string;
  perf_golden?: string;
  perf_unit?: string;
  note: string;
}

export const columns = compatibilityTableMetadata.columns.map(([, label]) => label);
const appColumns = new Set(["app_name", "app_id", "owners", "dependencies", "hpc_version", "app_version", "manifest_source"]);
export const detailColumns = compatibilityTableMetadata.columns.filter(([key]) => !appColumns.has(key));
export const archName = (arch?: string) => (compatibilityTableMetadata.architecture_labels as Record<string, string>)[arch ?? ""] ?? arch ?? "";

export function cell(row: ReleaseRow, key: string): string {
  if (key === "manifest_source") return publicationManifestSourceLabel(row.manifest_source);
  if (key === "arch") return archName(row.arch);
  if (key === "image_config") return [row.image_target, row.python_label, row.pytorch_label].filter(Boolean).join(" · ");
  const value = row[key as keyof ReleaseRow];
  return Array.isArray(value) ? value.join(", ") : String(value ?? "");
}

export function cells(row: ReleaseRow): string[] {
  return compatibilityTableMetadata.columns.map(([key]) => cell(row, key));
}

export function groupGpuRows(rows: ReleaseRow[]): ReleaseRow[] {
  const groups = new Map<string, { row: ReleaseRow; chips: Set<string> }>();
  const ignored = new Set([...compatibilityTableMetadata.gpu_fields, ...compatibilityTableMetadata.group_ignored_fields]);
  for (const row of rows) {
    const key = JSON.stringify(Object.keys(row).filter(key => !ignored.has(key)).sort()
      .map(key => [key, row[key as keyof ReleaseRow]]));
    let group = groups.get(key);
    if (!group) {
      group = { row, chips: new Set() };
      groups.set(key, group);
    }
    for (const chip of row.GPU ? [row.GPU] : row.chips ?? []) group.chips.add(chip);
  }
  return [...groups.values()].map(({ row, chips }) => ({ ...row, GPU: [...chips].join(", "), chips: [...chips] }));
}
