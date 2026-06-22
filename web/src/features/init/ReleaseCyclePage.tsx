/**
 * 周期管理 (Release Cycle / init) tab — index.html:614-682, JS:1983+/4989+
 *
 * RM-only. Two sub-panes:
 *   发布周期  — create release, edit deadlines, final-lock/unlock, releases table
 *   首次初始化 — CSV import bootstrap
 *
 * Data: GET /api/state (shared selectedReleaseId from uiStore)
 * Mutations:
 *   POST /api/releases/create
 *   POST /api/releases/deadlines
 *   POST /api/releases/final-lock
 *   POST /api/releases/final-unlock
 *   POST /api/import-initial
 *
 * R2: explicit refetch only — no polling.
 */
import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshBar } from "../../components/RefreshBar";
import { apiGet, apiPost } from "../../api/http";
import { useUiStore } from "../../store/uiStore";
import { formatServerTime } from "../../lib/time";
import type { StatePayload, ReleaseSummary } from "../../types";

// ---------------------------------------------------------------------------
// Query key + fetcher (mirrors appWorkbench / dashboard pattern)
// ---------------------------------------------------------------------------

const STATE_KEY = (releaseId?: string) =>
  releaseId ? ["state", releaseId] : ["state"];

async function fetchState(releaseId?: string): Promise<StatePayload> {
  const qs = releaseId ? `?release_id=${encodeURIComponent(releaseId)}` : "";
  return apiGet<StatePayload>(`/api/state${qs}`);
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Normalize deadline input/display to YYYY-MM-DD. */
function toDateValue(s: string | null | undefined): string {
  const normalized = String(s ?? "").trim().replace(/[/.]/g, "-");
  const formatted = formatServerTime(normalized);
  return formatted ? formatted.substring(0, 10) : "";
}

// ---------------------------------------------------------------------------
// Releases table (mirrors index.html:1983-1994)
// ---------------------------------------------------------------------------

interface ReleasesTableProps {
  releases: ReleaseSummary[];
}

function ReleasesTable({ releases }: ReleasesTableProps) {
  const phaseLabels: Record<string, string> = {
    before_app_freeze: "App 冻结前",
    after_app_freeze: "已冻结",
    released: "已发布",
  };

  if (!releases.length) {
    return <p className="muted small">暂无发布周期数据。</p>;
  }

  return (
    <div className="table" data-testid="releases-table">
      <table>
        <thead>
          <tr>
            <th>Release</th>
            <th>阶段</th>
            <th>App 冻结</th>
            <th>Doc deadline</th>
            <th>Lock</th>
            <th>来源</th>
          </tr>
        </thead>
        <tbody>
          {releases.map((r) => (
            <tr key={r.id}>
              <td>{r.name}</td>
              <td>{phaseLabels[r.phase] ?? r.phase}</td>
              <td>{toDateValue(r.app_freeze_deadline) || "—"}</td>
              <td>{toDateValue(r.doc_deadline) || "—"}</td>
              <td>
                {r.released_locked
                  ? `已锁 (${formatServerTime(r.released_locked_at) || ""})`
                  : "未锁"}
              </td>
              <td>{r.source || "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 发布周期 sub-pane
// ---------------------------------------------------------------------------

interface ReleaseCyclePaneProps {
  releases: ReleaseSummary[];
  currentRelease: ReleaseSummary | null;
  onMutated: (newReleaseId?: string) => void;
}

function ReleaseCyclePane({ releases, currentRelease, onMutated }: ReleaseCyclePaneProps) {
  // Create-new form
  const [newName, setNewName] = useState("");
  const [newAppFreeze, setNewAppFreeze] = useState("");
  const [newDocDeadline, setNewDocDeadline] = useState("");
  const [creating, setCreating] = useState(false);
  const [createErr, setCreateErr] = useState("");

  // Edit-current form — syncs from currentRelease
  const [editName, setEditName] = useState(currentRelease?.name ?? "");
  const [editAppFreeze, setEditAppFreeze] = useState(
    toDateValue(currentRelease?.app_freeze_deadline),
  );
  const [editDocDeadline, setEditDocDeadline] = useState(
    toDateValue(currentRelease?.doc_deadline),
  );
  const [saving, setSaving] = useState(false);
  const [saveErr, setSaveErr] = useState("");

  // Keep edit form in sync when the selected release changes
  useEffect(() => {
    setEditName(currentRelease?.name ?? "");
    setEditAppFreeze(toDateValue(currentRelease?.app_freeze_deadline));
    setEditDocDeadline(toDateValue(currentRelease?.doc_deadline));
    setSaveErr("");
  }, [currentRelease?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  async function handleCreate() {
    if (!newName.trim()) return setCreateErr("请填写新 Release 名称");
    setCreateErr("");
    setCreating(true);
    try {
      const r = await apiPost<{ release_id: string }>("/api/releases/create", {
        name: newName.trim(),
        app_freeze_deadline: newAppFreeze || "",
        doc_deadline: newDocDeadline || "",
      });
      setNewName("");
      setNewAppFreeze("");
      setNewDocDeadline("");
      onMutated(r.release_id);
      alert("新 Release 已创建");
    } catch (e) {
      setCreateErr(e instanceof Error ? e.message : "创建失败");
    } finally {
      setCreating(false);
    }
  }

  async function handleSaveDeadlines() {
    if (!editName.trim()) return setSaveErr("Release 名称不能为空");
    if (!currentRelease) return;
    setSaveErr("");
    setSaving(true);
    try {
      await apiPost("/api/releases/deadlines", {
        release_id: currentRelease.id,
        name: editName.trim(),
        app_freeze_deadline: editAppFreeze || "",
        doc_deadline: editDocDeadline || "",
      });
      onMutated();
      alert("Release 设置已更新");
    } catch (e) {
      setSaveErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  }

  async function handleFinalLock() {
    if (!currentRelease) return;
    if (!window.confirm("确认最终锁定？锁定后所有信息冻结，仅可由 RM 解锁。")) return;
    try {
      await apiPost("/api/releases/final-lock", { release_id: currentRelease.id });
      onMutated();
      alert("已最终锁定。");
    } catch (e) {
      alert(`最终 Lock 失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  async function handleFinalUnlock() {
    if (!currentRelease) return;
    if (!window.confirm("确认要解锁？解锁后最终 artifacts 将被清除。")) return;
    try {
      await apiPost("/api/releases/final-unlock", { release_id: currentRelease.id });
      onMutated();
    } catch (e) {
      alert(`解锁失败：${e instanceof Error ? e.message : String(e)}`);
    }
  }

  function handleExportCsv() {
    if (!currentRelease) return;
    window.open(
      `/api/test-scope.csv?release_id=${encodeURIComponent(currentRelease.id)}`,
      "_blank",
    );
  }

  return (
    <div data-testid="release-cycle-pane">
      {/* Create new release */}
      <div className="panel">
        <div className="panel-head"><h2>新建发布周期</h2></div>
        <div className="panel-body">
          <div className="form form-compact">
            <label>
              新 Release 名称
              <input
                className="input"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                data-testid="new-release-name"
              />
            </label>
            <label>
              App 冻结 deadline（北京时间）
              <input
                className="input"
                type="text"
                inputMode="numeric"
                pattern="\d{4}-\d{2}-\d{2}"
                placeholder="YYYY-MM-DD"
                value={newAppFreeze}
                onChange={(e) => setNewAppFreeze(e.target.value)}
                onBlur={() => setNewAppFreeze(toDateValue(newAppFreeze))}
                data-testid="new-app-freeze"
              />
            </label>
            <label>
              Doc deadline（北京时间）
              <input
                className="input"
                type="text"
                inputMode="numeric"
                pattern="\d{4}-\d{2}-\d{2}"
                placeholder="YYYY-MM-DD"
                value={newDocDeadline}
                onChange={(e) => setNewDocDeadline(e.target.value)}
                onBlur={() => setNewDocDeadline(toDateValue(newDocDeadline))}
                data-testid="new-doc-deadline"
              />
            </label>
          </div>
          {createErr && <p className="lerr" data-testid="create-err">{createErr}</p>}
          <div className="row" style={{ marginTop: 13 }}>
            <button
              className="btn primary"
              onClick={() => void handleCreate()}
              disabled={creating}
              data-testid="create-release-btn"
            >
              {creating ? "创建中…" : "从上版克隆新 Release"}
            </button>
          </div>
          <p className="hint">
            两个 deadline 决定权限：app 冻结前可任意新增 / 调整 release 决策；冻结后只能把决策切到
            cicd_only / stopped；doc deadline 前可继续编辑表单 / 上传 app_info；doc deadline 后只能
            QA 操作。
          </p>
        </div>
      </div>

      {/* Edit current release deadlines */}
      <div className="panel">
        <div className="panel-head"><h2>当前 Release 设置</h2></div>
        <div className="panel-body">
          {!currentRelease ? (
            <p className="muted small">暂无当前 Release 数据。</p>
          ) : (
            <>
              <div className="form form-compact">
                <label>
                  Release 名称
                  <input
                    className="input"
                    value={editName}
                    onChange={(e) => setEditName(e.target.value)}
                    data-testid="edit-release-name"
                  />
                </label>
                <label>
                  App 冻结 deadline
                  <input
                    className="input"
                    type="text"
                    inputMode="numeric"
                    pattern="\d{4}-\d{2}-\d{2}"
                    placeholder="YYYY-MM-DD"
                    value={editAppFreeze}
                    onChange={(e) => setEditAppFreeze(e.target.value)}
                    onBlur={() => setEditAppFreeze(toDateValue(editAppFreeze))}
                    data-testid="edit-app-freeze"
                  />
                </label>
                <label>
                  Doc deadline
                  <input
                    className="input"
                    type="text"
                    inputMode="numeric"
                    pattern="\d{4}-\d{2}-\d{2}"
                    placeholder="YYYY-MM-DD"
                    value={editDocDeadline}
                    onChange={(e) => setEditDocDeadline(e.target.value)}
                    onBlur={() => setEditDocDeadline(toDateValue(editDocDeadline))}
                    data-testid="edit-doc-deadline"
                  />
                </label>
              </div>
              {saveErr && <p className="lerr">{saveErr}</p>}
              <div className="row" style={{ marginTop: 13 }}>
                <button
                  className="btn"
                  onClick={() => void handleSaveDeadlines()}
                  disabled={saving}
                  data-testid="save-deadlines-btn"
                >
                  {saving ? "保存中…" : "保存 Release 设置"}
                </button>
                <button
                  className="btn primary"
                  onClick={handleExportCsv}
                  data-testid="export-csv-btn"
                >
                  导出测试范围 CSV
                </button>
              </div>
            </>
          )}
        </div>
      </div>

      {/* Final lock / unlock */}
      <div className="panel">
        <div className="panel-head"><h2>最终发布锁</h2></div>
        <div className="panel-body">
          <p className="hint" style={{ marginTop: 0 }}>
            Manager 在 Gerrit 上 merge 完报告后，由 RM 执行最终锁，所有信息从此冻结。
          </p>
          <div className="row" style={{ marginTop: 11 }}>
            <button
              className="btn danger"
              onClick={() => void handleFinalLock()}
              disabled={!currentRelease}
              data-testid="final-lock-btn"
            >
              最终 Lock Release
            </button>
            <button
              className="btn warn"
              onClick={() => void handleFinalUnlock()}
              disabled={!currentRelease}
              data-testid="final-unlock-btn"
            >
              解锁 Release
            </button>
          </div>
          <ReleasesTable releases={releases} />
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 首次初始化 sub-pane
// ---------------------------------------------------------------------------

interface InitImportPaneProps {
  onMutated: (newReleaseId?: string) => void;
}

function InitImportPane({ onMutated }: InitImportPaneProps) {
  const [csvFile, setCsvFile] = useState<File | null>(null);
  const [releaseName, setReleaseName] = useState("");
  const [importing, setImporting] = useState(false);
  const [log, setLog] = useState("");

  async function handleImport() {
    if (!csvFile) return setLog("请选择初始化 CSV 文件");
    setLog("导入中...");
    setImporting(true);
    try {
      const csv = await csvFile.text();
      if (!csv.trim()) return setLog("请选择初始化 CSV 文件");
      const r = await apiPost<{ release_id: string }>("/api/import-initial", {
        csv,
        release_name: releaseName,
      });
      setLog(`导入完成：${r.release_id}`);
      onMutated(r.release_id);
    } catch (e) {
      setLog(`导入失败：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setImporting(false);
    }
  }

  return (
    <div className="panel" data-testid="init-import-pane">
      <div className="panel-head"><h2>首次初始化</h2></div>
      <div className="panel-body">
        <p className="hint" style={{ marginTop: 0 }}>
          导入一个 CSV 即可（列：类别, id, 名称, Owner, 类型, 描述, git_url,
          git_branch）。同一 (git_url, git_branch) 视为同一个 app；后续 release 从上一版本克隆。
        </p>
        <div className="form" style={{ gridTemplateColumns: "1fr 1fr", marginTop: 12 }}>
          <label>
            初始化 CSV
            <input
              className="input"
              type="file"
              accept=".csv"
              onChange={(e) => setCsvFile(e.target.files?.[0] ?? null)}
              data-testid="init-csv-input"
            />
          </label>
          <label>
            Release 名称
            <input
              className="input"
              value={releaseName}
              onChange={(e) => setReleaseName(e.target.value)}
              placeholder="留空则用 maca_version"
              data-testid="init-release-name"
            />
          </label>
        </div>
        <div className="row" style={{ marginTop: 13 }}>
          <button
            className="btn primary"
            onClick={() => void handleImport()}
            disabled={importing}
            data-testid="import-btn"
          >
            {importing ? "导入中…" : "导入初始化数据"}
          </button>
        </div>
        {log && (
          <div className="log" data-testid="import-log">{log}</div>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ReleaseCyclePage — root
// ---------------------------------------------------------------------------

type SubPane = "releaseCyclePane" | "initialImportPane";

export function ReleaseCyclePage() {
  const queryClient = useQueryClient();
  const [subPane, setSubPane] = useState<SubPane>("releaseCyclePane");

  // Shared release selector — same pattern as AppWorkbenchPage / DashboardPage.
  // "" in store becomes undefined so the bootstrap query fires.
  const selectedReleaseId = useUiStore((s) => s.selectedReleaseId) || undefined;
  const setSelectedReleaseId = useUiStore((s) => s.setSelectedReleaseId);

  const queryKey = STATE_KEY(selectedReleaseId);

  const { data, isFetching, dataUpdatedAt, refetch, error } = useQuery({
    queryKey,
    queryFn: () => fetchState(selectedReleaseId),
    staleTime: Infinity,
    refetchInterval: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    refetchOnMount: true,
  });

  // Seed shared store + specific cache key after first load.
  useEffect(() => {
    if (data?.release?.id && !selectedReleaseId) {
      queryClient.setQueryData(STATE_KEY(data.release.id), data);
      setSelectedReleaseId(data.release.id);
    }
  }, [data, data?.release?.id, selectedReleaseId, setSelectedReleaseId, queryClient]);

  const releases = data?.releases ?? [];
  const currentRelease = data?.release ?? null;

  // After a mutation, invalidate the current key and refetch; optionally
  // switch to a newly-created release.
  function handleMutated(newReleaseId?: string) {
    if (newReleaseId && newReleaseId !== selectedReleaseId) {
      // Seed cache then flip release selector → triggers a new fetch with the new key.
      setSelectedReleaseId(newReleaseId);
      void queryClient.invalidateQueries({ queryKey: STATE_KEY(newReleaseId) });
    } else {
      void refetch();
    }
  }

  if (error) {
    return (
      <section className="view active" data-testid="release-cycle-page">
        <p className="muted" style={{ padding: "1rem" }}>
          加载失败：{(error as Error).message}
        </p>
      </section>
    );
  }

  return (
    <section className="view active" data-testid="release-cycle-page">
      <div className="page-toolbar">
        <h2>周期管理</h2>
        {releases.length > 0 && (
          <select
            className="input"
            style={{ width: "auto", minWidth: 160 }}
            value={selectedReleaseId ?? currentRelease?.id ?? ""}
            onChange={(e) => setSelectedReleaseId(e.target.value)}
            aria-label="选择 release"
          >
            {releases.map((r) => (
              <option key={r.id} value={r.id}>
                {r.name}
                {r.maca_version ? ` (${r.maca_version})` : ""}
              </option>
            ))}
          </select>
        )}
        <span className="spacer" />
        <RefreshBar
          dataUpdatedAt={dataUpdatedAt}
          isFetching={isFetching}
          onRefresh={() => void refetch()}
        />
      </div>

      {/* Sub-tab switcher (mirrors index.html:621-624) */}
      <div className="subtabs">
        <button
          className={`subtab${subPane === "releaseCyclePane" ? " active" : ""}`}
          onClick={() => setSubPane("releaseCyclePane")}
          data-testid="subtab-cycle"
        >
          发布周期
        </button>
        <button
          className={`subtab${subPane === "initialImportPane" ? " active" : ""}`}
          onClick={() => setSubPane("initialImportPane")}
          data-testid="subtab-import"
        >
          首次初始化
        </button>
      </div>

      {isFetching && !data ? (
        <div style={{ padding: "1rem" }} className="muted">加载中…</div>
      ) : (
        <>
          {subPane === "releaseCyclePane" && (
            <ReleaseCyclePane
              releases={releases}
              currentRelease={currentRelease}
              onMutated={handleMutated}
            />
          )}
          {subPane === "initialImportPane" && (
            <InitImportPane onMutated={handleMutated} />
          )}
        </>
      )}
    </section>
  );
}
