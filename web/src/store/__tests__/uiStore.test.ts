import { describe, it, expect, beforeEach } from "vitest";
import { useUiStore } from "../uiStore";

// Reset store state between tests
beforeEach(() => {
  useUiStore.setState({
    selectedApp: "",
    appDetailDirty: false,
    appDetailEditMode: false,
    appDetailEditAppId: "",
    lateDecisionEditAppId: "",
    detailSectionOpen: {},
    qaEditMode: false,
    qaEditReleaseId: "",
    qaAiSuggestions: {},
    qaAiJob: null,
    qaReportsReleaseId: "",
    qaReportCompareId: "",
    qaReportFilters: {
      release: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 } },
      test: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 } },
      manager: { filter: "", colFilters: {}, sort: { col: -1, dir: 1 } },
    },
    qaReportVisibleColumns: { release: null, test: null, manager: null },
    cicdOverviewFilter: "Running",
    cicdRecentDays: 30,
    wikiUi: { selectedId: "", filter: "", mode: "list" },
    ldapStatus: { enabled: false, uri: "" },
  });
});

describe("selectedApp", () => {
  it("defaults to empty string", () => {
    expect(useUiStore.getState().selectedApp).toBe("");
  });

  it("updates via setSelectedApp", () => {
    useUiStore.getState().setSelectedApp("app1");
    expect(useUiStore.getState().selectedApp).toBe("app1");
  });
});

describe("app detail dirty/edit mode", () => {
  it("setAppDetailDirty updates flag", () => {
    useUiStore.getState().setAppDetailDirty(true);
    expect(useUiStore.getState().appDetailDirty).toBe(true);
    useUiStore.getState().setAppDetailDirty(false);
    expect(useUiStore.getState().appDetailDirty).toBe(false);
  });

  it("setAppDetailEditMode updates flag", () => {
    useUiStore.getState().setAppDetailEditMode(true);
    expect(useUiStore.getState().appDetailEditMode).toBe(true);
  });

  it("setAppDetailEditAppId updates id", () => {
    useUiStore.getState().setAppDetailEditAppId("myapp");
    expect(useUiStore.getState().appDetailEditAppId).toBe("myapp");
  });
});

describe("detailSectionOpen", () => {
  it("individual sections can be toggled", () => {
    useUiStore.getState().setDetailSectionOpen("doc", true);
    expect(useUiStore.getState().detailSectionOpen["doc"]).toBe(true);

    useUiStore.getState().setDetailSectionOpen("doc", false);
    expect(useUiStore.getState().detailSectionOpen["doc"]).toBe(false);
  });

  it("preserves other sections when one is toggled", () => {
    useUiStore.getState().setDetailSectionOpen("doc", true);
    useUiStore.getState().setDetailSectionOpen("qa", true);
    expect(useUiStore.getState().detailSectionOpen["doc"]).toBe(true);
    expect(useUiStore.getState().detailSectionOpen["qa"]).toBe(true);
  });
});

describe("QA edit mode", () => {
  it("setQaEditMode updates flag", () => {
    useUiStore.getState().setQaEditMode(true);
    expect(useUiStore.getState().qaEditMode).toBe(true);
  });

  it("setQaEditReleaseId updates id", () => {
    useUiStore.getState().setQaEditReleaseId("rel1");
    expect(useUiStore.getState().qaEditReleaseId).toBe("rel1");
  });
});

describe("QA AI suggestions", () => {
  it("setQaAiSuggestions stores suggestions", () => {
    const suggestions = {
      app1: { qa_status: "qa_passed", qa_issue_note: "", test_results: [] },
    };
    useUiStore.getState().setQaAiSuggestions(suggestions);
    expect(useUiStore.getState().qaAiSuggestions["app1"].qa_status).toBe("qa_passed");
  });

  it("clearQaAiSuggestions resets to empty object", () => {
    useUiStore.getState().setQaAiSuggestions({
      app1: { qa_status: "cannot_release", qa_issue_note: "x", test_results: [] },
    });
    useUiStore.getState().clearQaAiSuggestions();
    expect(useUiStore.getState().qaAiSuggestions).toEqual({});
  });
});

describe("QA report filters", () => {
  it("setQaReportFilter updates the text filter for a kind", () => {
    useUiStore.getState().setQaReportFilter("release", "search text");
    expect(useUiStore.getState().qaReportFilters.release.filter).toBe("search text");
    // Other kinds unaffected
    expect(useUiStore.getState().qaReportFilters.test.filter).toBe("");
  });

  it("setQaReportColFilter updates a specific column filter", () => {
    useUiStore.getState().setQaReportColFilter("test", "Owner", "alice");
    expect(useUiStore.getState().qaReportFilters.test.colFilters["Owner"]).toBe("alice");
  });

  it("setQaReportSort updates sort state", () => {
    useUiStore.getState().setQaReportSort("manager", 3, -1);
    const sort = useUiStore.getState().qaReportFilters.manager.sort;
    expect(sort.col).toBe(3);
    expect(sort.dir).toBe(-1);
  });

  it("resetQaReportState resets a kind to defaults", () => {
    useUiStore.getState().setQaReportFilter("release", "some text");
    useUiStore.getState().setQaReportSort("release", 2, -1);
    useUiStore.getState().resetQaReportState("release");

    const state = useUiStore.getState().qaReportFilters.release;
    expect(state.filter).toBe("");
    expect(state.sort.col).toBe(-1);
    expect(state.sort.dir).toBe(1);
  });
});

describe("CICD filters", () => {
  it("cicdOverviewFilter defaults to Running", () => {
    expect(useUiStore.getState().cicdOverviewFilter).toBe("Running");
  });

  it("setCicdOverviewFilter updates filter", () => {
    useUiStore.getState().setCicdOverviewFilter("Stopped");
    expect(useUiStore.getState().cicdOverviewFilter).toBe("Stopped");
  });

  it("cicdRecentDays defaults to 30", () => {
    expect(useUiStore.getState().cicdRecentDays).toBe(30);
  });

  it("setCicdRecentDays updates days", () => {
    useUiStore.getState().setCicdRecentDays(7);
    expect(useUiStore.getState().cicdRecentDays).toBe(7);
  });
});

describe("wiki UI state", () => {
  it("defaults to list mode", () => {
    expect(useUiStore.getState().wikiUi.mode).toBe("list");
  });

  it("setWikiUi patches state", () => {
    useUiStore.getState().setWikiUi({ selectedId: "wiki_123", mode: "view" });
    expect(useUiStore.getState().wikiUi.selectedId).toBe("wiki_123");
    expect(useUiStore.getState().wikiUi.mode).toBe("view");
    // Other fields preserved
    expect(useUiStore.getState().wikiUi.filter).toBe("");
  });
});

describe("LDAP status", () => {
  it("defaults to disabled", () => {
    expect(useUiStore.getState().ldapStatus.enabled).toBe(false);
  });

  it("setLdapStatus updates the value", () => {
    useUiStore.getState().setLdapStatus({ enabled: true, uri: "ldap://example.com" });
    expect(useUiStore.getState().ldapStatus.enabled).toBe(true);
    expect(useUiStore.getState().ldapStatus.uri).toBe("ldap://example.com");
  });
});
