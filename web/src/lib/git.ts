const GERRIT_PATH_MARKER = "/PDE/HPC/";

export function formatGerritUrl(url: string | null | undefined): string {
  const value = (url ?? "").trim();
  if (!value) return "";
  const index = value.indexOf(GERRIT_PATH_MARKER);
  if (index < 0) return value;
  return value.slice(index + GERRIT_PATH_MARKER.length) || value;
}
