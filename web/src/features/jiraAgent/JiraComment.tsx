import { Markdown } from "../../components/Markdown";

// Convert the Jira wiki syntax used by comments, then use the shared sanitized renderer.
export function JiraComment({ value }: { value: string }) {
  const parts: string[] = [];
  const keep = (text: string) => `\uE000${parts.push(text) - 1}\uE001`;
  const fence = (text: string, minimum: number) => "`".repeat(
    Math.max(minimum, ...Array.from(text.matchAll(/`+/g), (match) => match[0].length + 1)),
  );
  const escape = (text: string) => text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const content = value.replace(/\r\n?/g, "\n")
    .replace(/\uE000/g, "&#57344;").replace(/\uE001/g, "&#57345;")
    .replace(/\{(noformat|code)(?::[^}\n]*)?\}([\s\S]*?)\{\1\}/g, (_match, _kind: string, text: string) => {
      const code = text.replace(/^\n|\n$/g, "");
      const delimiter = fence(code, 3);
      return keep(`\n\n${delimiter}\n${code}\n${delimiter}\n\n`);
    })
    .replace(/\{\{([^\n]*?)\}\}/g, (_match, text: string) => {
      const delimiter = fence(text, 1);
      return keep(`${delimiter} ${text} ${delimiter}`);
    })
    // JIRA attachment links ([^file], [label|^file]) have no URL on this site; show their name.
    .replace(/\[(?:([^\]\n|]+)\|)?\^([^\]\n]+)\]/g, (_match, label: string | undefined, name: string) => keep(
      escape((label ?? name).trim()).replace(/[\\`*_[\]()#+.!|~-]/g, "\\$&"),
    ))
    .replace(/\[([^\]\n|]+)\|([^\]\n]+)\]/g, (_match, label: string, url: string) => keep(
      `[${escape(label).replace(/[\\[\]]/g, "\\$&")}](${url.trim().replace(/[\s<>\\]/g, encodeURIComponent).replace(/\(/g, "%28").replace(/\)/g, "%29")})`,
    ))
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/^( *)([*#]+)\s+/gm, (_match, indent: string, markers: string) =>
      indent + "    ".repeat(markers.length - 1) + (markers.endsWith("#") ? "1. " : "- "))
    .replace(/^h([1-6])\.\s+/gm, (_match, level: string) => "#".repeat(Number(level)) + " ")
    .replace(/(?<![\w*])\*(?![\s*])([^*\n]+?)(?<!\s)\*(?![\w*])/g, "**$1**")
    .replace(/^bq\. /gm, "> ")
    .replace(/\\\\/g, "  \n")
    .replace(/(?<!\n)\n(?!\n)/g, "  \n")
    .replace(/\uE000(\d+)\uE001/g, (_match, index: string) => parts[Number(index)]);

  return <Markdown value={content} className="md-view jira-agent-md jira-comment" />;
}
