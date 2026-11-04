import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { JiraComment } from "../JiraComment";

describe("Jira comment preview", () => {
  it("renders the saved wiki comment as formatted text", () => {
    const { container } = render(<JiraComment value={[
      "HPC 数字员工 · 第 1 轮", "", "*已定位到运行时接口。*", "",
      "h3. 验证结果", "* 新版持续增长", "* 旧版稳定", "",
      "# 运行独立案例", "# 核对结果", "",
      "调用 {{mcLaunchKernel}}，日志位于 work/run_1.log。", "",
      "[复现程序与运行说明|https://example.test/jira-agent?conversation=jac_1]", "",
      "----", "", "_本评论由 JIRA 数字员工自动生成。_",
    ].join("\n")} />);
    expect(container.querySelector("strong")).toHaveTextContent("已定位到运行时接口。");
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("验证结果");
    expect(container.querySelectorAll("ul li")).toHaveLength(2);
    expect(container.querySelectorAll("ol li")).toHaveLength(2);
    expect(container.querySelector("code")).toHaveTextContent("mcLaunchKernel");
    expect(container).toHaveTextContent("work/run_1.log");
    expect(screen.getByRole("link", { name: "复现程序与运行说明" })).toHaveAttribute("href", "https://example.test/jira-agent?conversation=jac_1");
    expect(container.querySelector("em")).toHaveTextContent("本评论由 JIRA 数字员工自动生成。");
    expect(container.querySelector("hr")).toBeInTheDocument();
    expect(container.querySelector("pre")).toBeNull();
  });

  it.each(["noformat", "code:python"])("preserves commands inside {%s}", (macro) => {
    const command = 'printf "*literal* {{value}} [a|b] _name_ `tick` ```"\nif (x < 4 && y > 1) run();\n  # keep indentation';
    const { container } = render(<JiraComment value={`*执行命令*\n{${macro}}\n${command}\n{${macro.split(":")[0]}}`} />);
    expect(container.querySelector("pre code")?.textContent).toBe(command);
    expect(container.querySelector("pre strong, pre a, pre em")).toBeNull();
    expect(container.querySelector("strong")).toHaveTextContent("执行命令");
  });

  it("preserves inline symbols and renders explicit line breaks", () => {
    const { container } = render(<JiraComment value={"{{*ptr & name_1 < end `tick`}}\\\\下一行"} />);
    expect(container.querySelector("code")?.textContent).toBe("*ptr & name_1 < end `tick`");
    expect(container.querySelector("br")).toBeInTheDocument();
  });

  it("shows JIRA attachment links as their names instead of broken links", () => {
    const { container } = render(<JiraComment value={"[SOLUTION.md|^SOLUTION-jat_1-jaf_2.md] 列出配置；附件：[^a_b-jat_1-jaf_3.md]、[x*y.md|^x-jat_1-jaf_4.md]"} />);
    expect(container.querySelector("a")).toBeNull();
    expect(container).toHaveTextContent("SOLUTION.md 列出配置；附件：a_b-jat_1-jaf_3.md、x*y.md");
  });

  it("keeps untrusted markup inert and rejects script links", () => {
    const { container } = render(<JiraComment value={'<script>alert(1)</script>\n<img src=x onerror=alert(1)>\n[危险链接|javascript:alert(1)]\n[报告|https://example.test/a_(b)?x=1&y=2]'} />);
    expect(container.querySelector("script, img, [onerror], a[href^='javascript:']")).toBeNull();
    expect(screen.getByRole("link", { name: "报告" })).toHaveAttribute("href", "https://example.test/a_%28b%29?x=1&y=2");
    expect(screen.getByRole("link", { name: "报告" })).toHaveAttribute("rel", "noopener noreferrer");
  });
});
