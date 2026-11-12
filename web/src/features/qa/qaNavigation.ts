export const QA_PANES = [
  { key: "mark", label: "qa log" },
  { key: "release", label: "本期发布app" },
  { key: "test", label: "测试命令" },
  { key: "community", label: "开发者社区产物" },
] as const;

export function qaPaneFromSearch(search: string) {
  const key = new URLSearchParams(search).get("tab");
  return QA_PANES.find((pane) => pane.key === key)?.key ?? "mark";
}
