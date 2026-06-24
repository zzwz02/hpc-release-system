export const GERRIT_HPC_BASE = "ssh://sw-gerrit-devops.metax-internal.com:29418/PDE/HPC";
export const GERRIT_MANIFEST_REPO_URL = `${GERRIT_HPC_BASE}/manifest`;

const GERRIT_PATH_MARKER = "/PDE/HPC/";
const MANIFEST_PATH_PREFIX = "manifest/";

function stripKnownPrefix(value: string, prefix: string): string | null {
  if (value === prefix) return "";
  const withSlash = `${prefix}/`;
  if (value.startsWith(withSlash)) return value.slice(withSlash.length);
  return null;
}

function stripLeadingSlash(value: string): string {
  return value.replace(/^\/+/, "");
}

export function formatGerritUrl(url: string | null | undefined): string {
  const value = (url ?? "").trim();
  if (!value) return "";
  const index = value.indexOf(GERRIT_PATH_MARKER);
  if (index < 0) return value;
  return value.slice(index + GERRIT_PATH_MARKER.length) || value;
}

export function formatCicdRepoPath(
  url: string | null | undefined,
  repoType?: string | null,
): string {
  const value = (url ?? "").trim();
  if (!value) return "";

  const manifestRelative = stripKnownPrefix(value, GERRIT_MANIFEST_REPO_URL);
  if (manifestRelative !== null) return stripLeadingSlash(manifestRelative);

  const hpcPath = formatGerritUrl(value);
  if (
    (repoType === "repo" || hpcPath.endsWith(".xml")) &&
    hpcPath.startsWith(MANIFEST_PATH_PREFIX)
  ) {
    return hpcPath.slice(MANIFEST_PATH_PREFIX.length);
  }
  return hpcPath;
}

export function normalizeCicdRepoInput(repoType: string | null | undefined, value: string): string {
  const raw = (value ?? "").trim();
  if (!raw) return "";

  if (repoType === "repo") {
    const manifestRelative = stripKnownPrefix(raw, GERRIT_MANIFEST_REPO_URL);
    if (manifestRelative !== null) return stripLeadingSlash(manifestRelative);

    const hpcPath = formatGerritUrl(raw);
    if (hpcPath.startsWith(MANIFEST_PATH_PREFIX)) {
      return stripLeadingSlash(hpcPath.slice(MANIFEST_PATH_PREFIX.length));
    }
    return stripLeadingSlash(hpcPath);
  }

  const hpcRelative = stripKnownPrefix(raw, GERRIT_HPC_BASE);
  if (hpcRelative !== null) return stripLeadingSlash(hpcRelative);
  return stripLeadingSlash(raw);
}

export function normalizeGitUrl(url: string): string {
  const value = (url ?? "").trim();
  if (!value) return value;
  if (value.includes("://") || value.startsWith("git@") || value.endsWith(".xml")) return value;
  return `${GERRIT_HPC_BASE}/${stripLeadingSlash(value)}`;
}

export function isFullGitRemote(value: string | null | undefined): boolean {
  const raw = (value ?? "").trim();
  return raw.includes("://") || raw.startsWith("git@");
}
