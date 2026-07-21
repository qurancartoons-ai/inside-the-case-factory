# Asset Gate Implementation Report

## Files changed
- inside_case_factory/core/relevance.py
  - Added a scene-level asset gate validator that checks each scene for at least one acceptable asset candidate before director finalization.
  - Introduced scene-intent-aware checks so generic topical assets are rejected when they do not clearly match the scene's requested evidence.
- inside_case_factory/core/autonomous_direction.py
  - Invoked the asset gate immediately before director manifests are finalized.
  - When the gate fails, the engine writes a blocked director report and raises a runtime error instead of silently proceeding.
- tests/test_autonomous_direction.py
  - Added focused regression tests for pass/fail cases, blocking behavior, and multi-scene evaluation.

## Exact validation logic
The new gate evaluates each scene against the following checks:
1. Review eligibility: the selected asset must be marked review-eligible.
2. Relevance threshold: the asset must meet the configured relevance threshold.
3. Duplicate and cross-project filters: duplicates and assets from another project are rejected.
4. Scene linkage: the asset must be linked to the scene or shot via mapping metadata.
5. Scene-intent matching: if the scene has scene context (heading, media requirements, queries, people/events/locations/dates, or shot media intent), the asset text must share at least one token with that context; otherwise the asset is treated as an intent mismatch.

The gate reports a scene as acceptable only if at least one asset passes all active checks.

## Where the gate runs
The gate is executed inside the director planning flow, immediately after the cinematic plan is validated and before the director plan, shot media manifest, and report artifacts are written.

## Blocking behavior
If any scene has no acceptable asset candidate, the system:
- writes a blocked report to manifests/director_report.json,
- sets the status to blocked,
- and raises a runtime error that prevents director finalization.

This keeps the pipeline from committing a director plan with clearly unsupported or irrelevant media selections.

## Tests run
Verified with:
- PYTHONPATH=. pytest -q tests/test_autonomous_direction.py

Result:
- 13 passed in 0.21s

## Assumptions
- The gate is intentionally conservative and uses existing scene metadata plus the selected asset metadata already present in the manifest pipeline.
- It does not redesign the broader media pipeline; it adds a focused safeguard at the point where director output is finalized.
- Valid assets that are clearly linked and contextually relevant continue to pass, while generic topical assets are rejected.
