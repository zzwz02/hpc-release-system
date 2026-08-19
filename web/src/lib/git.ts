import sharedIntegrations from "../../../shared/integrations.json" with { type: "json" };

const gerrit = sharedIntegrations.gerrit;
const configuredSshBaseUrl = __GERRIT_SSH_BASE_URL__.trim();
export const GERRIT_SSH_BASE_URL = (configuredSshBaseUrl || gerrit.ssh_base_url).replace(/\/+$/, "");
export const GERRIT_HPC_PROJECT = gerrit.hpc_project.replace(/^\/+|\/+$/g, "");
export const GERRIT_MANIFEST_PROJECT = gerrit.manifest_project.replace(/^\/+|\/+$/g, "");
export const GERRIT_MANIFEST_BRANCH = gerrit.manifest_branch.trim();
export const GERRIT_HPC_BASE = `${GERRIT_SSH_BASE_URL}/${GERRIT_HPC_PROJECT}`;
export const GERRIT_MANIFEST_REPO_URL = `${GERRIT_HPC_BASE}/${GERRIT_MANIFEST_PROJECT}`;

if (!GERRIT_SSH_BASE_URL || !GERRIT_HPC_PROJECT || !GERRIT_MANIFEST_PROJECT || !GERRIT_MANIFEST_BRANCH) {
  throw new Error("Shared Gerrit configuration values must be non-empty");
}

const GERRIT_PATH_MARKER = `/${GERRIT_HPC_PROJECT}/`;
const MANIFEST_PATH_PREFIX = `${GERRIT_MANIFEST_PROJECT}/`;

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
