/**
 * Hand-over machine choice: the agent picks from the group's system machine
 * list, or the user gives user@host after uploading server B's public key.
 * Reports "" (auto), the target, or null while the choice cannot be handed over.
 */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { SshKeyUploadDialog } from "./SshKeyUploadDialog";
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
  const queryClient = useQueryClient();
  const [mode, setMode] = useState<"auto" | "custom">("auto");
  const [target, setTarget] = useState("");
  const [uploading, setUploading] = useState(false);

  const machinesQuery = useQuery({ queryKey: JIRA_AGENT_MACHINES_KEY, queryFn: listMachines });
  const keyQuery = useQuery({
    queryKey: jiraAgentSshKeyInfoKey(agentGroup),
    queryFn: () => getSshKeyInfo(agentGroup),
    enabled: mode === "custom" && Boolean(agentGroup),
  });

  const machines = (machinesQuery.data?.machines ?? []).filter((machine) => machine.agent_group === agentGroup);
  const trimmed = target.trim();
  const validTarget = SSH_TARGET_RE.test(trimmed);
  const verified = validTarget && (keyQuery.data?.verified_targets ?? []).includes(trimmed);

  let machine: string | null = null;
  if (mode === "auto") machine = machines.length > 0 ? "" : null;
  else if (verified) machine = trimmed;
  useEffect(() => {
    onChange(machine);
  }, [machine, onChange]);

  return (
    <>
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
            <div className="row">
              <input
                aria-label="自填机器"
                placeholder="例如 hpctest@10.2.118.80，请使用专用测试账号"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              />
              {verified ? (
                <span className="pill ok">已上传公钥并通过连接测试</span>
              ) : (
                <button
                  type="button"
                  className="btn sm"
                  disabled={!validTarget || !keyQuery.data}
                  onClick={() => setUploading(true)}
                >
                  上传 SSH 公钥
                </button>
              )}
            </div>
            {trimmed && !validTarget && <p className="jira-agent-warning">请填写 user@host，不带端口。</p>}
            {validTarget && !verified && keyQuery.data && (
              <p className="muted">需要先上传服务器 B 的 SSH 公钥并通过连接测试，才能交给 agent。</p>
            )}
            {keyQuery.isError && <p className="jira-agent-warning">{errorMessage(keyQuery.error)}</p>}
          </>
        )}
      </fieldset>
      {uploading && keyQuery.data && (
        <SshKeyUploadDialog
          target={trimmed}
          keyInfo={keyQuery.data}
          onClose={() => {
            setUploading(false);
            void queryClient.invalidateQueries({ queryKey: jiraAgentSshKeyInfoKey(agentGroup) });
          }}
        />
      )}
    </>
  );
}
