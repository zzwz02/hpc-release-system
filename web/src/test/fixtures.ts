/** Test fixtures for the monthly release model (App, Entry, cycle, state). */
import type {
  App,
  AppInfoVersion,
  Entry,
  ReleaseDetail,
  ReleaseSummary,
  StatePayload,
} from "../types";

export function makeAppInfo(overrides: Partial<AppInfoVersion> = {}): AppInfoVersion {
  return {
    id: 1,
    commit_id: "abcdef1234567890",
    fetched_at: "2026-11-10 02:00:00",
    source: "hpc_x maca:app_info.json",
    app_version: "1.0",
    x86_chips: ["C500"],
    arm_chips: [],
    build_os: ["ubuntu22.04"],
    build_arches: ["amd64"],
    python_labels: [],
    pytorch_labels: [],
    tests: [
      { path: "hpc.x.t1", command: "run t1", supported_chips: ["C500"], arch_list: ["ubuntu22.04_amd64"], platforms: [{ build: "ubuntu22.04_amd64", os: "ubuntu22.04", arch: "amd64", chips: ["C500"], python_label: "", pytorch_label: "" }] },
      { path: "hpc.x.t2", command: "run t2", supported_chips: ["C500"], arch_list: ["ubuntu22.04_amd64"], platforms: [{ build: "ubuntu22.04_amd64", os: "ubuntu22.04", arch: "amd64", chips: ["C500"], python_label: "", pytorch_label: "" }] },
    ],
    community: {},
    dependencies: null,
    ...overrides,
  };
}

export function makeApp(id: string, overrides: Partial<App> = {}): App {
  return {
    id,
    name: `App ${id} 1.0`,
    git_url: `hpc_${id}`,
    git_branch: "maca",
    aliases: [],
    created_by: "rm",
    created_at: "2026-01-01 00:00:00",
    cicd_repo_type: "git",
    cicd_build_image: "",
    cicd_test_timeout: "40",
    cicd_notes: "",
    cicd_status: "running",
    official_name: `App ${id}`,
    type: "性能基准测试",
    official_url: "",
    description: "",
    doc_target: "manual",
    owners: ["alice"],
    doc: {},
    community: {},
    sanity: {},
    test_docs: [],
    sync_head_commit: "abcdef1234567890",
    sync_checked_at: "2026-11-10 02:00:00",
    sync_error: "",
    latest_app_info: makeAppInfo(),
    dependencies: [],
    dependency_declared: false,
    official: null,
    open_status_request: null,
    open_signups: [],
    allowed_actions: ["app.edit"],
    ...overrides,
  };
}

export function makeEntry(appId: string, overrides: Partial<Entry> = {}): Entry {
  const info = makeAppInfo();
  return {
    id: 1,
    release_id: "rel-1",
    app_id: appId,
    status: "active",
    base_image_id: 1,
    target_version: "1.0",
    planned_features: "feature A",
    actual_delivery: "",
    known_issues: "",
    locked_version_id: info.id,
    excluded_tests: [],
    qa_status: "not_checked",
    qa_note: "",
    deliverable_image: "",
    deliverable_digest: "",
    deliverable_commit: "",
    deliverable_images: [],
    delivery_revision: "revision-1",
    delivery_builds: [{ build_key: "ubuntu22.04_amd64", build: "ubuntu22.04_amd64", os: "ubuntu22.04", arch: "amd64",
      python_label: "", pytorch_label: "", image_target: "release" }],
    result: "",
    created_at: "2026-11-20 10:00:00",
    created_by: "alice",
    updated_at: "2026-11-20 10:00:00",
    updated_by: "alice",
    locked_app_info: info,
    scope_tests: info.tests.map((t) => ({ ...t, excluded: false })),
    pending_scope_request: null,
    behind_latest: false,
    ...overrides,
  };
}

export function makeReleaseSummary(overrides: Partial<ReleaseSummary> = {}): ReleaseSummary {
  return {
    id: "rel-1",
    name: "2026.12",
    signup_deadline: "2026-12-01 23:59",
    qa_deadline: "2026-12-26 23:59",
    release_date: "2026-12-31",
    released_locked: false,
    released_locked_at: "",
    released_locked_by: "",
    created_at: "2026-11-10 10:00:00",
    created_by: "rm",
    phase: "signup",
    entry_count: 0,
    released_count: 0,
    ...overrides,
  };
}

export function makeRelease(entries: Entry[] = [], overrides: Partial<ReleaseDetail> = {}): ReleaseDetail {
  return {
    ...makeReleaseSummary(),
    environment: [{ component: "MACA SDK", version: "3.3", note: "" }],
    base_images: [{ id: 1, release_id: "rel-1", name: "ubuntu22.04", image: "img", digest: "sha256:base", note: "" }],
    entries,
    scope_requests: [],
    ...overrides,
  };
}

export function makeState(apps: App[], release: ReleaseDetail | null, overrides: Partial<StatePayload> = {}): StatePayload {
  return {
    apps,
    app_types: [],
    releases: release ? [release] : [],
    release,
    artifacts: [],
    user: { username: "rm", role: "RM", display_name: "" },
    user_display_names: {},
    qa_log: null,
    qa_audit_logs: {},
    release_schedule: [],
    ...overrides,
  };
}
