# Incremental Upgrade Gap Analysis for Factory V2

## Purpose

This document compares the current Inside the Case Factory implementation with the desired Factory V2 product and agent specifications, using the existing repository as the baseline. The goal is not to replace the current system. The goal is to identify what can be preserved, what should be strengthened, and what should be introduced only as thin, reviewable additions.

The guiding principle is conservative incrementalism:

- preserve the existing manifest-driven workflow
- preserve the current production orchestration model
- preserve the current research, claims, scene planning, producer/director, and review logic
- add the missing Factory V2 controls as explicit contracts, gates, and validation rules
- avoid building a second, parallel production stack

---

## Classification legend

- ALREADY EXISTS: the current codebase already covers the capability in a usable form.
- EXISTS BUT NEEDS IMPROVEMENT: the capability exists, but it is too loose, too fragmented, or not strong enough for the Factory V2 standard.
- MISSING: the capability is not materially implemented as a reviewable, enforced part of the current pipeline.
- DO NOT BUILD: the capability should not be added as a new independent subsystem in this repo.

---

## Executive summary

The current factory already has a strong base for Factory V2:

- a resumable, approval-aware production engine
- a durable manifest structure
- research and claim handling
- relevance scoring and review logic
- producer and director planning modules
- a documentary-quality critique layer
- a render path that can be treated as an external execution backend

The main gaps are not foundational workflow gaps. The main gaps are governance and contract gaps:

- stronger evidence-to-claim enforcement
- stronger scene-to-asset linkage
- an explicit archival-first asset policy
- explicit anti-monotony and anti-static-clip rules
- mandatory high-quality voice selection without silent fallback
- a clear handoff contract to OpenMontage
- post-render quality checks that are tied to the approved plan rather than only generic heuristics

In other words, the current codebase is already close to a Factory V2 foundation. What is missing is tighter structure rather than a completely new engine.

---

## Capability-by-capability gap review

| Factory V2 capability | Current state | Classification | Incremental recommendation |
| --- | --- | --- | --- |
| Central orchestrator with stage-based progression | The workflow engine in [inside_case_factory/core/production.py](../inside_case_factory/core/production.py) already manages stage transitions, approvals, resumability, and manifest updates. | ALREADY EXISTS | Preserve and extend it. Add clearer module-specific handoff contracts rather than replacing it. |
| Manifest-driven workflow and project memory | The system already persists project state, research, claims, scripts, scenes, media, and workflow artifacts in project-local manifests. | ALREADY EXISTS | Keep this as the foundation. Add a small set of new V2 manifests for scene-asset contracts, review decisions, and montage handoff. |
| Research planning and source inventory | The research stack in [inside_case_factory/core/research.py](../inside_case_factory/core/research.py) already assembles sources, snapshots, claims, and relevance metadata. | ALREADY EXISTS | Keep this intact and align it with the new Research Agent responsibilities. |
| Source verification and reliability tagging | The code already has relevance and reliability logic in [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py), and the research flow distinguishes duplicate and weak sources. | EXISTS BUT NEEDS IMPROVEMENT | Strengthen this with explicit verification outcomes and a stricter approval gate before claims can be used downstream. |
| Claim extraction and evidence linkage | The current research flow creates claims, links them to sources, and stores evidence-related metadata. | EXISTS BUT NEEDS IMPROVEMENT | Tighten the rule that every claim must be traceable to an approved source and that unsupported claims cannot advance. |
| Timeline and narrative structure | The producer and narrative modules in [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py) and [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py) already model pacing, story beats, and narrative structure. | EXISTS BUT NEEDS IMPROVEMENT | Keep these modules, but make them operate as part of a single editorial contract rather than as parallel planning layers. |
| Script generation from approved claims | The repository already has narrative and script-related planning structures, but the current implementation is more of a scaffold than a fully hardened claims-to-script contract. | EXISTS BUT NEEDS IMPROVEMENT | Preserve the current modules, but enforce that each narration segment must carry claim IDs and a source provenance trail. |
| Scene planning with explicit narrative goals | The current pipeline has scene planning and director planning, but it is not yet expressed as a strict scene-to-claim-to-asset contract. | EXISTS BUT NEEDS IMPROVEMENT | Add a formal scene contract that requires each scene to reference approved claims, visual intent, and asset requirements. |
| Asset discovery for scenes | The system has media and media review concepts, but not a fully dedicated scene-driven asset hunting workflow comparable to the Factory V2 design. | EXISTS BUT NEEDS IMPROVEMENT | Keep the current media-layer concepts, but wrap them in a dedicated asset-discovery stage that is driven by scene goals and approved claims. |
| Asset relevance judging and rejection of unrelated media | The relevance module already computes relevance, exclusion reasons, and review eligibility in [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py). | EXISTS BUT NEEDS IMPROVEMENT | Strengthen the current rules so that assets are rejected when they are generic, weakly linked, or visually disconnected from the approved scene claim. |
| Archival-first media selection | The current system does not enforce a strong archival-first preference over generic or synthetic stock. | MISSING | Add a policy layer that explicitly prefers relevant archival and licensed material over generic B-roll, and make that a gated decision rather than an informal preference. |
| Rejection of long repeated or static clips | The current producer and director layers already reason about repetition and pacing, but they do not enforce a hard production rule against long repeated or static footage. | MISSING | Add explicit montage constraints in the review/plan contract, not as a new full editor. |
| Mandatory high-quality voice provider and no silent fallback | The current code has voice-related infrastructure, but there is no guarantee that a high-quality provider will always be selected or that silent or low-quality fallback is blocked. | MISSING | Introduce a hard voice contract: approved script segments require a selected voice provider and a quality gate before proceeding. |
| Specialized agents under one orchestrator | The repository has multiple relevant modules, but it does not yet have the clear, contract-based agent boundaries described in the Factory V2 agent spec. | MISSING | Implement this as a thin orchestration layer that invokes current modules through a common contract, rather than creating parallel services. |
| Explicit handoff contract from Factory V2 to OpenMontage | The current rendering path is present, but the handoff to an external renderer is not expressed as a formal contract with approved montage instructions, timing, and asset references. | MISSING | Add a canonical montage plan artifact that is produced by Factory V2 and consumed by OpenMontage. |
| Post-render quality review | The critic and director planning layers in [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py) already produce critique and review artifacts. | EXISTS BUT NEEDS IMPROVEMENT | Keep this, but connect it more tightly to the approved editorial plan so that failures route back to the originating stage. |
| YouTube packaging and export metadata | Packaging is part of the broader pipeline, but it is not yet expressed as a distinct, contract-driven stage with a hard review gate. | EXISTS BUT NEEDS IMPROVEMENT | Preserve the existing packaging path and add a formal packaging manifest and approval gate. |
| Human review and approval gates | The orchestration layer already pauses for approvals and review. | ALREADY EXISTS | Preserve and formalize this as a core requirement of Factory V2. |
| Resumable execution and safe retries | The production engine already persists work and can resume. | ALREADY EXISTS | Keep this as-is; it is one of the strongest existing foundations. |
| Separate OpenMontage copy inside this repo | The Factory V2 spec explicitly says OpenMontage should remain external. | DO NOT BUILD | Do not copy OpenMontage into this repository. Treat it as a renderer and keep the handoff contract narrow. |
| Independent autonomous microservices for each agent | The proposed Factory V2 agent model is modular, but it should not evolve into a set of independent services. | DO NOT BUILD | Keep everything under a single orchestrated production flow with clearly scoped modules. |

---

## The biggest implementation gaps

### 1. Evidence-to-claim enforcement is present but not yet hard enough

The current code already creates claims and maintains source-linked records, but the pipeline still needs stricter enforcement that:

- no claim advances without a verified source
- no narration segment advances without claim IDs
- unsupported or weak claims are blocked before script writing and scene assembly

This is a governance and validation gap more than a missing engine gap.

### 2. Scene-to-asset linkage is still implicit

The current system has scene planning and media relevance, but the handoff between a scene and its chosen asset is not yet expressed as a strong, reviewable contract. Factory V2 needs a clear requirement that every narration segment is tied to one or more approved visual assets and that those assets are selected because they support the scene’s approved claims.

### 3. The current media policy is too permissive

The existing relevance logic can filter assets, but it does not enforce the Factory V2 rule that archival and rights-safe footage should be preferred when it directly supports the scene. A conservative upgrade should add this as a policy gate rather than broadening the system with a new media engine.

### 4. The current quality layer is useful but not yet fully aligned with the final product contract

The critic and director layers already assess visual and narrative quality, but the Factory V2 standard requires a tighter review loop that checks whether the final edit still respects the approved plan, the approved claims, and the approved asset selections. This should be framed as a review gate, not as a completely different editing subsystem.

### 5. Voice and rendering quality need hard gates

The current workflow does not yet encode a strict rule that narration must come from an approved high-quality voice path and that a low-quality or missing voice path is a blocking failure. This is one of the clearest places where a new policy layer can improve the system without replacing it.

---

## What should be preserved as-is

The following are core strengths that should remain unchanged in the incremental upgrade:

- the manifest-driven workflow
- the production orchestrator and stage progression model
- the approval gates and resumability
- the research and relevance engine
- the producer/director planning layers
- the review and critique artifacts
- the use of project-local state files as the source of truth

These are the parts of the repo that already make the system feel like a real production platform rather than a one-shot generator.

---

## What should be added incrementally

The recommended incremental additions are small and mostly structural:

1. A scene-to-claim-to-asset contract manifest
   - each scene should reference approved claims and asset requirements
   - this becomes the bridge between narrative planning and media selection

2. A stricter source and claim approval gate
   - no downstream script or asset selection should proceed without an approved evidence trail

3. An archival-first asset policy
   - prefer archival, relevant, rights-safe material over generic visuals
   - make this explicit and reviewable

4. An anti-monotony and anti-static-clip rule
   - enforce a minimum of variation in montage choices and reject obvious static repetition

5. A voice quality gate
   - require an approved high-quality voice provider path
   - block progress if the voice path is missing or degraded

6. A renderer handoff contract
   - produce a canonical montage plan artifact for OpenMontage
   - keep the contract narrow and deterministic

7. A post-render validation gate
   - verify that the exported video still matches the approved plan and evidence chain

---

## Recommended implementation posture

The safest path is to avoid a rewrite and instead layer a new Factory V2 contract over the current implementation:

- keep the existing modules as the execution engines
- add manifest-based contracts that define what each stage must produce
- enforce review gates before accepting stage outputs
- route failures back to the responsible stage rather than allowing silent progression
- keep OpenMontage as an external renderer and only interface with it through a formal handoff artifact

This approach preserves the current architecture while closing the gap to the Factory V2 requirements.

---

## Bottom line

The current repository already contains most of the machinery needed for Factory V2. The missing work is mostly about tightening the pipeline around evidence, scene-to-asset linkage, asset quality, voice quality, and renderer handoff. The right path is incremental enhancement, not replacement.
