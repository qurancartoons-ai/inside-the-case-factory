# Factory V2 Agent Specification

## Purpose

This document defines the specialized Factory V2 modules that operate under one central orchestrator. These are not twelve independent autonomous services. They are tightly scoped modules with explicit responsibilities, shared manifests, and review gates.

Factory V2 remains a single production system with one orchestrator and many specialized agents. The orchestrator owns workflow progression; each module owns one part of the documentary pipeline.

## Design principles

- One central orchestrator controls all stage advancement.
- Each module is specialized and must not take over unrelated responsibilities.
- Existing working research functionality in [inside_case_factory/core/research.py](../inside_case_factory/core/research.py) and the production workflow in [inside_case_factory/core/production.py](../inside_case_factory/core/production.py) remain the baseline and must be preserved and reused.
- OpenMontage remains a separate rendering engine and is not copied into this repository.
- No existing working functionality is removed without explicit justification.
- Every factual claim must be linked to source evidence.
- Every narration segment must be linked to specific visual assets.
- No silent fallback to low-quality narration or generic stock is allowed.

---

## Responsibility boundary

### Inside the Case Factory owns
- research
- source verification
- claims
- story development
- script writing
- scene planning
- asset selection
- voice direction
- montage instructions
- quality decisions
- YouTube packaging

### OpenMontage owns
- execution of the approved montage plan
- media assembly
- subtitle rendering
- transitions
- FFmpeg/Remotion rendering
- technical render diagnostics

The handoff between the two is a contract: Factory V2 produces approved editorial and montage instructions; OpenMontage executes them.

---

## 1. Project Orchestrator

### Purpose
To coordinate the end-to-end pipeline, maintain workflow state, enforce stage order, manage approvals, and route failures back to the correct module.

### Reuse existing files or modules
- [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)
- [inside_case_factory/pipeline/stages.py](../inside_case_factory/pipeline/stages.py)
- [inside_case_factory/web/dashboard.py](../inside_case_factory/web/dashboard.py)

### Exact inputs
- topic request
- project manifest
- workflow manifest
- production request
- prior stage artifacts
- approval state

### Exact outputs
- orchestration_state.json
- stage status updates
- next-action instructions
- approval requests
- retry and failure records

### Required JSON artifact
- manifests/orchestration_state.json

### Decisions it is allowed to make
- which stage should run next
- whether a stage is blocked by an approval gate
- whether a retry is allowed
- whether work should return to an earlier module
- when to pause for human review

### Decisions it is not allowed to make
- invent facts
- approve unsupported claims
- choose assets without a prior asset gate
- override a quality review failure
- replace research, script, or asset decisions with its own opinion

### Validation rules
- every stage must have required input artifacts
- every stage must produce a valid artifact version
- stage transitions must be logged
- approval gates must be satisfied before advancing

### Approval gate
- required before moving from one major phase to the next
- must pause when prior module outputs are missing or invalid

### Retry behavior
- bounded retries per stage
- each retry writes a new artifact version
- prior artifacts remain available for review

### Failure behavior
- write a failure report with stage, reason, retry count, and next action
- keep the project resumable
- do not silently skip a failed gate

### Conditions for returning work to an earlier module
- weak research → return to Research Agent
- unsupported claim → return to Claims Agent or Research Agent
- weak story structure → return to Story Architect
- irrelevant asset → return to Asset Hunter with a new strategy
- low-quality narration → return to Voice Director
- failed render quality → return to the exact responsible stage
- factual inconsistency in final video → block export and return to Claims Agent and Script Writer

### Logging and provenance requirements
- write module name, stage, timestamp, input artifact version, output artifact version, and decision reason
- preserve immutable prior artifact versions

### Acceptance criteria
- the orchestrator advances stages only when dependencies and approvals are satisfied
- failures are visible and resumable
- no stage is skipped silently

### LLM requirement
- Prohibited

---

## 2. Research Agent

### Purpose
To gather, rank, and structure research candidates while preserving and extending the existing research workflow rather than replacing it.

### Reuse existing files or modules
- [inside_case_factory/core/research.py](../inside_case_factory/core/research.py)
- [inside_case_factory/core/reference_intake.py](../inside_case_factory/core/reference_intake.py)
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)

### Exact inputs
- topic manifest
- project manifest
- research plan inputs
- user constraints
- prior source inventory if present

### Exact outputs
- research_plan.json
- sources.json
- research_dossier.json
- research_agent_output.json

### Required JSON artifact
- manifests/research_agent_output.json

### Decisions it is allowed to make
- which sources are relevant enough to investigate
- which sources should be prioritized
- what metadata should be attached to a source candidate
- whether a source should be marked as weak or duplicate

### Decisions it is not allowed to make
- declare a claim true without source support
- skip source verification and move directly to script writing
- bypass approval gates

### Validation rules
- every candidate source must have title, URL, and provenance metadata when available
- weak or duplicate sources must be marked explicitly
- research must not be treated as complete if it lacks reviewable source artifacts

### Approval gate
- requires Research approval before script generation
- requires at least one approved source and one approved claim before downstream progression

### Retry behavior
- if research is weak, regenerate or broaden the search without losing prior results
- preserve prior source selections and mark revised ones as versioned

### Failure behavior
- if research is too weak or too sparse, stop and return to Research Agent with a new search strategy
- do not advance to claims or script stages with an empty or unreviewed source inventory

### Conditions for returning work to an earlier module
- weak research → return to Research Agent
- unsupported claim or weak evidentiary support → return to Claims Agent or Research Agent

### Logging and provenance requirements
- store source URLs, retrieval context, confidence notes, and version number
- preserve evidence trail for every source and every decision

### Acceptance criteria
- returns a reviewable source inventory
- source quality is explicit and traceable
- downstream claims can be built from the returned evidence

### LLM requirement
- Optional

---

## 3. Source Verification Agent

### Purpose
To validate the usability, relevance, and reliability of candidate sources before they are used for claims or narration.

### Reuse existing files or modules
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
- [inside_case_factory/core/reference_intake.py](../inside_case_factory/core/reference_intake.py)

### Exact inputs
- sources.json
- research_dossier.json
- research_agent_output.json

### Exact outputs
- verified_sources.json
- source_review.json
- rejected_source_list.json

### Required JSON artifact
- manifests/source_verification.json

### Decisions it is allowed to make
- mark a source as verified, weak, duplicate, or rejected
- attach reliability tier and evidence notes
- reject anonymous or low-quality sources for critical claims

### Decisions it is not allowed to make
- declare a claim true or false solely from a source title
- remove a source without recording the reason
- approve a source for use in narration without evidence status

### Validation rules
- critical claims require stronger support than trivial background statements
- duplicate and low-relevance sources must be marked explicitly
- no source may be used downstream without a verification status

### Approval gate
- required before Claims Agent can proceed with claim drafting

### Retry behavior
- if verification fails, recheck the source or request a manual review
- no silent acceptance of unverifiable source data

### Failure behavior
- if verification cannot be completed, stop and return work to Research Agent

### Conditions for returning work to an earlier module
- weak or unverifiable source → return to Research Agent
- duplicate or low-value source → return to Research Agent for broader sourcing

### Logging and provenance requirements
- record source URL, verification timestamp, reviewer notes, and verification outcome

### Acceptance criteria
- source status is explicit and machine-readable
- only verified sources are allowed to support claims

### LLM requirement
- Optional

---

## 4. Claims Agent

### Purpose
To extract factual claims from verified sources and ensure every claim is tied to one or more verified sources.

### Reuse existing files or modules
- [inside_case_factory/core/research.py](../inside_case_factory/core/research.py)
- [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py)

### Exact inputs
- verified_sources.json
- source_review.json
- research_dossier.json

### Exact outputs
- claims.json
- claim_source_links.json
- rejected_claims.json

### Required JSON artifact
- manifests/claims.json

### Decisions it is allowed to make
- draft factual claims
- choose which verified sources support each claim
- mark a claim as approved, disputed, weak, or rejected

### Decisions it is not allowed to make
- introduce unsupported claims
- use unverified sources
- allow a claim without source linkage into the script

### Validation rules
- every factual claim must have one or more source IDs
- unsupported claims must be rejected or rewritten
- disputed or weak claims must be blocked from narration unless explicitly approved

### Approval gate
- Claims approval is required before Script Writer can proceed

### Retry behavior
- if a claim is unsupported, rewrite or reject it and return to the Claims Agent or Research Agent
- preserve previous claim versions

### Failure behavior
- if no claims can be supported, stop and return to Research Agent

### Conditions for returning work to an earlier module
- unsupported claim → return to Claims Agent or Research Agent
- claim depends on weak source → return to Source Verification Agent or Research Agent

### Logging and provenance requirements
- log claim text, source IDs, confidence, review status, and revision history

### Acceptance criteria
- every final claim is source-backed
- no claim enters the script without source linkage

### LLM requirement
- Optional

---

## 5. Timeline Builder

### Purpose
To create the documentary’s macro-structure and pacing before deeper narrative work begins.

### Reuse existing files or modules
- [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py)
- [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)

### Exact inputs
- topic manifest
- claims.json
- approved source set
- target duration

### Exact outputs
- timeline.json
- act_structure.json

### Required JSON artifact
- manifests/timeline.json

### Decisions it is allowed to make
- structure acts and beats
- assign rough durations to sections
- decide the overall narrative progression

### Decisions it is not allowed to make
- write final narration
- make unsupported factual claims
- replace claim verification with assumed context

### Validation rules
- timeline must align to the target duration
- every major beat must be traceable to claims or research themes

### Approval gate
- optional review before Story Architect proceeds

### Retry behavior
- if the structure is weak, regenerate and preserve prior versions

### Failure behavior
- if timeline is incoherent, return to Story Architect or Claims Agent

### Conditions for returning work to an earlier module
- weak story structure → return to Story Architect
- timeline conflicts with claims → return to Claims Agent

### Logging and provenance requirements
- log beats, durations, and rationale for each section

### Acceptance criteria
- timeline is coherent and compatible with the approved claims

### LLM requirement
- Optional

---

## 6. Story Architect

### Purpose
To turn approved claims and the timeline into a narrative architecture with scene intent, emotional progression, and evidence sequencing.

### Reuse existing files or modules
- [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py)
- [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)
- [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py)

### Exact inputs
- timeline.json
- claims.json
- topic manifest
- approved sources

### Exact outputs
- story_architecture.json
- scene_intent_map.json

### Required JSON artifact
- manifests/story_architecture.json

### Decisions it is allowed to make
- define dramatic arcs, tensions, and evidence progression
- organize the story into a sequence of intended scenes
- define scene goals and narrative functions

### Decisions it is not allowed to make
- invent facts or claims
- write final polished narration
- select assets without later review

### Validation rules
- story structure must be compatible with approved claims
- major tension points must map to factual progression
- weak structure must be flagged before downstream work

### Approval gate
- required before Script Writer and Scene Director proceed

### Retry behavior
- if story is weak, regenerate with stricter factual and pacing constraints

### Failure behavior
- if story architecture is incoherent or under-supported, return to Story Architect

### Conditions for returning work to an earlier module
- weak story structure → return to Story Architect
- structural mismatch with claims → return to Claims Agent

### Logging and provenance requirements
- record architecture version, scene intents, and evidence mapping

### Acceptance criteria
- story architecture can be turned into a clear script and scene plan
- narrative progression is coherent and source-backed

### LLM requirement
- Optional

---

## 7. Script Writer

### Purpose
To write the documentary narration from approved claims and the story architecture while keeping factual boundaries intact.

### Reuse existing files or modules
- [inside_case_factory/core/script_repair.py](../inside_case_factory/core/script_repair.py)
- [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py)
- [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)

### Exact inputs
- story_architecture.json
- claims.json
- topic manifest
- target duration
- language

### Exact outputs
- script.json
- script_revision_log.json

### Required JSON artifact
- manifests/script.json

### Decisions it is allowed to make
- write narration segments
- assign claim IDs to narration segments
- decide approximate segment durations

### Decisions it is not allowed to make
- claim facts outside approved claims
- omit source linkage for factual statements
- ignore approval gates

### Validation rules
- every factual narration segment must point to approved claims
- unsupported narration must be rejected or rewritten
- no narration may appear if no supporting claims exist

### Approval gate
- Script approval required before Scene Director and asset discovery

### Retry behavior
- if script quality fails, regenerate a revised version and preserve the prior version

### Failure behavior
- if unsupported or weak script content appears, stop and return to Claims Agent or Story Architect

### Conditions for returning work to an earlier module
- unsupported claim → return to Claims Agent or Research Agent
- weak story structure → return to Story Architect

### Logging and provenance requirements
- store segment text, claim IDs, revision history, and approval state

### Acceptance criteria
- script is factually anchored and reviewable
- each segment can be linked to claims and later to assets

### LLM requirement
- Optional

---

## 8. Scene Director

### Purpose
To split the approved script into scenes, define scene goals, and prepare the scene structure for asset search and montage planning.

### Reuse existing files or modules
- [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py)
- [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)

### Exact inputs
- script.json
- story_architecture.json
- claims.json

### Exact outputs
- scenes.json
- scene_to_claim_map.json

### Required JSON artifact
- manifests/scenes.json

### Decisions it is allowed to make
- partition narration into scenes
- assign scene-specific visual goals
- define the expected asset requirements for each scene

### Decisions it is not allowed to make
- select final assets without later judging
- invent new factual content
- skip scene review if the structure is weak

### Validation rules
- every scene must map to script segments and claims
- every scene must have a defined visual goal
- long static scenes must be flagged

### Approval gate
- Scene approval required before asset discovery or full asset assignment

### Retry behavior
- if the scene plan is weak, rebuild it and preserve the prior version

### Failure behavior
- if scene structure is incoherent, return to Story Architect or Script Writer

### Conditions for returning work to an earlier module
- weak story structure → return to Story Architect
- scene plan mismatches script → return to Script Writer

### Logging and provenance requirements
- record scene IDs, script segment IDs, claims, visual goals, and version

### Acceptance criteria
- each scene is actionable for asset discovery and montage planning
- every scene is tied to narrative and claim context

### LLM requirement
- Optional

---

## 9. Asset Hunter

### Purpose
To discover candidate visual assets for each scene using a strict archival-first search order and explicit provenance tracking.

### Reuse existing files or modules
- [inside_case_factory/core/discovery.py](../inside_case_factory/core/discovery.py)
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)

### Exact inputs
- scenes.json
- claims.json
- story_architecture.json
- asset search policy

### Exact outputs
- asset_discovery.json
- discovered_asset_candidates.json
- search_trace.json

### Required JSON artifact
- manifests/asset_discovery.json

### Decisions it is allowed to make
- search for candidate assets
- rank sources by archival priority
- record search provenance and provider used

### Decisions it is not allowed to make
- accept generic stock footage merely because it loosely matches a keyword
- silently skip archival search when a better archival source exists
- bypass the Asset Judge

### Validation rules
- search order must follow: Wikimedia Commons → Internet Archive → Europeana or archival sources → Pexels → Pixabay → Unsplash → AI-generated only as a last resort
- weak or irrelevant candidates must be kept only as rejected candidates with reasons
- historical archive media must be preferred over generic stock when available

### Approval gate
- asset discovery output must pass to Asset Judge before being accepted for montage planning

### Retry behavior
- if results are weak or generic, broaden the search strategy and create a new search iteration

### Failure behavior
- if no useful real asset is available, request approval before AI-generated media is considered
- do not silently degrade into a generic stock fallback

### Conditions for returning work to an earlier module
- irrelevant asset → return to Asset Hunter with a new search strategy
- repeated or generic stock → reject and search again
- missing archival material → broaden archival search before using stock
- no acceptable real asset → request approval before AI-generated media

### Logging and provenance requirements
- log source provider, URL, title, search strategy, and timestamp

### Acceptance criteria
- assets are discovered in the required priority order
- candidates are traceable and reviewable

### LLM requirement
- Optional

---

## 10. Asset Judge

### Purpose
To judge whether each candidate asset is relevant, rights-safe, and appropriate for the scene and narration segment.

### Reuse existing files or modules
- [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py)
- [inside_case_factory/core/discovery.py](../inside_case_factory/core/discovery.py)

### Exact inputs
- asset_discovery.json
- scenes.json
- claims.json
- script.json

### Exact outputs
- asset_relevance.json
- approved_asset_assignments.json
- rejected_assets.json

### Required JSON artifact
- manifests/asset_relevance.json

### Decisions it is allowed to make
- accept or reject candidate assets
- assign the best asset to a scene or narration segment
- mark a clip as too generic, too long, too static, or irrelevant

### Decisions it is not allowed to make
- accept unrelated footage simply because it is available
- accept a long static clip without warning
- downgrade a rejected asset to approved without explicit review

### Validation rules
- every narration segment must be linked to a specific visual asset or explicitly deferred
- generic stock footage must be rejected when it is only loosely relevant
- visual relevance must be explicit and explainable
- long static clips should be avoided

### Approval gate
- required before Montage Planner may build the final plan

### Retry behavior
- if the asset set is weak, return to Asset Hunter for a new search strategy

### Failure behavior
- if no acceptable asset exists, stop and require an explicit approval path before AI-generated media or fallback use

### Conditions for returning work to an earlier module
- irrelevant asset → return to Asset Hunter
- repeated or generic stock → reject and search again
- missing archival material → broaden archival search first

### Logging and provenance requirements
- record asset ID, score, reason, scene ID, and review status

### Acceptance criteria
- approved assets are relevant to the scene and claim context
- unrelated or generic footage is rejected

### LLM requirement
- Optional

---

## 11. Voice Director

### Purpose
To generate or direct narration audio that is intelligible, properly timed, and suitable for documentary use.

### Reuse existing files or modules
- existing voice-over provider integration in [inside_case_factory/cli/main.py](../inside_case_factory/cli/main.py)
- provider configuration in the repository’s provider settings

### Exact inputs
- script.json
- voice configuration
- approved narration segments

### Exact outputs
- narration_manifest.json
- audio files
- timing metadata

### Required JSON artifact
- manifests/voiceover_manifest.json

### Decisions it is allowed to make
- choose the preferred voice provider when configured
- request regeneration of a segment when quality is poor
- mark narration as approved, rejected, or needs review

### Decisions it is not allowed to make
- silently fall back to a low-quality robotic voice
- mark poor audio as acceptable without review
- proceed to final render with inaudible or badly timed narration

### Validation rules
- narration must be audible and synchronized
- ElevenLabs is preferred when configured
- low-quality or robotic output must be rejected or regenerated

### Approval gate
- required before final render if voice quality is below threshold or provider is non-preferred

### Retry behavior
- regenerate low-quality or poorly timed segments
- preserve prior audio artifacts for comparison

### Failure behavior
- if narration quality is unacceptable, stop and return to Voice Director or request explicit approval for a fallback

### Conditions for returning work to an earlier module
- low-quality or robotic narration → regenerate or stop; never silently fall back

### Logging and provenance requirements
- log provider, model, duration, quality score, and output path

### Acceptance criteria
- narration is audible, intelligible, and properly timed
- the selected provider is explicit and reviewable

### LLM requirement
- Optional

---

## 12. Montage Planner

### Purpose
To turn approved scenes, assets, narration, and timing into an explicit montage plan for eventual execution.

### Reuse existing files or modules
- [inside_case_factory/rendering](../inside_case_factory/rendering)
- [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)

### Exact inputs
- scenes.json
- asset_relevance.json
- voiceover_manifest.json
- script.json

### Exact outputs
- montage_plan.json
- edit_timeline.json
- subtitle_plan.json

### Required JSON artifact
- manifests/montage_plan.json

### Decisions it is allowed to make
- choose cuts, pacing, transitions, and asset ordering
- map narration segments to assets and timings
- prepare subtitle placement and scene duration

### Decisions it is not allowed to make
- alter the approved story or claims
- substitute unapproved assets into the plan
- bypass quality review

### Validation rules
- every narration segment must be tied to specific visual assets
- every cut must be explicit and reviewable
- timing must be consistent with narration and assets

### Approval gate
- required before render execution

### Retry behavior
- if montage plan fails technical or editorial validation, regenerate with corrected asset or timing data

### Failure behavior
- if the plan is structurally invalid, return to Asset Judge or Scene Director

### Conditions for returning work to an earlier module
- failed render quality → return to the exact responsible stage
- asset mismatch or timing issue → return to Asset Judge or Voice Director

### Logging and provenance requirements
- log scene IDs, asset IDs, timing, transitions, and output version

### Acceptance criteria
- the montage plan is explicit enough for OpenMontage to execute
- narration and visuals are aligned

### LLM requirement
- Optional

---

## 13. Documentary Critic

### Purpose
To review the finished render for factual consistency, visual relevance, pacing, audio quality, subtitle synchronization, and technical integrity.

### Reuse existing files or modules
- [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py)
- [inside_case_factory/core/script_repair.py](../inside_case_factory/core/script_repair.py)

### Exact inputs
- final_video.mp4
- render_report.json
- montage_plan.json
- claims.json
- script.json

### Exact outputs
- quality_review.json
- remediation_tasks.json
- block_export.json if needed

### Required JSON artifact
- manifests/quality_review.json

### Decisions it is allowed to make
- pass or fail the final documentary
- identify the responsible stage for remediation
- block export for factual inconsistency or major quality failure

### Decisions it is not allowed to make
- approve a faulty render without remediation
- bypass factual inconsistency checks
- permit export if the video violates the quality thresholds

### Validation rules
- final duration must be within the allowed range
- narration must be audible
- subtitles must be synchronized
- black frames must be absent
- repeated or irrelevant footage must be flagged
- factual inconsistency must block export

### Approval gate
- required before packaging and export

### Retry behavior
- if quality fails, return to the exact stage responsible for the issue

### Failure behavior
- if factual inconsistency is detected, block export and route remediation back to Claims Agent, Script Writer, or Asset Judge as appropriate

### Conditions for returning work to an earlier module
- failed render quality → return to the exact responsible stage
- factual inconsistency in final video → block export

### Logging and provenance requirements
- store scores, failed checks, responsible stage, and remediation reasons

### Acceptance criteria
- quality review is explicit and auditable
- export is blocked when quality thresholds are not met

### LLM requirement
- Optional

---

## 14. YouTube Packaging Agent

### Purpose
To prepare the final documentary package: title, description, chapters, thumbnail, and export metadata for review.

### Reuse existing files or modules
- current packaging and dashboard-related flow in [inside_case_factory/web/dashboard.py](../inside_case_factory/web/dashboard.py)

### Exact inputs
- quality_review.json
- story_architecture.json
- approved visuals and render outputs

### Exact outputs
- youtube_package.json
- export bundle
- thumbnail asset and metadata

### Required JSON artifact
- manifests/youtube_package.json

### Decisions it is allowed to make
- create title, description, tags, chapters, and thumbnail concepts
- prepare the final export package for human review

### Decisions it is not allowed to make
- publish without explicit approval
- override a quality review failure
- invent facts not already approved in the documentary

### Validation rules
- title and description must reflect the approved story and evidence
- export must not be produced if the documentary quality review failed

### Approval gate
- required before publish or release

### Retry behavior
- if packaging metadata is weak, regenerate it with corrected content

### Failure behavior
- if the underlying product failed review, packaging stops and returns to the Documentary Critic

### Conditions for returning work to an earlier module
- quality review failure → return to Documentary Critic
- factual inconsistency → block export and return to earlier editorial stages

### Logging and provenance requirements
- log generated metadata version, asset paths, and review state

### Acceptance criteria
- final package is complete, reviewable, and consistent with the approved documentary

### LLM requirement
- Optional

---

## Explicit feedback loops

The following loops must be implemented explicitly and not left implicit:

- weak research → return to Research Agent
- unsupported claim → return to Claims Agent or Research Agent
- weak story structure → return to Story Architect
- irrelevant asset → return to Asset Hunter with a new search strategy
- repeated or generic stock → reject and search again
- missing archival material → broaden archival search before using stock
- no acceptable real asset → request approval before AI-generated media
- low-quality or robotic narration → regenerate or stop; never silently fall back
- failed render quality → return to the exact responsible stage
- factual inconsistency in final video → block export

Each feedback loop must write a new versioned artifact and a clear reason code so the orchestrator can route work correctly.

---

## Canonical Agent Execution Order

### Normal path

Topic → Project Orchestrator → Research Agent → Source Verification Agent → Claims Agent → Timeline Builder → Story Architect → Script Writer → Scene Director → Asset Hunter → Asset Judge → Voice Director → Montage Planner → OpenMontage Render → Documentary Critic → YouTube Packaging Agent

### Return paths

- Research Agent → Project Orchestrator
- Source Verification Agent → Research Agent
- Claims Agent → Research Agent or Source Verification Agent
- Timeline Builder → Story Architect or Claims Agent
- Story Architect → Claims Agent or Script Writer
- Script Writer → Story Architect or Claims Agent
- Scene Director → Script Writer or Story Architect
- Asset Hunter → Asset Judge or Project Orchestrator
- Asset Judge → Asset Hunter or Montage Planner
- Voice Director → Project Orchestrator or earlier editorial stage
- Montage Planner → Asset Judge, Voice Director, or Scene Director
- Documentary Critic → Claims Agent, Script Writer, Asset Judge, Voice Director, or Montage Planner
- YouTube Packaging Agent → Documentary Critic

### Execution rule

The orchestrator always chooses the next step based on:
1. prior artifacts
2. approval status
3. validation results
4. explicit failure or retry reasons

It never allows one specialized module to bypass the rules of another module or the approval gates of the pipeline.
