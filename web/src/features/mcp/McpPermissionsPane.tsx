import { Fragment, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "../../lib/toast";
import { getPermissions, savePermissions, type McpPermissions } from "./mcpApi";

type GuestJiraPermission = "jira_write" | "jira_write_all";
type Draft = {
  base: McpPermissions;
  grants: Record<string, string[]>;
  guestGrants: Record<GuestJiraPermission, Map<string, boolean>>;
};

const CATEGORY_TITLES: Record<string, string> = { jira: "JIRA", wiki: "WIKI", skill: "技能" };

export function McpPermissionsPane({ viewer, active }: { viewer: string; active: boolean }) {
  const queryClient = useQueryClient();
  const queryKey = ["mcp", "permissions", viewer];
  const matrixQuery = useQuery({ queryKey, queryFn: getPermissions, enabled: active, refetchOnWindowFocus: false });
  const [draft, setDraft] = useState<Draft | null>(null);
  const [guestSearch, setGuestSearch] = useState("");
  const matrix = draft?.base ?? matrixQuery.data;
  const save = useMutation({
    mutationFn: ({ base, grants, guestGrants }: Draft) =>
      savePermissions(base.revision, grants, {
        guest_jira_write: Object.fromEntries(guestGrants.jira_write),
        guest_jira_write_all: Object.fromEntries(guestGrants.jira_write_all),
      }),
    onSuccess: (updated) => {
      queryClient.setQueryData(queryKey, updated);
      setDraft(null);
      void queryClient.invalidateQueries({ queryKey: ["mcp", "info"] });
      toast.success("MCP 权限已保存");
    },
  });

  useEffect(() => {
    if (!draft) return;
    const protect = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = ""; };
    window.addEventListener("beforeunload", protect);
    return () => window.removeEventListener("beforeunload", protect);
  }, [draft]);

  function changeDraft(update: (next: Draft) => void) {
    if (!matrix) return;
    save.reset();
    setDraft((previous) => {
      const next = {
        base: previous?.base ?? matrix,
        grants: { ...previous?.grants },
        guestGrants: {
          jira_write: new Map(previous?.guestGrants.jira_write),
          jira_write_all: new Map(previous?.guestGrants.jira_write_all),
        },
      };
      update(next);
      const guestChanged = next.guestGrants.jira_write.size || next.guestGrants.jira_write_all.size;
      return Object.keys(next.grants).length || guestChanged ? next : null;
    });
  }

  function toggle(tool: McpPermissions["tools"][number], principal: string, checked: boolean) {
    changeDraft(({ grants }) => {
      const selected = new Set(grants[tool.name] ?? tool.grants);
      if (checked) selected.add(principal);
      else selected.delete(principal);
      const changed = selected.size !== tool.grants.length || tool.grants.some((id) => !selected.has(id));
      if (changed) grants[tool.name] = [...selected];
      else delete grants[tool.name];
    });
  }

  function toggleGuest(guest: McpPermissions["guests"][number], permission: GuestJiraPermission, checked: boolean) {
    changeDraft(({ guestGrants }) => {
      const changes = guestGrants[permission];
      if (checked === guest[permission]) changes.delete(guest.username);
      else changes.set(guest.username, checked);
    });
  }

  function discard() {
    setDraft(null);
    save.reset();
    void matrixQuery.refetch();
  }

  const error = save.error ?? matrixQuery.error;
  const guestNeedle = guestSearch.trim().toLowerCase();
  const guests = (matrix?.guests ?? []).filter(({ username, display_name }) =>
    username.toLowerCase().includes(guestNeedle) || (display_name || "").toLowerCase().includes(guestNeedle));
  return (
    <div className="panel mcp-permissions" hidden={!active}>
      <div className="panel-head">
        <h3>MCP 权限</h3>
        <div className="mcp-permission-actions">
          <button className="btn sm" disabled={save.isPending || matrixQuery.isFetching} onClick={discard}>
            {draft ? "放弃修改并重读" : "刷新"}
          </button>
          <button className="btn primary sm" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>
            {save.isPending ? "保存中…" : "保存权限"}
          </button>
        </div>
      </div>
      <div className="panel-body">
        {error && <div className="pane-error" role="alert">{error.message}</div>}
        {matrixQuery.isLoading && <p className="hint">正在读取权限矩阵…</p>}
        {matrix && (
          <section className="mcp-guest-jira-permissions">
            <h4>Guest JIRA 写入权限</h4>
            <p className="hint">两项权限均允许评论和上传附件。「允许 JIRA 写入」限本人负责的 MC3 工单。「允许写入所有 JIRA」可独立开启，覆盖任意项目和经办人。搜索和读取权限仍按 Guest 角色执行。</p>
            {matrix.guests.length > 0 && (
              <input type="search" aria-label="搜索 Guest 账号" placeholder="搜索用户名或姓名"
                value={guestSearch} onChange={(event) => setGuestSearch(event.target.value)} />
            )}
            {guests.length > 0 ? (
              <div className="table mcp-guest-jira-table">
                <table aria-label="Guest JIRA 写入权限">
                  <thead>
                    <tr>
                      <th scope="col">Guest 账号</th>
                      <th scope="col">允许 JIRA 写入</th>
                      <th scope="col">允许写入所有 JIRA</th>
                    </tr>
                  </thead>
                  <tbody>
                    {guests.map((guest) => (
                      <tr key={guest.username}>
                        <th scope="row">{guest.display_name || guest.username}
                          {guest.display_name && guest.display_name !== guest.username && (
                            <div className="muted small">{guest.username}</div>
                          )}
                        </th>
                        <td><input type="checkbox" aria-label={`允许 ${guest.username} 写入 JIRA`}
                          checked={draft?.guestGrants.jira_write.get(guest.username) ?? guest.jira_write}
                          disabled={save.isPending}
                          onChange={(event) => toggleGuest(guest, "jira_write", event.target.checked)} /></td>
                        <td><input type="checkbox" aria-label={`允许 ${guest.username} 写入所有 JIRA`}
                          checked={draft?.guestGrants.jira_write_all.get(guest.username) ?? guest.jira_write_all}
                          disabled={save.isPending}
                          onChange={(event) => toggleGuest(guest, "jira_write_all", event.target.checked)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : <p className="hint">{matrix.guests.length ? "没有匹配的 Guest 账号" : "暂无 Guest 账号"}</p>}
          </section>
        )}
        <h4>工具权限矩阵</h4>
        <p className="hint">用户的角色或任一所属域组被勾选，即可使用该工具。域组列按名称前缀匹配。</p>
        {matrix && (
          <div className="table mcp-permission-table">
            <table aria-label="MCP 工具权限矩阵">
              <thead>
                <tr>
                  <th scope="col">工具</th>
                  <th scope="col">类型</th>
                  {matrix.columns.map((column) => (
                    <th key={column.id} scope="col">{column.kind === "group" ? <code>{column.label}</code> : column.label}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {matrix.tools.map((tool, index) => {
                  const selected = draft?.grants[tool.name] ?? tool.grants;
                  const previous = matrix.tools[index - 1];
                  const startsSection = tool.category !== previous?.category || tool.jenkins_site !== previous?.jenkins_site;
                  const sectionTitle = tool.jenkins_site
                    ? `${tool.jenkins_site.toUpperCase()} Jenkins`
                    : (CATEGORY_TITLES[tool.category] ?? tool.category);
                  return (
                    <Fragment key={tool.name}>
                      {startsSection && (
                        <tr className="mcp-permission-group">
                          <th colSpan={matrix.columns.length + 2}>{sectionTitle}</th>
                        </tr>
                      )}
                      <tr>
                        <th scope="row">
                          <strong>{tool.title}</strong>
                          <div className="muted small"><code>{tool.name}</code>{!tool.enabled && " · 服务未配置"}</div>
                        </th>
                        <td>{tool.read_only ? "只读" : "写入"}</td>
                        {matrix.columns.map((column) => (
                          <td key={column.id}>
                            <input
                              type="checkbox"
                              aria-label={`${tool.name} · ${column.label}`}
                              checked={selected.includes(column.id)}
                              disabled={save.isPending}
                              onChange={(event) => toggle(tool, column.id, event.target.checked)}
                            />
                          </td>
                        ))}
                      </tr>
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
