# Current asset selection flow

## 1) Scene or narration segment enters planning
- File: [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py)
- Function/class: ProducerEngine.plan
- Input: scene list with narration/text, duration, and optional interview metadata
- Output: producer blueprint with section role, pacing, ratios, and visual mix
- Current decision logic: scene text length and duration are converted into a narrative role and visual rhythm; no asset choice happens here
- Smallest weakness: the scene is not yet tied to a specific claim or asset requirement, so generic visuals can still be treated as acceptable

## 2) Asset search/query is triggered
- File: [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)
- Function/class: _run_production_locked
- Input: generated scenes and project state
- Output: a call to discover_project_scene_media(project_root, limit_per_source=3)
- Current decision logic: the production orchestrator simply triggers media discovery; it does not constrain search terms, source preference, or scene-specific intent
- Smallest weakness: discovery is broad and can surface weak or generic candidates early

## 3) Candidate ranking
- File: [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
- Function/class: rebuild_relevance_cache
- Input: project context plus each asset from manifests/media_sources.json
- Output: per-asset relevance_score, relevance_reason, relevance_matches, review_eligible, review_exclusion_reason
- Current decision logic: relevance is computed from topic overlap in title/summary/description/transcript/source_type, and then eligibility is gated by threshold, linkage, preview availability, source URL, duplicates, and cross-project status
- Smallest weakness: ranking is mostly topical overlap; it does not strongly penalize generic stock footage or a weak visual match to the scene

## 4) Rejection
- File: [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
- Function/class: rebuild_relevance_cache
- Input: the same asset list after scoring
- Output: assets marked as ineligible with review_exclusion_reason
- Current decision logic: an asset is rejected if it fails the relevance threshold, lacks linkage, lacks preview/source metadata, is duplicate, or is from another project
- Smallest weakness: the filter is permissive enough that a barely relevant, generic asset can still pass

## 5) Final asset selection
- File: [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)
- Function/class: DirectorEngine.plan
- Input: cinematic plan, producer blueprint, scenes, and media assets already stored in manifests/media_sources.json
- Output: shot-level selections and alternatives written into a shot media manifest
- Current decision logic: the director plan adds media intent, asset requirements, and search queries for each shot, then records chosen assets and alternatives linked by shot_id; the actual final selection is not re-validated here against scene claim intent
- Smallest weakness: the final picked asset can still be weak if the earlier relevance pass was only loosely tied to scene meaning

## 6) Manifest storage
- File: [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
- Function/class: rebuild_relevance_cache
- Input: scored asset records
- Output: updated manifests/media_sources.json
- File: [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)
- Function/class: DirectorEngine.plan
- Input: shot selections and alternatives
- Output: manifests/shot_media_manifest.json, director_plan.json, visual_direction.json
- Current decision logic: relevance results and shot selections are persisted as manifest artifacts for downstream rendering
- Smallest weakness: there is no hard gate that forces each narration segment to have at least one clearly relevant asset before it is accepted into the final plan

## One first implementation task
Add one hard per-scene asset gate before the director plan is finalized: require each scene or narration segment to have at least one asset that is both above the current relevance threshold and clearly linked to the scene’s stated intent/claim context; otherwise block that scene from advancing. This would improve asset relevance most without rebuilding the media pipeline.
