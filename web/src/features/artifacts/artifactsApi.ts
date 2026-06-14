/**
 * Artifacts API wrappers — typed helpers for all /api/artifacts/* endpoints.
 *
 * Mirrors app/api/routers/artifacts.py exactly.
 * Artifact bodies are plain text (or CSV); we use apiGetText for those.
 * Mutation endpoints return JSON; we use apiPost.
 */

import { apiGetText, apiPost } from "../../api/http";
import type {
  ArtifactKind,
  ArtifactGenerateResponse,
  ArtifactManagerReviewResponse,
} from "../../types";

// ---------------------------------------------------------------------------
// Query keys — single source of truth
// ---------------------------------------------------------------------------

export const ARTIFACT_KEY = (releaseId: string, kind: ArtifactKind) =>
  ["artifacts", releaseId, kind] as const;

export const TEST_SCOPE_KEY = (releaseId: string) =>
  ["artifacts", "test-scope", releaseId] as const;

// ---------------------------------------------------------------------------
// Shape returned by fetchArtifact
// ---------------------------------------------------------------------------

export interface ArtifactResult {
  /** Plain text content (Markdown or CSV). */
  text: string;
  /** Filename from Content-Disposition / X-Artifact-Name header. */
  name: string;
  /** ISO timestamp from X-Artifact-Generated-At header. */
  generatedAt: string;
}

// ---------------------------------------------------------------------------
// GET /api/artifacts/{kind}?release_id=...
// ---------------------------------------------------------------------------

export async function fetchArtifact(
  releaseId: string,
  kind: ArtifactKind,
): Promise<ArtifactResult> {
  const { text, headers } = await apiGetText(
    `/api/artifacts/${encodeURIComponent(kind)}?release_id=${encodeURIComponent(releaseId)}`,
  );
  return {
    text,
    name: headers.get("X-Artifact-Name") ?? "",
    generatedAt: headers.get("X-Artifact-Generated-At") ?? "",
  };
}

// ---------------------------------------------------------------------------
// GET /api/test-scope.csv?release_id=...
// ---------------------------------------------------------------------------

export async function fetchTestScopeCsv(releaseId: string): Promise<string> {
  const { text } = await apiGetText(
    `/api/test-scope.csv?release_id=${encodeURIComponent(releaseId)}`,
  );
  return text;
}

// ---------------------------------------------------------------------------
// POST /api/artifacts/generate
// ---------------------------------------------------------------------------

export function generateArtifacts(
  releaseId: string,
): Promise<ArtifactGenerateResponse> {
  return apiPost<ArtifactGenerateResponse>("/api/artifacts/generate", {
    release_id: releaseId,
    final: false,
  });
}

// ---------------------------------------------------------------------------
// POST /api/artifacts/manager-review
// ---------------------------------------------------------------------------

export function generateManagerReview(
  releaseId: string,
  fields: string[],
): Promise<ArtifactManagerReviewResponse> {
  return apiPost<ArtifactManagerReviewResponse>("/api/artifacts/manager-review", {
    release_id: releaseId,
    fields,
  });
}
