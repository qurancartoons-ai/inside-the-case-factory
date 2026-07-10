# Implementation Plan

## Goal

Build a modular, low-cost AI-assisted video production system for cinematic documentary and true-crime YouTube videos. The system should keep facts traceable, require human review before expensive generation and publishing, and remain usable from a Chromebook through GitHub Codespaces.

## Guiding Architecture

The pipeline should be manifest-driven. Each stage reads structured inputs, writes structured outputs, and records provenance. This avoids a fragile one-shot script and makes review, regeneration, batching, and provider replacement practical.

## Pipeline Stages

1. Topic intake: capture the working title, scope, target runtime, tone, and constraints.
2. Research: collect reliable sources with URLs, publishers, retrieval dates, and notes.
3. Fact check: extract claims and map every factual statement to one or more sources.
4. Script: produce narration only from approved claims.
5. Scene plan: split the script into cinematic beats with estimated timing.
6. Image prompts: create detailed prompts, negative prompts, continuity notes, and rights notes.
7. Asset generation/import: generate or manually import images, clips, maps, documents, and stills.
8. Voice-over: generate or import narration audio.
9. Edit plan: synchronize visual beats, narration, effects, subtitles, and transitions.
10. Subtitles: create SRT/VTT from narration timing.
11. Render: use FFmpeg to assemble the final video.
12. Package: create thumbnail concept, YouTube title, description, chapters, and tags.
13. Publish: later upload and schedule only after explicit human approval.

## Review Gates

Human review should happen:

- After research, before scripting.
- After script, before scene planning.
- After scene planning and prompt generation, before paid visual generation.
- Before voice-over generation.
- Before final rendering.
- Before any publishing or scheduling.

## Data Model

Recommended manifests:

- `project.json`: topic, slug, status, paths, dates.
- `research/dossier.json`: source records and research notes.
- `research/claims.json`: individual claims, source references, confidence, review status.
- `script.json`: narration sections with linked claim IDs.
- `scenes.json`: scene timing, visual intent, evidence references, mood, camera direction.
- `image_prompts.json`: prompts, negative prompts, continuity constraints, provider settings.
- `assets.json`: generated/imported asset inventory, license notes, source paths.
- `edit_plan.json`: timeline, transitions, effects, audio sync, subtitle references.
- `youtube_package.json`: title, description, tags, chapters, thumbnail concept.

## Provider Strategy

All AI and media providers should be replaceable modules. Each provider should expose capabilities through narrow interfaces and return structured results with estimated cost, provenance, and review requirements.

Initial provider categories:

- Research provider
- Text generation provider
- Image generation provider
- Image-to-video provider
- Voice-over provider
- Render provider
- Publishing provider

The current scaffold includes only offline stubs and local FFmpeg detection.

## Near-Term Milestones

### Milestone 1: Foundation

- Create configuration files.
- Create Python package and CLI.
- Define pipeline stages.
- Define provider interfaces.
- Create project workspace generator.
- Add health and doctor commands.

### Milestone 2: Research And Source Tracking

- Add source manifest schema.
- Add manual source ingestion commands.
- Add claim extraction format.
- Add review status updates for claims.
- Add validation that script lines cite approved claims.

### Milestone 3: Script And Scene Planning

- Add script manifest schema.
- Add scene manifest schema.
- Add local template-based drafts.
- Add review commands for approving or rejecting stages.

### Milestone 4: Render Prototype

- Accept manually provided images and audio.
- Generate subtitle files.
- Create FFmpeg render plans.
- Render a simple but cinematic video from real visual assets, not black text screens.

### Milestone 5: Provider Integrations

- Add real providers behind interfaces.
- Add cost estimates and budget checks.
- Add dry-run previews.
- Add provider-specific config files excluded from source control.

### Milestone 6: Batch Production

- Add queue manifests.
- Add resumable stage execution.
- Add per-project logs and reports.

### Milestone 7: Publishing

- Add YouTube metadata validation.
- Add explicit publish approval.
- Add upload and scheduling provider.

## First Task Outcome

This first task should stop at the foundation. It should not implement full research, generation, voice-over, or publishing.
