/**
 * Zustand UI store — replaces the ~25 global let/var declarations in
 * index.html that track purely client-side interaction state.
 *
 * Server data (releases, snapshots, CICD tasks, etc.) lives in TanStack
 * Query, NOT here.  This store holds only:
 *   - Which app is selected (selectedApp)
 *   - Edit / dirty mode flags
 *   - Filter / sort state for each section
 *   - CICD section filters
 *   - QA section state (edit mode, AI job, suggestions, report state)
 *   - Wiki section state
 *   - LDAP status (loaded once on boot, rarely changes)
 *
 * Legacy global map (index.html lines):
 *   selectedApp                     :1215
 *   appDetailDirty                  :1230
 *   appDetailEditMode               :1231
 *   appDetailEditAppId              :1232
 *   lateDecisionEditAppId           :1233
 *   detailSectionOpen               :1234
 *   qaEditMode                      :1235
 *   qaEditReleaseId                 :1236
 *   qaAiSuggestions                 :1239
 *   qaAiJob                         :1240
 *   qaReports                       :1241
 *   qaReportsReleaseId              :1242
 *   qaReportFilter                  :1246
 *   qaReportColFilters              :1247
 *   qaReportSort                    :1248
 *   qaReportVisibleColumns          :1249
 *   qaReportCompareId               :1250
 *   ldapStatus                      :1254
 *   wikiState                       :1256
 *   wikiEditingId                   :1257
 *   cicdOverviewFilter              :3889
 *   cicdRecentDays                  :3890
 *   selectedReleaseId               :L3 (cross-tab shared release selector)
 */

import { create } from "zustand";
import type { QaAnalysisJob, LdapStatusResponse } from "../types";

// ---------------------------------------------------------------------------
// QA report filter/sort state
// ---------------------------------------------------------------------------

export type QaReportKind = "release" | "test" | "manager";

export interface QaReportFilterState {
  filter: string;
  colFilters: Record<string, string>;
  sort: { col: number; dir: 1 | -1 };
}

const defaultQaReportFilter = (): QaReportFilterState => ({
  filter: "",
  colFilters: {},
  sort: { col: -1, dir: 1 },
});

// ---------------------------------------------------------------------------
// AI suggestion shape (per app_id)
// ---------------------------------------------------------------------------

export interface QaAiSuggestion {
  qa_status: string;
  qa_issue_note: string;
  test_results: unknown[];
}

// ---------------------------------------------------------------------------
// Wiki UI state
// ---------------------------------------------------------------------------

export interface WikiUiState {
  selectedId: string;
  filter: string;
  /** "list" | "view" | "edit" | "new" */
  mode: "list" | "view" | "edit" | "new";
}

// ---------------------------------------------------------------------------
// Store shape
// ---------------------------------------------------------------------------

export interface UiStore {
  // ── Shared release selector (L3) ───────────────────────────────────────
  /**
   * The release_id currently selected in the header release selector.
   * Shared across ALL tabs (dashboard, apps, qa, artifacts, init, cicd).
   * "" means "not yet seeded" — the first tab to load /api/state sets it.
   * Mirrors legacy index.html currentReleaseId() / state.release?.id logic.
   */
  selectedReleaseId: string;
  setSelectedReleaseId: (id: string) => void;

  // ── App workbench ──────────────────────────────────────────────────────
  /** Currently selected app_id (index.html:1215 selectedApp) */
  selectedApp: string;
  setSelectedApp: (id: string) => void;

  /** True when the detail form has unsaved edits (index.html:1230) */
  appDetailDirty: boolean;
  setAppDetailDirty: (v: boolean) => void;

  /** True when the detail panel is in edit mode (index.html:1231) */
  appDetailEditMode: boolean;
  setAppDetailEditMode: (v: boolean) => void;

  /** The app_id currently open in the detail panel (index.html:1232) */
  appDetailEditAppId: string;
  setAppDetailEditAppId: (id: string) => void;

  /** App whose late-decision is being edited (index.html:1233) */
  lateDecisionEditAppId: string;
  setLateDecisionEditAppId: (id: string) => void;

  /** Which collapsible sections are expanded in the detail panel */
  detailSectionOpen: Record<string, boolean>;
  setDetailSectionOpen: (section: string, open: boolean) => void;

  // ── QA section ─────────────────────────────────────────────────────────
  /** True when QA status edit form is active (index.html:1235) */
  qaEditMode: boolean;
  setQaEditMode: (v: boolean) => void;

  /** The release_id currently shown in QA edit (index.html:1236) */
  qaEditReleaseId: string;
  setQaEditReleaseId: (id: string) => void;

  /**
   * AI-suggested QA annotations keyed by app_id (index.html:1239).
   * Populated when an analysis completes; cleared on save/cancel.
   */
  qaAiSuggestions: Record<string, QaAiSuggestion>;
  setQaAiSuggestions: (v: Record<string, QaAiSuggestion>) => void;
  clearQaAiSuggestions: () => void;

  /** Current async AI analysis job (index.html:1240) */
  qaAiJob: QaAnalysisJob | null;
  setQaAiJob: (job: QaAnalysisJob | null) => void;

  /** The release_id for which QA reports are currently loaded (index.html:1242) */
  qaReportsReleaseId: string;
  setQaReportsReleaseId: (id: string) => void;

  /** Compare-release selector (index.html:1250) */
  qaReportCompareId: string;
  setQaReportCompareId: (id: string) => void;

  /** Free-text filter per report kind (index.html:1246) */
  qaReportFilters: Record<QaReportKind, QaReportFilterState>;
  setQaReportFilter: (kind: QaReportKind, filter: string) => void;
  setQaReportColFilter: (kind: QaReportKind, col: string, value: string) => void;
  setQaReportSort: (kind: QaReportKind, col: number, dir: 1 | -1) => void;
  resetQaReportState: (kind: QaReportKind) => void;

  /**
   * Which columns are visible per report kind.
   * null = all visible (default).  index.html:1249.
   */
  qaReportVisibleColumns: Record<QaReportKind, Set<string> | null>;
  setQaReportVisibleColumns: (kind: QaReportKind, cols: Set<string> | null) => void;

  // ── CICD section ───────────────────────────────────────────────────────
  /** Active status filter in the task overview panel (index.html:3889) */
  cicdOverviewFilter: string;
  setCicdOverviewFilter: (v: string) => void;

  /** Days window for the "recent requests" pane (index.html:3890) */
  cicdRecentDays: number;
  setCicdRecentDays: (v: number) => void;

  // ── Wiki section ───────────────────────────────────────────────────────
  /** Wiki UI state (index.html:1256) */
  wikiUi: WikiUiState;
  setWikiUi: (patch: Partial<WikiUiState>) => void;

  // ── LDAP (loaded on boot) ──────────────────────────────────────────────
  ldapStatus: LdapStatusResponse;
  setLdapStatus: (v: LdapStatusResponse) => void;
}

// ---------------------------------------------------------------------------
// Store implementation
// ---------------------------------------------------------------------------

export const useUiStore = create<UiStore>((set) => ({
  // ── Shared release selector ────────────────────────────────────────────
  selectedReleaseId: "",
  setSelectedReleaseId: (id) => set({ selectedReleaseId: id }),

  // ── App workbench ──────────────────────────────────────────────────────
  selectedApp: "",
  setSelectedApp: (id) => set({ selectedApp: id }),

  appDetailDirty: false,
  setAppDetailDirty: (v) => set({ appDetailDirty: v }),

  appDetailEditMode: false,
  setAppDetailEditMode: (v) => set({ appDetailEditMode: v }),

  appDetailEditAppId: "",
  setAppDetailEditAppId: (id) => set({ appDetailEditAppId: id }),

  lateDecisionEditAppId: "",
  setLateDecisionEditAppId: (id) => set({ lateDecisionEditAppId: id }),

  detailSectionOpen: {},
  setDetailSectionOpen: (section, open) =>
    set((s) => ({
      detailSectionOpen: { ...s.detailSectionOpen, [section]: open },
    })),

  // ── QA ────────────────────────────────────────────────────────────────
  qaEditMode: false,
  setQaEditMode: (v) => set({ qaEditMode: v }),

  qaEditReleaseId: "",
  setQaEditReleaseId: (id) => set({ qaEditReleaseId: id }),

  qaAiSuggestions: {},
  setQaAiSuggestions: (v) => set({ qaAiSuggestions: v }),
  clearQaAiSuggestions: () => set({ qaAiSuggestions: {} }),

  qaAiJob: null,
  setQaAiJob: (job) => set({ qaAiJob: job }),

  qaReportsReleaseId: "",
  setQaReportsReleaseId: (id) => set({ qaReportsReleaseId: id }),

  qaReportCompareId: "",
  setQaReportCompareId: (id) => set({ qaReportCompareId: id }),

  qaReportFilters: {
    release: defaultQaReportFilter(),
    test: defaultQaReportFilter(),
    manager: defaultQaReportFilter(),
  },
  setQaReportFilter: (kind, filter) =>
    set((s) => ({
      qaReportFilters: {
        ...s.qaReportFilters,
        [kind]: { ...s.qaReportFilters[kind], filter },
      },
    })),
  setQaReportColFilter: (kind, col, value) =>
    set((s) => ({
      qaReportFilters: {
        ...s.qaReportFilters,
        [kind]: {
          ...s.qaReportFilters[kind],
          colFilters: {
            ...s.qaReportFilters[kind].colFilters,
            [col]: value,
          },
        },
      },
    })),
  setQaReportSort: (kind, col, dir) =>
    set((s) => ({
      qaReportFilters: {
        ...s.qaReportFilters,
        [kind]: { ...s.qaReportFilters[kind], sort: { col, dir } },
      },
    })),
  resetQaReportState: (kind) =>
    set((s) => ({
      qaReportFilters: {
        ...s.qaReportFilters,
        [kind]: defaultQaReportFilter(),
      },
    })),

  qaReportVisibleColumns: { release: null, test: null, manager: null },
  setQaReportVisibleColumns: (kind, cols) =>
    set((s) => ({
      qaReportVisibleColumns: { ...s.qaReportVisibleColumns, [kind]: cols },
    })),

  // ── CICD ─────────────────────────────────────────────────────────────
  cicdOverviewFilter: "Running",
  setCicdOverviewFilter: (v) => set({ cicdOverviewFilter: v }),

  cicdRecentDays: 30,
  setCicdRecentDays: (v) => set({ cicdRecentDays: v }),

  // ── Wiki ──────────────────────────────────────────────────────────────
  wikiUi: {
    selectedId: "",
    filter: "",
    mode: "list",
  },
  setWikiUi: (patch) =>
    set((s) => ({ wikiUi: { ...s.wikiUi, ...patch } })),

  // ── LDAP ─────────────────────────────────────────────────────────────
  ldapStatus: { enabled: false, uri: "" },
  setLdapStatus: (v) => set({ ldapStatus: v }),
}));
