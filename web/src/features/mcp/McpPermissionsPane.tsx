import { Fragment, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "../../lib/toast";
import { getPermissions, savePermissions, type McpPermissions } from "./mcpApi";

type Draft = {
  base: McpPermissions;
  grants: Record<string, string[]>;
};

const CATEGORY_TITLES: Record<string, string> = { jira: "JIRA", wiki: "WIKI", skill: "技能" };

export function McpPermissionsPane({ viewer, active }: { viewer: string; active: boolean }) {
  const queryClient = useQueryClient();
  const queryKey = ["mcp", "permissions", viewer];
  const matrixQuery = useQuery({ queryKey, queryFn: getPermissions, enabled: active, refetchOnWindowFocus: false });
  const [draft, setDraft] = useState<Draft | null>(null);
  const matrix = draft?.base ?? matrixQuery.data;
  const save = useMutation({
    mutationFn: ({ base, grants }: Draft) => savePermissions(base.revision, grants),
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

  function toggle(tool: McpPermissions["tools"][number], principal: string, checked: boolean) {
    if (!matrix) return;
    save.reset();
    setDraft((previous) => {
      const grants = { ...previous?.grants };
      const selected = new Set(grants[tool.name] ?? tool.grants);
      if (checked) selected.add(principal);
      else selected.delete(principal);
      const changed = selected.size !== tool.grants.length || tool.grants.some((id) => !selected.has(id));
      if (changed) grants[tool.name] = [...selected];
      else delete grants[tool.name];
      return Object.keys(grants).length ? { base: previous?.base ?? matrix, grants } : null;
    });
  }

  function discard() {
    setDraft(null);
    save.reset();
    void matrixQuery.refetch();
  }

  const error = save.error ?? matrixQuery.error;
  return (
    <div className="panel mcp-permissions" hidden={!active}>
      <div className="panel-head">
        <h3>工具权限矩阵</h3>
        <div className="mcp-permission-actions">
          {draft && <button className="btn sm" disabled={save.isPending} onClick={discard}>放弃修改并重读</button>}
          <button className="btn primary sm" disabled={!draft || save.isPending} onClick={() => draft && save.mutate(draft)}>
            {save.isPending ? "保存中…" : "保存权限"}
          </button>
        </div>
      </div>
      <div className="panel-body">
        <p className="hint mt-0">用户的角色或任一所属域组被勾选，即可使用该工具。域组列按名称前缀匹配。</p>
        {error && <div className="pane-error" role="alert">{error.message}</div>}
        {matrixQuery.isLoading && <p className="hint">正在读取权限矩阵…</p>}
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
