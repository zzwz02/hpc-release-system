import { useQuery } from "@tanstack/react-query";
import { useAuth } from "../../api/AuthContext";
import { isRM } from "../../lib/roles";
import { listAgentModels } from "./jiraAgentApi";

export interface ModelSelection { model_provider?: string; model?: string; effort?: string }

export function ModelChoice({ group, value, onChange, disabled = false, lockProvider = false }: {
  group: string; value: ModelSelection; onChange: (value: ModelSelection) => void;
  disabled?: boolean; lockProvider?: boolean;
}) {
  const { user } = useAuth();
  const rm = isRM(user);
  const query = useQuery({
    queryKey: ["jira-agent-models", group, user?.username, user?.role],
    queryFn: () => listAgentModels(group),
    enabled: rm && Boolean(group) && !disabled,
  });
  if (!rm) return null;
  const legacy = lockProvider && !value.model_provider;
  const providerId = legacy ? "" : value.model_provider || query.data?.default_provider || "";
  const provider = query.data?.providers.find((item) => item.id === providerId);
  const models = query.data?.models.filter((model) => model.provider === providerId) ?? [];
  const selected = models.find((model) => model.model === (value.model || provider?.default_model));
  const defaultEffort = value.model ? selected?.defaultReasoningEffort : provider?.default_effort;
  return (
    <div className="jira-agent-model-choice">
      <label>服务商 <select aria-label="服务商" value={providerId} disabled={disabled || lockProvider || !query.data}
        onChange={(event) => onChange({ model_provider: event.target.value, model: "", effort: "" })}>
        {!provider && <option value={providerId}>{providerId || (legacy ? "原对话服务商待确认" : "加载中…")}</option>}
        {query.data?.providers.map((item) => <option key={item.id} value={item.id}>
          {item.name}{item.id === query.data.default_provider ? " (default)" : ""}
        </option>)}
      </select></label>
      <label>模型 <select aria-label="模型" value={value.model || ""} disabled={disabled || legacy || !provider}
        onChange={(event) => onChange({ model_provider: providerId, model: event.target.value, effort: "" })}>
        <option value="">{provider?.default_model ? `${provider.default_model} (default)` : query.isError ? "未能读取默认模型" : "加载中…"}</option>
        {value.model && !selected && <option value={value.model}>{value.model}</option>}
        {models.map((model) => <option key={model.model} value={model.model}>{model.displayName}</option>)}
      </select></label>
      <label>effort <select aria-label="effort" value={value.effort || ""} disabled={disabled || legacy || !selected}
        onChange={(event) => onChange({ ...value, model_provider: providerId, effort: event.target.value })}>
        <option value="">{defaultEffort ? `${defaultEffort} (default)` : query.isError ? "未能读取默认 effort" : "加载中…"}</option>
        {value.effort && !selected?.supportedReasoningEfforts.some((e) => e.reasoningEffort === value.effort) && <option value={value.effort}>{value.effort}</option>}
        {selected?.supportedReasoningEfforts.map((effort) => (
          <option key={effort.reasoningEffort} value={effort.reasoningEffort}>{effort.reasoningEffort}</option>
        ))}
      </select></label>
      <span className="muted">{legacy ? "旧对话的服务商将在续办时确认" : lockProvider ? "切换服务商需新建对话" : "服务商在本对话内固定"}</span>
      <button type="button" className="btn" disabled={query.isFetching} onClick={() => void query.refetch()}>刷新模型</button>
      {query.isError && <span className="error">模型列表加载失败：{String(query.error)}</span>}
      {query.data?.errors.map((error) => <span className="error" key={error.provider}>{error.provider}：{error.message}</span>)}
    </div>
  );
}
