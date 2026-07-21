# Scene-aware semantic ranking report

## Summary

This increment adds a deterministic scene-aware semantic gate to the existing asset-selection flow before director finalization. The improvement preserves the current manifest-driven architecture, but it now rejects assets that are only broadly topical if they do not also match the meaning of a specific scene or shot.

## Files changed

- [inside_case_factory/core/relevance.py](inside_case_factory/core/relevance.py)
  - Added deterministic scene/shot semantic scoring helpers.
  - Extended the per-scene asset gate to use scene-match results.
  - Persisted the best scene-match result onto asset records in the existing media manifest.
- [inside_case_factory/core/autonomous_direction.py](inside_case_factory/core/autonomous_direction.py)
  - Ensured the director planning flow applies the updated gate before finalization.
- [tests/test_autonomous_direction.py](tests/test_autonomous_direction.py)
  - Added regression tests for specific-match acceptance, generic-footage rejection, required-concept mismatch failure, and multi-scene independent evaluation.

## Scoring factors and penalties

The new scorer evaluates a candidate asset against scene context using deterministic token-based matching:

- Scene context is assembled from heading, visual summary, media requirements, archival queries, alternative queries, people, locations, events, dates, and shot media intent.
- The asset is scored against required concepts and the broader scene context.
- A penalty is applied when an asset looks generic rather than scene-specific, such as city skylines, office footage, road footage, landscapes, overviews, or broad topical clips.
- Missing required concepts also lower the score and can trigger a failure even when the asset has some topical overlap.
- Time-period contradictions are flagged as mismatches.

The final gate uses the result of this scoring pass to reject assets that fail the new scene-match requirement while preserving the existing eligibility, linkage, preview, duplication, and cross-project checks.

## Integration point

The semantic rank is integrated at the existing asset gate boundary that runs before the director finalizes a plan. This keeps the change small and backward-compatible with the current pipeline while strengthening the quality of the assets that reach the final director plan.

## Persistence location

Scene-match results are stored on each asset record in the existing media manifest at [inside_case_factory/core/relevance.py](inside_case_factory/core/relevance.py) during cache rebuilds. The fields written include:

- semantic_match_score
- matched_concepts
- missing_required_concepts
- generic_visual_penalty
- mismatch_reasons
- final_scene_match_passed
- explanation

## Tests run and results

Targeted verification:

- `PYTHONPATH=. pytest -q tests/test_autonomous_direction.py`

Result:

- 17 tests passed, 0 failed

## Assumptions and limitations

- The scoring is deterministic and lightweight; it does not require any external API.
- It is intentionally conservative and favors explicit scene-specific evidence over broad topical relevance.
- The implementation uses token overlap rather than deeper semantic understanding, so it works best when scene descriptions and asset metadata contain recognizable concepts.
- The gate still preserves the existing non-semantic checks, so it remains compatible with the current production workflow.
