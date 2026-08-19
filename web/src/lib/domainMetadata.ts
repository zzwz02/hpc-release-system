import metadata from "../../../shared/domain_metadata.json" with { type: "json" };

function orderedKeys<T extends Record<string, { order: number }>>(items: T): Array<keyof T> {
  return (Object.keys(items) as Array<keyof T>)
    .sort((left, right) => items[left].order - items[right].order);
}

export const releasePhaseMetadata = metadata.release_phases;
export type ReleasePhase = keyof typeof releasePhaseMetadata;
export type ReleasePhaseTrait =
  | "before_app_freeze"
  | "before_doc_deadline"
  | "qa_scope_frozen";

export const RELEASE_PHASES = orderedKeys(releasePhaseMetadata);

export function releasePhaseLabel(phase: string): string {
  const item = releasePhaseMetadata[phase as ReleasePhase];
  return item?.label ?? phase;
}

export function releasePhaseHasTrait(phase: string, trait: ReleasePhaseTrait): boolean {
  const item = releasePhaseMetadata[phase as ReleasePhase];
  return item?.[trait] === true;
}

export const releaseDecisionMetadata = metadata.release_decisions;
export type ReleaseDecision = keyof typeof releaseDecisionMetadata;

export const RELEASE_DECISIONS = orderedKeys(releaseDecisionMetadata);

export function releaseDecisionCicdStatus(decision: string): "Running" | "Stopped" {
  const item = releaseDecisionMetadata[decision as ReleaseDecision];
  return item?.cicd_status === "Stopped" ? "Stopped" : "Running";
}

export function crossesReleaseDecisionRuntimeBoundary(
  oldDecision: string,
  newDecision: string,
): boolean {
  return releaseDecisionCicdStatus(oldDecision) !== releaseDecisionCicdStatus(newDecision);
}

export const docTargetMetadata = metadata.doc_targets;
export type DocTarget = keyof typeof docTargetMetadata;
export const DOC_TARGETS = orderedKeys(docTargetMetadata);
export const DOC_TARGET_DEFAULT = DOC_TARGETS.find(
  (target) => docTargetMetadata[target].default,
) as DocTarget;

const docTargetAliases = new Map<string, DocTarget>(
  DOC_TARGETS.flatMap((target) =>
    [target, ...docTargetMetadata[target].aliases]
      .map((alias) => [alias.toLowerCase(), target] as const),
  ),
);

export function normalizeDocTarget(value: string | null | undefined): DocTarget {
  return docTargetAliases.get((value ?? "").trim().toLowerCase()) ?? DOC_TARGET_DEFAULT;
}

export const qaStatusMetadata = metadata.qa_statuses;
export type QaStatus = keyof typeof qaStatusMetadata;
export const QA_STATUSES = orderedKeys(qaStatusMetadata);
export const QA_STATUS_DEFAULT = QA_STATUSES.find(
  (status) => qaStatusMetadata[status].default,
) as QaStatus;

export function qaStatusRequiresIssueNote(status: string): boolean {
  const item = qaStatusMetadata[status as QaStatus];
  return item?.issue_note_required === true;
}
