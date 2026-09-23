/**
 * Hand-over machine choice: the agent picks from the group's system machine
 * list, or the user gives user@host.  A user-given machine gets server B's
 * public key uploaded (risk warning + password) on every hand-over, see
 * HandoverComposer.  Reports "" (auto), the target, or null while the choice
 * cannot be handed over.
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  JIRA_AGENT_MACHINES_KEY,
  SSH_TARGET_RE,
  getSshKeyInfo,
  jiraAgentSshKeyInfoKey,
  listMachines,
} from "./jiraAgentApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function MachineChoice({
  agentGroup,
  disabled,
  onChange,
}: {
  agentGroup: string;
  disabled: boolean;
  onChange: (machine: string | null) => void;
}) {
  const [mode, setMode] = useState<"auto" | "custom">("auto");
  const [target, setTarget] = useState("");

  const machinesQuery = useQuery({ queryKey: JIRA_AGENT_MACHINES_KEY, queryFn: listMachines });
  // server B's key: checked up front so a missing SSH_KEY_PATH shows before hand-over
  const keyQuery = useQuery({
    queryKey: jiraAgentSshKeyInfoKey(agentGroup),
    queryFn: () => getSshKeyInfo(agentGroup),
    enabled: mode === "custom" && Boolean(agentGroup),
  });

  const machines = (machinesQuery.data?.machines ?? []).filter((machine) => machine.agent_group === agentGroup);
  const trimmed = target.trim();
  const validTarget = SSH_TARGET_RE.test(trimmed);

  let machine: string | null = null;
  if (mode === "auto") machine = machines.length > 0 ? "" : null;
  else if (validTarget && keyQuery.data) machine = trimmed;
  useEffect(() => {
    onChange(machine);
  }, [machine, onChange]);

  return (
    <fieldset className="jira-agent-machine" disabled={disabled}>
      <legend>执行机器</legend>
      <label className="jira-agent-check">
        <input type="radio" name="jira-agent-machine" checked={mode === "auto"} onChange={() => setMode("auto")} />
        agent 从系统机器列表自动选择
        <span className="muted">（本组 {machines.length} 台）</span>
      </label>
      {mode === "auto" && machinesQuery.data && machines.length === 0 && (
        <p className="jira-agent-warning">系统机器列表为空，请联系 RM 添加机器，或自填 user@host。</p>
      )}
      <label className="jira-agent-check">
        <input type="radio" name="jira-agent-machine" checked={mode === "custom"} onChange={() => setMode("custom")} />
        自填 user@host（不加入系统机器列表）
      </label>
      {mode === "custom" && (
        <>
          <input
            aria-label="自填机器"
            placeholder="例如 hpctest@10.2.118.80，请使用专用测试账号"
            value={target}
            onChange={(event) => setTarget(event.target.value)}
          />
          {trimmed && !validTarget && <p className="jira-agent-warning">请填写 user@host，不带端口。</p>}
          {validTarget && keyQuery.data && (
            <p className="muted">每次交给 agent 时都会弹出风险提醒，需输入该账号密码上传服务器 B 的 SSH 公钥并通过连接测试。</p>
          )}
          {keyQuery.isError && <p className="jira-agent-warning">{errorMessage(keyQuery.error)}</p>}
        </>
      )}
    </fieldset>
  );
}
