# Evidence-Grade Foundation Gate Report

## Files changed

- `inside_case_factory/core/production.py`
- `inside_case_factory/core/relevance.py`
- `inside_case_factory/core/autonomous_direction.py`
- `tests/test_orchestration.py`
- `tests/test_autonomous_direction.py`

## Quality-mode behavior

- Added explicit run-quality modes:
	- `evidence_grade`
	- `sample_or_demo`
- Mode is normalized and persisted in existing manifests:
	- `workflow.json` (`run_quality_mode`, `run_outcome_status`, `is_evidence_grade`)
	- `production_request.json` (`run_quality_mode`)
- No automatic inference from empty data is used.

## Exact gate conditions

### Research gate (evidence-grade only)

Blocks with `blocked_missing_research` when either is true:

- `research.json.status` is not `completed` or `approved`
- No usable approved source exists:
	- source `review_status == approved`
	- source `relevance_status != irrelevant`
	- has basic source identity (`url` or `publisher` or `title`)

### Claim gate (evidence-grade only)

Blocks with `blocked_missing_claims` when:

- no approved claim exists that is source-linked to usable approved sources
- unsupported/unlinked approved claims do not count

### Media gate (evidence-grade only)

Blocks with `blocked_missing_media` when:

- discovered media has zero `review_eligible` assets before director finalization

## Blocking statuses

Implemented distinct top-level outcomes in orchestration/workflow:

- `evidence_grade_completed`
- `demo_completed`
- `blocked_missing_research`
- `blocked_missing_claims`
- `blocked_missing_media`

## Structured blocking results

Foundation gate results are persisted using existing production/review state in `quality_cycle.json`:

- `foundation_gates[]`
- `latest_foundation_gate`

Each gate result includes:

- `stage`
- `passed`
- `blocking_code`
- `blocking_reason`
- `missing_requirements`
- `run_quality_mode`
- `next_action`
- `evaluated_at`

## Asset gate behavior update

`validate_scene_asset_gate(...)` is now mode-aware:

- In `evidence_grade` mode:
	- zero candidate assets => gate fails
	- per-scene result includes `rejection_reasons: ["no-candidates"]`
- In `sample_or_demo` mode:
	- zero candidate assets => gate may pass with fallback reporting
	- gate response marks `fallback_mode_used: true`
	- blocking reason explains demo fallback state

Director reports now also persist `run_quality_mode` in both blocked and ready states.

## Sample/demo fallback behavior

- Existing sample fallback flow is preserved.
- When media asset list is empty in sample mode, production can proceed with demo fallback and no evidence-grade success label is used.
- Final status is explicitly `demo_completed`.

## Tests run and results

Command run:

```bash
PYTHONPATH=. pytest -q tests/test_orchestration.py tests/test_autonomous_direction.py
```

Result:

- `39 passed`

Added focused coverage for:

- evidence-grade empty research blocks
- research present but no approved/source-linked claims blocks
- research+claims present but no eligible media blocks
- empty media no longer passes asset gate in evidence mode
- sample mode retains fallback behavior for empty media gate
- demo completion is distinct from evidence-grade completion
- valid evidence-grade foundation advances to completion

## Assumptions

- Default run-quality mode is `sample_or_demo` for backward compatibility unless explicitly set.
- Existing review/approval manifests are extended in place rather than replaced.
- This change intentionally does not address rerender approval mismatch, critic/producer arbitration, voice synthesis, or rendering internals.
