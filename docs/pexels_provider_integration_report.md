# Pexels provider integration report

Date: 2026-07-21
Scope: Native Pexels stock-media support in the existing Inside the Case Factory media discovery flow.

## Files changed

- `inside_case_factory/providers/runtime_media.py`
- `inside_case_factory/core/discovery.py`
- `inside_case_factory/core/relevance.py`
- `config/providers.toml`
- `tests/test_pexels_provider_integration.py`

## Provider behavior

A native adapter was added:
- `PexelsStockMediaProvider`
- Uses `PEXELS_API_KEY`
- Disabled-by-default configuration support
- Performs provider API requests with timeout control
- Produces provider-specific, clear errors without exposing secrets
- Gracefully skips calls when disabled or when no API key is available

Behavior details:
- Supports image and video search modes based on desired media type.
- Uses scene-specific query topics supplied by the existing discovery pipeline.
- Deduplicates provider results by source id and preview URL before returning candidates.
- Tracks last-call provider status:
  - configured
  - key_available
  - attempted
  - candidates_returned
  - skipped_reason
  - error_reason

## Integration point

No second media pipeline was added.

Pexels is integrated through existing connectors in `discover_project_scene_media()` and `discover_archival_media()`:
- Existing local/approved assets are still considered first.
- Existing archival providers run first.
- Pexels runs after archival connectors.
- Existing generated/safe fallback behavior remains unchanged and is only used when suitable concrete assets are unavailable.

## Query generation

Scene-specific query generation was expanded in the existing discovery code:
- Derives queries from:
  - scene intent subject
  - people/entities
  - location
  - historical events
  - time period
  - visual requirements
  - content reason
  - a short narration fragment
- Avoids sending full narration by using a capped short fragment.
- Deduplicates queries case-insensitively.
- Keeps existing per-source limits in place.

## Manifest normalization

Pexels candidates are normalized to fit the existing manifest flow and include:
- provider
- source URL
- preview URL
- media type
- title
- description
- dimensions
- duration (video when available)
- attribution metadata
- license metadata
- scene/shot linkage metadata

Discovery persistence was extended to retain these fields in asset metadata.

## Stock-media policy behavior

Pexels assets are marked as stock media and remain policy-penalized relative to archival sources:
- `source_category = generic_stock_footage`
- source policy scoring keeps stock below archival categories
- semantic relevance and existing gates still apply
- stock assets can still be selected when archival options are not suitable

## Provider availability reporting

Discovery output now includes `provider_availability` with per-provider status:
- configured
- key_available
- attempted
- candidates_returned
- skipped_reason
- error_reason

This is persisted to `manifests/media_discovery.json` by existing discovery flows.

## Tests executed

Focused tests (mocked/no real provider calls):

1. `python -m unittest tests.test_pexels_provider_integration`
- Result: `OK` (9 tests)

2. `python -m unittest tests.test_project_scaffold.ProjectScaffoldTests.test_discovery_workflow_requires_review_before_render_use tests.test_project_scaffold.ProjectScaffoldTests.test_project_discovery_searches_per_shot_and_persists_intent tests.test_visual_direction.VisualDirectionTests.test_safe_generated_fallback_is_owned_and_offline`
- Result: `OK` (3 tests)

New focused test coverage includes:
- successful normalization
- missing API key handling
- provider timeout handling
- provider error handling
- scene-specific query generation
- duplicate removal
- semantic ranking integration (stock penalty)
- asset gate integration
- AI fallback only when no suitable Pexels candidate exists

## Remaining limitations

- Pexels is configuration-disabled by default and requires explicit enablement plus `PEXELS_API_KEY`.
- Query generation is deterministic and token-based; no external NLP expansion is used.
- Provider integration currently focuses on search normalization and existing pipeline compatibility (no separate direct download manager beyond current discovery path).
- Asset suitability still depends on existing review and gate decisions; this integration does not bypass those controls.
