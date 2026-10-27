import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "../../api/AuthContext";
import { Markdown } from "../../components/Markdown";
import { toast } from "../../lib/toast";
import { TestingEvaluation } from "./TestingEvaluation";
import { TestingRecords } from "./TestingRecords";
import { ConversationBody } from "./JiraAgentPage";
import { MachineChoice } from "./MachineChoice";
import { ExecutionPreflightDialog } from "./ExecutionPreflightDialog";
import { cancelConversationTurn, fileToUpload, jiraAgentConversationKey, defaultExecution, type ExecutionConfig, type ExecutionSubmission } from "./jiraAgentApi";
import {
  getTestCase, importTestCase, listTestCases, REVIEW_LABELS, reviewTestRun, saveTestCase, startTestSuite, listTestSuites, stopTestSuite, retryTestSuite, resultLabel, currentResult, draftTestCriteria,
  exportTestPackage, previewTestPackage,
  type CaseDraft, type CaseVersion, type ReviewDecision, type TestBatch, type TestRun, type TestInput,
} from "./jiraTestingApi";

const blank = (): CaseDraft => ({ title: "", input: { issue: { key: "TEST-1", summary: "", description: "", attachments: [], comments: [] }, note: "", files: [] }, provenance: {} });
const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const isActive = (run: TestRun) => ["queued", "running"].includes(run.status) || run.delivery_status === "collecting";
const reviewTone = (run: TestRun) => ({ pass: "ok", fail: "bad", partial: "warn" })[currentResult(run).decision as "pass" | "fail" | "partial"] ?? "";
type ReviewDraft = { decision: ReviewDecision | ""; note: string; editing: boolean };

export function TestingWorkspace() {
  const { user } = useAuth();
  const client = useQueryClient();
  const [params, setParams] = useSearchParams();
  const id = params.get("case") ?? "";
  const imported = params.get("import") ?? "";
  const selected = params.get("run") ?? "";
  const identity = `${user?.username}:${user?.role}`;
  const options = useQuery({ queryKey: ["jira-testing", identity, "cases"], queryFn: listTestCases });
  const detail = useQuery({ queryKey: ["jira-testing", identity, id], queryFn: () => getTestCase(id), enabled: !!id });
  const suites = useQuery({ queryKey: ["jira-testing", identity, "suites"], queryFn: listTestSuites,
    refetchInterval: (query) => query.state.data?.suites.some((item) => item.active) ? 3000 : false });
  const records = suites.data?.suites.filter((item) => !id || item.entries.some((entry) => entry.case_id === id)) ?? [];
  const suite = records.find((item) => item.id === params.get("suite")) ?? records.find((item) => item.entries.some((entry) => entry.batch?.runs.some((run) => run.conversation_id === selected)));
  const entry = suite?.entries.filter((item) => !id || item.case_id === id).flatMap((item) => item.batch ? item.batch.runs.map((run) => ({ batch: item.batch!, run, caseId: item.case_id, title: item.title })) : []).find((item) => item.run.conversation_id === selected);
  const selectedCase = useQuery({ queryKey: ["jira-testing", identity, entry?.caseId], queryFn: () => getTestCase(entry!.caseId), enabled: !!entry });
  const [chosen, setChosen] = useState<string[]>([]);
  const submitting = useRef(false);
  const pending = useRef<string[]>([]);
  const source = useQuery({ queryKey: ["jira-testing", identity, "import", imported], queryFn: () => importTestCase(imported), enabled: !!imported, retry: false, staleTime: 0, refetchOnMount: "always" });
  const [draft, setDraft] = useState<CaseDraft | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const [transfer, setTransfer] = useState("");
  const [packageError, setPackageError] = useState("");
  const [editing, setEditing] = useState("");
  const [busy, setBusy] = useState(false);
  const [configuring, setConfiguring] = useState(false);
  const [failure, setFailure] = useState("");
  const [filter, setFilter] = useState("");
  const [groupName, setGroupName] = useState("");
  const [machine, setMachine] = useState<ExecutionConfig | null>(defaultExecution);
  const [uploading, setUploading] = useState(false);
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("xhigh");
  const [reviews, setReviews] = useState<Record<string, ReviewDraft>>({});
  const [folds, setFolds] = useState<Record<string, boolean>>({});
  const group = options.data?.groups.find((item) => item.name === groupName) ?? options.data?.groups[0];
  const current = detail.data?.versions[0];
  const visible = options.data?.cases.filter((item) => item.title.toLowerCase().includes(filter.trim().toLowerCase())) ?? [];
  const picked = options.data?.cases.filter((item) => chosen.includes(item.id)) ?? [];
  const hidden = picked.filter((item) => !visible.some((shown) => shown.id === item.id)).length;
  const occupied = new Set(suites.data?.suites.flatMap((item) => item.entries.filter((entry) => item.status === "preparing" || entry.batch?.runs.some(isActive)).map((entry) => entry.case_id)) ?? []);
  const conflicts = picked.filter((item) => occupied.has(item.id));

  useEffect(() => { if (source.data) { setDraft(source.data); setEditing(""); } }, [source.data]);
  const selectedConversation = entry?.run.conversation_id;
  const selectedStatus = entry?.run.status;
  const selectedDelivery = entry?.run.delivery_status;
  useEffect(() => {
    if (selected && selectedConversation) void client.invalidateQueries({ queryKey: jiraAgentConversationKey(selectedConversation) });
  }, [client, selected, selectedConversation, selectedStatus, selectedDelivery]);
  const refresh = useCallback(() => {
    void client.invalidateQueries({ queryKey: ["jira-testing", identity] });
    if (selected && selected !== "none") void client.invalidateQueries({ queryKey: jiraAgentConversationKey(selected) });
  }, [client, identity, selected]);

  function navigate(caseId: string, runId?: string) {
    setParams({ view: "testing", ...(caseId ? { case: caseId } : {}), ...(runId ? { tab: "records", run: runId } : {}) });
    setDraft(null);
  }
  async function start(ids: string[], submission?: ExecutionSubmission) {
    if (!group || machine === null || submitting.current || !ids.length) return;
    if (!submission) { pending.current = ids; setUploading(true); return; }
    const body = { case_ids: ids, agent_group: group.name, model: model || group.model, effort, ...submission };
    setFailure(""); setBusy(true); submitting.current = true;
    try {
      const result = await startTestSuite(body);
      setUploading(false);
      await suites.refetch();
      setParams({ view: "testing", tab: "records", suite: result.id });
    } finally { setBusy(false); submitting.current = false; }
  }
  async function save(value: CaseDraft) {
    const result = await saveTestCase(value, editing);
    client.setQueryData(["jira-testing", identity, result.id], result);
    await options.refetch();
    navigate(result.id);
    if (result.existing) toast.success("相同测试输入已存在，已打开已有用例。");
  }
  async function importPackage(file: File) {
    setPackageError(""); setTransfer("import");
    try {
      if (file.size > 41 * 1024 * 1024) throw new Error("用例包超过 41MB 限制");
      const value = await previewTestPackage(file);
      setParams({ view: "testing" }); setEditing(""); setDraft(value);
    } catch (error) { setPackageError(errorText(error)); } finally { setTransfer(""); }
  }
  async function exportPackage() {
    if (!current || transfer) return;
    setTransfer("export"); setPackageError("");
    try {
      const blob = await exportTestPackage(id, current.id);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a"); link.href = url;
      link.download = `${current.title.replace(/[\\/:*?"<>|]/g, "_").slice(0, 80)}.zip`;
      document.body.appendChild(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) { setPackageError(errorText(error)); } finally { setTransfer(""); }
  }
  async function suiteAction(action: "stop" | "retry") {
    if (!suite) return;
    try { await (action === "stop" ? stopTestSuite(suite.id) : retryTestSuite(suite.id)); refresh(); }
    catch (error) { toast.error(errorText(error)); }
  }
  function selectSuite(value: string) {
    setParams((previous) => { const next = new URLSearchParams(previous); next.set("tab", "records"); next.delete("run"); value ? next.set("suite", value) : next.delete("suite"); return next; });
  }
  return <section className="view active jira-agent jira-testing-workspace">
    <div className="page-toolbar"><div><h2>测试用例</h2><p className="hint">按保存的输入进行完整分析，结果不回写 Jira。</p></div><button className="btn" onClick={() => setParams({ view: "handled" })}>返回工单</button></div>
    {failure && <p role="alert" className="jira-agent-warning">{failure}</p>}
    {options.isError && <p role="alert">{errorText(options.error)}</p>}
    {options.data?.builtin_warning && <p role="alert" className="jira-agent-warning">{options.data.builtin_warning}</p>}
    {source.isFetching && <p role="status">正在读取测试输入…</p>}
    {source.isError && <p role="alert">{errorText(source.error)}</p>}
    <div className="test-layout">
      <aside className="panel test-sidebar" aria-label="测试用例">
        <div className="test-sidebar-heading"><h3>用例</h3><div className="actions">
          <button className="test-text-button" disabled={!!transfer || !!draft} onClick={() => { setDraft(blank()); setEditing(""); }}>新建用例</button>
          <button className="test-text-button" disabled={!!transfer || !!draft} onClick={() => picker.current?.click()}>{transfer === "import" ? "正在导入…" : "导入用例"}</button>
          <input ref={picker} type="file" accept=".zip" aria-label="上传用例包" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void importPackage(file); event.target.value = ""; }} />
        </div></div>
        <button className={`test-overview-button ${!id && !draft ? "selected" : ""}`} aria-current={!id && !draft ? "page" : undefined} disabled={!!draft} onClick={() => navigate("")}><span>测试汇总</span><span aria-hidden="true">→</span></button>
        <div className="test-sidebar-filter"><input aria-label="筛选用例" placeholder="按名称筛选用例" value={filter} onChange={(event) => setFilter(event.target.value)} /></div>
        <div className="test-selection-heading"><label><input type="checkbox" aria-label="选择当前列表全部用例" disabled={!visible.length} checked={!!visible.length && visible.every((item) => chosen.includes(item.id))} ref={(element) => { if (element) element.indeterminate = visible.some((item) => chosen.includes(item.id)) && !visible.every((item) => chosen.includes(item.id)); }} onChange={(event) => setChosen((previous) => event.target.checked ? [...new Set([...previous, ...visible.map((item) => item.id)])] : previous.filter((value) => !visible.some((item) => item.id === value)))} />全选</label><span>{visible.length} 个用例</span></div>
        <ul className="test-sidebar-list" aria-label="用例列表">
          {options.isPending && <li role="status" className="empty">正在读取用例…</li>}
          {!options.isPending && !visible.length && <li className="empty">没有符合条件的用例。</li>}
          {visible.map((item) => {
            const latest = suites.data?.suites.flatMap((record) => record.entries).find((record) => record.case_id === item.id && record.batch)?.batch?.runs[0];
            return <li key={item.id} className={`test-sidebar-case ${id === item.id ? "selected" : ""}`}>
              <input type="checkbox" aria-label={`选择 ${item.title}`} checked={chosen.includes(item.id)} onChange={(event) => setChosen((previous) => event.target.checked ? [...previous, item.id] : previous.filter((value) => value !== item.id))} />
              <button className="test-case-button" aria-current={id === item.id ? "page" : undefined} disabled={!!draft} onClick={() => navigate(item.id)}><span>{item.title}</span>
                <small className={occupied.has(item.id) ? "test-running" : "muted"}>{occupied.has(item.id) ? "排队或执行中" : latest ? resultLabel(latest) : "尚未测试"}{!item.criteria?.trim() && " · 未设置通过标准"}</small>
              </button>
            </li>;
          })}
        </ul>
        <div className="test-sidebar-footer">
          <div className="test-selection-heading"><strong>已选 {picked.length} 个</strong>{!!picked.length && <button className="test-text-button" onClick={() => setChosen([])}>清空选择</button>}</div>
          {!!hidden && <p className="hint">有 {hidden} 个被筛选隐藏，仍会一起测试。</p>}
          {picked.some((item) => !item.criteria?.trim()) && <p className="hint">未设置通过标准的用例可执行，AI 不判断通过。</p>}
          {!!conflicts.length && <p className="hint">{conflicts.length} 个所选用例正在测试，请等待或取消勾选。</p>}
          <div className="test-start-actions"><button className="btn primary" disabled={busy || !!draft || !picked.length || !group || machine === null || !!conflicts.length} onClick={() => void start(picked.map((item) => item.id))}>{busy ? "正在提交…" : `开始测试（${picked.length}）`}</button><button className="test-text-button" disabled={busy} onClick={() => setConfiguring(true)}>测试设置</button></div>
        </div>
      </aside>
      <div className="test-main">
        {packageError && <p role="alert" className="jira-agent-warning">{packageError}</p>}
        {draft ? <CaseEditor draft={draft} save={save} close={() => setDraft(null)} openCase={navigate} group={group?.name ?? ""} /> : <section className="panel test-workspace" aria-label={id ? "用例详情" : "测试汇总"}>
          <div className="panel-head test-case-heading"><h3>{id ? current?.title || "用例详情" : "测试汇总"}</h3>
            {id && current && <div className="actions"><button className="test-text-button" disabled={!!transfer} onClick={() => void exportPackage()}>{transfer === "export" ? "正在导出…" : "导出用例"}</button><button className="btn sm" onClick={() => { setDraft(current); setEditing(id); }}>修改用例</button></div>}
          </div>
          <div className="panel-body">
            {id && detail.isPending && <p role="status">正在读取用例…</p>}{id && detail.isError && <p role="alert">{errorText(detail.error)}</p>}
            {id && current && <div className="test-case-input" key={id}><CaseInput value={current} label="用例内容与通过标准" /></div>}
            {id && <h3 className="test-history-heading">测试记录</h3>}
            {suites.isPending && <p role="status">正在读取测试记录…</p>}{suites.isError && <p role="alert">{errorText(suites.error)}</p>}
            {suites.isSuccess && <TestingRecords records={records} caseId={id} suite={suite} selected={selected} selectSuite={selectSuite}
              selectRun={(value) => setParams((previous) => { const next = new URLSearchParams(previous); if (id) next.delete("suite"); else if (suite) next.set("suite", suite.id); value ? next.set("run", value) : next.delete("run"); return next; })}
              action={(value) => void suiteAction(value)} rerun={(ids) => void start(ids)} busy={busy}>
              {entry && <RunDetail key={selected} title={entry.title} close={() => setParams((previous) => { const next = new URLSearchParams(previous); next.delete("run"); return next; })} batch={entry.batch} run={entry.run} input={selectedCase.data?.versions.find((version) => version.id === entry.batch.version_id)} refresh={refresh}
                review={reviews[selected]} setReview={(value) => setReviews((previous) => ({ ...previous, [selected]: value }))}
                processOpen={folds[selected] ?? false} toggleProcess={(open) => setFolds((previous) => ({ ...previous, [selected]: open }))} />}
            </TestingRecords>}
          </div>
        </section>}
      </div>
    </div>
    <TestDialog title="测试设置" open={configuring} close={() => setConfiguring(false)}>
      {configuring && <>
        {(options.data?.groups.length ?? 0) > 1 && <label>分析组<select aria-label="分析组" value={group?.name ?? ""} onChange={(event) => { setGroupName(event.target.value); setMachine(defaultExecution()); setModel(""); }}>{options.data?.groups.map((item) => <option key={item.name}>{item.name}</option>)}</select></label>}
        {group && <MachineChoice key={group.name} agentGroup={group.name} initialValue={machine ?? undefined} disabled={busy} onChange={setMachine} />}
        <div className="test-controls"><label>模型<input type="text" aria-label="模型" value={model || group?.model || ""} onChange={(event) => setModel(event.target.value)} /></label>
          <label>推理强度<select aria-label="推理强度" value={effort} onChange={(event) => setEffort(event.target.value)}>{["low", "medium", "high", "xhigh"].map((item) => <option key={item}>{item}</option>)}</select></label></div>
        <p className="hint">使用分析服务当前的 skill。设置应用于下一次提交的整批测试。</p>
        <div className="dialog-actions"><button className="btn primary" disabled={machine === null || !(model || group?.model)} onClick={() => setConfiguring(false)}>完成</button></div>
      </>}
    </TestDialog>
    {uploading && machine && group && <ExecutionPreflightDialog agentGroup={group.name} execution={machine}
      context={{ kind: "suite", case_ids: pending.current }} onReady={submission => start(pending.current, submission)} onClose={() => setUploading(false)} />}

  </section>;
}

function TestDialog({ title, open, close, children, className = "" }: { title: string; open: boolean; close: () => void; children: ReactNode; className?: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  return <dialog ref={dialog} className={`test-dialog ${className}`} aria-label={title} onCancel={(event) => { event.preventDefault(); close(); }}>
    <div className="test-dialog-heading"><h3>{title}</h3><button className="btn sm" aria-label={`关闭${title}`} onClick={close}>关闭</button></div>
    <div className="test-dialog-body">{children}</div>
  </dialog>;
}

function RunDetail({ title, close, batch, run, input, refresh, review, setReview, processOpen, toggleProcess }: {
  title: string; close: () => void; batch: TestBatch; run: TestRun; input?: CaseVersion; refresh: () => void;
  review?: ReviewDraft; setReview: (value: ReviewDraft) => void; processOpen: boolean; toggleProcess: (open: boolean) => void;
}) {
  const [busy, setBusy] = useState(false);
  async function cancel() {
    setBusy(true);
    try { await cancelConversationTurn(run.conversation_id); refresh(); }
    catch (error) { toast.error(errorText(error)); } finally { setBusy(false); }
  }
  return <section id="test-run-detail" className="test-run-detail" aria-label="测试详情">
    <div className="test-run-heading test-detail-heading"><h3>{title}</h3><div className="actions">
      {["queued", "running"].includes(run.status) && <button className="btn danger sm" disabled={busy} onClick={() => void cancel()}>取消测试</button>}
      <button className="test-text-button" onClick={close}>收起详情</button></div>
    </div>
    <RunReview run={run} refresh={refresh} draft={review} update={setReview} />
    {run.error && <p className="jira-agent-warning" role="alert">{run.error}</p>}
    <TestingEvaluation run={run} refresh={refresh} />
    <ConversationBody id={run.conversation_id} onChanged={refresh} testing={{ processOpen, onProcessToggle: toggleProcess }} />
    <details className="test-run-config"><summary>本次配置</summary>
      <p>模型：{batch.config.model} · 推理强度：{batch.config.effort}</p>
      <p>选机方式：{batch.config.execution?.targets.map(t => t.target).join("、") || batch.config.machine || "agent 自动选择机器"}</p>
      {(run.machines_used?.length || run.machine_used) && <p>实际执行机器：{run.machines_used?.join("、") || run.machine_used}</p>}
      <p>{batch.config.skill_source === "analysis_service" ? "skill 服务快照" : "skill 仓库提交"}：<code>{batch.config.skill_source === "analysis_service" ? batch.config.skill_sha256 : batch.config.skill_commit}</code></p>
      <p>开始：{run.started_at || "—"} · 结束：{run.finished_at || "—"}</p>
      {run.evaluation && <><p>初评模型：{run.evaluation.config.model || "未配置"} / {run.evaluation.config.effort || "—"}</p>
        <p>初评规则：<code>{run.evaluation.config.rule_sha256 || "—"}</code></p><p>评价材料：<code>{run.evaluation.material_hash || "尚未采集"}</code></p></>}
      {input && <CaseInput value={input} label="本次测试输入" />}
    </details>

  </section>;
}

function CaseInput({ value, label, expanded = false }: { value: CaseVersion; label: string; expanded?: boolean }) {
  const description = <Markdown value={value.input.issue.description ?? ""} />;
  const content = <div className="test-input-content">
    <h4>{value.input.issue.summary}</h4>
    {expanded ? <details className="test-input-section"><summary>用例描述</summary><div className="test-input-content">{description}</div></details> : description}
    {value.criteria && <section className="test-input-criteria"><h4>通过标准</h4><ul>{value.criteria.split("\n").filter((line) => line.trim()).map((line, index) => <li key={index}>{line}</li>)}</ul></section>}
    {value.reference && <details><summary>参考结果（仅供评价）</summary><Markdown value={value.reference} /></details>}
    <div className="test-input-meta"><p>任务要求：{value.input.note || "无额外要求"}</p><p>输入文件：{value.input.files.map((file) => file.path).join("、") || "无"}</p></div>
    <details className="test-input-source"><summary>原始数据</summary><div className="test-input-source-body"><pre className="test-json">{JSON.stringify(value.input.issue, null, 2)}</pre><small>输入校验：{value.input_sha256}</small></div></details>
  </div>;
  return expanded ? content : <details className="test-input-section"><summary>{label}</summary>{content}</details>;
}

function CaseEditor({ draft, save, close, openCase, group }: {
  draft: CaseDraft; save: (value: CaseDraft) => Promise<void>; close: () => void; openCase: (id: string) => void; group: string;
}) {
  const [title, setTitle] = useState(draft.title);
  const [issue, setIssue] = useState(JSON.stringify(draft.input.issue, null, 2));
  const [note, setNote] = useState(draft.input.note);
  const [files, setFiles] = useState(draft.input.files);
  const [checked, setChecked] = useState(false);
  const [criteria, setCriteria] = useState(draft.criteria ?? "");
  const [reference, setReference] = useState(draft.reference ?? "");
  const [update, setUpdate] = useState("");
  const [suggestion, setSuggestion] = useState("");
  const [busy, setBusy] = useState(false);
  const preview = (() => { try { return JSON.parse(issue) as TestInput["issue"]; } catch { return null; } })();
  async function submit() {
    setBusy(true);
    try { await save({ ...draft, title, criteria, reference, import_update: update, input: { ...draft.input, issue: JSON.parse(issue), note, files } }); }
    catch (error) { toast.error(errorText(error)); } finally { setBusy(false); }
  }
  async function suggest() {
    setBusy(true);
    try { setSuggestion((await draftTestCriteria(group, { ...draft.input, issue: JSON.parse(issue), note, files }, reference)).criteria); }
    catch (error) { toast.error(errorText(error)); } finally { setBusy(false); }
  }
  return <div className="panel"><div className="panel-body test-editor"><h3>保存测试输入</h3>
    <p className="hint">{draft.notice || "输入仅保留复现问题所需的信息，答案、历史分析报告和已有修复不要放入输入。修改不会影响已有测试记录。"}</p>
    {draft.package_import && <>
      {draft.duplicates?.map((item) => <p className="jira-agent-warning" key={item.id}>上传包的输入已存在：{item.title}。{item.review_differs && <><strong>通过标准或参考结果不同。</strong><details><summary>查看现有标准与参考结果</summary><pre>{item.criteria || "未设置标准"}</pre><pre>{item.reference || "无参考结果"}</pre></details><label><input type="checkbox" checked={update === item.id} onChange={(event) => setUpdate(event.target.checked ? item.id : "")} />将导入内容保存为该用例的新版本</label></>} <button className="btn sm" disabled={busy} onClick={() => openCase(item.id)}>打开已有用例</button></p>)}
      {!!draft.same_name?.length && <p className="jira-agent-warning">已有同名但内容不同的用例，请修改名称后保存。</p>}
      {preview && <section aria-label="导入内容预览"><h4>{preview.summary}</h4><Markdown value={preview.description} />
        {Array.isArray(preview.comments) && preview.comments.map((comment: unknown, index: number) => (
          comment && typeof comment === "object" && "body" in comment && typeof comment.body === "string"
            ? <Markdown key={index} value={comment.body} /> : null
        ))}
      </section>}

    </>}
    <label>用例名称<input aria-label="用例名称" value={title} onChange={(event) => setTitle(event.target.value)} /></label>
    <details open={draft.package_import ? undefined : true}><summary>编辑工单快照</summary>
      <label>工单快照（JSON，可删除含答案的评论）<textarea aria-label="工单快照" rows={12} value={issue} onChange={(event) => setIssue(event.target.value)} /></label>
    </details>
    <div>输入文件：{files.length === 0 && "无"}{files.map((file) => <p key={file.path}>{file.path} <button className="btn sm" onClick={() => setFiles(files.filter((item) => item.path !== file.path))}>移除</button></p>)}</div>
    <p className="hint">移除附件时，请同时从工单快照的 attachments 中移除对应记录。</p>
    <input aria-label="测试输入文件" type="file" multiple onChange={async (event) => {
      const incoming = Array.from(event.target.files ?? []);
      try {
        if (incoming.reduce((size, file) => size + file.size, 0) > 20 * 1024 * 1024) throw new Error("文件总大小不能超过 20MB");
        const additions = await Promise.all(incoming.map(async (file) => ({ path: `uploads/${file.name}`, content_base64: (await fileToUpload(file)).content_base64 })));
        setFiles((previous) => [...previous.filter((file) => !additions.some((item) => item.path === file.path)), ...additions]);
      } catch (error) { toast.error(errorText(error)); }
    }} />
    <label>提交给模型的任务要求<textarea aria-label="任务要求" rows={3} value={note} onChange={(event) => setNote(event.target.value)} /></label>
    <section className="test-standard-editor"><div className="test-run-heading"><h4>通过标准</h4><button className="btn sm" disabled={busy || !group} onClick={() => void suggest()}>起草标准</button></div>
      <p className="hint">每行一条，写清必须达到的结果和核对依据。任务要求中也应说明所需交付。留空可运行，但不判断通过。</p>
      <textarea aria-label="通过标准" rows={5} value={criteria} onChange={(event) => setCriteria(event.target.value)} />
      {suggestion && <div className="test-criteria-suggestion"><pre>{suggestion}</pre><div className="actions"><button className="btn sm" onClick={() => { setCriteria(suggestion); setSuggestion(""); }}>使用草稿</button><button className="btn sm" onClick={() => setSuggestion("")}>放弃草稿</button></div></div>}
      <details><summary>参考结果（可选，仅供评价）</summary><textarea aria-label="参考结果" rows={4} value={reference} onChange={(event) => setReference(event.target.value)} /></details>
    </section>
    <label><input type="checkbox" checked={checked} onChange={(event) => setChecked(event.target.checked)} />已核对输入完整、不含参考答案，标准与任务要求一致</label>
    <div className="actions"><button className="btn primary" disabled={busy || !checked || !title.trim() || (!!draft.duplicates?.some((item) => item.review_differs) && !update)} onClick={() => void submit()}>保存用例</button><button className="btn" onClick={close}>取消</button></div>
  </div></div>;
}


function RunReview({ run, refresh, draft, update }: {
  run: TestRun; refresh: () => void; draft?: ReviewDraft; update: (value: ReviewDraft) => void;
}) {
  const [busy, setBusy] = useState(false);
  const latest = run.reviews.find((item) => item.current !== false);
  const effective = currentResult(run);
  const evaluation = run.evaluation;
  const value = draft ?? { decision: latest?.decision ?? "", note: latest?.note ?? "", editing: false };
  const active = isActive(run);
  const canPass = run.status === "completed" && run.delivery_status === "complete";
  async function save() {
    const decision = value.decision;
    if (!decision) return;
    setBusy(true);
    try {
      await reviewTestRun(run.conversation_id, decision, value.note, { evaluation_id: evaluation?.id, evidence_key: run.evidence_key });
      update({ decision, note: value.note, editing: false });
      refresh();
    } catch (error) { toast.error(errorText(error)); } finally { setBusy(false); }
  }
  return <section className="test-review" aria-label="人工评价">
    <div className="test-run-heading"><h3>测试评价 <span className={`pill ${reviewTone(run)}`}>{active ? "待完成" : resultLabel(run)}</span></h3>
      {!active && !value.editing && <button className="btn sm" onClick={() => update({ ...value, editing: true })}>{latest ? "修改人工评价" : "人工评价"}</button>}
    </div>
    {effective.note && <p className="test-review-note">{effective.note}<small className="muted">{latest ? `${latest.reviewer} · ${latest.created_at}` : effective.source === "ai" ? "AI 初评，人工评价可选" : ""}</small></p>}
    {!effective.note && !value.editing && <p className="muted">{active ? "测试结束后可填写评价。" : "可直接填写人工评价，不需要等待 AI 初评。"}</p>}
    {!active && value.editing && <div className="test-review-form">
      <label>评价结果 <select aria-label="人工评价" value={value.decision} onChange={(event) => update({ ...value, decision: event.target.value as ReviewDecision | "" })}>
        <option value="" disabled>请选择</option>
        {Object.entries(REVIEW_LABELS).filter(([key]) => key !== "pending").map(([key, text]) => <option key={key} value={key} disabled={key === "pass" && !canPass}>{text}</option>)}
      </select></label>
      {!canPass && <p className="hint">执行或文件交付尚未完成，不能标记为通过。</p>}
      <textarea aria-label="评价说明" rows={3} value={value.note} onChange={(event) => update({ ...value, note: event.target.value })} placeholder="简要说明判断依据。" />
      <div className="actions"><button className="btn primary" disabled={busy || !value.decision || !value.note.trim() || (value.decision === "pass" && !canPass)} onClick={() => void save()}>保存评价</button>
        <button className="btn" disabled={busy} onClick={() => update({ ...value, editing: false })}>取消</button></div>
    </div>}
    {run.reviews.length > 0 && <details><summary>评价历史</summary>{run.reviews.map((review) => <p key={review.id}><strong>{REVIEW_LABELS[review.decision]}</strong> · {review.reviewer} · {review.created_at}<br />{review.note}</p>)}</details>}
  </section>;
}
