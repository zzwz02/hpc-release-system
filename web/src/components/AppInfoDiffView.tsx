/**
 * AppInfoDiffView — app_info changes as a unified diff.
 *
 * Used by the App workbench's「app_info 与 diff」section and by the QA
 * scope-confirmation dialog, so both read the same way.
 */
import { diffValues } from "../lib/appInfoDiff";
import type { AppInfoDiff } from "../types";

interface AppInfoDiffViewProps {
  diffs: AppInfoDiff[];
  /** Shown instead of the list when there is nothing to diff. */
  emptyText?: string;
  /** Extra line above the list, e.g. the change count in the QA dialog. */
  caption?: string;
}

export function AppInfoDiffView({ diffs, emptyText, caption }: AppInfoDiffViewProps) {
  if (!diffs.length) {
    return emptyText ? <div className="small muted">{emptyText}</div> : null;
  }
  return (
    <div className="diff-list" data-testid="app-info-diff">
      {caption && <div className="diff-caption">{caption}</div>}
      {diffs.map((diff, index) => (
        <div className="diff-block" key={diff.id || `${diff.field}-${index}`}>
          <div className="diff-head">
            <span className="diff-type">{diff.type}</span>
            <span className="diff-field mono">{diff.field}</span>
          </div>
          <div className="diff-body">
            {diffValues(diff.old_value, diff.new_value).map((line, lineIndex) => (
              <div className={`diff-line ${line.kind}`} key={lineIndex}>
                <span className="diff-sign" aria-hidden="true">
                  {line.kind === "del" ? "−" : line.kind === "add" ? "+" : " "}
                </span>
                <span className="diff-text">{line.text || " "}</span>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
