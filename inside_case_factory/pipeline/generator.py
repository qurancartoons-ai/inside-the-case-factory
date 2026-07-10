from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.media import ensure_media_manifest, image_for_scene
from inside_case_factory.core.project import create_project
from inside_case_factory.pipeline.sample_content import build_sample_script, build_visual_prompt
from inside_case_factory.providers.elevenlabs import (
    ElevenLabsVoiceOverProvider,
    elevenlabs_config_from_settings,
)
from inside_case_factory.providers.offline_media import FFmpegFliteVoiceOverProvider, SVGPlaceholderImageProvider
from inside_case_factory.rendering.probe import media_duration_seconds
from inside_case_factory.utils.files import write_json
from inside_case_factory.utils.text import compact_whitespace, svg_escape


@dataclass(frozen=True)
class GeneratedVideo:
    project_slug: str
    project_root: Path
    final_video: Path
    duration_seconds: float


def _progress(message: str) -> None:
    print(f"[case-factory] {message}", flush=True)


def _run(command: list[str]) -> None:
    if command and command[0] == "ffmpeg":
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", *command[1:]]
    subprocess.run(command, check=True)


def _format_timestamp(seconds: float) -> str:
    millis = int(round(seconds * 1000))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, ms = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{secs:02},{ms:03}"


def _wrap_subtitle(text: str, width: int = 58) -> str:
    words = compact_whitespace(text).split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) > width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return "\n".join(lines[:3])


def _write_srt(path: Path, scenes: list[dict[str, object]]) -> None:
    lines: list[str] = []
    for index, scene in enumerate(scenes, start=1):
        start = float(scene["start_seconds"])
        end = float(scene["end_seconds"])
        lines.extend(
            [
                str(index),
                f"{_format_timestamp(start)} --> {_format_timestamp(end)}",
                _wrap_subtitle(str(scene["narration"])),
                "",
            ]
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def _scene_svg(title: str, scene: dict[str, object], index: int) -> str:
    palettes = [
        ("#0b1217", "#1f3a46", "#b89154", "#d8dde0"),
        ("#111018", "#32304f", "#8b3244", "#e0d5c6"),
        ("#15120f", "#3b3327", "#a75138", "#d9d2c5"),
        ("#0d1412", "#263e35", "#9b2f31", "#d9dfd9"),
        ("#12151b", "#344052", "#c7a15d", "#e7e4dc"),
    ]
    bg, mid, accent, ink = palettes[(index - 1) % len(palettes)]
    heading = svg_escape(str(scene["heading"]).upper())
    short_title = svg_escape(title[:52])
    prompt_hint = svg_escape(str(scene["visual_summary"])[:86])
    number = f"{index:02}"

    # Local generated asset: layered SVG with document cards, map lines,
    # surveillance frames, grain, and color grading cues.
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="{bg}"/>
      <stop offset="55%" stop-color="{mid}"/>
      <stop offset="100%" stop-color="#050607"/>
    </linearGradient>
    <radialGradient id="light" cx="68%" cy="32%" r="70%">
      <stop offset="0%" stop-color="{accent}" stop-opacity="0.55"/>
      <stop offset="45%" stop-color="{mid}" stop-opacity="0.18"/>
      <stop offset="100%" stop-color="#000000" stop-opacity="0.72"/>
    </radialGradient>
    <filter id="grain">
      <feTurbulence type="fractalNoise" baseFrequency="0.85" numOctaves="2" stitchTiles="stitch"/>
      <feColorMatrix type="saturate" values="0"/>
      <feComponentTransfer>
        <feFuncA type="table" tableValues="0 0.16"/>
      </feComponentTransfer>
    </filter>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="0" dy="18" stdDeviation="18" flood-color="#000000" flood-opacity="0.45"/>
    </filter>
  </defs>
  <rect width="1920" height="1080" fill="url(#bg)"/>
  <rect width="1920" height="1080" fill="url(#light)"/>
  <path d="M0 830 C330 760 520 870 760 790 C1050 695 1240 760 1540 640 C1710 575 1820 590 1920 548 L1920 1080 L0 1080 Z" fill="#040506" opacity="0.55"/>
  <g opacity="0.22" stroke="{ink}" stroke-width="2">
    <path d="M110 225 C390 170 550 260 805 220 S1280 100 1730 205"/>
    <path d="M190 735 C420 610 675 650 905 545 S1310 430 1695 465"/>
    <path d="M310 105 L360 910 M715 90 L670 940 M1130 120 L1175 905 M1540 115 L1495 930"/>
  </g>
  <g filter="url(#shadow)">
    <rect x="170" y="150" width="520" height="350" rx="10" fill="#e6ded0" opacity="0.91" transform="rotate(-4 430 325)"/>
    <rect x="232" y="210" width="240" height="28" fill="#222831" opacity="0.75" transform="rotate(-4 430 325)"/>
    <rect x="232" y="270" width="355" height="12" fill="#222831" opacity="0.38" transform="rotate(-4 430 325)"/>
    <rect x="232" y="305" width="310" height="12" fill="#222831" opacity="0.28" transform="rotate(-4 430 325)"/>
    <rect x="232" y="340" width="380" height="12" fill="#222831" opacity="0.28" transform="rotate(-4 430 325)"/>
    <rect x="1125" y="150" width="520" height="330" rx="8" fill="#0b0f12" opacity="0.88"/>
    <rect x="1160" y="185" width="450" height="250" fill="{mid}" opacity="0.75"/>
    <circle cx="1220" cy="225" r="10" fill="{accent}"/>
    <text x="1185" y="420" fill="{ink}" font-family="DejaVu Sans, Arial" font-size="24" opacity="0.82">CAM 03 / LOCAL PLACEHOLDER</text>
    <rect x="760" y="560" width="470" height="290" rx="8" fill="#d9d0c2" opacity="0.92" transform="rotate(3 995 705)"/>
    <circle cx="840" cy="635" r="44" fill="{accent}" opacity="0.78"/>
    <rect x="920" y="610" width="220" height="16" fill="#222831" opacity="0.46"/>
    <rect x="820" y="705" width="320" height="12" fill="#222831" opacity="0.28"/>
    <rect x="820" y="742" width="270" height="12" fill="#222831" opacity="0.24"/>
  </g>
  <g stroke="{accent}" stroke-width="5" opacity="0.66" fill="none">
    <path d="M452 325 C650 400 780 445 1000 705"/>
    <path d="M1220 225 C1105 350 1040 480 1000 705"/>
    <path d="M840 635 C610 590 510 490 452 325"/>
  </g>
  <g fill="{ink}" font-family="DejaVu Sans, Arial">
    <text x="110" y="890" font-size="30" opacity="0.68">INSIDE THE CASE FACTORY</text>
    <text x="110" y="940" font-size="62" font-weight="700">{heading}</text>
    <text x="112" y="990" font-size="30" opacity="0.74">{short_title}</text>
    <text x="1540" y="930" font-size="96" font-weight="700" opacity="0.58">{number}</text>
    <text x="111" y="1040" font-size="24" opacity="0.58">{prompt_hint}</text>
  </g>
  <rect width="1920" height="1080" fill="#000000" opacity="0" filter="url(#grain)"/>
  <rect x="0" y="0" width="1920" height="1080" fill="none" stroke="#000000" stroke-width="80" opacity="0.24"/>
</svg>
"""


def _render_svg_to_png(svg_path: Path, png_path: Path, width: int, height: int) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(svg_path),
            "-vf",
            f"scale={width}:{height}",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(png_path),
        ]
    )


def _render_scene_video(image_path: Path, output_path: Path, duration: float, fps: int, index: int) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(duration * fps)))
    drift_x = 22 + index * 3
    drift_y = 14 + index * 2
    zoom_speed = 0.00055 + (index % 3) * 0.00012
    filtergraph = (
        "scale=2304:1296:force_original_aspect_ratio=increase,"
        "crop=2304:1296,"
        f"zoompan=z='min(1.0+on*{zoom_speed},1.16)':"
        f"x='iw/2-(iw/zoom/2)+sin(on/52)*{drift_x}':"
        f"y='ih/2-(ih/zoom/2)+cos(on/67)*{drift_y}':"
        f"d={frames}:s=1920x1080:fps={fps},"
        "noise=alls=5:allf=t,"
        "vignette=PI/5,"
        "format=yuv420p"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-vf",
            filtergraph,
            "-frames:v",
            str(frames),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            str(output_path),
        ]
    )


def _concat_audio(audio_paths: list[Path], list_path: Path, output_path: Path) -> None:
    list_path.parent.mkdir(parents=True, exist_ok=True)
    list_path.write_text(
        "".join(f"file '{path.resolve()}'\n" for path in audio_paths),
        encoding="utf-8",
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c",
            "copy",
            str(output_path),
        ]
    )


def _xfade_videos(scene_videos: list[Path], narration_durations: list[float], transition: float, output_path: Path) -> None:
    if len(scene_videos) == 1:
        _run(["ffmpeg", "-y", "-i", str(scene_videos[0]), "-c", "copy", str(output_path)])
        return

    command = ["ffmpeg", "-y"]
    for path in scene_videos:
        command.extend(["-i", str(path)])

    filters: list[str] = []
    previous = "[0:v]"
    cumulative = narration_durations[0]
    transitions = ["fade", "smoothleft", "circleopen", "distance", "fadeblack"]
    for index in range(1, len(scene_videos)):
        out_label = f"[vx{index}]"
        transition_name = transitions[(index - 1) % len(transitions)]
        filters.append(
            f"{previous}[{index}:v]xfade=transition={transition_name}:duration={transition:.3f}:"
            f"offset={cumulative:.3f}{out_label}"
        )
        previous = out_label
        if index < len(scene_videos) - 1:
            cumulative += narration_durations[index]

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            previous,
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
    )
    _run(command)


def _mux_subtitles_and_audio(video_path: Path, audio_path: Path, subtitles_path: Path, output_path: Path) -> None:
    subtitle_filter = (
        f"subtitles={subtitles_path.resolve()}:"
        "force_style='FontName=DejaVu Sans,FontSize=26,PrimaryColour=&H00F2F2F2,"
        "OutlineColour=&H00101010,BorderStyle=1,Outline=2,Shadow=1,MarginV=70'"
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-vf",
            subtitle_filter,
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
    )


def _select_voice_provider(settings: Settings) -> tuple[object, str]:
    voice_settings = settings.providers.get("voice_over", {})
    provider_name = str(voice_settings.get("provider", "ffmpeg_flite"))
    elevenlabs_settings = voice_settings.get("elevenlabs", {})
    elevenlabs = ElevenLabsVoiceOverProvider(elevenlabs_config_from_settings(elevenlabs_settings))
    paid_allowed = bool(settings.pipeline.get("allow_paid_providers", False))
    if provider_name == "elevenlabs":
        if not paid_allowed:
            _progress("ElevenLabs blocked because paid providers are not explicitly allowed; using FFmpeg Flite TTS")
            return FFmpegFliteVoiceOverProvider(), "FFmpeg Flite TTS"
        if elevenlabs.available:
            return elevenlabs, "ElevenLabs TTS"
        _progress("ElevenLabs requested but not available; falling back to FFmpeg Flite TTS")
        return FFmpegFliteVoiceOverProvider(), "FFmpeg Flite TTS"
    if provider_name == "auto" and paid_allowed and elevenlabs.available:
        return elevenlabs, "ElevenLabs TTS"
    return FFmpegFliteVoiceOverProvider(), "FFmpeg Flite TTS"


def generate_video_project(settings: Settings, topic: str, slug: str | None = None) -> GeneratedVideo:
    width = int(settings.video.get("width", 1920))
    height = int(settings.video.get("height", 1080))
    fps = int(settings.video.get("fps", 24))
    transition = 0.75

    _progress("creating project workspace")
    project = create_project(settings.projects_dir, topic, slug)
    manifests_dir = project.root / "manifests"
    assets_dir = project.root / "assets"
    generated_dir = assets_dir / "generated"
    audio_dir = assets_dir / "audio"
    workspace_dir = project.root / "workspace"
    exports_dir = project.root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    media_manifest = ensure_media_manifest(project.root)

    _progress("generating sample documentary narration")
    script = build_sample_script(topic)
    write_json(manifests_dir / "script.json", script)

    _progress("dividing narration into cinematic scenes")
    sections = list(script["sections"])  # type: ignore[index]
    prompts = [
        build_visual_prompt(section, str(script["title"]), index)
        for index, section in enumerate(sections, start=1)
    ]
    write_json(manifests_dir / "visual_prompts.json", {"prompts": prompts})

    voice, voice_label = _select_voice_provider(settings)
    image_provider = SVGPlaceholderImageProvider()

    _progress(f"creating voice-over segments with {voice_label}")
    scene_audio_paths: list[Path] = []
    narration_durations: list[float] = []
    scenes: list[dict[str, object]] = []
    start = 0.0
    for index, section in enumerate(sections, start=1):
        text = compact_whitespace(str(section["narration"]))
        scene_id = str(section["id"])
        wav_path = audio_dir / f"{scene_id}_voice.wav"
        text_path = workspace_dir / f"{scene_id}_voice.txt"
        voice.synthesize_to_file(text, wav_path, text_path)
        duration = max(2.5, media_duration_seconds(wav_path))
        end = start + duration
        prompt = prompts[index - 1]
        scenes.append(
            {
                "id": scene_id,
                "index": index,
                "heading": section["heading"],
                "narration": text,
                "start_seconds": round(start, 3),
                "duration_seconds": round(duration, 3),
                "end_seconds": round(end, 3),
                "visual_summary": prompt["prompt"],
                "camera_motion": prompt["camera_motion"],
            }
        )
        scene_audio_paths.append(wav_path)
        narration_durations.append(duration)
        start = end

    write_json(manifests_dir / "scenes.json", {"scenes": scenes})
    write_json(
        manifests_dir / "narration_timing.json",
        {
            "provider": voice.name,
            "provider_label": voice_label,
            "total_duration_seconds": round(sum(narration_durations), 3),
            "segments": [
                {
                    "scene_id": scene["id"],
                    "start_seconds": scene["start_seconds"],
                    "end_seconds": scene["end_seconds"],
                    "duration_seconds": scene["duration_seconds"],
                    "audio_path": str(path.relative_to(project.root)),
                }
                for scene, path in zip(scenes, scene_audio_paths, strict=True)
            ],
        },
    )

    _progress("creating subtitles")
    subtitles_path = manifests_dir / "subtitles.srt"
    _write_srt(subtitles_path, scenes)
    write_json(
        manifests_dir / "subtitles.json",
        {
            "format": "srt",
            "path": str(subtitles_path.relative_to(project.root)),
            "entries": [
                {
                    "scene_id": scene["id"],
                    "start_seconds": scene["start_seconds"],
                    "end_seconds": scene["end_seconds"],
                    "text": scene["narration"],
                }
                for scene in scenes
            ],
        },
    )

    _progress("selecting scene images")
    scene_videos: list[Path] = []
    scene_image_sources: list[dict[str, object]] = []
    for index, scene in enumerate(scenes, start=1):
        scene_id = str(scene["id"])
        real_image = image_for_scene(project.root, scene_id)
        video_path = generated_dir / f"{scene_id}_animated.mp4"

        if real_image:
            image_path = project.root / str(real_image["path"])
            source_info = {
                "kind": "real_image",
                "media_id": real_image.get("id", ""),
                "path": str(image_path.relative_to(project.root)),
                "source_url": real_image.get("source_url", ""),
                "credit": real_image.get("credit", ""),
                "license_notes": real_image.get("license_notes", ""),
                "usage_notes": real_image.get("usage_notes", ""),
                "scene_relevance": real_image.get("scene_relevance", ""),
            }
        else:
            svg_path = generated_dir / f"{scene_id}.svg"
            png_path = generated_dir / f"{scene_id}.png"
            image_provider.write_svg(svg_path, _scene_svg(str(script["title"]), scene, index))
            _render_svg_to_png(svg_path, png_path, width, height)
            image_path = png_path
            source_info = {
                "kind": "generated_placeholder",
                "svg": str(svg_path.relative_to(project.root)),
                "path": str(png_path.relative_to(project.root)),
                "source_url": "",
                "credit": "Inside the Case Factory local SVG placeholder",
                "license_notes": "Generated local placeholder; replace with licensed or owned real media.",
                "usage_notes": "Fallback only when no mapped real image exists for the scene.",
                "scene_relevance": str(scene["visual_summary"]),
            }

        video_duration = float(scene["duration_seconds"]) + (transition if index < len(scenes) else 0.0)
        _render_scene_video(image_path, video_path, video_duration, fps, index)
        scene["asset_paths"] = {
            "image": source_info["path"],
            "animated_video": str(video_path.relative_to(project.root)),
        }
        scene["media_source"] = source_info
        scene_image_sources.append({"scene_id": scene_id, **source_info})
        scene_videos.append(video_path)

    write_json(manifests_dir / "scenes.json", {"scenes": scenes})

    _progress("combining narration audio")
    voiceover_path = audio_dir / "voiceover.wav"
    _concat_audio(scene_audio_paths, workspace_dir / "voiceover_concat.txt", voiceover_path)

    _progress("animating scenes and adding transitions")
    silent_video_path = workspace_dir / "xfade_silent.mp4"
    _xfade_videos(scene_videos, narration_durations, transition, silent_video_path)

    _progress("writing render plan")
    render_plan = {
        "renderer": "ffmpeg",
        "width": width,
        "height": height,
        "fps": fps,
        "transition_seconds": transition,
        "media_manifest": str(media_manifest.relative_to(project.root)),
        "voiceover": str(voiceover_path.relative_to(project.root)),
        "subtitles": str(subtitles_path.relative_to(project.root)),
        "scene_images": scene_image_sources,
        "scene_videos": [str(path.relative_to(project.root)) for path in scene_videos],
        "intermediate_video": str(silent_video_path.relative_to(project.root)),
        "final_video": "exports/final_video.mp4",
    }
    write_json(manifests_dir / "render_plan.json", render_plan)

    _progress("rendering final MP4 with subtitles and synchronized narration")
    final_video_path = exports_dir / "final_video.mp4"
    _mux_subtitles_and_audio(silent_video_path, voiceover_path, subtitles_path, final_video_path)

    duration = media_duration_seconds(final_video_path)
    _progress(f"complete: {final_video_path} ({duration:.1f}s)")
    return GeneratedVideo(project.slug, project.root, final_video_path, duration)
