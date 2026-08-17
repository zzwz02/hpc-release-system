import { useMemo, useState } from "react";
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
  todayString,
  type FailureRecordDetail,
  type FailureRecordFilters,
} from "./cicdAgentApi";

const SUMMARY_GROUP_OPTIONS = [
  { value: "normalized_stage", label: "Stage", filterKey: "normalized_stage" },
  { value: "job_type", label: "Job Type", filterKey: "job_type" },
  { value: "owner_role", label: "责任组", filterKey: "owner_role" },
  { value: "code_owner", label: "Code Owner", filterKey: "code_owner" },
  { value: "official_name", label: "Official Name", filterKey: "official_name" },
  { value: "maca_project", label: "Project", filterKey: "maca_project" },
  { value: "chip", label: "Chip", filterKey: "chip" },
  { value: "matched_rule_id", label: "Rule", filterKey: "matched_rule_id" },
] as const;

const SUMMARY_COLLAPSED_LIMIT = 12;

type SummaryGroupKey = (typeof SUMMARY_GROUP_OPTIONS)[number]["value"];

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
  return groupBy === "code_owner" ? userDisplayLabel(value, displayNames) : value;
}

function uniqueOptions(options: Array<string | null | undefined>): string[] {
  return Array.from(new Set(options.filter(Boolean) as string[]));
}

function formatDateTime(value: string | null | undefined): string {
  if (!value) return "N/A";
  return value.replace("T", " ").slice(0, 19);
}

export function JenkinsFailuresPage() {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [filters, setFilters] = useState<FailureRecordFilters>(emptyFilters);
  const [page, setPage] = useState(1);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [summaryGroup, setSummaryGroup] = useState<SummaryGroupKey>("normalized_stage");
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
                    <b>{userDisplayLabel(record.owner_account, displayNames)}</b>
                    <small className={record.owner_role === "DevOps" ? "pill accent" : "pill warnp"}>
                      {fmt(record.owner_role)}
                    </small>
                  </td>
                  <td className="reason-cell">{record.reason_summary || "暂无摘要"}</td>
                  <td>{formatDateTime(record.created_at)}</td>
                </tr>
              ))}
              {!records.length && (
                <tr>
                  <td colSpan={12} className="muted center">
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

      <FailureDetail
        record={detailQuery.data}
        loading={detailQuery.isFetching}
        displayNames={displayNames}
      />
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

function FailureDetail({ record, loading, displayNames }: {
  record?: FailureRecordDetail;
  loading: boolean;
  displayNames: Record<string, string>;
}) {
  if (!record) {
    return (
      <section className="panel p-2r">
        <h3>记录详情</h3>
        <p className="muted">{loading ? "正在加载..." : "点击一条失败记录查看详情"}</p>
      </section>
    );
  }

  return (
    <section className="panel p-2r">
      <div className="section-head-row">
        <h3>记录详情</h3>
        {record.build_url && (
          <a className="btn sm jenkins-link" href={record.build_url} target="_blank" rel="noreferrer">
            打开 Jenkins
          </a>
        )}
      </div>
      <div className="cicd-agent-detail-grid">
        <Detail label="Job" value={`${record.job_name} #${record.build_number}`} />
        <Detail label="Stage" value={record.normalized_stage || record.failed_stage} />
        <Detail label="Code Owner" value={userDisplayLabel(record.code_owner, displayNames)} />
        <Detail label="责任人" value={userDisplayLabel(record.owner_account, displayNames)} />
        <Detail label="责任角色" value={record.owner_role} />
        <Detail label="官方名称" value={record.official_name} />
        <Detail label="版本" value={record.app_version} />
        <Detail label="仓库" value={record.git_url} />
        <Detail label="分支" value={record.git_branch} />
        <Detail label="Project/Chip" value={`${fmt(record.maca_project)} / ${fmt(record.chip)}`} />
        <Detail label="镜像版本" value={record.maca_version} />
        <Detail label="Rule" value={record.matched_rule_id} />
        <Detail label="原因摘要" value={record.reason_summary} wide />
        <Detail label="关键错误" value={record.key_error_snippet} wide preserve />
        <Detail label="建议" value={record.action_suggestion} wide />
        <Detail label="责任认定" value={record.route_reason || record.owner_reason} wide />
      </div>
    </section>
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
