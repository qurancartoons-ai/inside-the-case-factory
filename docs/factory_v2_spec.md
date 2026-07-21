# Factory V2 Specification

## Definition of Factory V2

Factory V2 is an autonomous, long-form documentary production system for approximately 10–60 minute YouTube videos. It is the research, editorial, and orchestration brain of the workflow. It produces factual, source-backed documentary content through a manifest-driven pipeline with explicit review gates, provenance tracking, and resumable execution. It is not a Shorts generator.

Factory V2 must be able to take a topic, build a source-backed research dossier, verify information, shape a narrative timeline, generate a script, plan scenes, discover and judge assets, generate narration, plan montage and render, review the output, and package a final YouTube-ready export. It must preserve factual integrity, reject unsupported claims, require evidence for every claim, and keep every narration segment tied to specific visual assets.

---

## 1. Product goal

Build a production-grade autonomous documentary system for long-form factual YouTube videos that:

- accepts a high-level topic and target duration
- researches the topic using reliable sources
- verifies claims against evidence
- produces a structured editorial plan
- writes a long-form documentary script
- plans scenes and matching visuals
- discovers and judges archival or licensed assets
- generates narration with a preferred high-quality voice provider
- assembles a montage and render plan
- performs documentary-quality review before final export
- packages a final video with thumbnail and YouTube metadata

The system must be safe, reviewable, and deterministic enough for human oversight, while still automating the major workflow steps.

---

## 2. Non-goals

Factory V2 does not aim to:

- generate Shorts or short-form social clips
- create fictional or speculative content as fact
- publish automatically to YouTube without explicit approval
- replace human editorial judgment for sensitive or high-risk factual topics
- use generic stock footage as a substitute for relevant archival material when better evidence exists
- copy OpenMontage into this repository
- become a general-purpose video editor with full creative control beyond documentary assembly

---

## 3. Complete pipeline stages

The end-to-end workflow is:

1. Topic
2. Research
3. Source verification
4. Timeline
5. Story architecture
6. Claims
7. Script
8. Scene planning
9. Asset discovery
10. Asset relevance judging
11. Voice generation
12. Montage planning
13. Render
14. Documentary quality review
15. Thumbnail
16. YouTube title, description and export

Each stage must produce a structured artifact and be reviewable before downstream execution.

---

## 4. Responsibility of every stage

### 1. Topic
Responsibilities:
- capture the user’s topic, required duration, tone, language, factual constraints, and production mode
- create a project shell and initial workflow state

Inputs:
- user prompt
- target duration
- language
- content mode
- audience expectations

Outputs:
- topic manifest
- project manifest
- initial workflow manifest

### 2. Research
Responsibilities:
- collect candidate sources relevant to the topic
- gather metadata, URLs, publication context, and source types
- prioritize reliable and archival sources

Inputs:
- topic manifest
- project constraints
- approved research settings

Outputs:
- source inventory manifest
- research dossier
- candidate source list

### 3. Source verification
Responsibilities:
- verify that each source is real, relevant, and usable
- reject low-value or duplicate sources
- attach reliability notes and evidence status

Inputs:
- source inventory
- source metadata
- optional manual review decisions

Outputs:
- verified sources manifest
- source review decisions
- rejected source list

### 4. Timeline
Responsibilities:
- create a high-level narrative timeline for the entire documentary
- define major acts, beats, transitions, and expected pacing

Inputs:
- verified sources
- topic and duration target
- research dossier

Outputs:
- timeline manifest
- act and beat structure

### 5. Story architecture
Responsibilities:
- convert the timeline into a narrative architecture
- define dramatic structure, evidence progression, suspense, and scene intent

Inputs:
- timeline manifest
- verified sources
- project tone and duration

Outputs:
- story architecture manifest
- scene intention map

### 6. Claims
Responsibilities:
- extract factual claims from the researched material
- connect every claim to one or more verified sources
- reject unsupported claims

Inputs:
- verified sources
- research dossier
- source excerpts or summaries

Outputs:
- claims manifest
- claim/source linkage records
- rejected claim list

### 7. Script
Responsibilities:
- write narration only from approved claims
- preserve factual boundaries and avoid unsupported statements
- maintain a clear documentary voice

Inputs:
- approved claims
- story architecture
- target duration
- language
- editorial tone

Outputs:
- script manifest
- narration segments
- linked claim IDs per segment

### 8. Scene planning
Responsibilities:
- split the script into scenes or beats
- assign each scene to one or more visual goals
- specify scene duration, pacing, and expected evidence needs

Inputs:
- script manifest
- story architecture
- claims

Outputs:
- scenes manifest
- scene-to-claim mapping
- scene narrative goals

### 9. Asset discovery
Responsibilities:
- search for candidate visual assets for each scene
- prioritize archival and rights-safe media
- record search provenance and source ranking

Inputs:
- scenes manifest
- scene goals
- claims and topic context
- configured asset search order

Outputs:
- asset discovery manifest
- discovered candidates per scene
- search trace records

### 10. Asset relevance judging
Responsibilities:
- evaluate whether each candidate asset is relevant to the scene and claims
- reject unrelated, generic, or weak matches
- choose the highest-quality asset for each narration segment

Inputs:
- discovered assets
- scene goals
- claims
- visual relevance criteria

Outputs:
- asset relevance manifest
- approved asset assignments
- rejected asset reasons

### 11. Voice generation
Responsibilities:
- generate narration audio for approved script segments
- prefer a high-quality configured voice provider
- ensure audio quality and intelligibility

Inputs:
- approved script segments
- voice provider configuration
- project voice settings

Outputs:
- voiceover manifest
- audio files
- timing metadata

### 12. Montage planning
Responsibilities:
- plan how visuals, narration, cuts, and transitions align
- define scene timing, shot changes, and pacing rules
- ensure each narration segment is tied to specific visual assets

Inputs:
- scenes manifest
- approved assets
- narration timing
- script structure

Outputs:
- montage plan manifest
- edit timeline
- asset assignment map

### 13. Render
Responsibilities:
- render the final documentary using the montage plan
- assemble narration, subtitles, visuals, transitions, and timing

Inputs:
- montage plan
- audio files
- visual assets
- subtitle plan

Outputs:
- rendered video file
- render report
- render diagnostics

### 14. Documentary quality review
Responsibilities:
- review the rendered output for factual consistency, visual quality, timing, narration clarity, and edit integrity
- reject or trigger remediation if quality falls below threshold

Inputs:
- rendered video
- narration and subtitle timing
- scene/asset mapping
- claims manifest

Outputs:
- quality review manifest
- pass/fail decision
- remediation tasks

### 15. Thumbnail
Responsibilities:
- create a thumbnail concept and image asset that represents the documentary

Inputs:
- story architecture
- approved visuals
- topic and title

Outputs:
- thumbnail manifest
- thumbnail asset

### 16. YouTube title, description and export
Responsibilities:
- generate title, description, tags, chapters, and final export packaging
- prepare the package for human review and optional upload

Inputs:
- approved story and render output
- quality review result
- topic and claims

Outputs:
- YouTube metadata manifest
- export bundle
- publish package

---

## 5. Inputs and outputs for every stage

Each stage must use explicit structured inputs and outputs. The following contract is required:

| Stage | Inputs | Outputs |
| --- | --- | --- |
| Topic | prompt, duration, language, tone, constraints | topic.json, project.json, workflow.json |
| Research | topic.json, project.json | research_plan.json, sources.json, research_dossier.json |
| Source verification | sources.json, research_dossier.json | verified_sources.json, source_review.json |
| Timeline | verified_sources.json, topic.json | timeline.json |
| Story architecture | timeline.json, verified_sources.json | story_architecture.json |
| Claims | verified_sources.json, research_dossier.json | claims.json |
| Script | claims.json, story_architecture.json | script.json |
| Scene planning | script.json, story_architecture.json | scenes.json |
| Asset discovery | scenes.json, claims.json | asset_discovery.json |
| Asset relevance judging | asset_discovery.json, scenes.json | asset_relevance.json |
| Voice generation | script.json, voice config | narration_manifest.json, audio files |
| Montage planning | scenes.json, asset_relevance.json, narration_manifest.json | montage_plan.json |
| Render | montage_plan.json, audio files, assets | final_video.mp4, render_report.json |
| Documentary quality review | final_video.mp4, render_report.json, claims.json | quality_review.json |
| Thumbnail | story_architecture.json, final_video.mp4 | thumbnail.json, thumbnail asset |
| YouTube metadata/export | quality_review.json, story_architecture.json | youtube_package.json, export bundle |

---

## 6. Required JSON schemas

The following schemas are required for Factory V2.

### Topic schema
- id
- title
- prompt
- target_duration_minutes
- language
- tone
- content_mode
- created_at
- constraints

### Source schema
- id
- title
- url
- publisher
- publication_date
- source_type
- source_country
- reliability_tier
- access_date
- summary
- verification_status
- review_status
- linked_claim_ids

### Claim schema
- id
- text
- claim_type
- confidence
- source_ids
- review_status
- status_reason
- created_at

### Script segment schema
- id
- text
- claim_ids
- scene_id
- duration_seconds
- voiceover_status
- review_status

### Scene schema
- id
- heading
- script_segment_ids
- estimated_duration_seconds
- visual_goal
- claim_ids
- asset_requirements
- review_status

### Asset candidate schema
- id
- scene_id
- source_type
- provider
- url
- title
- license
- rights_status
- relevance_score
- relevance_reason
- archive_preferred
- review_status

### Asset assignment schema
- scene_id
- narration_segment_id
- asset_id
- relevance_score
- relevance_reason
- review_status
- timing_start_seconds
- timing_end_seconds

### Voiceover schema
- segment_id
- audio_path
- provider
- model_id
- duration_seconds
- quality_score
- review_status

### Montage plan schema
- scene_id
- narration_segment_id
- asset_id
- transition
- duration_seconds
- timing_start_seconds
- timing_end_seconds
- subtitle_enabled
- subtitle_text

### Quality review schema
- overall_score
- factual_consistency_score
- visual_relevance_score
- narration_quality_score
- timing_score
- render_integrity_score
- failures
- passed

### YouTube package schema
- title
- description
- tags
- chapters
- thumbnail_path
- export_path
- publish_ready

---

## 7. Approval gates

Factory V2 must preserve explicit approval gates before expensive or irreversible steps.

Required gates:

1. Research approval
   - before script generation
   - requires at least one approved source and one approved claim

2. Claim approval
   - before script generation
   - every factual claim used in narration must be approved

3. Script approval
   - before scene planning and asset discovery

4. Scene approval
   - before asset discovery or full asset assignment

5. Asset approval
   - before generation of final montage/render plan

6. Voice approval
   - before final render if voice quality is below threshold or provider is non-preferred

7. Quality review approval
   - before final export and packaging

8. Publish approval
   - separate from export packaging and only after explicit user consent

No stage may skip its gate silently. If a gate is not met, the system must stop and report the reason.

---

## 8. Retry and failure behavior

Factory V2 must be resilient and explicit.

### Retry rules
- retries must be bounded
- each retry must generate a new artifact version
- the prior artifact must remain available for review
- failed retries must be recorded with reason and stage context

### Failure behavior
- if a stage fails, the project must remain resumable
- the system must write a failure report with stage, reason, retry count, and next action
- unsupported claims must be rejected or rewritten rather than silently passed through
- failed voice generation must not silently fall back to a low-quality robotic voice without explicit confirmation
- failed asset discovery must not degrade into a generic stock fallback without a relevance gate

### Recovery behavior
- the pipeline may resume from the last completed stage
- user intervention may be required at approval gates
- recovery must preserve prior artifacts and not overwrite approved content blindly

---

## 9. Source and citation rules

The system must follow strict factual grounding rules.

Hard rules:
- every factual claim must be connected to one or more verified sources
- unsupported claims must be rejected or rewritten
- claims without source linkage are invalid for final narration
- source provenance must be stored with each claim
- source reliability and evidence quality must be visible to the editor
- quotation or paraphrase use must remain traceable to the underlying source
- if a claim cannot be tied to evidence, it must not appear in the final script

Required source behavior:
- prefer primary, archival, official, court, or reputable journalistic sources where available
- avoid weak or anonymous internet sources for critical factual claims
- require stronger support for disputed, sensitive, or high-impact claims

---

## 10. Asset relevance rules

Asset discovery and selection must be conservative and evidence-driven.

Hard rules:
- every narration segment must be linked to specific visual assets
- generic stock footage must not be accepted only because it loosely matches a keyword
- historical archive media must be preferred over generic stock
- a visual relevance judge must reject unrelated footage
- long static clips should be avoided
- if no strong relevant archive asset exists, the system must either:
  - defer the scene until a better asset is found, or
  - use a lower-confidence asset only with explicit approval and a visible warning

Required search order:
1. Wikimedia Commons
2. Internet Archive
3. Europeana or other archival sources
4. Pexels
5. Pixabay
6. Unsplash
7. AI-generated media only as a last resort

Asset acceptance criteria:
- relevance must match the specific scene and claim
- rights status must be known or acceptable
- the asset must not be obviously unrelated
- the asset must contribute to the narrative and not merely satisfy a keyword match

---

## 11. Voice quality rules

Narration quality is a first-class requirement.

Hard rules:
- ElevenLabs must be the preferred narration provider when configured
- the system must not silently fall back to a low-quality robotic voice
- if the preferred provider is unavailable or not configured, the system must report the issue and either:
  - use a fallback only with explicit approval, or
  - stop and require a manual voice-over decision
- narration must be intelligible, natural, and properly timed
- segments with weak audio quality must be rejected or regenerated

Voice quality requirements:
- audible narration with minimal distortion
- stable pacing and prosody
- clear pronunciation of names and terms
- no obvious robotic artifacts when a high-quality provider is available

---

## 12. Documentary quality scoring

The final documentary must be evaluated with a clear quality rubric.

### Required scoring dimensions
- factual consistency
- visual relevance
- narration quality
- pacing and timing
- edit rhythm
- subtitle synchronization
- audio clarity
- absence of black frames or dead air
- absence of repeated or irrelevant footage

### Minimum quality thresholds
- factual consistency must be high and source-backed
- visual asset relevance must be above threshold for all primary scenes
- narration must be audible and synchronized
- no major visual dead zones or static overuse
- no obvious repeated footage without editorial intent

### Quality review must fail if
- the final duration is outside the acceptable range
- narration is not audible
- subtitles are out of sync
- black frames appear
- irrelevant footage is used without justification
- factual inconsistency is detected

---

## 13. Integration boundary with OpenMontage

OpenMontage remains a separate rendering engine and must not be copied into this repository.

Integration boundary:
- Inside the Case Factory remains the research, editorial, and orchestration brain
- OpenMontage is responsible for rendering and montage execution as an external engine or integration target
- Factory V2 may call OpenMontage through a defined interface, but must not embed its implementation inside this repository
- the interface must be narrow and contract-based

Required boundary rules:
- this repository owns story, claims, source linkage, scene planning, asset selection, and review orchestration
- OpenMontage owns render execution, montage engine behavior, and final video assembly details
- the contract between them must be versioned and explicit

---

## 14. Which existing modules can be reused

The following existing modules should be considered for reuse or extension:

- [inside_case_factory/core/production.py](../inside_case_factory/core/production.py) for orchestration and resumable workflow execution
- [inside_case_factory/pipeline/stages.py](../inside_case_factory/pipeline/stages.py) for stage definitions
- [inside_case_factory/core/research.py](../inside_case_factory/core/research.py) for research planning and source handling
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py) for relevance scoring logic
- [inside_case_factory/core/reference_intake.py](../inside_case_factory/core/reference_intake.py) for source intake and reference handling
- [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py) for narrative pacing and blueprinting
- [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py) for editorial planning
- [inside_case_factory/core/script_repair.py](../inside_case_factory/core/script_repair.py) for repair and validation workflows
- [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py) for quality validation
- [inside_case_factory/web/dashboard.py](../inside_case_factory/web/dashboard.py) for review and operator interaction
- [inside_case_factory/rendering](../inside_case_factory/rendering) for render-related helpers

These modules can be reused where they already fit the new architecture, but they should be treated as implementation building blocks rather than as the final contract.

---

## 15. Which existing modules need refactoring

The following modules are likely to require refactoring to support Factory V2 cleanly:

### Production orchestration
- [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)
Needs refactoring to support a clearer stage contract, explicit artifact versioning, and a stronger separation between orchestration and content generation.

### Research and claims workflow
- [inside_case_factory/core/research.py](../inside_case_factory/core/research.py)
Needs refactoring to make claim-source linkage a first-class invariant rather than a side effect.

### Relevance and asset judgment
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
Needs refactoring to support stronger visual relevance rules, asset provenance, and explicit rejection reasons.

### Scene and script contract
- current script and scene generation flows should be consolidated around stricter schemas and clearer stage dependencies

### Manifest handling
- manifest writing and validation should be standardized so every stage can validate its own output and dependency state explicitly

### Dashboard integration
- the dashboard should be aligned with the new stage model rather than operating as an independent workflow surface

No existing working functionality should be removed without explicit justification. Refactoring should preserve behavior while improving clarity and contract enforcement.

---

## 16. Phased migration plan

### Phase 0 — Foundation and audit alignment
Goal:
- lock the Factory V2 contract and stage definitions
- define the required schemas and approval gates
- document the migration boundary with OpenMontage

Acceptance criteria:
- the stage list is agreed and documented
- the required schemas exist in draft form
- the OpenMontage boundary is explicit
- no production behavior is changed

### Phase 1 — Orchestration and manifest contracts
Goal:
- replace ad hoc stage progression with a canonical pipeline contract
- add manifest validation for every stage
- ensure resumability and artifact versioning

Acceptance criteria:
- every stage produces a versioned artifact
- a failed stage can be resumed without losing prior approved content
- stage dependencies are enforceable

### Phase 2 — Research, claims and source verification
Goal:
- make source-backed claims a hard requirement
- enforce claim-source linkage and reject unsupported claims
- introduce stronger source review behavior

Acceptance criteria:
- every claim references one or more sources
- unsupported claims are rejected or rewritten
- research approval cannot be bypassed

### Phase 3 — Script, scenes and story architecture
Goal:
- generate a script and scene plan from approved claims only
- enforce scene-to-asset and scene-to-claim linkage

Acceptance criteria:
- narrations are linked to claims
- every scene has a clear visual goal
- script approval is required before downstream scene work

### Phase 4 — Asset discovery and relevance judgement
Goal:
- add archival-first asset discovery
- enforce relevance and rights checks
- reject unrelated generic footage

Acceptance criteria:
- asset search order matches the required priority list
- irrelevant footage is rejected
- every narration segment maps to a specific asset or explicitly deferred asset decision

### Phase 5 — Voice and montage planning
Goal:
- integrate high-quality narration provider selection
- avoid silent low-quality fallback
- build an explicit montage plan tied to assets and narration

Acceptance criteria:
- ElevenLabs is preferred when configured
- low-quality fallback requires explicit approval
- montage plan contains explicit asset and timing data

### Phase 6 — Render, review and packaging
Goal:
- render the documentary, run quality checks, and package the output
- ensure final export satisfies the required review criteria

Acceptance criteria:
- final render passes duration, audio, subtitle, black-frame, relevance, and factual consistency checks
- quality review is explicit and stored as a manifest
- metadata packaging is generated for review

---

## 17. Acceptance criteria for each phase

### Phase 0 acceptance criteria
- architecture doc exists and is approved
- stage list and schemas are documented
- OpenMontage boundary is agreed

### Phase 1 acceptance criteria
- pipeline stages are enforceable through manifests
- previous artifacts survive retry and resume
- stage transitions are recorded

### Phase 2 acceptance criteria
- source-backed claims are mandatory
- unsupported claims are rejected
- approval gates block invalid progression

### Phase 3 acceptance criteria
- script generation uses only approved claims
- scene planning is tied to claims and narrative goals
- script approval is required before scene generation

### Phase 4 acceptance criteria
- archival-first asset selection is enforced
- asset relevance judge rejects unrelated footage
- generic stock is not accepted solely by keyword match

### Phase 5 acceptance criteria
- high-quality narration provider is selected when configured
- low-quality voice fallback is explicit and visible
- montage plan ties every narration segment to concrete assets

### Phase 6 acceptance criteria
- final video passes the documented quality checks
- review result is stored and can block export
- thumbnail and YouTube export package are generated

---

## Implementation principles

1. Preserve existing working functionality.
2. Keep the system manifest-driven.
3. Prefer explicit review gates over silent automation.
4. Tie every factual claim to source evidence.
5. Tie every narration segment to specific visual assets.
6. Prefer archival and documentary-appropriate media over generic stock.
7. Keep OpenMontage separate and integration-based.
8. Make failures visible, resumable, and auditable.
