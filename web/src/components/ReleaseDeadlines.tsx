import type { ReleaseSummary } from "../types";
import { releasePhaseLabel } from "../lib/phase";
import { formatServerTime } from "../lib/time";

export function ReleaseDeadlines({ release }: { release: ReleaseSummary | null | undefined }) {
  if (!release) return null;
  return (
    <section className="release-deadlines" aria-label="当前版本时间节点">
      <span className="muted">{releasePhaseLabel(release.phase)}</span>
      <dl>
        <div>
          <dt>报名截止</dt>
          <dd>{formatServerTime(release.signup_deadline) || "未设置"}</dd>
        </div>
        <div>
          <dt>QA 截止</dt>
          <dd>{formatServerTime(release.qa_deadline) || "未设置"}</dd>
        </div>
        <div>
          <dt>发布日</dt>
          <dd>{release.release_date || "未设置"}</dd>
        </div>
      </dl>
    </section>
  );
}
