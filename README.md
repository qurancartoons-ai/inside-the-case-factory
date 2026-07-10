# Inside the Case Factory

Inside the Case Factory is an offline-first scaffold for a future automated YouTube production pipeline for cinematic documentary and true-crime style videos.

The current version does not call paid AI APIs, does not publish to YouTube, and does not pretend to generate finished videos. It creates the architecture needed to add replaceable providers later while preserving source tracking, review gates, and low-cost local operation in GitHub Codespaces.

## Current Capabilities

- Loads project configuration from TOML files in `config/`.
- Defines the full intended production pipeline as explicit stages.
- Provides replaceable provider interfaces for research, text generation, image generation, and voice-over.
- Includes offline stub providers so commands can run without paid services.
- Creates per-topic project workspaces with manifests, research folders, review folders, assets, and exports.
- Checks local runtime health and confirms FFmpeg availability.
- Runs an offline vertical-slice video pipeline with local TTS, generated visual assets, animated scenes, transitions, subtitles, and final MP4 rendering.

## Quick Start

Run from the repository root:

```bash
python3 -m inside_case_factory health
python3 -m inside_case_factory doctor
python3 -m inside_case_factory plan
python3 -m inside_case_factory init-project "The Disappearance of Example Jane" --slug example-jane
python3 -m inside_case_factory generate "The mysterious disappearance of Example Jane"
python3 -m inside_case_factory elevenlabs voices
python3 -m inside_case_factory dashboard --host 0.0.0.0 --port 8000
```

No dependency installation is required for this scaffold. `requirements.txt` is intentionally empty except for future notes.

## Autonomous offline verification

Repository agents follow [AGENTS.md](AGENTS.md). Run the complete unittest suite and health check in a fixed, fail-fast sequence with:

```bash
python3 -m inside_case_factory autonomous-check
```

In `auto` mode, the command also runs all Dutch language fixtures when the working tree contains script, prompt, validator, or Dutch fixture changes. Force that check for script-language work with:

```bash
python3 -m inside_case_factory autonomous-check --language always
```

The runner is entirely offline. It does not generate projects, call providers, alter approval gates, or hide subprocess output. A failed command is shown with its exact exit code; fix the root cause and rerun until all required checks pass.

## Project Structure

```text
inside_case_factory/        Python application package
  cli/                      Command line interface
  config/                   TOML settings loader
  core/                     Domain models and project creation
  pipeline/                 Stage registry and pipeline definitions
  providers/                Replaceable provider contracts and local stubs
  rendering/                FFmpeg integration helpers
  reports/                  Future reporting outputs
  schemas/                  Future JSON schemas for manifests
  utils/                    Shared utilities
config/                     Local configuration
docs/                       Architecture and implementation notes
projects/                   Per-video production workspaces
research/                   Legacy/general research folder from initial workspace
scripts/                    Legacy/general scripts folder from initial workspace
make_video.py               Existing FFmpeg proof-of-concept script
```

## Safety Defaults

The default configuration keeps the expensive and risky actions off:

- `allow_paid_providers = false`
- `require_paid_api_confirmation = true`
- `default_project_budget_usd = 0.25`
- `dry_run = true`
- `allow_publish = false`
- `stop_before_expensive_steps = true`
- Human review gates enabled after research, script, scene planning, generation prompts, rendering, and before publishing.

## Low-cost model strategy

Reasoning is disabled by default. When it is deliberately enabled later, each pipeline stage may select its own model in `config/providers.toml`:

- `gpt-4.1-nano` handles research-plan preparation, source cleaning, relevance filtering, claim extraction, and scene structuring.
- `gpt-4.1-mini` is reserved for the final narrative outline and documentary script.
- No stage defaults to GPT-5.5.

The checked-in token prices are editable planning assumptions, not live price quotes. Preview the configured worst-case estimate without making a network request:

```bash
python3 -m inside_case_factory estimate-cost --duration 12
```

Every new prompt-production project also receives `manifests/cost_estimate.json` before production proceeds. A paid reasoning call requires all of the following: paid providers enabled globally, reasoning enabled, dry-run disabled, an API key, sufficient project budget, and a project-local `paid_api_confirmation.json` containing `{"confirmed": true}`. Tavily and ElevenLabs are likewise blocked by default; their standalone CLI commands additionally require `--confirm-paid`.

Script generation uses a bounded validation retry: one initial low-cost script attempt and at most two revisions. Every candidate is validated immediately, a revision is requested only for concrete validator failures, and generation stops as soon as the first fully valid candidate is safely promoted. Confirmation and per-project spending checks still run before every provider call.

An isolated Dutch calibration can exercise the same behavior with `calibrate-dutch-script --confirm-up-to-three-paid-calls`. This explicit confirmation permits no more than three script calls, validates and records every candidate, and stops at the first accepted result. The command rejects a run before calling the provider when its cumulative worst-case estimate exceeds the configured project budget.

Keep API keys out of the repository. Confirmation only unlocks a configured call; it never bypasses the project budget.

## ElevenLabs TTS

ElevenLabs is optional. The pipeline keeps the offline FFmpeg Flite voice-over fallback, so generation still works when no API key is present.

The default ElevenLabs model is Eleven v3, configured as `model_id = "eleven_v3"`. ElevenLabs documents this model as its expressive text-to-speech model for narration, emotional delivery, and 70+ languages. The model remains configurable in `config/providers.toml` so you can switch back to another supported model later.

Do not put API keys in the repository. Set the key in your Codespace shell instead:

```bash
export ELEVENLABS_API_KEY="your_api_key_here"
```

Then enable ElevenLabs in [config/providers.toml](config/providers.toml):

```toml
[voice_over]
provider = "auto"
fallback_provider = "ffmpeg_flite"

[voice_over.elevenlabs]
enabled = true
voice_id = "your_voice_id"
model_id = "eleven_v3"
stability = 0.55
similarity_boost = 0.80
style = 0.0
use_speaker_boost = true
output_format = "mp3_44100_128"
```

The checked-in default voice ID is still `JBFqnCBsd6RMkjVDRZzb`; replace `voice_id` only if you want a different voice. Eleven v3 supports expressive bracketed audio tags in the narration text, such as `[whispers]`, `[curious]`, `[excited]`, and `[sighs]`. The provider passes those tags through directly to the ElevenLabs Text to Speech API. ElevenLabs notes that tag effectiveness depends on the selected voice and stability setting, so test your production voice before relying on a specific delivery.

Useful commands:

```bash
python3 -m inside_case_factory elevenlabs voices
python3 -m inside_case_factory elevenlabs test --output projects/elevenlabs_v3_test.wav
python3 -m inside_case_factory elevenlabs test "[whispers] Inside the Case begins... [sighs] then the evidence shifts." --output projects/custom_v3_test.wav
```

If `ELEVENLABS_API_KEY` is missing, these commands will report that safely and the main `generate` command will continue with offline TTS.

## Local Web Dashboard

The dashboard is a local Python web app for using Inside the Case Factory from a Chromebook without typing long commands. It uses the built-in Python WSGI server and the existing project pipeline; it does not publish to YouTube and does not call paid image or video APIs.

Start it from the repository root:

```bash
python3 -m inside_case_factory dashboard --host 0.0.0.0 --port 8000
```

In Codespaces, open the forwarded port `8000` in your browser. The dashboard lets you:

- start production from one natural-language prompt
- create a new video project from a topic
- view project status and generated manifests
- preview `script.json`, `scenes.json`, `visual_prompts.json`, `media_sources.json`, and `render_plan.json`
- upload real local images into a project
- store image source URL, credit, license notes, usage notes, and scene relevance
- map uploaded images to generated scenes
- generate/render a project with the existing local FFmpeg pipeline
- open or download the final MP4 when rendering is complete

Real images mapped in `media_sources.json` are used first by the render pipeline; generated placeholders are used only when a scene has no mapped real image. ElevenLabs v3 support remains unchanged and is still controlled by `config/providers.toml` and `ELEVENLABS_API_KEY`.

### Prompt Production Interface

The homepage has a simple agent-style production form:

- production prompt
- target duration
- language
- autonomy mode
- `Start Production`

Example prompt:

```text
Create a 12-minute Inside the Case documentary about the death of Michael Jackson. Focus on his final 24 hours, Conrad Murray, the emergency response, the investigation and the trial. Make it suspenseful but strictly factual. Use real archival media where possible.
```

`Start Production` creates a project, writes `production_request.json`, `production_plan.json`, and `production_activity.json`, then begins the safe staged workflow.

Autonomy modes:

- `Review Mode`: runs safe setup/research, then pauses for source and claim review. It also pauses after script and media selection.
- `Automatic Mode`: continues through safe stages only when the required approvals already exist. It never approves research, scripts, or media by itself, and never publishes to YouTube.

Provider behavior:

- Tavily is used only when `TAVILY_API_KEY` is set.
- ElevenLabs is used only when `ELEVENLABS_API_KEY` is set and the existing ElevenLabs config allows it.
- API keys must stay in environment variables; do not put them in the repository.
- If keys are missing, the dashboard writes a clear blocked status and uses safe fallbacks without fabricating facts.

The project page shows the production plan, current activity, recent activity log, and stage status. Manual source, claim, script, scene, and media controls are still available under `Advanced`.

### Factual Research and Script Workflow

Real documentary projects use an approval-gated factual workflow. Do not use the legacy one-step sample generator for factual cases.

Each project now has these research manifests:

- `manifests/sources.json`
- `manifests/research.json`
- `manifests/timeline.json`
- `manifests/claims.json`
- `manifests/workflow.json`

The dashboard workflow is:

1. Research
2. Review Sources & Claims
3. Approve Research
4. Generate Script
5. Review/Edit Script
6. Approve Script
7. Generate Scenes
8. Discover Media
9. Review Media
10. Generate Voice-over
11. Render Video

Sources store title, URL, publisher, publication date, source type, access date, and reliability notes. Claims must link to one or more source IDs and remain reviewable. Research approval requires at least one approved source and one approved claim.

Script generation is blocked until research is approved. The current script generator is deterministic and only uses approved source-backed claims; it does not invent facts or citations. You can edit the generated script directly in the dashboard before approving it.

Scene generation is blocked until the script is approved. Generated scenes include narration, estimated duration, factual claim IDs, people, locations, dates, events, archival media search queries, and fallback visual prompts for cases where real media is unavailable.

Live automated factual research is intentionally not faked. The current provider architecture includes a manual provider and is ready for a real web/search provider, but it will not fabricate Michael Jackson research without a configured external research API or manually entered source-backed claims.

#### Tavily Research Provider

Tavily is the first automated research provider. It uses `TAVILY_API_KEY` from your environment and never stores API keys in the repository.

```bash
export TAVILY_API_KEY="your_tavily_key_here"
python3 -m inside_case_factory research tavily-search "The death of Michael Jackson"
```

Provider configuration lives in [config/providers.toml](config/providers.toml):

```toml
[research]
provider = "local_stub"

[research.tavily]
enabled = false
max_results = 8
search_depth = "advanced"
include_domains = []
exclude_domains = []
```

The dashboard button `Run Automated Research` calls Tavily when `TAVILY_API_KEY` is set. It adds retrieved sources and drafted claim candidates to `sources.json` and `claims.json` as `pending_review`. It does not approve research, sources, or claims automatically. If the key is missing, the dashboard and CLI report that safely and write no fake facts.

Tavily claim drafting is conservative: it extracts candidate factual sentences from retrieved source content and links each claim to the source ID that produced it. You must review the original source and approve or reject each source and claim before script generation can proceed.

### Archival Media Discovery

The dashboard can search supported public archive APIs for candidate images. The first connectors are:

- Wikimedia Commons through the MediaWiki Action API.
- Internet Archive through the Advanced Search and item image services.

Use the `Discover Archival Media` form on a project page to search by topic, people, locations, dates, and events. Discovery downloads preview images into the project when the source exposes a usable preview URL and writes every candidate into `manifests/media_sources.json` with:

- original source URL
- title
- creator
- date
- license
- attribution requirements
- usage notes
- copyright status flag
- exact or near-duplicate fields
- relevance score
- suggested scene mappings
- source-specific metadata

Discovered images enter the dashboard review queue as `review_status = "pending_review"`. They are not used by the render pipeline until you approve them. Unknown or restrictive copyright status is flagged in the review queue and must be checked before approval. Rejected items remain in provenance records but are ignored by rendering.

The discovery layer only uses official APIs or clearly documented programmatic endpoints. Do not add connectors that scrape sites whose terms or robots policy prohibit automated access.

## Real Media Sources

Each project includes a source manifest at `manifests/media_sources.json`. This manifest is for manually curated real media before automated research or media sourcing exists. Store only images you own, have licensed, or have permission to use.

The manifest tracks:

- local asset path under `assets/images`
- source URL
- credit or attribution
- license and usage notes
- scene relevance
- mapped scene IDs

Add a local image to a project:

```bash
python3 -m inside_case_factory init-project "The Disappearance of Example Jane" --slug example-jane
python3 -m inside_case_factory media add-image example-jane ./local-photo.jpg \
  --scene s01 \
  --source-url "https://example.com/archive/local-photo" \
  --credit "Example Archive / Photographer Name" \
  --license-notes "Used with permission for project draft." \
  --usage-notes "Approved for internal render test." \
  --scene-relevance "Exterior location for the cold open."
```

Map one image to multiple scenes with `--scene s01,s03`, or use `--scene "*"` as a project-wide fallback image. During rendering, the pipeline uses a mapped real image first. If no usable real image is mapped for a scene, it falls back to the generated local SVG placeholder.

The render plan records the selected media for each scene in `manifests/render_plan.json`, and each scene records its selected `media_source` in `manifests/scenes.json`.

## Adding Providers Later

Provider SDKs should be wrapped behind the interfaces in `inside_case_factory/providers/base.py`. A provider should return structured payloads and cost estimates rather than writing directly to final outputs. This keeps human review and source tracking possible before costly generation or publishing.

Recommended order for real integrations:

1. Source-backed research provider.
2. Fact extraction and claim/source mapping.
3. Script generation from approved facts only.
4. Scene planning and image prompt generation.
5. Manual image import or low-cost image generation.
6. FFmpeg render assembly.
7. Voice-over integration.
8. YouTube packaging.
9. Upload and scheduling only after explicit approval workflows exist.

See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for the detailed roadmap.
