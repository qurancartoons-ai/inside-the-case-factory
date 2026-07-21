# Archival-first source policy report

## Files changed

- [inside_case_factory/core/relevance.py](inside_case_factory/core/relevance.py)
  - Added deterministic source-policy scoring for archival, historical, institutional, news, stock, and AI-generated media.
  - Integrated source-policy scoring into the existing relevance and semantic ranking flow.
  - Persisted source-policy metadata on each asset record in the existing media manifest.
- [tests/test_autonomous_direction.py](tests/test_autonomous_direction.py)
  - Added regression tests covering archival preference, historical-photo preference, semantically irrelevant archival rejection, generic-stock fallback, AI-generated-media deprioritization, institutional-rights preference, and existing gate behavior.

## Source categories and weights

The new policy uses deterministic heuristics rather than a new engine:

- archival_footage: strong preference for documentary-relevant archival or historical footage
- historical_photographs: strong preference for historical photographs, museum/library/university stills, and related evidence
- government_institutional_archive: strong preference for public-domain or institutional archive material
- newspaper_magazine_scans: strong preference for scanned press material when it fits the scene
- licensed_news_footage: moderate preference for reputable news footage over generic stock
- documentary_stills: moderate preference for still images that clearly match the scene
- generic_stock_footage: penalty for broad stock and B-roll
- ai_generated_imagery_video: strong penalty for synthetic media when real material is available

## Ranking integration

The final ranking now blends:

- existing topical relevance score
- semantic scene-match score
- source-policy score

The gate still preserves the existing scene-aware semantic gate and eligibility rules. Source policy can improve ranking when assets otherwise pass the same semantic and quality thresholds, but it does not rescue semantically irrelevant media.

## Fallback behavior

- A strong archival or institutional asset is preferred over generic stock when both pass semantic and quality thresholds.
- Generic stock remains usable only when it is the only available candidate and the scene context is weak or absent.
- AI-generated media is strongly deprioritized when suitable real material exists.
- Source policy alone does not override a weak semantic match.

## Persistence location

The policy metadata is stored directly on the existing asset entries in [inside_case_factory/core/relevance.py](inside_case_factory/core/relevance.py) and written to the existing media manifest via the relevance-cache rebuild flow. The persisted fields include:

- source_category
- source_policy_score
- source_policy_reason
- archival_priority
- rights_confidence
- generic_stock_penalty
- synthetic_media_penalty
- preferred_over_asset_ids

## Tests run and results

Targeted verification:

- `PYTHONPATH=. pytest -q tests/test_autonomous_direction.py`

Result:

- 23 passed in 0.31s

## Assumptions and limitations

- The policy is deterministic and lightweight; it relies on metadata cues rather than external classification services.
- It is intentionally conservative and does not let source type override actual scene relevance.
- The implementation uses keyword-based heuristics, so it works best when assets carry descriptive titles, descriptions, or rights metadata.
