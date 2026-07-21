# Current Factory Analysis

## Executive summary

Inside the Case Factory is already much more than a toy scaffold. It is a manifest-driven, review-gated documentary production system with a CLI, a local dashboard, a resumable production engine, provider abstraction, research and claim management, media review, and an FFmpeg-based render path. The codebase is strongest when viewed as a workflow platform for producing documented, safety-conscious documentary assets rather than as a simple one-shot generator.

The architecture is coherent, but it has also grown through multiple overlapping layers. Several subsystems now cover related concerns in parallel, creating a system that is powerful but slightly harder to reason about than it needs to be. The current implementation is best understood as a mature prototype of a future Factory V2 rather than a single, minimal pipeline.

## What exists today

### 1. A real production entry point

The main runtime is exposed through the Python module CLI in [inside_case_factory/cli/main.py](../inside_case_factory/cli/main.py). It supports:

- health and doctor checks
- project creation
- generation and rendering
- project resume
- ElevenLabs commands
- media import
- dashboard startup
- autonomous/offline verification

This makes the package feel like an application rather than a loose collection of scripts.

### 2. A durable, manifest-driven workflow

The system stores state in project-local manifests under each project workspace. The most central documents include:

- project metadata
- research plans and source lists
- claims and evidence links
- script output
- scene plans
- producer/director planning artifacts
- media manifests
- review drafts and approvals
- production orchestration state
- workflow and render artifacts

This is the strongest design feature of the current factory. It gives the system a durable memory and makes the workflow reviewable and resumable.

### 3. A semantically rich pipeline

The intended stage registry is defined in [inside_case_factory/pipeline/stages.py](../inside_case_factory/pipeline/stages.py). The current pipeline includes topic intake, reference intake, research, fact-checking, script writing, scene planning, producer blueprints, image prompts, asset generation, voice-over, edit planning, subtitles, rendering, draft review, packaging, and publishing.

The runtime orchestrator in [inside_case_factory/core/production.py](../inside_case_factory/core/production.py) drives this workflow in a resumable, approval-aware way. It pauses at review gates and resumes safely when the next stage is approved or when prior artifacts already exist.

## Architectural shape

### 1. Core runtime layers

The repository is organized around a few major layers:

- CLI and user entry points
- project scaffolding and state management
- research and claim generation
- narrative and scene planning
- media discovery and relevance scoring
- rendering and asset generation
- dashboard and review experience

These responsibilities are not fully separated into a single clean architecture, but the boundaries are visible enough to understand the intended model.

### 2. Production orchestration

The central production engine in [inside_case_factory/core/production.py](../inside_case_factory/core/production.py) is the most important runtime component. It:

- creates new projects
- writes production plans and activity logs
- advances through stages safely
- stops at human approval gates
- persists durable state in manifests
- uses file locks to avoid concurrent resumption issues
- can recover from certain schema or restart-related interruptions

The system is designed to be resumable. That is a meaningful and valuable architectural property.

### 3. Research and fact workflow

The research stack is fairly substantial. The current implementation includes modules for:

- research planning
- source discovery
- claim extraction
- claim/source linkage
- relevance filtering
- source reliability and international coverage scoring
- research review and approval behavior

The modules in [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py) and [inside_case_factory/core/reference_intake.py](../inside_case_factory/core/reference_intake.py) are especially telling. They show that the system is not only concerned with generating content, but with grounding it, tracking provenance, and keeping editorial context attached to media and references.

### 4. Narrative and visual planning

The factory has multiple planning layers, including:

- producer planning in [inside_case_factory/core/producer.py](../inside_case_factory/core/producer.py)
- director planning in [inside_case_factory/core/autonomous_direction.py](../inside_case_factory/core/autonomous_direction.py)
- scene validation and script repair in [inside_case_factory/core/script_repair.py](../inside_case_factory/core/script_repair.py) and [inside_case_factory/core/narrative_quality.py](../inside_case_factory/core/narrative_quality.py)
- draft review support in [inside_case_factory/core/draft_review.py](../inside_case_factory/core/draft_review.py)

That means the system is trying to model not just content generation, but also editorial structure, pacing, and review quality.

### 5. Media and rendering pipeline

The rendering system is tied to project manifests and scene-level assets. The project uses a local FFmpeg-based path and supports real media mapping, scene-based visual planning, and render-state persistence. The package is structured for local or offline operation, and the README explicitly frames the system as a safe, low-cost scaffold.

### 6. Web-based review experience

The dashboard in [inside_case_factory/web/dashboard.py](../inside_case_factory/web/dashboard.py) turns the pipeline into a local production experience. It enables users to:

- create projects from prompts
- review sources and claims
- approve or block workflow stages
- edit scripts or scenes
- review media choices
- render video assets

This is an important product layer because it makes the workflow usable without requiring the user to understand the internal manifests.

## What is already working well

### Strong points

1. The workflow is durable and resumable.
   The production state machine and manifest files make the system robust to interruption.

2. The system is safety-conscious by design.
   Paid provider use is gated, approval gates exist, and the project defaults to offline-safe behavior.

3. The architecture is modular enough to evolve.
   Providers, render logic, planning components, and review experience are all separated by role.

4. The project is not purely speculative.
   There is a genuine local pipeline, a real project structure, and actual runtime behavior around generation, rendering, and review.

5. The project treats provenance as a first-class concern.
   Sources, claims, approvals, and media review are all represented in a structured way.

## Where the architecture feels crowded

The codebase shows signs of incremental growth rather than a single clean design. Several areas feel like they have been layered on top of one another:

### 1. Overlapping planning systems

Producer planning, director planning, autonomous direction, narrative quality checks, and draft review all contribute to what is essentially a larger “editorial intelligence” subsystem. These are related but not fully unified, which makes the system feel more complex than necessary for the current stage.

### 2. Multiple workflow surfaces

There is both a CLI and a dashboard, and both can influence the same underlying production flow. That is useful, but it also means workflow state and UI behavior can drift apart unless carefully coordinated.

### 3. Manifest proliferation

The system uses many JSON manifests, and they are meaningful. However, the number of artifact types can make the system feel hard to navigate unless one learns the conventions early.

### 4. Auxiliary subsystems that may be ahead of the core

Modules such as [inside_case_factory/core/reference_intake.py](../inside_case_factory/core/reference_intake.py), [inside_case_factory/core/relevance.py](../inside_case_factory/core/relevance.py), and [inside_case_factory/core/user_experience.py](../inside_case_factory/core/user_experience.py) add significant capability, but they also show that the project is trying to support multiple product concepts at once: editorial review, research workflow, reference integration, and production orchestration.

## Likely design tensions

The most important tensions in the current factory are:

- workflow control versus content generation
- human review versus automation
- local/offline resilience versus provider extensibility
- granular manifest persistence versus simplicity of mental models

The system currently manages these tensions reasonably well, but each one adds a layer of complexity.

## Missing or still-fragile areas

Even though the current implementation is substantial, a few areas remain more conceptual than fully hardened:

- there is still a strong “scaffold” feel around some provider integrations
- some paths are more mature than others, so the system can feel uneven across stages
- the workflow is powerful but depends heavily on consistent manifest conventions
- there is not one single, obvious “source of truth” for all state transitions without reading several modules

In other words, the factory is real, but it still behaves like a platform under construction rather than a fully polished production runtime.

## What should be preserved in a Factory V2

If this codebase evolves into a cleaner Factory V2, the following should be preserved:

1. The manifest-driven workflow
2. The resumable production engine
3. The approval-gated safety model
4. The offline-first defaults
5. The provider abstraction boundary
6. The ability to review and approve content before expensive steps

These are the strongest parts of the current system and should remain foundational.

## Recommended direction for the next iteration

A Factory V2 should probably aim for a simpler model:

- one canonical state machine for production progress
- one canonical manifest contract for stage artifacts
- one orchestration layer that both CLI and dashboard call
- one content generation pipeline that is easier to reason about than the current overlapping planning subsystems
- clearer separation between workflow control, content generation, and review UX

The current implementation is already strong enough to justify that simplification. The next step should be to reduce duplication and make the system feel more like a cohesive product and less like a collection of related capabilities.

## Safe refactor zones

If future work is undertaken, the safest refactor zones are:

- the production orchestration layer in [inside_case_factory/core/production.py](../inside_case_factory/core/production.py)
- the stage registry in [inside_case_factory/pipeline/stages.py](../inside_case_factory/pipeline/stages.py)
- the provider boundary in the providers package
- the dashboard actions that call the workflow engine
- the manifest contract around research, claims, scenes, media, and workflow state

These areas are central and already relatively well-defined, so they are better candidates for consolidation than the more experimental or feature-specific modules.

## Bottom line

Inside the Case Factory is already a thoughtful, ambitious production scaffold with real workflow structure, durable state, review gates, and a local rendering path. Its main weakness is not lack of capability; it is architectural sprawl. The system would benefit from consolidation around a smaller set of core abstractions while preserving the best parts of its current design.
