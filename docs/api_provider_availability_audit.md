# API/provider availability audit

Datum: 2026-07-21
Scope: alleen config/env-loading, provider/client-initialisatie, .env.example, en CI/devcontainer-achtige env-context.

## Bronnen (gecontroleerd)

Inside the Case Factory:
- inside_case_factory/config/env.py
- inside_case_factory/config/settings.py
- inside_case_factory/config/providers.toml
- inside_case_factory/core/research.py
- inside_case_factory/core/production.py
- inside_case_factory/providers/reasoning.py
- inside_case_factory/providers/production.py
- inside_case_factory/providers/elevenlabs.py
- inside_case_factory/providers/runtime_media.py
- inside_case_factory/pipeline/generator.py

OpenMontage:
- .env.example
- tools/base_tool.py
- tools/tool_registry.py
- lib/env_loader.py
- tools/google_credentials.py
- tools/graphics/*.py (openai, flux/recraft, pexels, pixabay, dashscope, google, grok)
- tools/video/*.py (sora, seedance, minimax, kling, runway, heygen, gemini_omni, pixabay, pexels, higgsfield)
- tools/video/stock_sources/*.py (pexels, pixabay, unsplash, pond5, nara, coverr, videvo)
- tools/audio/*.py (openai_tts, elevenlabs_tts, suno, dashscope_tts, doubao_tts, google_tts)
- tools/analysis/*.py (azure_stt, dashscope_asr)
- .github/workflows/ci.yml

## Env-loading gedrag

Inside the Case Factory:
- Laadt .env via inside_case_factory/config/env.py vanuit Path.cwd().
- Trigger via inside_case_factory/config/settings.py in load_settings().
- Gevolg: gebruikt een lokale .env van de huidige werkdirectory (meestal repo-root).

OpenMontage:
- Laadt .env op meerdere runtime-punten vanuit de OpenMontage repo-root:
  - tools/base_tool.py (import-time)
  - tools/tool_registry.py (discover-time)
  - lib/env_loader.py (via python-dotenv)
- Zet alleen variabelen die nog niet in os.environ bestaan (dus shell-env wint).

Gedeelde shell versus lokale .env:
- Beide projecten draaien in dezelfde container en delen dezelfde inherente shell-omgeving.
- Beide projecten laden daarnaast hun eigen lokale .env-bestand uit hun eigen repo.
- CI in OpenMontage (.github/workflows/ci.yml) definieert geen provider keys.
- In deze workspace is geen .devcontainer/devcontainer.json of andere project-specifieke env-injectie gevonden voor deze twee repos.

## .env.example dekking

- Inside the Case Factory: geen .env.example bestand gevonden.
- OpenMontage: .env.example aanwezig en bevat provider-variabelen voor media/voice/video/analyse.

## Provider matrix

Legenda:
- Inside leest?: key wordt in code/config gelezen voor die provider.
- OpenMontage leest?: key wordt in runtime-code gelezen voor die provider.
- Runtime env gedefinieerd?: in huidige shell os.environ (niet lokale .env) aanwezig.
- Naam-match?: gebruiken beide projecten dezelfde variabelenaam voor vergelijkbare capability.

| Provider | Verwachte env var(s) | Inside leest? | OpenMontage leest? | Runtime env gedefinieerd? | Naam-match? | Capability |
|---|---|---|---|---|---|---|
| OpenAI | OPENAI_API_KEY | Ja | Ja | Nee | Ja | Reasoning/text, TTS, image generatie; in OpenMontage ook Sora video |
| ElevenLabs | ELEVENLABS_API_KEY | Ja | Ja | Nee | Ja | TTS; in OpenMontage ook music/sfx tooling |
| Pexels | PEXELS_API_KEY | Nee | Ja | Nee | N.v.t. (Inside gebruikt niet) | Stock afbeeldingen en video |
| Pixabay | PIXABAY_API_KEY | Nee | Ja | Nee | N.v.t. (Inside gebruikt niet) | Stock afbeeldingen en video |
| Unsplash | UNSPLASH_ACCESS_KEY | Nee | Ja | Nee | N.v.t. (Inside gebruikt niet) | Stock afbeeldingen |
| Tavily | TAVILY_API_KEY | Ja | Nee | Nee | N.v.t. (OpenMontage gebruikt niet) | Geautomatiseerd research/search + extract |
| fal.ai gateway | FAL_KEY, FAL_AI_API_KEY | Nee (Inside gebruikt deze namen niet) | Ja | Nee | Nee | Image/video gateways (Flux/Recraft/Seedance/Kling/Minimax/Veo paden) |
| BFL direct (Flux) | BFL_API_KEY | Ja (config voor flux provider) | Nee | Nee | Nee | Flux image via api.bfl.ai in Inside |
| Google/Gemini | GEMINI_API_KEY (Inside), GOOGLE_API_KEY of GEMINI_API_KEY (OpenMontage) | Ja (GEMINI_API_KEY) | Ja (beide namen) | Nee | Gedeeltelijk | Gemini text/image/video en Google TTS/Imagen/Vertex paden |
| Anthropic (Claude) | ANTHROPIC_API_KEY | Ja | Nee | Nee | N.v.t. | Claude text reasoning/provider fallback in Inside |
| Kling official | KLING_API_KEY, KLING_API_BASE_URL | Nee | Ja | Nee | N.v.t. | Official Kling video/image/TTS/avatar/lip-sync |
| HeyGen | HEYGEN_API_KEY | Nee | Ja | Nee | N.v.t. | Video gateway/provider |
| Runway | RUNWAY_API_KEY (alias RUNWAYML_API_SECRET) | Nee | Ja | Nee | N.v.t. | Runway video generatie |
| xAI Grok | XAI_API_KEY | Nee | Ja | Nee | N.v.t. | Grok image/video |
| DashScope | DASHSCOPE_API_KEY | Nee | Ja | Nee | N.v.t. | Qwen image/TTS/ASR |
| Suno | SUNO_API_KEY | Nee | Ja | Nee | N.v.t. | Muziek generatie |
| Replicate | REPLICATE_API_TOKEN | Nee | Ja | Nee | N.v.t. | Seedance via Replicate |
| Higgsfield | HIGGSFIELD_KEY of HIGGSFIELD_API_KEY + HIGGSFIELD_API_SECRET | Nee | Ja | Nee | N.v.t. | Higgsfield video |
| Azure Speech | AZURE_SPEECH_KEY + AZURE_SPEECH_REGION/ENDPOINT | Nee | Ja | Nee | N.v.t. | Cloud speech-to-text |
| Doubao Speech | DOUBAO_SPEECH_API_KEY (+ DOUBAO_SPEECH_VOICE_TYPE) | Nee | Ja | Nee | N.v.t. | Doubao TTS |
| Pond5 | POND5_API_KEY | Nee | Ja | Nee | N.v.t. | Extra stock bron |
| Nara | NARA_API_KEY | Nee | Ja | Nee | N.v.t. | Extra stock bron |
| Coverr | COVERR_API_KEY | Nee | Ja | Nee | N.v.t. | Extra stock bron |
| Videvo | VIDEVO_API_KEY | Nee | Ja | Nee | N.v.t. | Extra stock bron |
| Freesound | FREESOUND_API_KEY | Nee | Ja | Nee | N.v.t. | Sound/music bibliotheekbron |

## Beschikbaarheid per project (op basis van lokale .env namen)

Opmerking: dit gaat om naam-aanwezigheid in lokale .env bestanden, niet om waarde-validiteit en niet om shell-export.

- Alleen beschikbaar voor OpenMontage (in OpenMontage .env, niet in Inside .env):
  - ELEVENLABS_API_KEY
  - PEXELS_API_KEY
  - PIXABAY_API_KEY
  - UNSPLASH_ACCESS_KEY
  - FAL_KEY
  - FAL_AI_API_KEY
  - GOOGLE_API_KEY
  - REPLICATE_API_TOKEN
  - KLING_API_KEY
  - KLING_API_BASE_URL
  - HEYGEN_API_KEY
  - RUNWAY_API_KEY
  - XAI_API_KEY
  - DASHSCOPE_API_KEY
  - SUNO_API_KEY
  - HIGGSFIELD_API_KEY
  - HIGGSFIELD_API_SECRET
  - AZURE_SPEECH_KEY
  - AZURE_SPEECH_REGION
  - GOOGLE_APPLICATION_CREDENTIALS
  - GOOGLE_CLOUD_PROJECT
  - GOOGLE_CLOUD_LOCATION
  - DOUBAO_SPEECH_API_KEY
  - DOUBAO_SPEECH_VOICE_TYPE

- Alleen beschikbaar voor Inside the Case Factory (in Inside .env, niet in OpenMontage .env):
  - TAVILY_API_KEY

- Beschikbaar in beide lokale .env bestanden:
  - OPENAI_API_KEY

- In huidige shell-runtime (os.environ) aanwezig:
  - Geen van de geaudite variabelen was gezet in de actieve shell op auditmoment.

## Naming mismatches

1. fal.ai/BFL mismatch:
- Inside gebruikt BFL_API_KEY voor flux in config/providers.toml.
- OpenMontage gebruikt FAL_KEY met alias FAL_AI_API_KEY voor vergelijkbare generatiepaden.

2. Google/Gemini mismatch:
- Inside verwacht voor Gemini uitsluitend GEMINI_API_KEY.
- OpenMontage accepteert GEMINI_API_KEY of GOOGLE_API_KEY (alias/fallback).

3. Runway alias (OpenMontage intern):
- OpenMontage ondersteunt RUNWAY_API_KEY en RUNWAYML_API_SECRET.
- Inside heeft geen overeenkomstige provider-key.

## Providers geconfigureerd maar niet daadwerkelijk aangeroepen door Inside

Bevinding op runtime-pad in Inside:
- ProductionProviderRouter.execute() wordt gebruikt voor:
  - voice_over (voice)
  - scene_image (image)
- Geen directe execute-pad gevonden voor text-kind via ProductionProviderRouter.

Concreet:
- Geconfigureerd in config/providers.toml maar niet actief aangeroepen via router execute-pad:
  - production.providers.openai_text
  - production.providers.gemini_text
  - production.providers.claude_text
  - production.providers.local_text

Nuance:
- Text reasoning gebeurt in Inside via providers/reasoning.py (OpenAIReasoningProvider of StructuredTextReasoningProvider), niet via het production text-routerpad.

## Kleinste veilige integratie-aanpassingen (niet geïmplementeerd)

1. Variabelenaam-harmonisatie zonder secrets te verplaatsen:
- Voeg in Inside alias-resolutie toe voor:
  - fal.ai: eerst FAL_KEY/FAL_AI_API_KEY proberen, daarna BFL_API_KEY voor flux-pad.
  - Gemini: GOOGLE_API_KEY als fallback naast GEMINI_API_KEY.
- Behoud bestaande namen backward compatible.

2. Stock/media-provider adapters in Inside toevoegen:
- Voeg kleine provider-adapters toe voor Pexels/Pixabay/Unsplash die alleen env-reads en API-call wrappers doen.
- Koppel ze in bestaande scene-image/media selectieflow als optionele bron voor approved/local media vóór AI-fallback.

3. OpenMontage provider-compatibiliteit gefaseerd toevoegen:
- Start met alleen read-only beschikbaarheidsdetectie + no-op selectie in Inside (zonder direct generatiepad te wijzigen).
- Activeer daarna gefaseerd per provider (bijv. Pexels eerst, daarna Pixabay/Unsplash, daarna fal-gateway varianten).

4. Config expliciet houden:
- Breid Inside config/providers.toml uit met disabled-by-default entries voor nieuwe providers en expliciete api_key_env velden.
- Geen automatische key-copy tussen projectbestanden.

5. Operationele veiligheid:
- Houd paid-call bevestiging en budgetgates van Inside intact voor elke nieuwe externe provider.

## Eindconclusie

- Beide projecten delen dezelfde basis shell-omgeving, maar laden daarnaast elk hun eigen lokale .env.
- OpenMontage heeft brede media-providerdekking en leest veel meer provider-keys.
- Inside gebruikt momenteel vooral OPENAI_API_KEY en TAVILY_API_KEY actief op kritieke paden, plus optionele provider-keys via config.
- De belangrijkste compatibiliteitsgaten zijn fal.ai/BFL naamverschil en beperkte stock/media-providerintegratie in Inside.
