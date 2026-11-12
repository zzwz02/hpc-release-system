import type { AppInfoTest } from "../types";

interface TestScopeTableProps {
  tests: Pick<AppInfoTest, "path" | "command" | "platforms">[];
}

export function TestScopeTable({ tests }: TestScopeTableProps) {
  return (
    <div className="table mt-10" data-testid="test-scope-table">
      <table>
        <thead><tr><th>测试项</th><th>OS</th><th>CPU 架构</th><th>支持芯片</th><th>命令</th></tr></thead>
        <tbody>
          {tests.length === 0 && <tr><td colSpan={5} className="muted">没有启用的测试项</td></tr>}
          {tests.flatMap((test, testIndex) => (test.platforms.length ? test.platforms : [null]).map((platform, index) => (
            <tr key={`${test.path}-${testIndex}-${index}`}>
              <td className="mono small">{test.path}</td>
              <td className="small">{platform?.os || "—"}</td>
              <td className="small">{platform?.arch === "amd64" ? "x86" : platform?.arch === "arm64" ? "ARM" : platform?.arch || "—"}</td>
              <td className="small">{platform ? platform.chips.join(", ") || "未声明" : "无匹配的启用构建"}</td>
              <td className="mono small break">{test.command}</td>
            </tr>
          )))}
        </tbody>
      </table>
    </div>
  );
}
