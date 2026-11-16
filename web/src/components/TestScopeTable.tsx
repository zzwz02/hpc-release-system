interface TestScopeTableProps {
  tests: { path: string; command?: string; platforms: { os: string; arch: string; chips: string[] }[] }[];
  showCommand?: boolean;
}

export function TestScopeTable({ tests, showCommand = true }: TestScopeTableProps) {
  return (
    <div className="table mt-10" data-testid="test-scope-table">
      <table>
        <thead><tr><th>测试项</th><th>OS</th><th>CPU 架构</th><th>支持芯片</th>{showCommand && <th>命令</th>}</tr></thead>
        <tbody>
          {tests.length === 0 && <tr><td colSpan={showCommand ? 5 : 4} className="muted">没有启用的测试项</td></tr>}
          {tests.flatMap((test, testIndex) => (test.platforms.length ? test.platforms : [null]).map((platform, index) => (
            <tr key={`${test.path}-${testIndex}-${index}`}>
              <td className="mono small">{test.path}</td>
              <td className="small">{platform?.os || "—"}</td>
              <td className="small">{platform?.arch === "amd64" ? "x86" : platform?.arch === "arm64" ? "ARM" : platform?.arch || "—"}</td>
              <td className="small">{platform ? platform.chips.join(", ") || "未声明" : "无匹配的启用构建"}</td>
              {showCommand && <td className="mono small break">{test.command}</td>}
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );
}
