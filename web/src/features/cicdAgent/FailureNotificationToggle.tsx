import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bell, RefreshCw } from "lucide-react";
import { apiGet, apiPost } from "../../api/http";

type Preference = { account: string; enabled: boolean; send_time: string; timezone: string };
const ENDPOINT = "/api/cicd-agent/failure-notifications/preferences";

export function FailureNotificationToggle({ username }: { username: string }) {
  const client = useQueryClient();
  const key = ["failure-notification-preference", username];
  const preference = useQuery({
    queryKey: key,
    queryFn: () => apiGet<Preference>(ENDPOINT),
    enabled: Boolean(username),
    retry: false,
  });
  const update = useMutation({
    mutationFn: (enabled: boolean) => apiPost<Preference>(ENDPOINT, { enabled }),
    onSuccess: data => client.setQueryData(key, data),
  });
  const error = update.error || preference.error;
  return <div className="cicd-agent-notification-setting" aria-busy={update.isPending}>
    <label className="check" title="北京时间周一至周五 09:45，提醒归属于你的 Code Owner 失败记录；周一汇总周五至周日，其余工作日汇总昨日，周末不发送">
      <input type="checkbox" checked={update.isPending ? Boolean(update.variables) : Boolean(preference.data?.enabled)}
        disabled={!preference.data || preference.isFetching || update.isPending}
        onChange={event => update.mutate(event.target.checked)} />
      <Bell size={15} aria-hidden="true" />每日邮件提醒
      <span className="muted small" aria-hidden="true">09:45</span>
    </label>
    {error && <span className="small" role="alert">
      {update.error ? "提醒设置保存失败" : "提醒设置加载失败"}
      <button className="btn ghost sm icon" type="button" aria-label="重试提醒设置" title={error.message}
        disabled={preference.isFetching || update.isPending}
        onClick={() => update.error ? update.mutate(update.variables ?? false) : void preference.refetch()}>
        <RefreshCw size={14} aria-hidden="true" />
      </button>
    </span>}
  </div>;
}
