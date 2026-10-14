/** The manual sheet ↔ account table, the only source of the mapping (RM only). */
import { useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "../../lib/toast";
import {
  WEEKLY_CHECK_MEMBERS_KEY,
  getMembers,
  parseMemberRows,
  saveMembers,
  type Member,
} from "./weeklyReportApi";

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : String(error);
}

export function MembersDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient();
  const membersQuery = useQuery({ queryKey: WEEKLY_CHECK_MEMBERS_KEY, queryFn: getMembers });
  const loaded = membersQuery.data;
  const [rows, setRows] = useState<Member[] | null>(null);
  const [pasted, setPasted] = useState("");
  const [busy, setBusy] = useState(false);

  // Start editing from the server's copy once it arrives.
  useEffect(() => {
    if (loaded && rows === null) setRows(loaded.members);
  }, [loaded, rows]);

  const editing = rows ?? [];
  const update = (index: number, patch: Partial<Member>) =>
    setRows(editing.map((row, at) => (at === index ? { ...row, ...patch } : row)));

  function importPasted() {
    const next = [...editing];
    for (const parsed of parseMemberRows(pasted)) {
      const at = next.findIndex((row) => row.sheet.toLowerCase() === parsed.sheet.toLowerCase());
      if (at >= 0) next[at] = { ...next[at], ...parsed };
      else next.push({ ...parsed, note: "" });
    }
    setRows(next);
    setPasted("");
  }

  async function save() {
    if (!loaded) return;
    setBusy(true);
    try {
      const saved = await saveMembers(editing, loaded.version);
      queryClient.setQueryData(WEEKLY_CHECK_MEMBERS_KEY, saved);
      toast.success("手工对应已保存，下一次分析生效");
      onClose();
    } catch (error) {
      toast.error(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="dialog" aria-modal="true" aria-label="手工对应">
      <div className="dialog-card maxw-660">
        <h3>手工对应</h3>
        <div className="dialog-body">
          <p className="muted">
            周报页对应哪个 JIRA 用户名只看这张表，页名不分大小写。Project、Template 这类不是个人的页登记后勾选“不检查”。
            周报里有、这里没有的页会提示“找不到对应的用户”，只检查是否写了 JIRA ID。
          </p>
          {membersQuery.isError && <p className="weekly-report-warning">{errorMessage(membersQuery.error)}</p>}
          <div className="table-wrap">
            <table className="weekly-report-member-table" data-testid="weekly-report-member-table">
              <thead>
                <tr>
                  <th>周报页名</th>
                  <th>JIRA 用户名</th>
                  <th>不检查</th>
                  <th>备注</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {editing.map((row, index) => (
                  <tr key={index}>
                    <td>
                      <input
                        className="input sm"
                        aria-label={`第 ${index + 1} 行周报页名`}
                        value={row.sheet}
                        onChange={(event) => update(index, { sheet: event.target.value })}
                      />
                    </td>
                    <td>
                      <input
                        className="input sm"
                        aria-label={`${row.sheet || `第 ${index + 1} 行`} 的 JIRA 用户名`}
                        value={row.jira_username}
                        onChange={(event) => update(index, { jira_username: event.target.value })}
                      />
                    </td>
                    <td className="center">
                      <input
                        type="checkbox"
                        aria-label={`不检查 ${row.sheet || `第 ${index + 1} 行`}`}
                        checked={row.skip}
                        onChange={(event) => update(index, { skip: event.target.checked })}
                      />
                    </td>
                    <td>
                      <input
                        className="input sm"
                        aria-label={`${row.sheet || `第 ${index + 1} 行`} 的备注`}
                        value={row.note}
                        onChange={(event) => update(index, { note: event.target.value })}
                      />
                    </td>
                    <td>
                      <button
                        type="button"
                        className="btn sm danger"
                        onClick={() => setRows(editing.filter((_row, at) => at !== index))}
                      >
                        删除
                      </button>
                    </td>
                  </tr>
                ))}
                {rows !== null && editing.length === 0 && (
                  <tr>
                    <td colSpan={5} className="muted">
                      还没有手工对应
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
          <div>
            <button
              type="button"
              className="btn sm"
              onClick={() => setRows([...editing, { sheet: "", jira_username: "", skip: false, note: "" }])}
            >
              添加一行
            </button>
          </div>
          <label className="weekly-report-field">
            <span>批量导入：每行“页名,JIRA 用户名”，不检查的页写“页名,,不检查”；已有的页名会被更新</span>
            <textarea
              aria-label="批量导入手工对应"
              rows={3}
              placeholder={"ZhangSan,m00123\nProject,,不检查"}
              value={pasted}
              onChange={(event) => setPasted(event.target.value)}
            />
          </label>
          <div>
            <button type="button" className="btn sm" disabled={!pasted.trim()} onClick={importPasted}>
              导入到上表
            </button>
          </div>
        </div>
        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose}>
            取消
          </button>
          <button type="button" className="btn primary" disabled={busy || !loaded} onClick={() => void save()}>
            保存
          </button>
        </div>
      </div>
    </div>
  );
}
