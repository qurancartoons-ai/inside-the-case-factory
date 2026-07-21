# Visual Variety Report

## Files changed

- [inside_case_factory/core/autonomous_direction.py](inside_case_factory/core/autonomous_direction.py)
  - Added deterministic montage-diversity analysis and safe shot reselection inside the existing Director planning flow.
  - Added diversity metadata persistence on shot rows and director artifacts.
- [tests/test_autonomous_direction.py](tests/test_autonomous_direction.py)
  - Added focused tests for repetition penalties, source repetition, still-image/static pressure, alternating assets, semantic safety preservation, and archival-first preservation.

## Diversity rules

The new pass runs inside Director planning, after the cinematic plan is built and before gate validation/final persistence.

It detects and discourages:

- consecutive reuse of the same asset identity
- consecutive reuse of the same source signature (provider/domain)
- repeated composition cadence
- excessive still-image usage
- static streaks and long periods with little visual change
- long uninterrupted supporting-detail/B-roll streaks
- repeated location signatures where unnecessary

When multiple acceptable candidates exist for a shot, the pass can reselect to improve diversity, but only if semantic/quality guards are preserved.

## Scoring

The planner now computes and persists:

- visual_variety_score
- repetition_penalty
- static_penalty
- source_diversity_score
- motion_balance_score
- transition_reason (per scene)

Selection safety constraints:

- candidate must remain semantically safe versus the baseline
- candidate must meet minimum semantic and quality floors
- archival-first priority is preserved when semantically safe archival candidates exist
- diversity cannot rescue low-quality or semantically weak media

## Integration point

The integration stays in the existing Director flow in [inside_case_factory/core/autonomous_direction.py](inside_case_factory/core/autonomous_direction.py):

1. build cinematic plan
2. apply deterministic montage diversity pass
3. run existing scene asset gate
4. persist existing director/shot artifacts

No new editor engine was added, and rendering/OpenMontage were not modified.

## Persistence

Diversity metadata is written into existing artifacts:

- `plan["visual_variety"]`
- `plan["director"]["visual_variety"]`
- per-shot `diversity_metadata` in `shot_media_manifest.json`
- per-scene `transition_reason` in planned scenes and shot rows

## Tests

Ran only the relevant test module:

- `PYTHONPATH=. pytest -q tests/test_autonomous_direction.py`

Result:

- 29 passed

New coverage includes:

- repeated asset rejection/penalty
- repeated source penalty
- excessive still-image pressure
- improved alternating assets with safe candidates
- preservation of semantic correctness during diversity optimization
- preservation of archival-first priority
- existing director planning success

## Assumptions

- Diversity is deterministic and metadata-driven; no external services are used.
- Existing candidate pools are reused; no new media engine or pipeline branch was introduced.
- Reselection is conservative by design: semantic quality and archival-first constraints remain higher priority than diversity gains.
