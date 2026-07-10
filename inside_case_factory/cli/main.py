from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from inside_case_factory import __version__
from inside_case_factory.core.media import add_image_asset
from inside_case_factory.core.research import TavilyResearchProvider, tavily_config_from_settings
from inside_case_factory.config.settings import load_settings
from inside_case_factory.core.project import create_project
from inside_case_factory.pipeline.generator import generate_video_project
from inside_case_factory.pipeline.stages import describe_pipeline
from inside_case_factory.providers.elevenlabs import (
    ElevenLabsError,
    ElevenLabsVoiceOverProvider,
    elevenlabs_config_from_settings,
)
from inside_case_factory.rendering.ffmpeg import ffmpeg_available, ffmpeg_version
from inside_case_factory.providers.reasoning import estimate_reasoning_cost, reasoning_config_from_settings
from inside_case_factory.web.dashboard import run_dashboard


DEFAULT_ELEVENLABS_TEST_TEXT = (
    "[whispers] Inside the Case begins with one quiet detail. "
    "[curious] A locked room, a missing hour, and a witness who remembers too much. "
    "[sighs] Then the evidence shifts... and nothing feels accidental anymore."
)


def _print_json(payload: object) -> None:
    print(json.dumps(payload, indent=2))


def cmd_health(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    _print_json(
        {
            "application": settings.app.get("name", "Inside the Case Factory"),
            "version": __version__,
            "root": str(settings.root),
            "python": sys.version.split()[0],
            "ffmpeg_available": ffmpeg_available(),
            "ffmpeg": ffmpeg_version(),
            "paid_providers_allowed": settings.pipeline.get("allow_paid_providers", False),
            "publishing_allowed": settings.pipeline.get("allow_publish", False),
        }
    )
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    _print_json(describe_pipeline())
    return 0


def cmd_estimate_cost(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    estimate = estimate_reasoning_cost(reasoning_config_from_settings(settings.providers.get("reasoning", {})))
    estimate["target_duration_minutes"] = args.duration
    estimate["note"] = "Planning estimate only; no API was called. Token caps, not duration, determine this maximum."
    _print_json(estimate)
    return 0


def cmd_init_project(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    project = create_project(settings.projects_dir, args.topic, args.slug)
    _print_json(
        {
            "created": True,
            "slug": project.slug,
            "topic": project.topic,
            "manifest": str(project.manifest_path),
        }
    )
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    problems: list[str] = []
    if settings.pipeline.get("allow_paid_providers"):
        problems.append("Paid providers are enabled; this first scaffold expects them disabled.")
    if settings.pipeline.get("allow_publish"):
        problems.append("Publishing is enabled; YouTube upload must stay disabled for now.")
    if not ffmpeg_available():
        problems.append("FFmpeg is not available on PATH.")

    _print_json(
        {
            "ok": not problems,
            "problems": problems,
            "projects_dir": str(settings.projects_dir),
            "default_project": settings.default_project,
            "providers": settings.providers,
        }
    )
    return 1 if problems else 0


def cmd_generate(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    result = generate_video_project(settings, args.topic, args.slug)
    _print_json(
        {
            "project": result.project_slug,
            "project_root": str(result.project_root),
            "final_video": str(result.final_video),
            "duration_seconds": round(result.duration_seconds, 3),
        }
    )
    return 0


def _elevenlabs_provider_from_args(args: argparse.Namespace) -> ElevenLabsVoiceOverProvider:
    settings = load_settings(Path(args.root))
    voice_settings = settings.providers.get("voice_over", {})
    elevenlabs_settings = voice_settings.get("elevenlabs", {})
    config = elevenlabs_config_from_settings({**elevenlabs_settings, "enabled": True})
    return ElevenLabsVoiceOverProvider(config)


def cmd_elevenlabs_voices(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    if not settings.pipeline.get("allow_paid_providers", False) or not args.confirm_paid:
        _print_json({"ok": False, "message": "Paid API call blocked. Enable paid providers and pass --confirm-paid explicitly."})
        return 0
    if not os.environ.get("ELEVENLABS_API_KEY"):
        _print_json({"ok": False, "message": "ELEVENLABS_API_KEY is not set."})
        return 0
    provider = _elevenlabs_provider_from_args(args)
    try:
        voices = provider.list_voices(page_size=args.limit)
    except ElevenLabsError as error:
        _print_json({"ok": False, "message": str(error)})
        return 1
    _print_json(
        {
            "ok": True,
            "voices": [
                {
                    "voice_id": voice.get("voice_id"),
                    "name": voice.get("name"),
                    "category": voice.get("category"),
                    "description": voice.get("description"),
                }
                for voice in voices.get("voices", [])
            ],
            "has_more": voices.get("has_more"),
            "next_page_token": voices.get("next_page_token"),
        }
    )
    return 0


def cmd_elevenlabs_test(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    if not settings.pipeline.get("allow_paid_providers", False) or not args.confirm_paid:
        _print_json({"ok": False, "message": "Paid API call blocked. Enable paid providers and pass --confirm-paid explicitly."})
        return 0
    if not os.environ.get("ELEVENLABS_API_KEY"):
        _print_json({"ok": False, "message": "ELEVENLABS_API_KEY is not set."})
        return 0
    provider = _elevenlabs_provider_from_args(args)
    output = Path(args.output)
    text_file = output.with_suffix(".txt")
    try:
        provider.synthesize_to_file(args.text, output, text_file)
    except ElevenLabsError as error:
        _print_json({"ok": False, "message": str(error)})
        return 1
    _print_json(
        {
            "ok": True,
            "output": str(output),
            "text_file": str(text_file),
            "provider": provider.name,
            "model_id": provider.config.model_id,
            "voice_id": provider.config.voice_id,
        }
    )
    return 0


def _split_scene_ids(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def cmd_media_add_image(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    project_root = settings.projects_dir / args.project
    if not project_root.is_dir():
        _print_json({"ok": False, "message": f"Project does not exist: {project_root}"})
        return 1

    try:
        asset = add_image_asset(
            project_root,
            Path(args.image),
            source_url=args.source_url,
            credit=args.credit,
            license_notes=args.license_notes,
            usage_notes=args.usage_notes,
            scene_relevance=args.scene_relevance,
            scene_ids=_split_scene_ids(args.scenes),
            media_id=args.media_id,
        )
    except OSError as error:
        _print_json({"ok": False, "message": str(error)})
        return 1

    _print_json(
        {
            "ok": True,
            "project": args.project,
            "media_manifest": str(project_root / "manifests" / "media_sources.json"),
            "asset": asset,
        }
    )
    return 0


def _tavily_provider_from_args(args: argparse.Namespace) -> TavilyResearchProvider:
    settings = load_settings(Path(args.root))
    research_settings = settings.providers.get("research", {})
    tavily_settings = research_settings.get("tavily", {}) if isinstance(research_settings, dict) else {}
    return tavily_config_from_settings(tavily_settings)


def cmd_tavily_search(args: argparse.Namespace) -> int:
    settings = load_settings(Path(args.root))
    if not settings.pipeline.get("allow_paid_providers", False) or not args.confirm_paid:
        _print_json({"ok": False, "message": "Paid API call blocked. Enable paid providers and pass --confirm-paid explicitly."})
        return 0
    provider = _tavily_provider_from_args(args)
    result = provider.search(args.query)
    if not result.get("ok"):
        _print_json(result)
        return 0
    _print_json(
        {
            "ok": True,
            "provider": provider.name,
            "results": [
                {
                    "title": item.get("title"),
                    "url": item.get("url"),
                    "score": item.get("score"),
                    "quality_score": item.get("quality_score"),
                    "source_type": item.get("source_type"),
                }
                for item in result.get("results", [])[: args.limit]
                if isinstance(item, dict)
            ],
        }
    )
    return 0


def cmd_dashboard(args: argparse.Namespace) -> int:
    run_dashboard(Path(args.root), args.host, args.port)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="case-factory")
    parser.add_argument("--root", default=".", help="Project root containing config/defaults.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    health = subparsers.add_parser("health", help="Show runtime and dependency status")
    health.set_defaults(func=cmd_health)

    plan = subparsers.add_parser("plan", help="Print the planned production pipeline")
    plan.set_defaults(func=cmd_plan)

    estimate_cost = subparsers.add_parser("estimate-cost", help="Estimate the configured maximum without calling an API")
    estimate_cost.add_argument("--duration", type=int, default=12, help="Target documentary duration in minutes")
    estimate_cost.set_defaults(func=cmd_estimate_cost)

    init_project = subparsers.add_parser("init-project", help="Create a new case project workspace")
    init_project.add_argument("topic", help="Video topic or case working title")
    init_project.add_argument("--slug", help="Optional project slug")
    init_project.set_defaults(func=cmd_init_project)

    doctor = subparsers.add_parser("doctor", help="Validate local safety and dependency settings")
    doctor.set_defaults(func=cmd_doctor)

    generate = subparsers.add_parser("generate", help="Run the offline end-to-end video pipeline")
    generate.add_argument("topic", help="Video topic or case working title")
    generate.add_argument("--slug", help="Optional project slug")
    generate.set_defaults(func=cmd_generate)

    elevenlabs = subparsers.add_parser("elevenlabs", help="ElevenLabs TTS utilities")
    elevenlabs_subparsers = elevenlabs.add_subparsers(dest="elevenlabs_command", required=True)

    voices = elevenlabs_subparsers.add_parser("voices", help="List available ElevenLabs voices")
    voices.add_argument("--limit", type=int, default=10, help="Maximum voices to request")
    voices.add_argument("--confirm-paid", action="store_true", help="Explicitly confirm this paid API call")
    voices.set_defaults(func=cmd_elevenlabs_voices)

    test = elevenlabs_subparsers.add_parser("test", help="Generate a short ElevenLabs voice sample")
    test.add_argument(
        "text",
        nargs="?",
        default=DEFAULT_ELEVENLABS_TEST_TEXT,
        help="Text to synthesize. Defaults to a short expressive Inside the Case v3 narration.",
    )
    test.add_argument("--output", default="projects/elevenlabs_v3_test.wav", help="Output WAV path")
    test.add_argument("--confirm-paid", action="store_true", help="Explicitly confirm this paid API call")
    test.set_defaults(func=cmd_elevenlabs_test)

    media = subparsers.add_parser("media", help="Manage real source media")
    media_subparsers = media.add_subparsers(dest="media_command", required=True)

    add_image = media_subparsers.add_parser("add-image", help="Copy a local image into a project media manifest")
    add_image.add_argument("project", help="Project slug under the configured projects directory")
    add_image.add_argument("image", help="Local image path to copy into assets/images")
    add_image.add_argument("--scene", "--scenes", dest="scenes", default="", help="Comma-separated scene IDs, for example s01,s03. Use * for every scene.")
    add_image.add_argument("--source-url", default="", help="Original source URL for the image")
    add_image.add_argument("--credit", default="", help="Required attribution or source credit")
    add_image.add_argument("--license-notes", default="", help="License or usage-rights notes")
    add_image.add_argument("--usage-notes", default="", help="Internal notes about approved usage")
    add_image.add_argument("--scene-relevance", default="", help="Why this image belongs in the mapped scene")
    add_image.add_argument("--media-id", help="Optional stable media ID")
    add_image.set_defaults(func=cmd_media_add_image)

    research = subparsers.add_parser("research", help="Research provider utilities")
    research_subparsers = research.add_subparsers(dest="research_command", required=True)
    tavily = research_subparsers.add_parser("tavily-search", help="Test Tavily search without writing project manifests")
    tavily.add_argument("query", help="Search query")
    tavily.add_argument("--limit", type=int, default=5, help="Maximum results to print")
    tavily.add_argument("--confirm-paid", action="store_true", help="Explicitly confirm this paid API call")
    tavily.set_defaults(func=cmd_tavily_search)

    dashboard = subparsers.add_parser("dashboard", help="Run the local web dashboard")
    dashboard.add_argument("--host", default="127.0.0.1", help="Host interface to bind")
    dashboard.add_argument("--port", type=int, default=8000, help="Port to listen on")
    dashboard.set_defaults(func=cmd_dashboard)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
