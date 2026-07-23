# Recycle Documentary workflow

The recycle workflow accepts a YouTube/Vimeo URL or a local video/audio file and uses the reference documentary as a narrative blueprint for a new production. The reference is never accepted as factual evidence. Every factual claim used in the new script must be linked to independently reviewed sources.

## Dashboard use

1. Open **Nieuw project** and select **Recycle Documentary**.
2. Add a YouTube/Vimeo URL or upload a local reference file.
3. Select the target duration, language, style, narrator, and review mode.
4. Create the project. The dashboard queues the recycle production automatically.
5. Review and approve independently discovered sources and claims before script generation.

Paid research remains behind the existing project-specific confirmation and budget gates.

## Complete-document processing

The intake reads native captions first. For YouTube it can fall back to subtitles downloaded by `yt-dlp`; when a local media copy exists and captions are unavailable, local Whisper transcription can be used. The timeline is divided into contiguous analysis windows of at most 45 seconds, including gaps between authored chapters. Every transcript segment must overlap an analysis window, and the transcript must reach at least 75% of the declared media duration. A truncated transcript blocks blueprint preparation instead of claiming full coverage.

When a local video copy exists, FFmpeg samples every analysis window and records scene-change, luminance, and saturation evidence. This is reported as `full_audiovisual`. If the video cannot be stored locally, the engine reports `transcript_and_timeline`; it does not pretend that the images were understood.

Coverage evidence is stored in `manifests/recycle_blueprint.json` and summarized in `manifests/recycle_engine_report.md`:

- analysis window count and maximum duration;
- transcript segment assignment and timeline reach;
- complete temporal coverage;
- visual sampling coverage;
- analysis grade.

## Verification and reconstruction

Reference claims are written to `recycle_verification_queue.json` as pending. The reference URL is stored as a rejected, blueprint-only source. Approval of independently sourced claims updates the verification audit; one approved claim can verify at most one reference claim. `recycle_verification_ready` is not set until at least one independently sourced match exists.

The blueprint supplies factual questions to the research plan and event-specific queries to scene/media discovery. `recycle_reconstruction_ready` is set only after scenes have actually been generated.

## Local prerequisites

- FFmpeg for duration probing, scene-change detection, and frame sampling.
- `yt-dlp` for a local YouTube working copy and subtitle fallback.
- Whisper only when neither native nor downloaded captions are available.

No paid provider is called merely by parsing or analyzing the reference. Independent external research follows the normal explicit confirmation and budget workflow.
