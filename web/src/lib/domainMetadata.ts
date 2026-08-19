import metadata from "../../../shared/domain_metadata.json" with { type: "json" };

export const releasePhaseMetadata = metadata.release_phases;
export type ReleasePhase = keyof typeof releasePhaseMetadata;
export type ReleasePhaseTrait =
  | "before_app_freeze"
  | "before_doc_deadline"
  | "qa_scope_frozen";

export const RELEASE_PHASES = (
  Object.entries(releasePhaseMetadata) as Array<
    [ReleasePhase, (typeof releasePhaseMetadata)[ReleasePhase]]
  >
)
  .sort(([, left], [, right]) => left.order - right.order)
  .map(([phase]) => phase);

export function releasePhaseLabel(phase: string): string {
  const item = releasePhaseMetadata[phase as ReleasePhase];
  return item?.label ?? phase;
}

export function releasePhaseHasTrait(phase: string, trait: ReleasePhaseTrait): boolean {
  const item = releasePhaseMetadata[phase as ReleasePhase];
  return item?.[trait] === true;
}
