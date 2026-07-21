# End-to-End Production Audit

## Scope

This audit ran one full documentary generation using the existing Inside the Case Factory pipeline without adding features, refactoring, or redesign.

- Run target project: projects/e2e-audit-documentary
- Command executed: `time python3 -m inside_case_factory generate "End-to-end audit documentary case" --slug e2e-audit-documentary`
- Total wall-clock runtime: 8m48.944s
- Final outcome: partial success with final MP4 produced, followed by rerender-loop failure at approval gate

## High-level outcome

- A complete first render was produced at projects/e2e-audit-documentary/exports/final_video.mp4.
- Full intermediate artifacts were generated and preserved under projects/e2e-audit-documentary/manifests.
- Critic requested rerender.
- Automatic rerender path failed with: "The factual script must be explicitly approved before rendering."

## Artifact collection

Collected artifacts include:

- Research output: projects/e2e-audit-documentary/manifests/research.json
- Claims: projects/e2e-audit-documentary/manifests/claims.json
- Timeline: projects/e2e-audit-documentary/manifests/timeline.json
- Script: projects/e2e-audit-documentary/manifests/script.json
- Producer plan: projects/e2e-audit-documentary/manifests/producer_blueprint.json
- Director plan: projects/e2e-audit-documentary/manifests/director_plan.json
- Media manifests: projects/e2e-audit-documentary/manifests/media_sources.json, projects/e2e-audit-documentary/manifests/shot_media_manifest.json
- Relevance manifests: projects/e2e-audit-documentary/manifests/media_sources.json
- Scene semantic scores: not present in this run due empty media asset set
- Archival policy scores: not present in this run due empty media asset set
- Diversity scores: projects/e2e-audit-documentary/manifests/director_report.json
- Render instructions: projects/e2e-audit-documentary/manifests/render_plan.json
- Final review artifacts: projects/e2e-audit-documentary/manifests/critic_report.json, projects/e2e-audit-documentary/manifests/producer_report.json, projects/e2e-audit-documentary/manifests/quality_cycle.json

## Stage-by-stage audit

Timing is based on command runtime output and artifact timestamps.

### 1) Create project
- Execution time: < 1s (project.json at 00:29:55 UTC)
- Status: Success
- Warnings: None
- Quality observations: Project scaffold created correctly
- Bottlenecks: None
- Suggestions: None

### 2) Research
- Execution time: < 1s (research/sources/claims/timeline all written around 00:29:55 UTC)
- Status: Technically success, substantively weak
- Warnings: research.json status = not_started, sources empty, claims empty, timeline empty
- Quality observations: No factual evidence entered pipeline
- Bottlenecks: Empty research path in sample generation mode
- Suggestions: Require minimum non-empty research evidence before high-quality mode claims completion

### 3) Claims extraction and timeline formation
- Execution time: < 1s
- Status: Success with empty payloads
- Warnings: claims.json and timeline.json are empty arrays
- Quality observations: No evidentiary backbone for documentary narrative
- Bottlenecks: Upstream research had no structured findings
- Suggestions: Add stage-level warning threshold when claims/timeline are empty

### 4) Script generation
- Execution time: < 1s (script.json at 00:29:55 UTC)
- Status: Success (sample script)
- Warnings: script status = sample_offline_generated, explicit disclaimer indicates non-factual demo text
- Quality observations: Script is coherent but not evidence-backed; full_narration exists but top-level narration field absent
- Bottlenecks: Script quality and approval assumptions mismatch for later rerender path
- Suggestions: Normalize script schema expectations across initial render and rerender entry points

### 5) Producer planning
- Execution time: ~11.9s (00:29:55 -> 00:30:06 UTC)
- Status: Success
- Warnings: producer_report later flagged weak categories (story_rhythm, tension_arc, emotional_impact, information_density)
- Quality observations: Strong structural metadata; pacing/retention signals generated
- Bottlenecks: Quality scores diverge from critic and trigger rerender
- Suggestions: Harmonize producer and critic gating criteria to reduce oscillation

### 6) Director planning
- Execution time: included in same ~11.9s window
- Status: Success
- Warnings: Asset gate passed because there were no candidate assets (blocking_reason: No candidate assets were available)
- Quality observations: Director metadata produced correctly, but with synthetic fallback dependency
- Bottlenecks: Gate permissiveness when media set is empty
- Suggestions: Surface explicit warning severity when asset gate passes due empty candidate pool

### 7) Media discovery / relevance / policy / semantic
- Execution time: effectively skipped or empty in this run
- Status: Not populated
- Warnings: media_sources.json assets = []
- Quality observations: No scene semantic scores, archival policy scores, or relevance-ranked assets available
- Bottlenecks: Sample path bypasses concrete media acquisition
- Suggestions: Distinguish sample-mode completion from evidence-grade completion in top-level run status

### 8) Voice-over generation
- Execution time: included in render phase; completed before final MP4
- Status: Success
- Warnings: Provider spent_usd = 0.2 across 5 calls
- Quality observations: narration_timing.json shows synchronized segment timing (total 50.55s)
- Bottlenecks: None critical
- Suggestions: Add per-segment synthesis latency stats to provider usage for audit granularity

### 9) Render planning and shot synthesis
- Execution time: ~7m33s from director output to render_plan write (00:30:06 -> 00:37:39 UTC)
- Status: Success
- Warnings: Render relied on generated/evidence-graphic fallback assets only
- Quality observations: render_plan, visual_direction, shot_media_manifest produced with full shot metadata and transition reasoning
- Bottlenecks: Heavy FFmpeg stage dominates runtime
- Suggestions: Cache reusable intermediate clips for repeated audit runs

### 10) Final render
- Execution time: ~1m04s from render_plan to final_video mtime (00:37:39 -> 00:38:43 UTC)
- Status: Success
- Warnings: None at encoder level
- Quality observations: final_video.mp4 produced; visual_quality_report valid=true, errors=[]
- Bottlenecks: Subtitle+audio mux is still a measurable tail stage
- Suggestions: Log render substep timings explicitly in manifest

### 11) Final review / rerender loop
- Execution time: immediate after final render
- Status: Failure in second-cycle continuation
- Warnings: critic_report overall_score=85.6 but producer_report overall_score=73.5; quality_cycle flagged rerender_pending
- Quality observations: Critic and producer assessments conflict materially
- Bottlenecks: Rerender re-entry hits script approval gate and aborts
- Suggestions: Unify rerender preconditions and approval checks with first-pass generation path

## Key measured quality signals from this run

- Critic overall score: 85.6 (weak category: tension_arc)
- Producer overall score: 73.5 (weak categories: story_rhythm, tension_arc, emotional_impact, information_density)
- Diversity scores:
  - visual_variety_score: 0.28
  - repetition_penalty: 0.81
  - static_penalty: 0.58
  - source_diversity_score: 0.90
  - motion_balance_score: 0.82
- Asset gate result: passed with no candidates available

## Five biggest remaining quality issues (ranked by impact)

1. Evidence pipeline can complete first render with empty research/claims/media
- Impact: Highest. Final documentary can look structurally valid while lacking factual grounding and real media evidence.

2. Rerender loop fails due approval-state mismatch after successful first render
- Impact: Very high. Quality-improvement cycle cannot complete end-to-end, blocking iterative refinement.

3. Inconsistent quality arbitration between critic and producer reports
- Impact: High. Conflicting scores (85.6 vs 73.5) create unstable rerender decisions and unclear quality truth.

4. Asset gate permissiveness when candidate media set is empty
- Impact: High. Gate semantics are satisfied without actual semantic/policy validation, reducing practical relevance guarantees.

5. Visual diversity remains weak in fallback-only media mode
- Impact: Medium-high. High repetition and static penalties reduce perceived editorial quality despite valid technical render.

## Final audit verdict

- End-to-end execution produced a documentary artifact and preserved intermediate manifests.
- The pipeline is operational but not yet robust for evidence-grade documentary quality under empty-research and fallback-media conditions.
- No production logic was modified as part of this audit.
