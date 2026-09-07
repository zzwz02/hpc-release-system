import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { apiGet } from "../../api/http";
import type { StatePayload } from "../../types";
import {
  CICD_AGENT_FAILURES_KEY,
  CICD_AGENT_FILTER_OPTIONS_KEY,
  CICD_AGENT_SUMMARY_KEY,
  fetchFailureDetail,
  fetchFailureFilterOptions,
  fetchFailureRecords,
  fetchFailureSummary,
  resolveFailureResponsibility,
  submitFailureResponsibilityFeedback,
  todayString,
  type FailureRecordDetail,
  type FailureRecordFilters,
  type FailureResponsibilityEvent,
  type FailureResponsibilityFeedbackPayload,
  type FailureResponsibilityResolutionPayload,
} from "./cicdAgentApi";

const SUMMARY_GROUP_OPTIONS = [
  { value: "normalized_stage", label: "Stage", filterKey: "normalized_stage" },
  { value: "job_type", label: "Job Type", filterKey: "job_type" },
  { value: "owner_role", label: "责任组", filterKey: "owner_role" },
  { value: "responsibility_status", label: "认定状态", filterKey: "responsibility_status" },
  { value: "code_owner", label: "Code Owner", filterKey: "code_owner" },
  { value: "official_name", label: "Official Name", filterKey: "official_name" },
  { value: "maca_project", label: "Project", filterKey: "maca_project" },
  { value: "chip", label: "Chip", filterKey: "chip" },
  { value: "matched_rule_id", label: "Rule", filterKey: "matched_rule_id" },
] as const;

const SUMMARY_COLLAPSED_LIMIT = 12;

const RESPONSIBILITY_STATUS_LABELS: Record<string, string> = {
  ai_suggested: "AI建议",
  disputed: "有争议",
  confirmed: "已确认",
  corrected: "已修正",
  unresolved: "无法判定",
};

const FEEDBACK_TYPE_OPTIONS = [
  { value: "owner_wrong", label: "责任人不对" },
  { value: "reason_wrong", label: "原因判断不对" },
  { value: "scope_unclear", label: "影响范围不清楚" },
  { value: "other", label: "其他" },
] as const;

const DEVOPS_OWNER_ACCOUNT = "m00930";
const RESPONSIBILITY_OWNER_ROLES = ["Code Owner", "DevOps"] as const;

type SummaryGroupKey = (typeof SUMMARY_GROUP_OPTIONS)[number]["value"];
type ResponsibilityOwnerRole = (typeof RESPONSIBILITY_OWNER_ROLES)[number];

function emptyFilters(): FailureRecordFilters {
  const today = todayString();
  return {
    date_from: today,
    date_to: today,
    job_type: "",
    normalized_stage: "",
    code_owner: "",
    owner_account: "",
    maca_project: "",
    maca_version: "",
    chip: "",
  };
}

function summaryFilterKey(groupBy: SummaryGroupKey): keyof FailureRecordFilters {
  return SUMMARY_GROUP_OPTIONS.find((option) => option.value === groupBy)?.filterKey
    ?? "normalized_stage";
}

function summaryGroupLabel(groupBy: SummaryGroupKey): string {
  return SUMMARY_GROUP_OPTIONS.find((option) => option.value === groupBy)?.label ?? groupBy;
}

function fmt(value: string | null | undefined): string {
  return value || "N/A";
}

function normalizeDisplayName(displayName: string | null | undefined, account: string): string {
  const value = String(displayName ?? "").trim();
  if (!value || value === account) return "";
  const accountPattern = account.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const accountSuffix = new RegExp(`\\s*[（(]\\s*${accountPattern}\\s*[）)]\\s*$`, "i");
  return value
    .replace(accountSuffix, "")
    .replace(/\s+-\s+.*$/, "")
    .replace(accountSuffix, "")
    .trim();
}

function userDisplayLabel(
  username: string | null | undefined,
  displayNames: Record<string, string>,
): string {
  const account = String(username ?? "").trim();
  if (!account) return "N/A";
  const displayName = normalizeDisplayName(displayNames[account], account);
  return displayName ? `${displayName} (${account})` : account;
}

function summaryDisplayValue(
  groupBy: SummaryGroupKey,
  value: string,
  displayNames: Record<string, string>,
): string {
  if (groupBy === "responsibility_status") return responsibilityStatusLabel(value);
  return groupBy === "code_owner" ? userDisplayLabel(value, displayNames) : value;
}

function uniqueOptions(options: Array<string | null | undefined>): string[] {
  return Array.from(new Set(options.filter(Boolean) as string[]));
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "N/A";
  return value.replace("T", " ").slice(0, 19);
}

function responsibilityStatusLabel(value: string | null | undefined): string {
  const status = value || "ai_suggested";
  return RESPONSIBILITY_STATUS_LABELS[status] ?? status;
}

function responsibilityStatusClass(value: string | null | undefined): string {
  switch (value || "ai_suggested") {
    case "disputed":
      return "responsibility-disputed";
    case "confirmed":
      return "responsibility-confirmed";
    case "corrected":
      return "responsibility-corrected";
    case "unresolved":
      return "responsibility-unresolved";
    default:
      return "responsibility-ai";
  }
}

function ownerRoleClass(value: string | null | undefined): string {
  if (value === "DevOps") return "accent";
  if (value === "Unresolved") return "bad";
  return "warnp";
}

function effectiveOwnerAccount(record: FailureRecordDetail): string | null | undefined {
  return record.effective_owner_account ?? record.owner_account;
}

function effectiveOwnerRole(record: FailureRecordDetail): string | null | undefined {
  return record.effective_owner_role ?? record.owner_role;
}

function effectiveReason(record: FailureRecordDetail): string | null | undefined {
  return record.effective_reason_summary ?? record.reason_summary;
}

function effectiveAction(record: FailureRecordDetail): string | null | undefined {
  return record.effective_action_suggestion ?? record.action_suggestion;
}

function ownerLabel(
  record: FailureRecordDetail,
  displayNames: Record<string, string>,
): string {
  if ((record.responsibility_status || "") === "unresolved") return "无法判定";
  return userDisplayLabel(effectiveOwnerAccount(record), displayNames);
}

function normalizeResponsibilityRole(value: string | null | undefined): ResponsibilityOwnerRole {
  return value === "DevOps" ? "DevOps" : "Code Owner";
}

function responsibilityOwnerAccount(
  record: FailureRecordDetail,
  role: ResponsibilityOwnerRole,
): string {
  if (role === "DevOps") return DEVOPS_OWNER_ACCOUNT;
  return String(record.code_owner || record.owner_account || "").trim();
}

function responsibilityChoiceLabel(
  record: FailureRecordDetail,
  role: ResponsibilityOwnerRole,
  displayNames: Record<string, string>,
): string {
  return `${role}：${userDisplayLabel(responsibilityOwnerAccount(record, role), displayNames)}`;
}

function inferResponsibilityRole(
  record: FailureRecordDetail,
  role: string | null | undefined,
  account: string | null | undefined,
): ResponsibilityOwnerRole {
  if (role) return normalizeResponsibilityRole(role);
  if (account === DEVOPS_OWNER_ACCOUNT) return "DevOps";
  if (account && account === record.code_owner) return "Code Owner";
  return normalizeResponsibilityRole(effectiveOwnerRole(record));
}

function latestFeedbackSuggestion(record: FailureRecordDetail): {
  account: string;
  role: ResponsibilityOwnerRole;
} | null {
  for (const event of record.responsibility_events ?? []) {
    const account = String(event.suggested_owner_account || event.new_owner_account || "").trim();
    const rawRole = event.suggested_owner_role || event.new_owner_role;
    if (!account && !rawRole) continue;
    const role = inferResponsibilityRole(record, rawRole, account);
    return {
      account: account || responsibilityOwnerAccount(record, role),
      role,
    };
  }
  return null;
}

function feedbackSuggestionLabel(
  record: FailureRecordDetail,
  event: FailureResponsibilityEvent,
  displayNames: Record<string, string>,
): string {
  const rawRole = event.suggested_owner_role || event.new_owner_role;
  const rawAccount = String(event.suggested_owner_account || event.new_owner_account || "").trim();
  if (!rawRole && !rawAccount) return "";
  const role = inferResponsibilityRole(record, rawRole, rawAccount);
  const account = rawAccount || responsibilityOwnerAccount(record, role);
  return `${role} / ${userDisplayLabel(account, displayNames)}`;
}

function rolePayload(
  record: FailureRecordDetail,
  role: ResponsibilityOwnerRole,
): Pick<FailureResponsibilityResolutionPayload, "final_owner_account" | "final_owner_role"> {
  return {
    final_owner_account: responsibilityOwnerAccount(record, role),
    final_owner_role: role,
  };
}

function errorText(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function JenkinsFailuresPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<FailureRecordFilters>(emptyFilters);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [summaryGroup, setSummaryGroup] = useState<SummaryGroupKey>("owner_role");
  const [summaryExpanded, setSummaryExpanded] = useState(false);

  const activeFilters = useMemo<FailureRecordFilters>(
    () => Object.fromEntries(Object.entries(filters).filter(([, value]) => value)),
    [filters],
  );

  const recordsQuery = useQuery({
    queryKey: [...CICD_AGENT_FAILURES_KEY, activeFilters, page],
    queryFn: () => fetchFailureRecords(activeFilters, page),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const optionsQuery = useQuery({
    queryKey: [...CICD_AGENT_FILTER_OPTIONS_KEY, filters.date_from, filters.date_to],
    queryFn: () => fetchFailureFilterOptions({
      date_from: filters.date_from,
      date_to: filters.date_to,
    }),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const summaryQuery = useQuery({
    queryKey: [...CICD_AGENT_SUMMARY_KEY, activeFilters, summaryGroup],
    queryFn: () => fetchFailureSummary(activeFilters, summaryGroup),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const stateQuery = useQuery({
    queryKey: ["state"],
    queryFn: () => apiGet<StatePayload>("/api/state"),
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const detailQuery = useQuery({
    queryKey: ["cicd-agent", "failure-detail", selectedId],
    queryFn: () => fetchFailureDetail(selectedId as number),
    enabled: selectedId != null,
    staleTime: Infinity,
    refetchOnWindowFocus: false,
  });

  const filterOptions = optionsQuery.data;
  const displayNames = stateQuery.data?.user_display_names ?? {};
  const records = recordsQuery.data?.records ?? [];
  const pageInfo = recordsQuery.data;
  const allSummaryGroups = summaryQuery.data?.groups ?? [];
  const summaryGroups = summaryExpanded
    ? allSummaryGroups
    : allSummaryGroups.slice(0, SUMMARY_COLLAPSED_LIMIT);
  const currentUsername = user?.username ?? "";
  const onlyMine = Boolean(currentUsername && filters.code_owner === currentUsername);
  const currentSummaryFilterKey = summaryFilterKey(summaryGroup);
  const currentSummaryFilterValue = String(filters[currentSummaryFilterKey] ?? "");
  const codeOwnerOptions = useMemo(
    () => uniqueOptions([
      ...(filterOptions?.owners ?? []).map((o) => o.code_owner),
      filters.code_owner,
    ]),
    [filterOptions?.owners, filters.code_owner],
  );

  function updateFilter(key: keyof FailureRecordFilters, value: string) {
    setPage(1);
    setSelectedId(null);
    setFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleOnlyMine(checked: boolean) {
    if (!currentUsername) return;
    updateFilter("code_owner", checked ? currentUsername : "");
  }

  function applySummaryFilter(groupValue: string) {
    updateFilter(summaryFilterKey(summaryGroup), groupValue === "N/A" ? "" : groupValue);
  }

  function clearSummaryFilter() {
    updateFilter(currentSummaryFilterKey, "");
  }

  function changeSummaryGroup(value: SummaryGroupKey) {
    setSummaryGroup(value);
    setSummaryExpanded(false);
  }

  function resetFilters() {
    setPage(1);
    setSelectedId(null);
    setSummaryExpanded(false);
    setFilters(emptyFilters());
  }

  function refresh() {
    void queryClient.invalidateQueries({ queryKey: ["cicd-agent"] });
  }

  async function submitResponsibilityFeedback(
    recordId: number,
    payload: FailureResponsibilityFeedbackPayload,
  ) {
    const result = await submitFailureResponsibilityFeedback(recordId, payload);
    queryClient.setQueryData(["cicd-agent", "failure-detail", recordId], result.record);
    await queryClient.invalidateQueries({ queryKey: ["cicd-agent"] });
  }

  async function resolveResponsibility(
    recordId: number,
    payload: FailureResponsibilityResolutionPayload,
  ) {
    const result = await resolveFailureResponsibility(recordId, payload);
    queryClient.setQueryData(["cicd-agent", "failure-detail", recordId], result.record);
    await queryClient.invalidateQueries({ queryKey: ["cicd-agent"] });
  }

  const closeDetail = useCallback(() => {
    setSelectedId(null);
  }, []);

  useEffect(() => {
    if (selectedId == null) return undefined;
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") closeDetail();
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [closeDetail, selectedId]);

  return (
    <section className="view active cicd-agent-view">
      <div className="page-toolbar">
        <h2>Jenkins失败查询</h2>
        <span className="muted small">匹配记录：{pageInfo?.total ?? summaryQuery.data?.total_records ?? 0}</span>
        <label className="check cicd-agent-own-toggle">
          <input
            type="checkbox"
            checked={onlyMine}
            disabled={!currentUsername}
            onChange={(event) => toggleOnlyMine(event.target.checked)}
          />
          只看我的
        </label>
        <div className="spacer" />
        <button className="btn sm" type="button" onClick={refresh}>刷新</button>
        <button className="btn ghost sm" type="button" onClick={resetFilters}>重置</button>
      </div>

      <section className="panel cicd-agent-filter-panel">
        <div className="cicd-agent-filters">
          <FilterInput
            label="开始时间"
            type="date"
            value={filters.date_from}
            onChange={(v) => updateFilter("date_from", v)}
          />
          <FilterInput
            label="结束时间"
            type="date"
            value={filters.date_to}
            onChange={(v) => updateFilter("date_to", v)}
          />
          <FilterSelect
            label="Code Owner"
            value={filters.code_owner}
            options={codeOwnerOptions}
            formatOption={(v) => userDisplayLabel(v, displayNames)}
            onChange={(v) => updateFilter("code_owner", v)}
          />
          <FilterSelect
            label="责任人"
            value={filters.owner_account}
            options={filterOptions?.owner_accounts ?? []}
            formatOption={(v) => userDisplayLabel(v, displayNames)}
            onChange={(v) => updateFilter("owner_account", v)}
          />
          <FilterSelect
            label="镜像版本"
            value={filters.maca_version}
            options={filterOptions?.maca_versions ?? []}
            onChange={(v) => updateFilter("maca_version", v)}
          />
          <FilterSelect
            label="Project"
            value={filters.maca_project}
            options={filterOptions?.maca_projects ?? []}
            onChange={(v) => updateFilter("maca_project", v)}
          />
          <FilterSelect
            label="Chip"
            value={filters.chip}
            options={filterOptions?.chips ?? []}
            onChange={(v) => updateFilter("chip", v)}
          />
          <FilterSelect
            label="Stage"
            value={filters.normalized_stage}
            options={filterOptions?.stages ?? []}
            onChange={(v) => updateFilter("normalized_stage", v)}
          />
          <FilterSelect
            label="Job_Type"
            value={filters.job_type}
            options={filterOptions?.job_types ?? []}
            onChange={(v) => updateFilter("job_type", v)}
          />
          <FilterSelect
            label="认定状态"
            value={filters.responsibility_status}
            options={filterOptions?.responsibility_statuses ?? []}
            formatOption={responsibilityStatusLabel}
            onChange={(v) => updateFilter("responsibility_status", v)}
          />
        </div>
      </section>

      <section className="cicd-agent-summary-section">
        <div className="cicd-agent-summary-head">
          <div>
            <h3>聚合</h3>
            <span className="muted small">
              显示 {summaryGroups.length} / {allSummaryGroups.length} 个分组
            </span>
          </div>
          <div className="cicd-agent-summary-actions">
            {currentSummaryFilterValue && (
              <button className="btn sm" type="button" onClick={clearSummaryFilter}>
                返回上层
              </button>
            )}
            <select
              value={summaryGroup}
              onChange={(event) => changeSummaryGroup(event.target.value as SummaryGroupKey)}
              aria-label="聚合维度"
            >
              {SUMMARY_GROUP_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>{option.label}</option>
              ))}
            </select>
          </div>
        </div>
        {currentSummaryFilterValue && (
          <div className="cicd-agent-summary-crumb">
            当前聚合筛选：{summaryGroupLabel(summaryGroup)} ={" "}
            <b>{summaryDisplayValue(summaryGroup, currentSummaryFilterValue, displayNames)}</b>
          </div>
        )}
        <div className="cicd-agent-summary-grid">
          {summaryGroups.length ? summaryGroups.map((group) => (
            <button
              type="button"
              key={group.group}
              className="panel cicd-agent-summary-card"
              onClick={() => applySummaryFilter(group.group)}
            >
              <span>{summaryDisplayValue(summaryGroup, group.group, displayNames)}</span>
              <strong>{group.failure_count}</strong>
              <small>{group.job_count} jobs · {group.project_count} projects</small>
            </button>
          )) : (
            <div className="panel muted center p-2r">暂无聚合数据</div>
          )}
        </div>
        {allSummaryGroups.length > SUMMARY_COLLAPSED_LIMIT && (
          <div className="cicd-agent-summary-more">
            <button
              className="btn sm"
              type="button"
              onClick={() => setSummaryExpanded((value) => !value)}
            >
              {summaryExpanded ? "收起" : `显示全部 ${allSummaryGroups.length} 个分组`}
            </button>
          </div>
        )}
      </section>

      <section className="panel cicd-agent-records">
        <div className="section-head-row">
          <h3>失败记录</h3>
          <div className="pager">
            <button
              className="btn sm"
              type="button"
              disabled={page <= 1}
              onClick={() => setPage((v) => Math.max(1, v - 1))}
            >
              上一页
            </button>
            <span className="muted small">{page} / {Math.max(pageInfo?.total_pages ?? 0, 1)}</span>
            <button
              className="btn sm"
              type="button"
              disabled={!pageInfo?.total_pages || page >= pageInfo.total_pages}
              onClick={() => setPage((v) => v + 1)}
            >
              下一页
            </button>
          </div>
        </div>
        <div className="table-scroll">
          <table className="data-table cicd-agent-table">
            <thead>
              <tr>
                <th>ID</th>
                <th>官方名称</th>
                <th>版本</th>
                <th>仓库/分支</th>
                <th>Project/Chip</th>
                <th>镜像版本</th>
                <th>Job</th>
                <th>Stage</th>
                <th>Code Owner</th>
                <th>责任人/角色</th>
                <th>原因摘要</th>
                <th>时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {records.map((record) => (
                <tr
                  key={record.id}
                  className={selectedId === record.id ? "selected" : ""}
                  onClick={() => setSelectedId(record.id)}
                >
                  <td>#{record.id}</td>
                  <td><b>{fmt(record.official_name)}</b></td>
                  <td>{fmt(record.app_version)}</td>
                  <td><b>{fmt(record.git_url)}</b><small>{fmt(record.git_branch)}</small></td>
                  <td><b>{fmt(record.maca_project)}</b><small>{fmt(record.chip)}</small></td>
                  <td>{fmt(record.maca_version)}</td>
                  <td><b>{record.job_name}</b><small>#{record.build_number} · {fmt(record.job_type)}</small></td>
                  <td>{fmt(record.normalized_stage || record.failed_stage)}</td>
                  <td>{userDisplayLabel(record.code_owner, displayNames)}</td>
                  <td>
                    <b>{ownerLabel(record, displayNames)}</b>
                    <div className="cicd-agent-owner-tags">
                      <small className={`pill ${ownerRoleClass(effectiveOwnerRole(record))}`}>
                        {fmt(effectiveOwnerRole(record))}
                      </small>
                      {record.responsibility_status && record.responsibility_status !== "ai_suggested" && (
                        <small className={`pill ${responsibilityStatusClass(record.responsibility_status)}`}>
                          {responsibilityStatusLabel(record.responsibility_status)}
                        </small>
                      )}
                    </div>
                  </td>
                  <td className="reason-cell">{effectiveReason(record) || "暂无摘要"}</td>
                  <td>{formatDateTime(record.created_at)}</td>
                  <td className="cicd-agent-table-action">
                    <button
                      className="btn sm cicd-agent-detail-trigger"
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        setSelectedId(record.id);
                      }}
                    >
                      <span>查看详情</span>
                      <span aria-hidden="true">›</span>
                    </button>
                  </td>
                </tr>
              ))}
              {!records.length && (
                <tr>
                  <td colSpan={13} className="muted center">
                    {recordsQuery.isLoading ? "正在加载..." : "没有匹配记录"}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      {recordsQuery.error && (
        <div className="error-banner">
          加载失败：{recordsQuery.error instanceof Error ? recordsQuery.error.message : String(recordsQuery.error)}
        </div>
      )}

      {selectedId != null ? (
        <div
          className="dialog-backdrop cicd-agent-detail-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="failure-detail-title"
          onClick={closeDetail}
        >
          <FailureDetail
            record={detailQuery.data}
            loading={detailQuery.isFetching}
            displayNames={displayNames}
            canResolve={user?.role === "RM"}
            onFeedback={submitResponsibilityFeedback}
            onResolve={resolveResponsibility}
            onClose={closeDetail}
          />
        </div>
      ) : null}
    </section>
  );
}

function FilterInput({ label, type, value, onChange }: {
  label: string;
  type: string;
  value?: string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <input type={type} value={value ?? ""} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function FilterSelect({ label, value, options, formatOption, onChange }: {
  label: string;
  value?: string;
  options: string[];
  formatOption?: (value: string) => string;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      {label}
      <select value={value ?? ""} onChange={(event) => onChange(event.target.value)}>
        <option value="">全部</option>
        {options.filter(Boolean).map((option) => (
          <option key={option} value={option}>{formatOption ? formatOption(option) : option}</option>
        ))}
      </select>
    </label>
  );
}

function FailureDetail({
  record,
  loading,
  displayNames,
  canResolve,
  onFeedback,
  onResolve,
  onClose,
}: {
  record?: FailureRecordDetail;
  loading: boolean;
  displayNames: Record<string, string>;
  canResolve: boolean;
  onFeedback: (recordId: number, payload: FailureResponsibilityFeedbackPayload) => Promise<void>;
  onResolve: (recordId: number, payload: FailureResponsibilityResolutionPayload) => Promise<void>;
  onClose: () => void;
}) {
  const [actionMode, setActionMode] = useState<"feedback" | "resolution" | null>(null);
  const [responsibilityExpanded, setResponsibilityExpanded] = useState(false);
  const [feedbackForm, setFeedbackForm] = useState<FailureResponsibilityFeedbackPayload>({
    feedback_type: "owner_wrong",
    reason: "",
    suggested_owner_account: "",
    suggested_owner_role: "Code Owner",
    evidence: "",
  });
  const [resolutionForm, setResolutionForm] = useState<FailureResponsibilityResolutionPayload>({
    action: "confirm",
    final_owner_account: "",
    final_owner_role: "DevOps",
    final_reason_summary: "",
    final_action_suggestion: "",
    note: "",
  });
  const [submitting, setSubmitting] = useState<"feedback" | "resolution" | null>(null);
  const [formError, setFormError] = useState<string | null>(null);

  useEffect(() => {
    if (!record) return;
    const suggestion = latestFeedbackSuggestion(record);
    const feedbackRole = suggestion?.role ?? normalizeResponsibilityRole(effectiveOwnerRole(record));
    const feedbackAccount = suggestion?.account ?? responsibilityOwnerAccount(record, feedbackRole);
    const resolutionRole = (
      record.responsibility_status === "disputed" && suggestion
        ? suggestion.role
        : normalizeResponsibilityRole(effectiveOwnerRole(record))
    );
    setActionMode(null);
    setResponsibilityExpanded(false);
    setFormError(null);
    setFeedbackForm({
      feedback_type: "owner_wrong",
      reason: "",
      suggested_owner_account: feedbackAccount,
      suggested_owner_role: feedbackRole,
      evidence: "",
    });
    setResolutionForm({
      action: record.responsibility_status === "disputed" ? "correct" : "confirm",
      final_owner_account: responsibilityOwnerAccount(record, resolutionRole),
      final_owner_role: resolutionRole,
      final_reason_summary: record.final_reason_summary || "",
      final_action_suggestion: record.final_action_suggestion || "",
      note: "",
    });
  }, [record]);

  function openFeedback() {
    setResponsibilityExpanded(true);
    setFormError(null);
    setActionMode((value) => value === "feedback" ? null : "feedback");
  }

  function openResolution() {
    setResponsibilityExpanded(true);
    setFormError(null);
    setActionMode((value) => value === "resolution" ? null : "resolution");
  }

  function toggleResponsibilityChain() {
    setResponsibilityExpanded((value) => {
      const nextValue = !value;
      if (!nextValue) {
        setActionMode(null);
        setFormError(null);
      }
      return nextValue;
    });
  }

  async function submitFeedback(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record) return;
    if (!String(feedbackForm.reason || "").trim()) {
      setFormError("请填写反馈原因");
      return;
    }
    if (!String(feedbackForm.suggested_owner_account || "").trim()) {
      setFormError("请选择建议责任归属");
      return;
    }
    setSubmitting("feedback");
    setFormError(null);
    try {
      await onFeedback(record.id, feedbackForm);
      setActionMode(null);
    } catch (error) {
      setFormError(errorText(error));
    } finally {
      setSubmitting(null);
    }
  }

  async function submitResolution(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!record) return;
    if (resolutionForm.action === "correct") {
      if (!String(resolutionForm.final_owner_account || "").trim()) {
        setFormError("请选择最终责任归属");
        return;
      }
      if (!String(resolutionForm.final_owner_role || "").trim()) {
        setFormError("请选择最终责任角色");
        return;
      }
    }
    setSubmitting("resolution");
    setFormError(null);
    try {
      await onResolve(record.id, resolutionForm);
      setActionMode(null);
    } catch (error) {
      setFormError(errorText(error));
    } finally {
      setSubmitting(null);
    }
  }

  if (!record) {
    return (
      <section
        className="panel p-2r cicd-agent-detail-dialog"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="section-head-row">
          <h3 id="failure-detail-title">记录详情</h3>
          <button className="btn sm cicd-agent-detail-close" type="button" onClick={onClose}>
            <span aria-hidden="true">×</span>
            关闭
          </button>
        </div>
        <p className="muted">{loading ? "正在加载..." : "没有找到记录详情"}</p>
      </section>
    );
  }

  const latestSuggestion = latestFeedbackSuggestion(record);

  return (
    <section
      className="panel p-2r cicd-agent-detail-dialog"
      onClick={(event) => event.stopPropagation()}
    >
      <div className="section-head-row">
        <h3 id="failure-detail-title">记录详情</h3>
        <div className="cicd-agent-detail-actions">
          {record.build_url && (
            <a className="btn sm jenkins-link" href={record.build_url} target="_blank" rel="noreferrer">
              打开 Jenkins
            </a>
          )}
          <button
            className="btn sm cicd-agent-feedback-trigger"
            type="button"
            onClick={openFeedback}
          >
            责任反馈
          </button>
          {canResolve && (
            <button
              className="btn sm cicd-agent-resolution-trigger"
              type="button"
              onClick={openResolution}
            >
              处理责任
            </button>
          )}
          <button
            className="btn ghost sm cicd-agent-chain-trigger"
            type="button"
            aria-expanded={responsibilityExpanded}
            onClick={toggleResponsibilityChain}
          >
            责任链
          </button>
          <button className="btn sm cicd-agent-detail-close" type="button" onClick={onClose}>
            <span aria-hidden="true">×</span>
            关闭
          </button>
        </div>
      </div>
      <div className="cicd-agent-responsibility-strip">
        <span>责任</span>
        <b>{ownerLabel(record, displayNames)}</b>
        <small>{fmt(effectiveOwnerRole(record))}</small>
        <small className={`pill ${responsibilityStatusClass(record.responsibility_status)}`}>
          {responsibilityStatusLabel(record.responsibility_status)}
        </small>
      </div>
      {responsibilityExpanded && (
        <section className="cicd-agent-responsibility-panel">
        <div className="cicd-agent-responsibility-head">
          <div>
            <h4>责任链</h4>
            <span className="muted small">
              当前状态：
              <b className={`pill ${responsibilityStatusClass(record.responsibility_status)}`}>
                {responsibilityStatusLabel(record.responsibility_status)}
              </b>
            </span>
          </div>
        </div>
        <div className="cicd-agent-responsibility-grid">
          <Detail label="AI建议责任人" value={userDisplayLabel(record.owner_account, displayNames)} />
          <Detail label="AI建议角色" value={record.owner_role} />
          <Detail
            label="反馈建议责任人"
            value={latestSuggestion ? userDisplayLabel(latestSuggestion.account, displayNames) : null}
          />
          <Detail label="反馈建议角色" value={latestSuggestion?.role} />
          <Detail label="最终责任人" value={userDisplayLabel(record.final_owner_account, displayNames)} />
          <Detail label="最终责任角色" value={record.final_owner_role} />
          <Detail label="最近处理人" value={userDisplayLabel(record.responsibility_updated_by, displayNames)} />
          <Detail label="最近处理时间" value={formatDateTime(record.responsibility_updated_at)} />
          <Detail label="责任备注" value={record.responsibility_note} wide />
          <Detail label="AI定责依据" value={record.route_reason || record.owner_reason} wide />
        </div>

        {actionMode === "feedback" && (
          <form className="cicd-agent-responsibility-form" onSubmit={(event) => void submitFeedback(event)}>
            <div className="cicd-agent-form-grid">
              <label>
                反馈类型
                <select
                  value={feedbackForm.feedback_type}
                  onChange={(event) => setFeedbackForm((current) => ({
                    ...current,
                    feedback_type: event.target.value,
                  }))}
                >
                  {FEEDBACK_TYPE_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>{option.label}</option>
                  ))}
                </select>
              </label>
              <label>
                建议责任归属
                <select
                  value={normalizeResponsibilityRole(feedbackForm.suggested_owner_role)}
                  onChange={(event) => setFeedbackForm((current) => ({
                    ...current,
                    suggested_owner_role: event.target.value as ResponsibilityOwnerRole,
                    suggested_owner_account: responsibilityOwnerAccount(
                      record,
                      event.target.value as ResponsibilityOwnerRole,
                    ),
                  }))}
                >
                  {RESPONSIBILITY_OWNER_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {responsibilityChoiceLabel(record, role, displayNames)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              反馈原因
              <textarea
                value={feedbackForm.reason}
                onChange={(event) => setFeedbackForm((current) => ({
                  ...current,
                  reason: event.target.value,
                }))}
                rows={3}
                placeholder="说明为什么当前责任认定不准确"
              />
            </label>
            <label>
              证据/补充日志
              <textarea
                value={feedbackForm.evidence ?? ""}
                onChange={(event) => setFeedbackForm((current) => ({
                  ...current,
                  evidence: event.target.value,
                }))}
                rows={3}
                placeholder="可选，贴关键日志、链接或沟通结论"
              />
            </label>
            <FormActions
              submitting={submitting === "feedback"}
              submitLabel="提交反馈"
              onCancel={() => {
                setActionMode(null);
                setFormError(null);
              }}
            />
          </form>
        )}

        {actionMode === "resolution" && canResolve && (
          <form className="cicd-agent-responsibility-form" onSubmit={(event) => void submitResolution(event)}>
            <div className="cicd-agent-form-grid">
              <label>
                处理方式
                <select
                  value={resolutionForm.action}
                  onChange={(event) => setResolutionForm((current) => ({
                    ...current,
                    action: event.target.value as FailureResponsibilityResolutionPayload["action"],
                    ...(
                      event.target.value === "correct"
                        ? rolePayload(record, normalizeResponsibilityRole(current.final_owner_role))
                        : {}
                    ),
                  }))}
                >
                  <option value="confirm">确认 AI 判断正确</option>
                  <option value="correct">修正最终责任人</option>
                  <option value="unresolved">暂无法判定</option>
                </select>
              </label>
              <label>
                最终责任归属
                <select
                  value={normalizeResponsibilityRole(resolutionForm.final_owner_role)}
                  disabled={resolutionForm.action !== "correct"}
                  onChange={(event) => setResolutionForm((current) => ({
                    ...current,
                    ...rolePayload(record, event.target.value as ResponsibilityOwnerRole),
                  }))}
                >
                  {RESPONSIBILITY_OWNER_ROLES.map((role) => (
                    <option key={role} value={role}>
                      {responsibilityChoiceLabel(record, role, displayNames)}
                    </option>
                  ))}
                </select>
              </label>
            </div>
            <label>
              最终原因摘要
              <textarea
                value={resolutionForm.final_reason_summary ?? ""}
                onChange={(event) => setResolutionForm((current) => ({
                  ...current,
                  final_reason_summary: event.target.value,
                }))}
                rows={3}
                placeholder="可选；填写后周报和查询会优先使用这里"
              />
            </label>
            <label>
              最终处理建议
              <textarea
                value={resolutionForm.final_action_suggestion ?? ""}
                onChange={(event) => setResolutionForm((current) => ({
                  ...current,
                  final_action_suggestion: event.target.value,
                }))}
                rows={3}
                placeholder="可选；填写后周报和查询会优先使用这里"
              />
            </label>
            <label>
              处理备注
              <textarea
                value={resolutionForm.note ?? ""}
                onChange={(event) => setResolutionForm((current) => ({
                  ...current,
                  note: event.target.value,
                }))}
                rows={3}
                placeholder="记录确认依据或改派原因"
              />
            </label>
            <FormActions
              submitting={submitting === "resolution"}
              submitLabel="保存处理"
              onCancel={() => {
                setActionMode(null);
                setFormError(null);
              }}
            />
          </form>
        )}

        {formError && <div className="error-banner cicd-agent-form-error">{formError}</div>}

        {(record.responsibility_events ?? []).length > 0 && (
          <div className="cicd-agent-responsibility-events">
            <h5>处理历史</h5>
            {(record.responsibility_events ?? []).slice(0, 6).map((event) => (
              <div key={event.id} className="cicd-agent-responsibility-event">
                <b>{responsibilityStatusLabel(event.to_status)}</b>
                <span>
                  {userDisplayLabel(event.actor, displayNames)} · {fmt(event.actor_role)} ·{" "}
                  {formatDateTime(event.created_at)}
                </span>
                <p>
                  {event.reason || event.evidence || event.feedback_type || "无备注"}
                  {feedbackSuggestionLabel(record, event, displayNames)
                    ? `；建议：${feedbackSuggestionLabel(record, event, displayNames)}`
                    : ""}
                </p>
              </div>
            ))}
          </div>
        )}
        </section>
      )}
      <div className="cicd-agent-detail-grid">
        <Detail label="Job" value={`${record.job_name} #${record.build_number}`} />
        <Detail label="Stage" value={record.normalized_stage || record.failed_stage} />
        <Detail label="Code Owner" value={userDisplayLabel(record.code_owner, displayNames)} />
        <Detail label="责任人" value={ownerLabel(record, displayNames)} />
        <Detail label="责任角色" value={effectiveOwnerRole(record)} />
        <Detail label="官方名称" value={record.official_name} />
        <Detail label="版本" value={record.app_version} />
        <Detail label="仓库" value={record.git_url} />
        <Detail label="分支" value={record.git_branch} />
        <Detail label="Project/Chip" value={`${fmt(record.maca_project)} / ${fmt(record.chip)}`} />
        <Detail label="镜像版本" value={record.maca_version} />
        <Detail label="Rule" value={record.matched_rule_id} />
        <Detail label="原因摘要" value={effectiveReason(record)} wide />
        <Detail label="关键错误" value={record.key_error_snippet} wide preserve />
        <Detail label="建议" value={effectiveAction(record)} wide />
      </div>
    </section>
  );
}

function FormActions({ submitting, submitLabel, onCancel }: {
  submitting: boolean;
  submitLabel: string;
  onCancel: () => void;
}) {
  return (
    <div className="cicd-agent-form-actions">
      <button className="btn primary sm" type="submit" disabled={submitting}>
        {submitting ? "保存中..." : submitLabel}
      </button>
      <button className="btn ghost sm" type="button" disabled={submitting} onClick={onCancel}>
        取消
      </button>
    </div>
  );
}

function Detail({ label, value, wide = false, preserve = false }: {
  label: string;
  value?: string | null;
  wide?: boolean;
  preserve?: boolean;
}) {
  return (
    <div className={wide ? "detail-item wide" : "detail-item"}>
      <span>{label}</span>
      <p className={preserve ? "preserve" : ""}>{fmt(value)}</p>
    </div>
  );
}
