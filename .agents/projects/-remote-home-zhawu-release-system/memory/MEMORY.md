# Memory Index

- [Timezone storage split](timezone-storage-split.md) — DB mixes UTC-ISO and naive-Beijing timestamp columns; the +8 frontend formatter only works on UTC input.
- [Identity seam not injective](identity-seam-not-injective.md) — repo->git seam maps 2 CICD tasks to 1 app on real data; CICD tasks are global but release_decision is per-release.
