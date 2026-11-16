export interface QaFailure {
  app_version: string;
  hpc_version: string;
  arch: string;
  OS: string;
  GPU: string;
  py_label: string;
  torch_label: string;
  test_name: string;
  qa_comment: string;
  qa_env: string;
}

export interface QaFailureGroup {
  app_id: string | null;
  reported_app_id?: string;
  app_name: string;
  git_branch: string;
  failures: QaFailure[];
}

export interface QaFailureSummary {
  release_id: string;
  release_name: string;
  total_rows: number;
  failed_rows: number;
  groups: QaFailureGroup[];
}

export function QaFailureDetails({ failures }: { failures: QaFailure[] }) {
  const groups = new Map<string, { row: QaFailure; tests: string[] }>();
  for (const row of failures) {
    const key = JSON.stringify([row.GPU, row.hpc_version, row.app_version, row.OS, row.arch,
      row.py_label, row.torch_label, row.qa_comment, row.qa_env]);
    const group = groups.get(key) ?? { row, tests: [] };
    group.tests.push(row.test_name);
    groups.set(key, group);
  }
  return <ul className="p-0" data-testid="qa-failure-details">
    {[...groups].map(([key, { row, tests }]) => <li key={key} className="mt-6 break">
      <b>{row.GPU || "未指定 GPU"}</b>{" "}<code>{tests.join("、")}</code>：
      <span>{row.qa_comment || "测试失败（QA report 未填写 QA comment）"}</span>
      <div className="small muted">HPC {row.hpc_version || "未记录"} · App {row.app_version || "未记录"} · {row.OS} · {row.arch}
        {[row.py_label, row.torch_label, row.qa_env].filter(Boolean).map(value => ` · ${value}`)}</div>
    </li>)}
  </ul>;
}
