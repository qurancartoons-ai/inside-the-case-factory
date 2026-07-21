# Rerender Cycle Approval Consistency Report

## Summary

Implemented the second highest-impact audit fix by making rerender approval checks deterministic and state-consistent without redesigning approvals, production orchestration, or rendering.

The rerender loop now:

- Reuses prior script approval when the script content hash matches the approved hash.
- Requires re-approval when script content changed.
- Preserves strict approval safety for genuinely unapproved scripts.
- Persists rerender history in existing quality-cycle state.

## Files changed

- `inside_case_factory/core/research.py`
- `inside_case_factory/pipeline/generator.py`
- `inside_case_factory/core/autonomous_direction.py`
- `tests/test_rerender_cycle.py`

## Root cause investigated

Mismatch existed between:

- first-pass generation path (sample/demo creation can run without approved factual script), and
- rerender entry path (`_approved_project`) which always required explicit factual script approval.

When rerender recursion started from a run that did not originate from approved-entry semantics, the stricter rerender entry could fail with:

- `The factual script must be explicitly approved before rendering.`

## Approval fingerprinting implemented

Deterministic script approval fingerprint now includes:

- `script_hash`
- `approved_at`
- `approval_source`
- `approval_valid`

Where stored:

- `manifests/script.json` at `approval_fingerprint`
- `manifests/workflow.json` at `script_approval_fingerprint`

Hashing method:

- Canonical, deterministic SHA-256 over script content payload.
- Excludes volatile approval/edit metadata keys to avoid false invalidation.

## Rerender approval reuse logic

Before factual rerender entry:

- Compute current script content hash.
- Load approved fingerprint from script/workflow.
- If `hash == approved_hash` and fingerprint is valid with approval timestamp/source:
  - reuse approval (and synchronize approval flags if needed).
- Else:
  - require approval again (no bypass).

Safety guarantees:

- No reuse for changed script content.
- No reuse for missing/invalid approval fingerprint metadata.
- Fingerprint is marked invalid if approved hash mismatches current content.

## Quality-cycle continuity

Rerender recursion now keeps the original entry mode (`approved-entry` vs `sample/demo-entry`) across recursive iterations, preventing mode-flip approval inconsistencies.

This enables the intended cycle to continue deterministically:

Render -> Critic -> Changes required -> Rerender -> Critic -> repeat until success or max iterations.

## Rerender history persistence

Added `rerender_history` entries in existing `manifests/quality_cycle.json` with:

- `iteration`
- `reason`
- `changed_artifacts`
- `quality_delta`

Also extended quality attempts to persist `overall_score` (and producer score) so deltas are deterministic.

## Regression tests added

New file:

- `tests/test_rerender_cycle.py`

Covers:

- unchanged script rerender succeeds via approval reuse
- changed script requires re-approval
- approval fingerprint persistence on `approve_script`
- multiple rerender history entries persistence
- existing approved behavior preserved (legacy approved scripts without fingerprint are backfilled)

## Relevant tests run

```bash
PYTHONPATH=. pytest -q tests/test_rerender_cycle.py tests/test_project_scaffold.py tests/test_autonomous_direction.py
```

Result:

- `62 passed`

## Constraints honored

- No approval-system redesign.
- No production architecture redesign.
- No rendering pipeline modifications.
- Only targeted consistency and persistence updates in existing manifests/helpers.
