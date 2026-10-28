import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { JIRA_AGENT_MACHINES_KEY, SSH_TARGET_RE, defaultExecution, listMachines, type ExecutionConfig } from "./jiraAgentApi";

export function MachineChoice({ agentGroup, disabled, onChange, initialValue }: {
  agentGroup: string; disabled: boolean; onChange: (value: ExecutionConfig | null) => void; initialValue?: ExecutionConfig;
}) {
  const [value, setValue] = useState<ExecutionConfig>(() => ({ ...(initialValue ?? defaultExecution()), selection_policy: "as_needed", dataset_requirement: "auto" }));
  const [rows, setRows] = useState(initialValue?.targets.length ? initialValue.targets : [{ target: "", purpose: "" }]);
  const machinesQuery = useQuery({ queryKey: JIRA_AGENT_MACHINES_KEY, queryFn: listMachines });
  const count = (machinesQuery.data?.machines ?? []).filter(m => m.agent_group === agentGroup).length;
  const normalized = useMemo(() => rows.map(row => {
    const parts = row.target.trim().split("@");
    return { target: parts.length === 2 ? `${parts[0]}@${parts[1].toLowerCase()}` : row.target.trim(), purpose: row.purpose.trim() };
  }), [rows]);
  const valid = normalized.length > 0 && normalized.length <= 32 && normalized.every(row => SSH_TARGET_RE.test(row.target) && row.target.length <= 286)
    && new Set(normalized.map(row => row.target)).size === normalized.length;
  const ready = useMemo(() => value.mode === "auto" ? (count ? { ...value, targets: [] } : null)
    : valid ? { ...value, targets: normalized } : null, [value, count, valid, normalized]);
  useEffect(() => onChange(ready), [ready, onChange]);

  return <fieldset className="jira-agent-machine" disabled={disabled}>
    <legend>执行机器</legend>
    <label className="jira-agent-check"><input type="radio" name="jira-agent-machine" checked={value.mode === "auto"} onChange={() => setValue(v => ({ ...v, mode: "auto" }))} />agent 从系统机器列表自动选择<span className="muted">（本组 {count} 台，按任务选择一台）</span></label>
    {value.mode === "auto" && machinesQuery.data && !count && <p className="jira-agent-warning">系统机器列表为空，请联系 RM 添加机器，或自填 user@host。</p>}
    {machinesQuery.isError && <p role="alert">{String(machinesQuery.error)}</p>}
    <label className="jira-agent-check"><input type="radio" name="jira-agent-machine" checked={value.mode === "custom"} onChange={() => setValue(v => ({ ...v, mode: "custom" }))} />自填 user@host（可填写多台，不加入系统机器列表）</label>
    {value.mode === "custom" && <>
      {rows.map((row, index) => <div className="jira-agent-target-row" key={index}>
        <input aria-label={index === 0 ? "自填机器" : `自填机器 ${index + 1}`} placeholder="tester@host，请使用专用测试账号" value={row.target} onChange={e => setRows(rows.map((r, i) => i === index ? { ...r, target: e.target.value } : r))} />
        <input aria-label={`机器类型 ${index + 1}`} placeholder="机器类型（可选），如 C500 / C600" value={row.purpose} maxLength={500} onChange={e => setRows(rows.map((r, i) => i === index ? { ...r, purpose: e.target.value } : r))} />
        <button type="button" className="btn sm" aria-label={`删除机器 ${index + 1}`} onClick={() => setRows(rows.length === 1 ? [{ target: "", purpose: "" }] : rows.filter((_, i) => i !== index))}>删除</button>
      </div>)}
      <button type="button" className="btn sm" disabled={rows.length >= 32} onClick={() => setRows([...rows, { target: "", purpose: "" }])}>添加机器</button>
      {!valid && rows.some(r => r.target) && <p className="jira-agent-warning">请逐行填写不重复的 user@host，不带端口。</p>}
      <p className="muted">交单前逐台完成登录授权，按提示输入机器账号密码；已配置免密登录时自动跳过。请使用专用测试账号。</p>
    </>}
  </fieldset>;
}
