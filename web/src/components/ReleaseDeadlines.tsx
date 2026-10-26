import type { ReleaseSummary } from "../types";
import { formatServerTime } from "../lib/time";

export function ReleaseDeadlines({ release }: { release: ReleaseSummary | null | undefined }) {
  if (!release) return null;
  return (
    <section className="release-deadlines" aria-label="当前 release 截止时间">
      <span className="muted">截止时间</span>
      <dl>
        <div>
          <dt>App freeze · 发布范围冻结</dt>
          <dd>{formatServerTime(release.app_freeze_deadline) || "未设置"}</dd>
        </div>
        <div>
          <dt>Doc freeze · 文档截止</dt>
          <dd>{formatServerTime(release.doc_deadline) || "未设置"}</dd>
        </div>
      </dl>
    </section>
  );
}
