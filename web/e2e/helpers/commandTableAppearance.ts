import type { Locator } from "@playwright/test";

export async function commandTableAppearance(root: Locator) {
  const read = async (selector: string, properties: string[]) => root.locator(selector).first().evaluate((node, names) => {
    const style = getComputedStyle(node);
    return Object.fromEntries(names.map(name => [name, style.getPropertyValue(name)]));
  }, properties);
  const text = ["font-family", "font-size", "font-weight", "line-height", "padding"];
  const input = [...text, "height", "border-width", "border-radius"];
  return {
    body: await read("tbody td", text),
    header: await read("thead tr:first-child th", text),
    command: await read("td.cmd", text),
    columnFilter: await read(".colfilter-row input", input),
  };
}
