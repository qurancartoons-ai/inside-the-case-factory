from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import subprocess

from inside_case_factory.config.settings import Settings
from inside_case_factory.core.autonomous_direction import (
    CriticEngine,
    DirectorEngine,
    QualityPolicy,
    improvement_decision,
    record_feedback,
    save_improvement_state,
)
from inside_case_factory.core.producer import ProducerEngine
from inside_case_factory.core.media import ensure_media_manifest
from inside_case_factory.core.models import ProductionProject, ReviewStatus
from inside_case_factory.core.project import create_project
from inside_case_factory.core.research import script_content_hash
from inside_case_factory.pipeline.sample_content import build_sample_script, build_visual_prompt
from inside_case_factory.providers.elevenlabs import (
    ElevenLabsVoiceOverProvider,
    elevenlabs_config_from_settings,
)
from inside_case_factory.providers.offline_media import FFmpegFliteVoiceOverProvider, LocalEvidenceGraphicProvider
from inside_case_factory.providers.production import ProductionProviderError, ProductionProviderRouter
from inside_case_factory.providers.runtime_media import FailoverVoiceOverProvider, RoutedImageProvider, RoutedVoiceOverProvider
from inside_case_factory.rendering.probe import media_duration_seconds
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace, svg_escape


@dataclass(frozen=True)
class GeneratedVideo:
    project_slug: str
    project_root: Path
    final_video: Path
    duration_seconds: float


def _progress(message: str) -> None:
    print(f"[case-factory] {message}", flush=True)


def _ensure_voice_segment(voice: object, text: str, wav_path: Path, text_path: Path) -> None:
    if wav_path.exists() and wav_path.stat().st_size > 0:
        return
    voice.synthesize_to_file(text, wav_path, text_path)  # type: ignore[attr-defined]


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


def _wrap_subtitle(text: str, width: int = 42) -> str:
    cleaned = compact_whitespace(text)
    words = [word for word in cleaned.split(" ") if word]
    if not words:
        return ""
    if len(cleaned) <= width:
        return cleaned

    best_split = 0
    best_score = 10**9
    for index in range(1, len(words)):
        left = " ".join(words[:index]).strip()
        right = " ".join(words[index:]).strip()
        if not left or not right:
            continue
        if len(left) > width + 8 or len(right) > width + 8:
            continue
        punctuation_bonus = -8 if left.endswith((",", ";", ":")) else 0
        score = abs(len(left) - len(right)) + max(0, len(left) - width) + max(0, len(right) - width) + punctuation_bonus
        if score < best_score:
            best_score = score
            best_split = index

    if best_split:
        first = " ".join(words[:best_split]).strip()
        second = " ".join(words[best_split:]).strip()
        return f"{first}\n{second}"

    # Fallback: hard width wrapping but still capped at two lines.
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
    return "\n".join(lines[:2])


def _ass_timestamp(seconds: float) -> str:
    centis = int(round(max(0.0, seconds) * 100))
    hours, remainder = divmod(centis, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    secs, cs = divmod(remainder, 100)
    return f"{hours}:{minutes:02}:{secs:02}.{cs:02}"


def _ass_escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


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


def _scene_has_lower_third_conflict(scene: dict[str, object], direction_by_scene: dict[str, dict[str, object]]) -> bool:
    scene_id = str(scene.get("id", ""))
    direction = direction_by_scene.get(scene_id, {}) if isinstance(direction_by_scene, dict) else {}
    shots = direction.get("shots", []) if isinstance(direction, dict) else []
    for shot in shots if isinstance(shots, list) else []:
        if not isinstance(shot, dict):
            continue
        composition = str(shot.get("composition", ""))
        text_overlay = str(shot.get("text_overlay", ""))
        if composition in {"picture_in_picture", "document_overlay", "split_screen"}:
            return True
        if text_overlay:
            return True
    return False


def _write_ass_subtitles(
    path: Path,
    scenes: list[dict[str, object]],
    direction_by_scene: dict[str, dict[str, object]],
    *,
    font_size: int,
    bottom_margin: int,
    fade_in_ms: int,
    fade_out_ms: int,
    max_chars: int,
) -> None:
    header = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1920",
        "PlayResY: 1080",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Base,DejaVu Sans,{font_size},&H00F2F2F2,&H00F2F2F2,&H00101010,&H64000000,0,0,0,0,100,100,0,0,1,2,1,2,58,58,{bottom_margin},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]
    events: list[str] = []
    for scene in scenes:
        start = float(scene.get("start_seconds", 0.0) or 0.0)
        end = float(scene.get("end_seconds", 0.0) or 0.0)
        wrapped = _wrap_subtitle(str(scene.get("narration", "")), width=max_chars)
        wrapped = "\\N".join(wrapped.split("\n")[:2])
        wrapped = _ass_escape(wrapped)
        align_tag = r"\an8" if _scene_has_lower_third_conflict(scene, direction_by_scene) else r"\an2"
        fade_tag = rf"\fad({max(0, fade_in_ms)},{max(0, fade_out_ms)})"
        tag = "{" + align_tag + fade_tag + "}"
        events.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(start)},{_ass_timestamp(max(start + 0.2, end))},"
            f"Base,,0,0,0,,{tag}{wrapped}"
        )
    content = "\n".join([*header, *events, ""])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _scene_svg(title: str, scene: dict[str, object], index: int, *, branding_text: str = "", branding_opacity: float = 0.0) -> str:
    palettes = [
        ("#0b1217", "#1f3a46", "#b89154", "#d8dde0"),
        ("#111018", "#32304f", "#8b3244", "#e0d5c6"),
        ("#15120f", "#3b3327", "#a75138", "#d9d2c5"),
        ("#0d1412", "#263e35", "#9b2f31", "#d9dfd9"),
        ("#12151b", "#344052", "#c7a15d", "#e7e4dc"),
    ]
    bg, mid, accent, ink = palettes[(index - 1) % len(palettes)]
    heading = svg_escape(str(scene["heading"]).upper())
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
    <text x="110" y="940" font-size="62" font-weight="700">{heading}</text>
    <text x="1540" y="930" font-size="96" font-weight="700" opacity="0.58">{number}</text>
    <text x="111" y="1040" font-size="24" opacity="0.58">{prompt_hint}</text>
        {f'<text x="110" y="890" font-size="28" opacity="{max(0.0, min(1.0, branding_opacity)):.2f}">{svg_escape(branding_text[:80])}</text>' if branding_text else ''}
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


def _render_scene_video(
    image_path: Path,
    output_path: Path,
    duration: float,
    fps: int,
    index: int,
    *,
    motion: str = "slow_zoom_in",
    style: dict[str, object] | None = None,
    width: int = 1920,
    height: int = 1080,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(round(duration * fps)))
    drift_x = 22 + index * 3
    drift_y = 14 + index * 2
    zoom_speed = 0.00055 + (index % 3) * 0.00012
    zoom = {
        "static": "1.0",
        "slow_zoom_out": f"max(1.16-on*{zoom_speed},1.0)",
        "rack_focus": "1.06",
        "ken_burns_pan": "1.08",
        "parallax": f"min(1.0+on*{zoom_speed * 0.7},1.12)",
        "controlled_push_in": f"min(1.0+on*{zoom_speed * 1.25},1.18)",
    }.get(motion, f"min(1.0+on*{zoom_speed},1.16)")
    x_expr = "iw/2-(iw/zoom/2)"
    y_expr = "ih/2-(ih/zoom/2)"
    if motion == "static":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    elif motion == "ken_burns_pan":
        x_expr = f"(iw-iw/zoom)*on/{max(1, frames - 1)}"
    elif motion == "parallax":
        x_expr = f"iw/2-(iw/zoom/2)+{drift_x * 0.35}*on/{max(1, frames - 1)}"
        y_expr = f"ih/2-(ih/zoom/2)+{drift_y * 0.2}*on/{max(1, frames - 1)}"
    elif motion == "controlled_push_in":
        x_expr = f"iw/2-(iw/zoom/2)+{drift_x * 0.18}*on/{max(1, frames - 1)}"
        y_expr = f"ih/2-(ih/zoom/2)+{drift_y * 0.12}*on/{max(1, frames - 1)}"
    elif motion == "rack_focus":
        x_expr = "iw/2-(iw/zoom/2)"
        y_expr = "ih/2-(ih/zoom/2)"
    profile = style or {}
    saturation = float(profile.get("saturation", 0.88))
    contrast = float(profile.get("contrast", 1.06))
    grain = max(0, min(20, int(float(profile.get("grain", 0.035)) * 100)))
    blur = ",gblur=sigma=0.8" if motion == "rack_focus" else ""
    filtergraph = (
        "scale=2304:1296:force_original_aspect_ratio=increase,"
        "crop=2304:1296,"
        f"zoompan=z='{zoom}':x='{x_expr}':y='{y_expr}':"
        f"d={frames}:s={width}x{height}:fps={fps}"
        f"{blur},eq=contrast={contrast}:saturation={saturation},"
        f"noise=alls={grain}:allf=t,"
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


def _render_asset_video(
    asset_path: Path,
    output_path: Path,
    duration: float,
    fps: int,
    index: int,
    *,
    motion: str,
    style: dict[str, object],
    width: int,
    height: int,
) -> None:
    if asset_path.suffix.lower() not in {".mp4", ".mov", ".mkv", ".webm", ".ogv", ".avi"}:
        _render_scene_video(
            asset_path, output_path, duration, fps, index, motion=motion, style=style, width=width, height=height
        )
        return
    saturation = float(style.get("saturation", 0.88))
    contrast = float(style.get("contrast", 1.06))
    filtergraph = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},fps={fps},eq=contrast={contrast}:saturation={saturation},vignette=PI/5,format=yuv420p"
    )
    _run([
        "ffmpeg", "-y", "-stream_loop", "-1", "-i", str(asset_path), "-t", f"{duration:.3f}", "-vf", filtergraph,
        "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(output_path),
    ])


def _render_composite_video(
    primary_path: Path,
    secondary_path: Path,
    output_path: Path,
    duration: float,
    fps: int,
    index: int,
    *,
    composition: str,
    motion: str,
    style: dict[str, object],
    width: int,
    height: int,
) -> None:
    """Render an actual two-source composition after both sources pass selection."""
    primary_video = output_path.with_name(f"{output_path.stem}-primary.mp4")
    secondary_video = output_path.with_name(f"{output_path.stem}-secondary.mp4")
    _render_asset_video(primary_path, primary_video, duration, fps, index, motion=motion, style=style, width=width, height=height)
    _render_asset_video(secondary_path, secondary_video, duration, fps, index + 1, motion="controlled_push_in", style=style, width=width, height=height)
    try:
        if composition == "split_screen":
            graph = (
                f"[0:v]scale={width // 2}:{height}:force_original_aspect_ratio=increase,crop={width // 2}:{height}[left];"
                f"[1:v]scale={width // 2}:{height}:force_original_aspect_ratio=increase,crop={width // 2}:{height}[right];"
                "[left][right]hstack=inputs=2,format=yuv420p[out]"
            )
        else:
            inset_width = max(320, int(width * 0.34))
            graph = (
                f"[1:v]scale={inset_width}:-2,pad=iw+8:ih+8:4:4:color=white[inset];"
                "[0:v][inset]overlay=W-w-60:H-h-60:format=auto,format=yuv420p[out]"
            )
        _run([
            "ffmpeg", "-y", "-i", str(primary_video), "-i", str(secondary_video), "-filter_complex", graph,
            "-map", "[out]", "-t", f"{duration:.3f}", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", str(output_path),
        ])
    finally:
        primary_video.unlink(missing_ok=True)
        secondary_video.unlink(missing_ok=True)


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


def _xfade_videos(
    scene_videos: list[Path],
    narration_durations: list[float],
    transition: float,
    output_path: Path,
    transitions: list[str] | None = None,
) -> None:
    if len(scene_videos) == 1:
        _run(["ffmpeg", "-y", "-i", str(scene_videos[0]), "-c", "copy", str(output_path)])
        return

    transition_map = {
        "hard_cut": "fade", "cross_dissolve": "fade", "dip_to_black": "fadeblack", "match_cut": "distance",
        "directional_wipe": "smoothleft", "document_to_scene": "circleopen", "blur": "fade",
    }
    planned = transitions or ["cross_dissolve", "directional_wipe", "document_to_scene", "match_cut", "dip_to_black"]
    current = scene_videos[0]
    temporary: list[Path] = []
    current_duration = narration_durations[0] + transition
    try:
        for index in range(1, len(scene_videos)):
            target = output_path.with_name(f"{output_path.stem}-step-{index:03}.mp4")
            temporary.append(target)
            transition_name = transition_map.get(planned[(index - 1) % len(planned)], "fade")
            offset = max(0.0, current_duration - transition)
            _run([
                "ffmpeg", "-y", "-i", str(current), "-i", str(scene_videos[index]),
                "-filter_complex", f"[0:v][1:v]xfade=transition={transition_name}:duration={transition:.3f}:offset={offset:.3f}[out]",
                "-map", "[out]", "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", str(target),
            ])
            if current in temporary:
                current.unlink(missing_ok=True)
            current = target
            current_duration += narration_durations[index]
        current.replace(output_path)
        temporary.remove(current)
    finally:
        for path in temporary:
            path.unlink(missing_ok=True)


def _mux_subtitles_and_audio(video_path: Path, audio_path: Path, subtitles_path: Path, output_path: Path) -> None:
    subtitle_filter = (
        f"ass={subtitles_path.resolve()}"
    )
    _run(
        [
            "ffmpeg", "-y", "-i", str(video_path), "-i", str(audio_path), "-vf", subtitle_filter,
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", "-shortest", "-movflags", "+faststart", str(output_path),
        ]
    )


def _mux_audio_only(video_path: Path, audio_path: Path, output_path: Path) -> None:
    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
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


def _mix_sound_design(
    voiceover_path: Path,
    project_root: Path,
    sound_design: dict[str, object],
    output_path: Path,
) -> list[dict[str, object]]:
    cues = sound_design.get("cues", [])
    usable = []
    for cue in cues if isinstance(cues, list) else []:
        if not isinstance(cue, dict):
            continue
        path = project_root / "assets" / "sound" / f"{cue.get('kind', '')}.wav"
        if path.is_file():
            usable.append((cue, path))
    if not usable:
        _run(["ffmpeg", "-y", "-i", str(voiceover_path), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(output_path)])
        return []
    command = ["ffmpeg", "-y", "-i", str(voiceover_path)]
    filters = ["[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice]"]
    sfx_labels = []
    used = []
    for index, (cue, path) in enumerate(usable, start=1):
        command.extend(["-i", str(path)])
        delay = max(0, int(float(cue.get("start_seconds", 0)) * 1000))
        gain = min(-18.0, float(cue.get("gain_db", -24.0)))
        duration = max(0.2, float(cue.get("duration_seconds", 1.0)))
        fade_in = max(0.02, float(cue.get("fade_in_seconds", 0.08)))
        fade_out = max(0.02, float(cue.get("fade_out_seconds", 0.12)))
        fade_out_start = max(fade_in, duration - fade_out)
        label = f"sfx{index}"
        filters.append(
            f"[{index}:a]atrim=0:{duration:.3f},afade=t=in:st=0:d={fade_in:.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={fade_out:.3f},volume={gain}dB,adelay={delay}|{delay}[{label}]"
        )
        sfx_labels.append(f"[{label}]")
        used.append({**cue, "path": str(path.relative_to(project_root))})
    filters.append(f"{''.join(sfx_labels)}amix=inputs={len(sfx_labels)}:normalize=0:dropout_transition=0[sfxbus]")
    filters.append("[sfxbus][voice]sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300[ducked]")
    filters.append("[voice][ducked]amix=inputs=2:normalize=0:dropout_transition=0,alimiter=limit=0.89[mix]")
    command.extend(["-filter_complex", ";".join(filters), "-map", "[mix]", str(output_path)])
    _run(command)
    return used


def _select_voice_provider(settings: Settings, router: ProductionProviderRouter | None = None, project_root: Path | None = None) -> tuple[object, str]:
    if router is not None and project_root is not None:
        selected = router.choose("voice", "voice_over")
        if selected is not None:
            return FailoverVoiceOverProvider(RoutedVoiceOverProvider(router, project_root), FFmpegFliteVoiceOverProvider()), f"{selected.name} / {selected.config.model} (local fallback)"
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


def _approved_project(project_root: Path) -> tuple[ProductionProject, dict[str, object], list[dict[str, object]]]:
    manifests = project_root / "manifests"
    workflow = read_json(manifests / "workflow.json")
    script = read_json(manifests / "script.json")
    scene_manifest = read_json(manifests / "scenes.json")
    media = read_json(manifests / "media_sources.json")
    current_script_hash = script_content_hash(script if isinstance(script, dict) else {})
    script_fingerprint = script.get("approval_fingerprint") if isinstance(script.get("approval_fingerprint"), dict) else {}
    workflow_fingerprint = workflow.get("script_approval_fingerprint") if isinstance(workflow.get("script_approval_fingerprint"), dict) else {}
    fingerprint = script_fingerprint or workflow_fingerprint

    has_explicit_approval = bool(workflow.get("script_approved")) and str(script.get("status", "")) == "approved"
    approved_hash = str(fingerprint.get("script_hash", ""))
    approved_at = str(fingerprint.get("approved_at", ""))
    approval_source = str(fingerprint.get("approval_source", ""))
    approval_valid = bool(fingerprint.get("approval_valid", False))
    reusable_approval = bool(
        approved_hash
        and approved_hash == current_script_hash
        and approved_at
        and approval_source
        and approval_valid
    )

    if has_explicit_approval and not fingerprint:
        synthesized = {
            "script_hash": current_script_hash,
            "approved_at": str(script.get("approved_at") or workflow.get("script_approved_at") or ""),
            "approval_source": "legacy_existing_approval",
            "approval_valid": True,
        }
        script["approval_fingerprint"] = synthesized
        workflow["script_approval_fingerprint"] = synthesized
        write_json(manifests / "script.json", script)
        write_json(manifests / "workflow.json", workflow)
        fingerprint = synthesized
        reusable_approval = True

    if has_explicit_approval and approved_hash and approved_hash != current_script_hash:
        updated = {**(fingerprint or {}), "approval_valid": False}
        script["approval_fingerprint"] = updated
        workflow["script_approval_fingerprint"] = updated
        write_json(manifests / "script.json", script)
        write_json(manifests / "workflow.json", workflow)
        raise RuntimeError("The factual script must be explicitly approved before rendering.")

    if not has_explicit_approval:
        if reusable_approval:
            workflow["script_approved"] = True
            script["status"] = "approved"
            script["approval_fingerprint"] = fingerprint
            workflow["script_approval_fingerprint"] = fingerprint
            write_json(manifests / "script.json", script)
            write_json(manifests / "workflow.json", workflow)
        else:
            raise RuntimeError("The factual script must be explicitly approved before rendering.")
    raw_scenes = scene_manifest.get("scenes", [])
    if not workflow.get("scenes_generated") or not isinstance(raw_scenes, list) or not raw_scenes:
        raise RuntimeError("Approved factual scenes are required before rendering.")
    assets = media.get("assets", [])
    if not isinstance(assets, list) or not assets:
        raise RuntimeError("Media review must contain at least one approved asset before rendering.")
    statuses = {str(asset.get("review_status", "pending_review")) for asset in assets if isinstance(asset, dict)}
    if "pending_review" in statuses or "approved" not in statuses:
        raise RuntimeError("Media review must be complete and contain at least one approved asset before rendering.")
    project_data = read_json(manifests / "project.json")
    project = ProductionProject(
        slug=str(project_data.get("slug", project_root.name)),
        topic=str(project_data.get("topic", script.get("title", project_root.name))),
        root=project_root,
        status=ReviewStatus.DRAFT,
    )
    return project, script, [scene for scene in raw_scenes if isinstance(scene, dict)]


def _append_rerender_history(
    manifests_dir: Path,
    *,
    iteration: int,
    reason: str,
    changed_artifacts: list[str],
    quality_delta: float | None,
) -> None:
    cycle_path = manifests_dir / "quality_cycle.json"
    cycle = read_json(cycle_path) if cycle_path.exists() else {"version": 1, "attempts": []}
    history = cycle.setdefault("rerender_history", [])
    if not isinstance(history, list):
        history = []
        cycle["rerender_history"] = history
    history.append(
        {
            "iteration": int(iteration),
            "reason": str(reason),
            "changed_artifacts": [str(item) for item in changed_artifacts],
            "quality_delta": None if quality_delta is None else round(float(quality_delta), 2),
        }
    )
    write_json(cycle_path, cycle)


def generate_video_project(
    settings: Settings,
    topic: str,
    slug: str | None = None,
    *,
    existing_project_root: Path | None = None,
    _quality_render_number: int | None = None,
    _previous_criticism: dict[str, object] | None = None,
    _require_approved_entry: bool | None = None,
    respect_existing_direction: bool = False,
) -> GeneratedVideo:
    width = int(settings.video.get("width", 1920))
    height = int(settings.video.get("height", 1080))
    fps = int(settings.video.get("fps", 24))
    transition = 0.75

    require_approved_entry = (existing_project_root is not None) if _require_approved_entry is None else bool(_require_approved_entry)

    if existing_project_root is None:
        _progress("creating project workspace")
        project = create_project(settings.projects_dir, topic, slug)
        script = build_sample_script(topic)
        source_scenes: list[dict[str, object]] | None = None
    else:
        if require_approved_entry:
            _progress("loading approved factual project")
            project, script, source_scenes = _approved_project(existing_project_root)
        else:
            _progress("loading existing sample/demo project")
            project_data = read_json((existing_project_root / "manifests" / "project.json"))
            project = ProductionProject(
                slug=str(project_data.get("slug", existing_project_root.name)),
                topic=str(project_data.get("topic", topic)),
                root=existing_project_root,
                status=ReviewStatus.DRAFT,
            )
            script = read_json(existing_project_root / "manifests" / "script.json")
            scene_manifest = read_json(existing_project_root / "manifests" / "scenes.json")
            raw_scenes = scene_manifest.get("scenes", [])
            source_scenes = [scene for scene in raw_scenes if isinstance(scene, dict)]
            if not source_scenes:
                source_scenes = None
    manifests_dir = project.root / "manifests"
    provider_router = ProductionProviderRouter.from_settings(project.root, settings.providers)
    provider_router.selection_manifest([
        ("producer_blueprint", "text"), ("director_plan", "text"), ("critic_review", "text"),
        ("voice_over", "voice"), ("scene_image", "image"),
    ])
    if _quality_render_number is None:
        cycle_path = manifests_dir / "quality_cycle.json"
        cycle = read_json(cycle_path) if cycle_path.exists() else {}
        attempts = cycle.get("attempts", []) if isinstance(cycle, dict) else []
        _quality_render_number = len(attempts) + 1 if cycle.get("status") == "rerender_pending" else 1
    assets_dir = project.root / "assets"
    generated_dir = assets_dir / "generated"
    audio_dir = assets_dir / "audio"
    workspace_dir = project.root / "workspace"
    exports_dir = project.root / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    media_manifest = ensure_media_manifest(project.root)

    if source_scenes is None:
        _progress("generating sample documentary narration")
        write_json(manifests_dir / "script.json", script)
        sections = list(script["sections"])  # type: ignore[index]
    else:
        _progress("using approved factual narration and scene plan")
        sections = [
            {
                "id": str(scene.get("id", f"s{index:02}")),
                "heading": str(scene.get("heading", f"Scene {index}")),
                "narration": str(scene.get("narration", "")),
            }
            for index, scene in enumerate(source_scenes, start=1)
        ]
    prompts = []
    for index, section in enumerate(sections, start=1):
        prompt = build_visual_prompt(section, str(script["title"]), index)
        if source_scenes is not None:
            source = source_scenes[index - 1]
            prompt["prompt"] = str(source.get("ai_visual_prompt", prompt["prompt"]))
        prompts.append(prompt)
    write_json(manifests_dir / "visual_prompts.json", {"prompts": prompts})

    voice, voice_label = _select_voice_provider(settings, provider_router, project.root)
    image_provider = LocalEvidenceGraphicProvider()
    routed_image_provider = RoutedImageProvider(provider_router, project.root) if provider_router.choose("image", "scene_image") else None

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
        _ensure_voice_segment(voice, text, wav_path, text_path)
        duration = max(2.5, media_duration_seconds(wav_path))
        end = start + duration
        prompt = prompts[index - 1]
        approved_scene = source_scenes[index - 1] if source_scenes is not None else {}
        scenes.append(
            {
                **approved_scene,
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

    _progress(f"Producer AI is building story blueprint {_quality_render_number}")
    producer_blueprint = ProducerEngine().plan(
        project.root, scenes, render_number=_quality_render_number, previous_review=_previous_criticism, provider_router=provider_router,
    )
    existing_direction_path = manifests_dir / "visual_direction.json"
    if respect_existing_direction and existing_direction_path.exists():
        _progress("reusing existing edited visual direction")
        cinematic_plan = read_json(existing_direction_path)
    else:
        _progress(f"Director AI is building render plan {_quality_render_number}")
        cinematic_plan = DirectorEngine().plan(
            project.root, scenes, width=width, height=height, render_number=_quality_render_number,
            criticism=_previous_criticism,
            provider_router=provider_router,
        )
    style_profile = cinematic_plan["style_profile"]
    subtitle_style = style_profile.get("subtitles", {}) if isinstance(style_profile, dict) else {}
    branding_style = style_profile.get("branding", {}) if isinstance(style_profile, dict) else {}
    direction_by_scene = {str(item["scene_id"]): item for item in cinematic_plan["scenes"]}

    _progress("creating subtitles")
    subtitles_path = manifests_dir / "subtitles.ass"
    existing_subtitles = read_json(manifests_dir / "subtitles.json") if (manifests_dir / "subtitles.json").exists() else {}
    edited_entries = existing_subtitles.get("entries", []) if isinstance(existing_subtitles.get("entries"), list) else []
    subtitle_rows = [
        {
            "id": str(entry.get("scene_id", "")),
            "start_seconds": float(entry.get("start_seconds", 0.0) or 0.0),
            "end_seconds": float(entry.get("end_seconds", 0.0) or 0.0),
            "narration": str(entry.get("text", "")),
        }
        for entry in edited_entries
        if isinstance(entry, dict) and str(entry.get("scene_id", "")).strip()
    ]
    subtitle_source = subtitle_rows if subtitle_rows and bool(existing_subtitles.get("manual_edit", False)) else scenes
    _write_ass_subtitles(
        subtitles_path,
        subtitle_source,
        direction_by_scene,
        font_size=int(subtitle_style.get("size", 16) or 16),
        bottom_margin=int(subtitle_style.get("bottom_margin", 54) or 54),
        fade_in_ms=int(subtitle_style.get("fade_in_ms", 150) or 150),
        fade_out_ms=int(subtitle_style.get("fade_out_ms", 180) or 180),
        max_chars=int(subtitle_style.get("max_chars_per_line", 38) or 38),
    )
    legacy_srt_path = manifests_dir / "subtitles.srt"
    _write_srt(legacy_srt_path, subtitle_source)
    write_json(
        manifests_dir / "subtitles.json",
        {
            "format": "ass",
            "path": str(subtitles_path.relative_to(project.root)),
            "enabled": bool(subtitle_style.get("enabled", False)),
            "manual_edit": bool(existing_subtitles.get("manual_edit", False)),
            "entries": [
                {
                    "scene_id": scene["id"],
                    "start_seconds": scene["start_seconds"],
                    "end_seconds": scene["end_seconds"],
                    "text": scene["narration"],
                }
                for scene in subtitle_source
            ],
        },
    )

    _progress("rendering directed cinematic shots")
    scene_videos: list[Path] = []
    scene_image_sources: list[dict[str, object]] = []
    shot_durations: list[float] = []
    shot_transitions: list[str] = []
    shot_number = 0
    for index, scene in enumerate(scenes, start=1):
        scene_id = str(scene["id"])
        direction = direction_by_scene[scene_id]
        rendered_shots = []
        for shot in direction["shots"]:
            shot_number += 1
            asset = shot["asset"]
            asset_path = str(asset.get("path", ""))
            if asset_path:
                image_path = project.root / asset_path
            else:
                svg_path = generated_dir / f"{shot['id']}.svg"
                png_path = generated_dir / f"{shot['id']}.png"
                graphic_scene = {**scene, "heading": shot.get("text_overlay") or scene.get("heading", "Evidence")}
                generated = False
                if routed_image_provider is not None:
                    try:
                        routed_image_provider.generate_to_file(str(scene.get("visual_summary", "Documentary evidence image")), png_path)
                        generated = True
                    except (ProductionProviderError, RuntimeError):
                        generated = False
                if not generated:
                    branding_enabled = bool(branding_style.get("enabled", False))
                    branding_text = str(branding_style.get("text", "")).strip() if branding_enabled else ""
                    image_provider.write_svg(
                        svg_path,
                        _scene_svg(
                            str(script["title"]),
                            graphic_scene,
                            shot_number,
                            branding_text=branding_text,
                            branding_opacity=float(branding_style.get("opacity", 0.0) or 0.0),
                        ),
                    )
                    _render_svg_to_png(svg_path, png_path, width, height)
                image_path = png_path
                asset_path = str(png_path.relative_to(project.root))
            video_path = generated_dir / f"{shot['id']}_animated.mp4"
            shot_duration = float(shot["duration_seconds"])
            render_duration = shot_duration + transition
            secondary = shot.get("secondary_asset")
            secondary_info = None
            if isinstance(secondary, dict) and secondary.get("path") and shot.get("composition") in {"picture_in_picture", "split_screen", "document_overlay"}:
                secondary_path = project.root / str(secondary["path"])
                if secondary_path.is_file():
                    _render_composite_video(
                        image_path, secondary_path, video_path, render_duration, fps, shot_number,
                        composition=str(shot["composition"]), motion=str(shot["motion"]), style=style_profile,
                        width=width, height=height,
                    )
                    secondary_info = {**secondary, "composition_role": "secondary"}
                else:
                    _render_asset_video(image_path, video_path, render_duration, fps, shot_number, motion=str(shot["motion"]), style=style_profile, width=width, height=height)
            else:
                _render_asset_video(
                    image_path, video_path, render_duration, fps, shot_number,
                    motion=str(shot["motion"]), style=style_profile, width=width, height=height,
                )
            source_info = {
                **asset,
                "path": asset_path,
                "shot_id": shot["id"],
                "motion": shot["motion"],
                "duration_seconds": shot_duration,
                "provenance_claim_ids": shot["claim_ids"],
            }
            rendered_shots.append({**shot, "asset": source_info, "secondary_asset": secondary_info, "composition_executed": bool(secondary_info), "animated_video": str(video_path.relative_to(project.root))})
            scene_image_sources.append({"scene_id": scene_id, **source_info})
            scene_videos.append(video_path)
            shot_durations.append(shot_duration)
            shot_transitions.append(str(direction["transition_to_next"]))
        scene["directed_shots"] = rendered_shots

    write_json(manifests_dir / "scenes.json", {"scenes": scenes})

    _progress("combining narration audio")
    voiceover_path = audio_dir / "voiceover.wav"
    _concat_audio(scene_audio_paths, workspace_dir / "voiceover_concat.txt", voiceover_path)

    _progress("mixing optional scene-bound sound design with voice-over ducking")
    mastered_audio_path = audio_dir / "mastered_voiceover.wav"
    used_sound_cues = _mix_sound_design(voiceover_path, project.root, cinematic_plan["sound_design"], mastered_audio_path)

    _progress("animating scenes and adding transitions")
    silent_video_path = workspace_dir / "xfade_silent.mp4"
    _xfade_videos(scene_videos, shot_durations, transition, silent_video_path, shot_transitions)

    _progress("writing render plan")
    render_plan = {
        "renderer": "ffmpeg",
        "width": width,
        "height": height,
        "fps": fps,
        "transition_seconds": transition,
        "media_manifest": str(media_manifest.relative_to(project.root)),
        "voiceover": str(mastered_audio_path.relative_to(project.root)),
        "sound_design": {**cinematic_plan["sound_design"], "used_cues": used_sound_cues},
        "visual_direction": "manifests/visual_direction.json",
        "producer_blueprint": "manifests/producer_blueprint.json",
        "visual_style_profile": "manifests/visual_style_profile.json",
        "visual_quality_report": "manifests/visual_quality_report.json",
        "subtitles": str(subtitles_path.relative_to(project.root)),
        "scene_images": scene_image_sources,
        "scene_videos": [str(path.relative_to(project.root)) for path in scene_videos],
        "intermediate_video": str(silent_video_path.relative_to(project.root)),
        "final_video": "exports/final_video.mp4",
    }
    write_json(manifests_dir / "render_plan.json", render_plan)

    _progress("rendering final MP4 with subtitles and synchronized narration")
    final_video_path = exports_dir / "final_video.mp4"
    if bool(subtitle_style.get("enabled", False)):
        _mux_subtitles_and_audio(silent_video_path, mastered_audio_path, subtitles_path, final_video_path)
    else:
        _mux_audio_only(silent_video_path, mastered_audio_path, final_video_path)

    duration = media_duration_seconds(final_video_path)
    _progress(f"Film Critic AI is evaluating render {_quality_render_number}")
    critic_report = CriticEngine().analyze(
        project.root, render_number=_quality_render_number, duration_seconds=duration, provider_router=provider_router,
    )
    producer_report = ProducerEngine().review_render(project.root, critic_report, provider_router=provider_router)
    combined_report = {
        **critic_report,
        "overall_score": min(float(critic_report["overall_score"]), float(producer_report["overall_score"])),
        "weak_categories": sorted(set(critic_report.get("weak_categories", [])) | set(producer_report.get("weak_categories", []))),
        "producer_score": producer_report["overall_score"],
        "producer_improvement_plan": producer_report["improvement_plan"],
    }
    policy = QualityPolicy.from_pipeline(settings.pipeline)
    decision = improvement_decision(combined_report, policy, _quality_render_number)
    save_improvement_state(project.root, decision, combined_report)
    record_feedback(project.root, critic_report.get("main_criticisms", []))
    director_report = read_json(manifests_dir / "director_report.json")
    director_report["critic_score"] = critic_report["overall_score"]
    director_report["rerender_required"] = decision["rerender_required"]
    director_report["rerender_reason"] = decision["reason"]
    write_json(manifests_dir / "director_report.json", director_report)
    if decision["rerender_required"]:
        cycle = read_json(manifests_dir / "quality_cycle.json")
        attempts = cycle.get("attempts", []) if isinstance(cycle, dict) else []
        current_score = float(combined_report.get("overall_score", 0.0))
        previous_score = float(attempts[-2].get("overall_score", 0.0)) if isinstance(attempts, list) and len(attempts) >= 2 else None
        quality_delta = None if previous_score is None else current_score - previous_score
        _append_rerender_history(
            manifests_dir,
            iteration=_quality_render_number + 1,
            reason=str(decision.get("reason", "")),
            changed_artifacts=[
                "manifests/producer_blueprint.json",
                "manifests/director_plan.json",
                "manifests/director_report.json",
                "manifests/render_plan.json",
                "manifests/critic_report.json",
                "manifests/producer_report.json",
                "exports/final_video.mp4",
            ],
            quality_delta=quality_delta,
        )
        _progress(decision["reason"])
        return generate_video_project(
            settings, topic, slug, existing_project_root=existing_project_root or project.root,
            _quality_render_number=_quality_render_number + 1, _previous_criticism=combined_report,
            _require_approved_entry=require_approved_entry,
        )
    if existing_project_root is not None:
        workflow = read_json(manifests_dir / "workflow.json")
        workflow["voiceover_generated"] = True
        workflow["video_rendered"] = True
        workflow["stage"] = "render_complete"
        write_json(manifests_dir / "workflow.json", workflow)
    from inside_case_factory.core.draft_review import create_review_draft
    create_review_draft(project.root)
    _progress(f"complete: {final_video_path} ({duration:.1f}s)")
    return GeneratedVideo(project.slug, project.root, final_video_path, duration)
