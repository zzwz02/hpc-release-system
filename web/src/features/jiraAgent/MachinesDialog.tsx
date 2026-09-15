/** System machine list: RM maintains it, everyone can look. */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { confirmDialog } from "../../lib/confirm";
import { toast } from "../../lib/toast";
import {
  JIRA_AGENT_MACHINES_KEY,
  SSH_TARGET_RE,
  createMachine,
  deleteMachine,
  listMachines,
  updateMachine,
  type AgentMachine,
} from "./jiraAgentApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function MachinesDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const machinesQuery = useQuery({ queryKey: JIRA_AGENT_MACHINES_KEY, queryFn: listMachines });
  const data = machinesQuery.data;
  const canManage = Boolean(data?.can_manage);
  const groups = data?.groups ?? [];
  const groupLabel = new Map(groups.map((group) => [group.name, group.display_name]));

  const [group, setGroup] = useState("");
  const [target, setTarget] = useState("");
  const [description, setDescription] = useState("");
  const [editing, setEditing] = useState<{ id: string; target: string; description: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const newGroup = group || groups[0]?.name || "";
  const refresh = () => void queryClient.invalidateQueries({ queryKey: JIRA_AGENT_MACHINES_KEY });

  async function run(action: () => Promise<unknown>, done: string) {
    setBusy(true);
    try {
      await action();
      toast.success(done);
      refresh();
      return true;
    } catch (error) {
      toast.error(errorMessage(error));
      return false;
    } finally {
      setBusy(false);
    }
  }

  async function add() {
    const ok = await run(
      () => createMachine({ agent_group: newGroup, ssh_target: target.trim(), description: description.trim() }),
      "已添加机器",
    );
    if (ok) {
      setTarget("");
      setDescription("");
    }
  }

  async function save() {
    if (!editing) return;
    const ok = await run(
      () => updateMachine(editing.id, { ssh_target: editing.target.trim(), description: editing.description.trim() }),
      "已保存",
    );
    if (ok) setEditing(null);
  }

  async function remove(machine: AgentMachine) {
    if (!(await confirmDialog({ body: `从系统机器列表删除 ${machine.ssh_target}？`, danger: true, confirmText: "删除" }))) {
      return;
    }
    await run(() => deleteMachine(machine.id), "已删除");
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="系统机器">
      <div className="dialog-card maxw-660">
        <h3>系统机器</h3>
        <div className="dialog-body">
          <p className="muted">
            交单选择“自动”时，agent 按工单需要从本组列表中选一台。RM 负责提前在服务器 B 上配好到这些机器的 SSH 免密登录。
          </p>
          {machinesQuery.isError && <p className="jira-agent-warning">{errorMessage(machinesQuery.error)}</p>}
          <div className="table-wrap">
            <table className="jira-agent-machine-table" data-testid="jira-agent-machine-table">
              <thead>
                <tr>
                  <th>数字员工</th>
                  <th>机器</th>
                  <th>说明</th>
                  {canManage && <th />}
                </tr>
              </thead>
              <tbody>
                {(data?.machines ?? []).map((machine) =>
                  editing?.id === machine.id ? (
                    <tr key={machine.id}>
                      <td>{groupLabel.get(machine.agent_group) ?? machine.agent_group}</td>
                      <td>
                        <input
                          aria-label="编辑机器"
                          value={editing.target}
                          onChange={(event) => setEditing({ ...editing, target: event.target.value })}
                        />
                      </td>
                      <td>
                        <input
                          aria-label="编辑说明"
                          value={editing.description}
                          onChange={(event) => setEditing({ ...editing, description: event.target.value })}
                        />
                      </td>
                      <td className="actions">
                        <button
                          type="button"
                          className="btn sm primary"
                          disabled={busy || !SSH_TARGET_RE.test(editing.target.trim())}
                          onClick={() => void save()}
                        >
                          保存
                        </button>
                        <button type="button" className="btn sm" onClick={() => setEditing(null)}>
                          取消
                        </button>
                      </td>
                    </tr>
                  ) : (
                    <tr key={machine.id}>
                      <td>{groupLabel.get(machine.agent_group) ?? machine.agent_group}</td>
                      <td>
                        <code>{machine.ssh_target}</code>
                      </td>
                      <td className="jira-agent-pre">{machine.description || "—"}</td>
                      {canManage && (
                        <td className="actions">
                          <button
                            type="button"
                            className="btn sm"
                            onClick={() =>
                              setEditing({ id: machine.id, target: machine.ssh_target, description: machine.description })
                            }
                          >
                            编辑
                          </button>
                          <button type="button" className="btn sm danger" disabled={busy} onClick={() => void remove(machine)}>
                            删除
                          </button>
                        </td>
                      )}
                    </tr>
                  ),
                )}
                {data && data.machines.length === 0 && (
                  <tr>
                    <td colSpan={canManage ? 4 : 3} className="muted">
                      暂无系统机器
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          {canManage && (
            <div className="jira-agent-machine-form">
              {groups.length > 1 && (
                <select aria-label="数字员工组" value={newGroup} onChange={(event) => setGroup(event.target.value)}>
                  {groups.map((item) => (
                    <option key={item.name} value={item.name}>
                      {item.display_name}
                    </option>
                  ))}
                </select>
              )}
              <input
                aria-label="新机器"
                placeholder="user@host，例如 hpcagent@10.2.118.75"
                value={target}
                onChange={(event) => setTarget(event.target.value)}
              />
              <input
                aria-label="新机器说明"
                placeholder="说明：硬件、用途（agent 选机时参考）"
                value={description}
                onChange={(event) => setDescription(event.target.value)}
              />
              <button
                type="button"
                className="btn sm primary"
                disabled={busy || !newGroup || !SSH_TARGET_RE.test(target.trim())}
                onClick={() => void add()}
              >
                添加
              </button>
            </div>
          )}
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            关闭
          </button>
        </div>
      </div>
    </div>
  );
}
