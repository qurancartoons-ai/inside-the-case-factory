from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
import json
from pathlib import Path
import re
import shutil
import subprocess
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from inside_case_factory.core.research import add_source, approved_claims, load_manifest, save_manifest
from inside_case_factory.rendering.probe import media_duration_seconds
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace


SUPPORTED_REFERENCE_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_REFERENCE_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac"}
SCENE_WINDOW_SECONDS = 45.0
YOUTUBE_USER_AGENT = "InsideTheCaseFactory/0.1 recycle-intake"


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            return parse_qs(parsed.query).get("v", [None])[0]
        if parsed.path.startswith(("/shorts/", "/embed/")):
            return parsed.path.rstrip("/").split("/")[-1]
    return None


def vimeo_video_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if host != "vimeo.com":
        return None
    parts = [part for part in parsed.path.split("/") if part]
    if not parts:
        return None
    candidate = parts[-1]
    return candidate if candidate.isdigit() else None


def infer_reference_input_kind(source_url: str = "", local_path: Path | None = None) -> str:
    if source_url:
        if youtube_video_id(source_url):
            return "youtube"
        if vimeo_video_id(source_url):
            return "vimeo"
        raise ValueError("Only YouTube and Vimeo URLs are supported for recycle intake.")
    if local_path is None:
        raise ValueError("A reference documentary URL or local MP4 is required.")
    suffix = local_path.suffix.lower()
    if suffix not in SUPPORTED_REFERENCE_VIDEO_SUFFIXES | SUPPORTED_REFERENCE_AUDIO_SUFFIXES:
        raise ValueError(f"Unsupported reference documentary file: {suffix or 'unknown type'}")
    return "local_mp4" if suffix in SUPPORTED_REFERENCE_VIDEO_SUFFIXES else "local_audio"


def _http_get(url: str, *, accept: str = "text/html") -> str:
    request = Request(url, headers={"User-Agent": YOUTUBE_USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=45) as response:
        return response.read().decode("utf-8", errors="replace")


def _extract_json_object(text: str, marker: str) -> dict[str, Any]:
    index = text.find(marker)
    if index < 0:
        return {}
    start = text.find("{", index)
    if start < 0:
        return {}
    depth = 0
    in_string = False
    escaped = False
    for position in range(start, len(text)):
        char = text[position]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                blob = text[start : position + 1]
                try:
                    data = json.loads(blob)
                except json.JSONDecodeError:
                    return {}
                return data if isinstance(data, dict) else {}
    return {}


def _timecode_to_seconds(value: str) -> float | None:
    parts = [piece for piece in value.strip().split(":") if piece]
    if not parts or len(parts) > 3:
        return None
    if not all(part.isdigit() for part in parts):
        return None
    seconds = 0.0
    for part in parts:
        seconds = seconds * 60 + float(part)
    return seconds


def _parse_description_chapters(description: str, duration_seconds: float) -> list[dict[str, Any]]:
    rows: list[tuple[float, str]] = []
    for raw_line in description.splitlines():
        line = compact_whitespace(raw_line)
        match = re.match(r"^((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(.+)$", line)
        if not match:
            continue
        seconds = _timecode_to_seconds(match.group(1))
        if seconds is None:
            continue
        rows.append((seconds, match.group(2).strip()))
    rows.sort(key=lambda item: item[0])
    chapters: list[dict[str, Any]] = []
    for index, (start, title) in enumerate(rows, start=1):
        end = rows[index][0] if index < len(rows) else max(start + 1.0, duration_seconds)
        chapters.append(
            {
                "id": f"ch{index:02}",
                "title": compact_whitespace(title),
                "start": round(start, 3),
                "end": round(max(start + 1.0, end), 3),
            }
        )
    return chapters


def _walk_values(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    stack = [node]
    while stack:
        current = stack.pop()
        if isinstance(current, dict):
            found.append(current)
            stack.extend(current.values())
        elif isinstance(current, list):
            stack.extend(current)
    return found


def _parse_youtube_chapters(initial_data: dict[str, Any], description: str, duration_seconds: float) -> list[dict[str, Any]]:
    chapters: list[dict[str, Any]] = []
    raw_starts: list[tuple[float, str]] = []
    for item in _walk_values(initial_data):
        chapter = item.get("chapterRenderer") if isinstance(item, dict) else None
        if not isinstance(chapter, dict):
            marker = item.get("macroMarkersListItemRenderer") if isinstance(item, dict) else None
            if isinstance(marker, dict):
                chapter = marker
        if not isinstance(chapter, dict):
            continue
        title_obj = chapter.get("title", {})
        title = ""
        if isinstance(title_obj, dict):
            if "simpleText" in title_obj:
                title = str(title_obj.get("simpleText", ""))
            else:
                title = " ".join(str(run.get("text", "")) for run in title_obj.get("runs", []) if isinstance(run, dict))
        start_ms = chapter.get("timeRangeStartMillis")
        if start_ms is None:
            time_obj = chapter.get("timeDescription", {})
            text = str(time_obj.get("simpleText", "")) if isinstance(time_obj, dict) else ""
            parsed = _timecode_to_seconds(text)
            if parsed is None:
                continue
            start_seconds = parsed
        else:
            start_seconds = float(start_ms) / 1000.0
        if not title.strip():
            continue
        raw_starts.append((start_seconds, compact_whitespace(title)))
    if not raw_starts:
        return _parse_description_chapters(description, duration_seconds)
    raw_starts.sort(key=lambda item: item[0])
    for index, (start, title) in enumerate(raw_starts, start=1):
        end = raw_starts[index][0] if index < len(raw_starts) else max(start + 1.0, duration_seconds)
        chapters.append(
            {
                "id": f"ch{index:02}",
                "title": title,
                "start": round(start, 3),
                "end": round(max(start + 1.0, end), 3),
            }
        )
    return chapters


def _download_caption_track(base_url: str) -> list[dict[str, Any]]:
    url = f"{base_url}&fmt=srv3"
    request = Request(url, headers={"User-Agent": YOUTUBE_USER_AGENT, "Accept": "application/xml,text/xml"})
    try:
        with urlopen(request, timeout=30) as response:
            xml_text = response.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError):
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    transcript: list[dict[str, Any]] = []
    for index, node in enumerate(root.findall(".//text"), start=1):
        start = float(node.attrib.get("start", "0") or 0.0)
        duration = max(0.2, float(node.attrib.get("dur", "2") or 2.0))
        text = compact_whitespace(unescape("".join(node.itertext())))
        if not text:
            continue
        transcript.append(
            {
                "id": f"seg{index:03}",
                "start": round(start, 3),
                "duration": round(duration, 3),
                "end": round(start + duration, 3),
                "text": text,
                "speaker": "",
            }
        )
    return transcript


def _fetch_youtube_metadata(source_url: str) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]], float]:
    video_id = youtube_video_id(source_url)
    if not video_id:
        raise ValueError("Invalid YouTube URL.")
    html = _http_get(f"https://www.youtube.com/watch?v={video_id}")
    player = _extract_json_object(html, "ytInitialPlayerResponse")
    initial_data = _extract_json_object(html, "ytInitialData")
    details = player.get("videoDetails", {}) if isinstance(player, dict) else {}
    title = compact_whitespace(str(details.get("title", "") or "YouTube documentary"))
    author = compact_whitespace(str(details.get("author", "")))
    description = str(details.get("shortDescription", ""))
    duration_seconds = float(details.get("lengthSeconds", 0) or 0)
    thumbnails = details.get("thumbnail", {}).get("thumbnails", []) if isinstance(details, dict) else []
    metadata = {
        "title": title,
        "channel": author,
        "video_id": video_id,
        "duration_seconds": duration_seconds,
        "description": description,
        "thumbnails": thumbnails if isinstance(thumbnails, list) else [],
        "metadata_source": "youtube_watch_page",
    }
    tracks = []
    caption_root = player.get("captions", {}).get("playerCaptionsTracklistRenderer", {}) if isinstance(player, dict) else {}
    if isinstance(caption_root, dict):
        tracks = caption_root.get("captionTracks", []) if isinstance(caption_root.get("captionTracks"), list) else []
    transcript: list[dict[str, Any]] = []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        base_url = str(track.get("baseUrl", ""))
        if not base_url:
            continue
        transcript = _download_caption_track(base_url)
        if transcript:
            metadata["transcript_language"] = str(track.get("languageCode", ""))
            break
    chapters = _parse_youtube_chapters(initial_data, description, duration_seconds)
    return metadata, transcript, chapters, duration_seconds


def _download_youtube_reference(source_url: str, storage_dir: Path) -> Path | None:
    yt_dlp = shutil.which("yt-dlp")
    if yt_dlp is None:
        return None
    output_template = storage_dir / "reference.%(ext)s"
    command = [
        yt_dlp,
        "--no-playlist",
        "-f",
        "mp4/bestvideo+bestaudio/best",
        "-o",
        str(output_template),
        source_url,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        return None
    candidates = sorted(storage_dir.glob("reference.*"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
    for candidate in candidates:
        if candidate.suffix.lower() in SUPPORTED_REFERENCE_VIDEO_SUFFIXES | SUPPORTED_REFERENCE_AUDIO_SUFFIXES:
            return candidate
    return None


def _fetch_vimeo_metadata(source_url: str) -> dict[str, Any]:
    try:
        payload = _http_get(f"https://vimeo.com/api/oembed.json?url={source_url}", accept="application/json")
        data = json.loads(payload)
        if isinstance(data, dict):
            return {
                "title": compact_whitespace(str(data.get("title", "Vimeo documentary"))),
                "channel": compact_whitespace(str(data.get("author_name", ""))),
                "duration_seconds": float(data.get("duration", 0) or 0),
                "metadata_source": "vimeo_oembed",
            }
    except Exception:
        pass
    return {"title": "Vimeo documentary", "channel": "", "duration_seconds": 0.0, "metadata_source": "vimeo_url"}


def _sidecar_payload(local_path: Path | None) -> dict[str, Any]:
    if local_path is None:
        return {}
    sidecar = local_path.with_suffix(local_path.suffix + ".json")
    if not sidecar.exists():
        return {}
    payload = read_json(sidecar)
    return payload if isinstance(payload, dict) else {}


def _reference_storage_dir(project_root: Path) -> Path:
    return project_root / "workspace" / "reference_documentary"


def _reference_manifest_path(project_root: Path) -> Path:
    return project_root / "manifests" / "reference_documentary.json"


def _normalize_transcript(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        segments: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            start = float(item.get("start", 0.0) or 0.0)
            duration = max(0.5, float(item.get("duration", item.get("end", start + 4.0)) or 4.0))
            if "end" in item:
                duration = max(0.5, float(item.get("end", start + duration)) - start)
            text = compact_whitespace(str(item.get("text", "")))
            if not text:
                continue
            segments.append(
                {
                    "id": str(item.get("id", f"seg{index + 1:03}")),
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "end": round(start + duration, 3),
                    "text": text,
                    "speaker": compact_whitespace(str(item.get("speaker", ""))),
                }
            )
        return segments
    if isinstance(raw, str) and raw.strip():
        pieces = [compact_whitespace(piece) for piece in re.split(r"(?<=[.!?])\s+", raw) if compact_whitespace(piece)]
        return [
            {
                "id": f"seg{index + 1:03}",
                "start": round(index * 6.0, 3),
                "duration": 6.0,
                "end": round(index * 6.0 + 6.0, 3),
                "text": piece,
                "speaker": "",
            }
            for index, piece in enumerate(pieces)
        ]
    return []


def _normalize_chapters(raw: Any, duration_seconds: float, transcript: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(raw, list):
        chapters: list[dict[str, Any]] = []
        for index, item in enumerate(raw):
            if not isinstance(item, dict):
                continue
            start = float(item.get("start", 0.0) or 0.0)
            end = float(item.get("end", 0.0) or 0.0)
            if end <= start:
                end = min(duration_seconds, start + SCENE_WINDOW_SECONDS)
            chapters.append(
                {
                    "id": str(item.get("id", f"ch{index + 1:02}")),
                    "title": compact_whitespace(str(item.get("title", f"Chapter {index + 1}"))),
                    "start": round(start, 3),
                    "end": round(max(start + 1.0, end), 3),
                }
            )
        if chapters:
            return chapters
    if not transcript:
        return [{"id": "ch01", "title": "Reference documentary", "start": 0.0, "end": max(1.0, duration_seconds)}]
    chapters = []
    start = 0.0
    cursor = 1
    while start < duration_seconds:
        end = min(duration_seconds, start + SCENE_WINDOW_SECONDS)
        chapters.append(
            {
                "id": f"ch{cursor:02}",
                "title": f"Scene block {cursor}",
                "start": round(start, 3),
                "end": round(max(start + 1.0, end), 3),
            }
        )
        cursor += 1
        start = end
    return chapters or [{"id": "ch01", "title": "Reference documentary", "start": 0.0, "end": max(1.0, duration_seconds)}]


def _transcribe_with_whisper(local_path: Path) -> list[dict[str, Any]]:
    try:
        import whisper  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "No transcript was supplied and Whisper is not installed. Add a transcript sidecar JSON, or install whisper for local transcription."
        ) from error
    model = whisper.load_model("base")
    result = model.transcribe(str(local_path), fp16=False, verbose=False)
    transcript = []
    for index, item in enumerate(result.get("segments", [])):
        if not isinstance(item, dict):
            continue
        start = float(item.get("start", 0.0) or 0.0)
        end = float(item.get("end", start + 1.0) or start + 1.0)
        text = compact_whitespace(str(item.get("text", "")))
        if not text:
            continue
        transcript.append(
            {
                "id": f"seg{index + 1:03}",
                "start": round(start, 3),
                "duration": round(max(0.5, end - start), 3),
                "end": round(max(start + 0.5, end), 3),
                "text": text,
                "speaker": "",
            }
        )
    return transcript


def create_reference_documentary(
    project_root: Path,
    *,
    source_url: str = "",
    local_path: Path | None = None,
    original_filename: str = "",
    metadata: dict[str, Any] | None = None,
    instructions: str = "",
) -> dict[str, Any]:
    kind = infer_reference_input_kind(source_url, local_path)
    sidecar = _sidecar_payload(local_path)
    payload_metadata: dict[str, Any] = {**sidecar.get("metadata", {}), **(metadata or {})}
    chapters: list[dict[str, Any]] = [item for item in sidecar.get("chapters", []) if isinstance(item, dict)]
    transcript: list[dict[str, Any]] = _normalize_transcript(sidecar.get("transcript", []))

    storage_dir = _reference_storage_dir(project_root)
    storage_dir.mkdir(parents=True, exist_ok=True)
    stored_path = ""

    if kind == "youtube":
        fetched_metadata, fetched_transcript, fetched_chapters, fetched_duration = _fetch_youtube_metadata(source_url)
        payload_metadata = {**fetched_metadata, **payload_metadata}
        if not transcript:
            transcript = _normalize_transcript(fetched_transcript)
        if not chapters:
            chapters = [item for item in fetched_chapters if isinstance(item, dict)]
        if float(payload_metadata.get("duration_seconds", 0.0) or 0.0) <= 0 and fetched_duration > 0:
            payload_metadata["duration_seconds"] = fetched_duration
        # Capture a local working copy so scene-bound visual analysis and local transcription can run.
        downloaded = _download_youtube_reference(source_url, storage_dir)
        if downloaded is not None:
            stored_path = str(downloaded.relative_to(project_root))
    elif kind == "vimeo":
        payload_metadata = {**_fetch_vimeo_metadata(source_url), **payload_metadata}
    else:
        if local_path is not None:
            destination = storage_dir / f"reference{local_path.suffix.lower()}"
            shutil.copy2(local_path, destination)
            stored_path = str(destination.relative_to(project_root))

    duration_seconds = float(sidecar.get("duration_seconds", payload_metadata.get("duration_seconds", 0.0)) or 0.0)

    # Fallback to local transcription when no transcript exists.
    if not transcript and stored_path:
        transcript = _transcribe_with_whisper(project_root / stored_path)
        if duration_seconds <= 0 and transcript:
            duration_seconds = max(float(item.get("end", 0.0) or 0.0) for item in transcript)
    if kind == "youtube" and not transcript:
        raise RuntimeError(
            "No YouTube transcript was available and no local download was created for Whisper fallback. Install yt-dlp or provide a local MP4/sidecar transcript."
        )

    document = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "input_kind": kind,
        "source_url": source_url.strip(),
        "stored_path": stored_path,
        "original_filename": original_filename or (local_path.name if local_path else ""),
        "metadata": payload_metadata,
        "instructions": compact_whitespace(instructions),
        "chapters": chapters,
        "transcript": transcript,
        "duration_seconds": float(duration_seconds or payload_metadata.get("duration_seconds", 0.0) or 0.0),
        "title": compact_whitespace(str(payload_metadata.get("title") or original_filename or Path(source_url).name or project_root.name)),
    }
    write_json(_reference_manifest_path(project_root), document)
    workflow = load_manifest(project_root, "workflow.json")
    workflow.update(
        {
            "workflow_type": "recycle_documentary",
            "reference_documentary_loaded": True,
            "reference_documentary_title": document["title"],
        }
    )
    save_manifest(project_root, "workflow.json", workflow)
    return document


def load_reference_documentary(project_root: Path) -> dict[str, Any]:
    path = _reference_manifest_path(project_root)
    if not path.exists():
        raise RuntimeError("No reference documentary has been loaded for this project.")
    data = read_json(path)
    return data if isinstance(data, dict) else {}


def _fallback_duration(project_root: Path, reference: dict[str, Any], transcript: list[dict[str, Any]]) -> float:
    explicit = float(reference.get("duration_seconds", 0.0) or 0.0)
    if explicit > 0:
        return explicit
    stored_path = str(reference.get("stored_path", ""))
    if stored_path:
        absolute = project_root / stored_path
        if absolute.exists():
            try:
                return round(media_duration_seconds(absolute), 3)
            except Exception:
                pass
    if transcript:
        return round(max(float(item.get("end", 0.0) or 0.0) for item in transcript), 3)
    return 0.0


def _sentence_split(text: str) -> list[str]:
    return [compact_whitespace(part) for part in re.split(r"(?<=[.!?])\s+", text) if compact_whitespace(part)]


def _extract_dates(text: str) -> list[str]:
    values = re.findall(r"\b(?:1[0-9]{3}|20[0-9]{2})\b", text)
    return list(dict.fromkeys(values))[:4]


def _extract_capitalized_phrases(text: str) -> list[str]:
    matches = re.findall(r"\b(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,3})\b", text)
    filtered = [item for item in matches if item.lower() not in {"the", "this", "that", "later", "chapter", "scene"}]
    return list(dict.fromkeys(filtered))[:12]


def _derive_event_focus(chapter_title: str, text: str) -> str:
    candidates = _sentence_split(" ".join(part for part in (chapter_title, text) if part))
    if candidates:
        sentence = candidates[0]
        tokens = sentence.split()
        return " ".join(tokens[: min(9, len(tokens))])
    return compact_whitespace(chapter_title) or "Reference event"


def _derive_action(text: str) -> str:
    lowered = text.lower()
    patterns = [
        ("explosion", "an explosion or fire incident unfolds"),
        ("burn", "an injury incident is unfolding and immediate response follows"),
        ("ambulance", "emergency responders are transporting the subject"),
        ("hospital", "medical treatment and aftermath are being addressed"),
        ("trial", "court proceedings and legal arguments are underway"),
        ("court", "legal testimony or judgment is taking place"),
        ("launch", "a mission launch sequence is being executed"),
        ("landing", "a landing sequence and recovery operation is underway"),
        ("interview", "witnesses or participants are giving testimony"),
        ("arrest", "law enforcement action is occurring"),
    ]
    for token, action in patterns:
        if token in lowered:
            return action
    words = [item for item in re.findall(r"[A-Za-z0-9'-]{4,}", text) if item.lower() not in {"with", "from", "that", "this", "after", "before", "their", "there", "because", "while"}]
    if words:
        return compact_whitespace(" ".join(words[:8]))
    return "the event progression described in the narration"


def _required_visuals(event_focus: str, entities: dict[str, list[str]], text: str) -> list[str]:
    visuals = [event_focus]
    visuals.extend(entities.get("places", []))
    visuals.extend(entities.get("organizations", []))
    if entities.get("dates"):
        visuals.append(f"date marker {entities['dates'][0]}")
    for sentence in _sentence_split(text)[:2]:
        tokens = [
            token
            for token in re.findall(r"[A-Za-z0-9'-]{4,}", sentence)
            if token.lower()
            not in {"with", "from", "that", "this", "after", "before", "their", "there", "because", "while"}
        ]
        if tokens:
            visuals.append(" ".join(tokens[:3]))
    return list(dict.fromkeys(compact_whitespace(item) for item in visuals if compact_whitespace(item)))[:8]


def _replacement_footage_goal(event_focus: str, action: str, entities: dict[str, list[str]]) -> str:
    people = ", ".join(entities.get("people", [])[:2])
    places = ", ".join(entities.get("places", [])[:2])
    context_bits = [item for item in [people, places] if item]
    context = f" around {' / '.join(context_bits)}" if context_bits else ""
    return compact_whitespace(
        f"Replacement footage should communicate the concrete event '{event_focus}', show that {action}, and provide verifiable context{context}."
    )


def _scene_entities(chapter_title: str, text: str) -> dict[str, list[str]]:
    capitalized = _extract_capitalized_phrases(f"{chapter_title} {text}")
    dates = _extract_dates(text)
    people = [item for item in capitalized if len(item.split()) <= 3][:5]
    places = [
        item
        for item in capitalized
        if any(
            token in item.lower()
            for token in (
                "city",
                "county",
                "street",
                "hospital",
                "ranch",
                "cape",
                "london",
                "paris",
                "houston",
                "southampton",
                "tokyo",
                "moscow",
                "berlin",
            )
        )
    ][:5]
    organizations = [
        item
        for item in capitalized
        if any(
            token in item.lower()
            for token in (
                "agency",
                "court",
                "government",
                "police",
                "company",
                "archive",
                "administration",
                "nasa",
                "bbc",
                "pepsi",
                "fbi",
                "cia",
            )
        )
    ][:5]
    if not places and len(capitalized) > 3:
        places = capitalized[1:3]
    return {
        "people": list(dict.fromkeys(people)),
        "places": list(dict.fromkeys(places)),
        "organizations": list(dict.fromkeys(organizations)),
        "dates": dates,
        "historical_events": list(dict.fromkeys([_derive_event_focus(chapter_title, text)])),
        "objects": [],
    }


def _sample_scene_change_times(video_path: Path, duration_seconds: float) -> list[float]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-i",
        str(video_path),
        "-vf",
        "select='gt(scene,0.33)',showinfo",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0 and not completed.stderr:
        return []
    values = sorted(
        {
            round(float(match.group(1)), 3)
            for match in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", completed.stderr)
            if 0 < float(match.group(1)) < max(1.0, duration_seconds - 0.5)
        }
    )
    return values[:300]


def _frame_signal(video_path: Path, timestamp: float) -> dict[str, Any]:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "info",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-vf",
        "signalstats,metadata=print:file=-",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    output = completed.stdout + "\n" + completed.stderr
    values: dict[str, float] = {}
    for key in ("YAVG", "SATAVG", "HUEMED"):
        match = re.search(rf"{key}=(-?[0-9]+(?:\.[0-9]+)?)", output)
        if match:
            values[key] = float(match.group(1))
    yavg = values.get("YAVG", 110.0)
    sat = values.get("SATAVG", 70.0)
    descriptors: list[str] = []
    if yavg < 55:
        descriptors.append("low-light frame")
    elif yavg > 170:
        descriptors.append("bright high-key frame")
    else:
        descriptors.append("mid-tone frame")
    if sat < 45:
        descriptors.append("desaturated archival look")
    elif sat > 120:
        descriptors.append("high-saturation modern footage")
    else:
        descriptors.append("neutral saturation")
    return {
        "timestamp_seconds": round(timestamp, 3),
        "yavg": round(yavg, 2),
        "satavg": round(sat, 2),
        "descriptors": descriptors,
    }


def _extract_frame(video_path: Path, output: Path, timestamp: float) -> bool:
    output.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{max(0.0, timestamp):.3f}",
        "-i",
        str(video_path),
        "-frames:v",
        "1",
        "-q:v",
        "3",
        str(output),
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    return completed.returncode == 0 and output.exists() and output.stat().st_size > 0


def _visual_analysis(
    project_root: Path,
    stored_path: str,
    chapters: list[dict[str, Any]],
    duration_seconds: float,
) -> dict[str, Any]:
    if not stored_path:
        return {"version": 1, "mode": "no_local_video", "samples": [], "scene_observations": {}}
    video_path = project_root / stored_path
    if not video_path.exists() or video_path.suffix.lower() not in SUPPORTED_REFERENCE_VIDEO_SUFFIXES:
        return {"version": 1, "mode": "unsupported_media", "samples": [], "scene_observations": {}}

    scene_changes = _sample_scene_change_times(video_path, duration_seconds)
    sample_dir = project_root / "workspace" / "reference_documentary" / "frames"
    all_samples: list[dict[str, Any]] = []
    observations: dict[str, list[dict[str, Any]]] = {}

    for chapter in chapters:
        scene_id = str(chapter.get("id", ""))
        start = float(chapter.get("start", 0.0) or 0.0)
        end = float(chapter.get("end", min(duration_seconds, start + SCENE_WINDOW_SECONDS)) or start + SCENE_WINDOW_SECONDS)
        mid = start + max(0.5, (end - start) / 2)
        anchor = [start + 0.8, mid, max(start + 1.0, end - 0.8)]
        local_changes = [value for value in scene_changes if start <= value <= end]
        if local_changes:
            anchor.append(local_changes[len(local_changes) // 2])
        chosen = sorted({round(max(start, min(end - 0.2, value)), 3) for value in anchor if end - start > 0.5})

        bucket: list[dict[str, Any]] = []
        for index, timestamp in enumerate(chosen, start=1):
            frame_path = sample_dir / f"{scene_id}_{index:02}.jpg"
            extracted = _extract_frame(video_path, frame_path, timestamp)
            signal = _frame_signal(video_path, timestamp)
            signal["frame_path"] = str(frame_path.relative_to(project_root)) if extracted else ""
            signal["is_scene_change_candidate"] = any(abs(timestamp - value) < 1.0 for value in local_changes)
            bucket.append(signal)
            all_samples.append({"scene_id": scene_id, **signal})
        observations[scene_id] = bucket

    return {
        "version": 1,
        "mode": "video_frame_sampling",
        "scene_change_count": len(scene_changes),
        "scene_change_timestamps": scene_changes,
        "samples": all_samples,
        "scene_observations": observations,
    }


def _scene_payload(
    index: int,
    chapter: dict[str, Any],
    transcript: list[dict[str, Any]],
    visual_observations: list[dict[str, Any]],
) -> dict[str, Any]:
    start = float(chapter.get("start", 0.0) or 0.0)
    end = float(chapter.get("end", start + SCENE_WINDOW_SECONDS) or start + SCENE_WINDOW_SECONDS)
    matching = [
        item
        for item in transcript
        if float(item.get("start", 0.0) or 0.0) < end and float(item.get("end", 0.0) or 0.0) > start
    ]
    narration = compact_whitespace(" ".join(str(item.get("text", "")) for item in matching))
    event_focus = _derive_event_focus(str(chapter.get("title", "")), narration)
    entities = _scene_entities(str(chapter.get("title", "")), narration)
    visual_tags = [
        tag
        for sample in visual_observations
        for tag in sample.get("descriptors", [])
        if isinstance(tag, str) and tag
    ]
    if any("scene_change" in str(sample.get("is_scene_change_candidate")) for sample in visual_observations):
        visual_tags.append("scene transition emphasis")
    visual_tags = list(dict.fromkeys(visual_tags))[:6]
    action_occurring = _derive_action(narration)
    visible_people = list(entities.get("people", []))[:3]
    location_shown = ", ".join(entities.get("places", [])[:2]) or "unspecified location"
    purpose = f"Explain the event '{event_focus}' and move the chronology forward."
    if visual_tags:
        purpose = f"Explain the event '{event_focus}' using {', '.join(visual_tags[:2])} as visual evidence."
    viewer = f"Understand what happened during '{event_focus}' and why it matters to the broader timeline."
    replacement_goal = _replacement_footage_goal(event_focus, action_occurring, entities)
    return {
        "scene_id": f"scene_{index:02}",
        "chapter_id": str(chapter.get("id", f"ch{index:02}")),
        "title": compact_whitespace(str(chapter.get("title", f"Scene {index}"))),
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "duration_seconds": round(max(1.0, end - start), 3),
        "narration": narration,
        "event_focus": event_focus,
        "who_is_visible": visible_people,
        "event_shown": event_focus,
        "where_it_takes_place": location_shown,
        "action_occurring": action_occurring,
        "purpose": purpose,
        "viewer_understanding": viewer,
        "required_visuals": _required_visuals(event_focus, entities, narration),
        "entities": entities,
        "why_shown": purpose,
        "replacement_footage_should_communicate": replacement_goal,
        "visual_observations": visual_observations,
    }


def _claim_candidates(scene: dict[str, Any]) -> list[dict[str, Any]]:
    statements = []
    for sentence in _sentence_split(str(scene.get("narration", ""))):
        if len(sentence) < 35 or len(sentence) > 240 or "?" in sentence:
            continue
        if not re.search(
            r"\b(is|was|were|had|did|announced|released|landed|departed|died|won|started|began|ended|burned|founded|launched|returned|trial|arrested|opened|closed)\b",
            sentence,
            re.I,
        ):
            continue
        dates = _extract_dates(sentence) or list(scene.get("entities", {}).get("dates", []))[:1]
        statements.append(
            {
                "statement": sentence,
                "scene_id": scene["scene_id"],
                "dates": dates,
                "people": list(scene.get("entities", {}).get("people", [])),
                "places": list(scene.get("entities", {}).get("places", [])),
                "organizations": list(scene.get("entities", {}).get("organizations", [])),
                "event_focus": str(scene.get("event_focus", "")),
            }
        )
        if len(statements) >= 6:
            break
    return statements


def _event_query_expansions(person: str, event_focus: str, location: str, date: str) -> list[str]:
    focus = compact_whitespace(event_focus)
    outputs = [compact_whitespace(f"{person} {focus} archive footage")]
    if location:
        outputs.append(compact_whitespace(f"{person} {location} {focus}"))
    if date:
        outputs.append(compact_whitespace(f"{person} {focus} {date}"))
    lowered = focus.lower()
    if "accident" in lowered or "burn" in lowered:
        outputs.append(compact_whitespace(f"{person} {focus} hospital"))
        outputs.append(compact_whitespace(f"{person} {focus} interview"))
        outputs.append(compact_whitespace(f"{person} {focus} ambulance"))
        outputs.append(compact_whitespace(f"{person} {focus} explosion site"))
        outputs.append(compact_whitespace(f"{person} {focus} newspaper headline"))
    if "trial" in lowered or "court" in lowered:
        outputs.append(compact_whitespace(f"{person} {focus} courtroom"))
        outputs.append(compact_whitespace(f"{person} {focus} newspapers"))
        outputs.append(compact_whitespace(f"{person} {focus} court sketch"))
        outputs.append(compact_whitespace(f"{person} {focus} press conference"))
    if "launch" in lowered or "landing" in lowered:
        outputs.append(compact_whitespace(f"{person} {focus} mission footage"))
    if "performance" in lowered or "concert" in lowered:
        outputs.append(compact_whitespace(f"{person} early performance archive"))
    return outputs


def _search_queries(scene: dict[str, Any]) -> list[str]:
    subjects = [item for item in scene.get("entities", {}).get("people", [])[:2] if compact_whitespace(str(item))]
    places = [item for item in scene.get("entities", {}).get("places", [])[:2] if compact_whitespace(str(item))]
    organizations = [
        item for item in scene.get("entities", {}).get("organizations", [])[:2] if compact_whitespace(str(item))
    ]
    dates = [item for item in scene.get("entities", {}).get("dates", [])[:2] if compact_whitespace(str(item))]
    event_focus = compact_whitespace(str(scene.get("event_focus", "")))
    title = compact_whitespace(str(scene.get("title", "")))
    queries: list[str] = []

    for subject in subjects:
        queries.extend(_event_query_expansions(subject, event_focus, places[0] if places else "", dates[0] if dates else ""))
    if organizations and event_focus:
        queries.append(compact_whitespace(f"{organizations[0]} {event_focus} documents"))
    if places and event_focus:
        queries.append(compact_whitespace(f"{places[0]} {event_focus} historical footage"))
    if event_focus and dates:
        queries.append(compact_whitespace(f"{event_focus} {dates[0]} archive"))
    queries.append(compact_whitespace(f"{event_focus} news footage"))
    queries.append(compact_whitespace(f"{event_focus} documentary evidence"))
    queries.append(compact_whitespace(f"{title} documentary archive"))
    replacement_goal = compact_whitespace(str(scene.get("replacement_footage_should_communicate", "")))
    if replacement_goal:
        queries.append(replacement_goal)

    deduped: list[str] = []
    seen: set[str] = set()
    for query in queries:
        cleaned = compact_whitespace(query)
        lowered = cleaned.lower()
        if len(cleaned.split()) < 2:
            continue
        # Avoid generic person-only searches.
        if subjects and cleaned.lower() in {str(subject).lower() for subject in subjects}:
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped[:8]


def _timeline_events(claims: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for index, claim in enumerate(claims, start=1):
        raw_dates = claim.get("dates", [])
        date = raw_dates[0] if isinstance(raw_dates, list) and raw_dates else ""
        events.append(
            {
                "event_id": f"event_{index:02}",
                "date": date,
                "label": compact_whitespace(str(claim.get("event_focus", "")) or str(claim.get("statement", ""))[:72]),
                "summary": str(claim.get("statement", "")),
                "scene_id": str(claim.get("scene_id", "")),
            }
        )
    if not events:
        for index, scene in enumerate(scenes, start=1):
            events.append(
                {
                    "event_id": f"event_{index:02}",
                    "date": (scene.get("entities", {}).get("dates") or [""])[0],
                    "label": str(scene.get("event_focus", f"Scene {index}")),
                    "summary": str(scene.get("viewer_understanding", "")),
                    "scene_id": str(scene.get("scene_id", "")),
                }
            )
    events.sort(key=lambda item: str(item.get("date", "9999")) or "9999")
    return events


def _shot_plan(
    events: list[dict[str, Any]],
    scenes_by_id: dict[str, dict[str, Any]],
    search_queries: dict[str, list[str]],
) -> list[dict[str, Any]]:
    plan = []
    for event in events:
        scene = scenes_by_id.get(str(event.get("scene_id", "")), {})
        visuals = list(scene.get("required_visuals", []))
        plan.append(
            {
                "event_id": str(event.get("event_id", "")),
                "scene_id": str(event.get("scene_id", "")),
                "date": str(event.get("date", "")),
                "narration_goal": str(event.get("summary", "")),
                "visual_goal": visuals[0] if visuals else str(event.get("label", "")),
                "emotion": "focused curiosity" if event != events[-1] else "resolution",
                "supporting_media": visuals[:5],
                "search_queries": list(search_queries.get(str(event.get("scene_id", "")), [])),
            }
        )
    return plan


def _verification_query(claim: dict[str, Any]) -> str:
    parts = []
    if claim.get("people"):
        parts.append(str(claim["people"][0]))
    if claim.get("event_focus"):
        parts.append(str(claim["event_focus"]))
    if claim.get("dates"):
        parts.append(str(claim["dates"][0]))
    return compact_whitespace(" ".join(parts)) or compact_whitespace(str(claim.get("statement", ""))[:120])


def seed_recycle_research(project_root: Path, blueprint: dict[str, Any]) -> dict[str, Any]:
    sources_manifest = load_manifest(project_root, "sources.json")
    existing_sources = {
        str(item.get("url", ""))
        for item in sources_manifest.get("sources", [])
        if isinstance(item, dict) and item.get("url")
    }
    reference = load_reference_documentary(project_root)
    reference_url = str(reference.get("source_url", ""))
    source_title = str(reference.get("title", project_root.name))
    source_id = ""
    if reference_url and reference_url not in existing_sources:
        source = add_source(
            project_root,
            title=f"Reference documentary blueprint: {source_title}",
            url=reference_url,
            publisher="reference_documentary_blueprint",
            source_type="video",
            reliability_notes="Blueprint only. Never treat this reference documentary as a factual source. Verify each claim independently.",
        )
        source_id = str(source.get("id", ""))
        sources = load_manifest(project_root, "sources.json")
        for item in sources.get("sources", []):
            if isinstance(item, dict) and item.get("id") == source_id:
                item.update({"relevance_status": "relevant", "review_status": "rejected", "blueprint_only": True})
        save_manifest(project_root, "sources.json", sources)

    request_path = project_root / "manifests" / "production_request.json"
    request = read_json(request_path) if request_path.exists() else {}
    request["workflow_type"] = "recycle_documentary"
    request["reference_documentary"] = {
        "title": reference.get("title", ""),
        "source_url": reference_url,
        "input_kind": reference.get("input_kind", ""),
        "duration_seconds": blueprint.get("duration_seconds", 0.0),
    }
    request["recycle_blueprint"] = {
        "storyline": blueprint.get("storyline", []),
        "timeline": blueprint.get("timeline", []),
        "people": blueprint.get("people", []),
        "places": blueprint.get("places", []),
        "organizations": blueprint.get("organizations", []),
        "historical_events": blueprint.get("historical_events", []),
        "verification_queries": [item.get("verification_query", "") for item in blueprint.get("verification_queue", [])[:20]],
    }
    if source_id:
        request["recycle_blueprint"]["blueprint_source_id"] = source_id
    write_json(request_path, request)
    return request


def _report_markdown(reference: dict[str, Any], blueprint: dict[str, Any], discovery_priorities: list[str]) -> str:
    lines = [
        "# Recycle Engine Report",
        "",
        "## Reference documentary",
        f"- Title: {reference.get('title', '')}",
        f"- Input kind: {reference.get('input_kind', '')}",
        f"- Source: {reference.get('source_url', '') or reference.get('stored_path', '')}",
        f"- Duration seconds: {blueprint.get('duration_seconds', 0.0)}",
        "",
        "## Timeline",
    ]
    for event in blueprint.get("timeline", []):
        lines.append(f"- {event.get('date', 'undated')}: {event.get('label', '')}")
    lines.extend(["", "## Claims extracted"])
    for claim in blueprint.get("claims", []):
        lines.append(f"- {claim.get('statement', '')}")
    lines.extend(
        [
            "",
            "## Claims verified",
            "- Pending independent verification by the research pipeline.",
            "",
            "## Claims corrected",
            "- None yet.",
            "",
            "## Claims removed",
            "- None yet.",
            "",
            "## Claims added",
            "- None yet.",
            "",
            "## Shot plan",
        ]
    )
    for item in blueprint.get("shot_plan", []):
        lines.append(
            f"- {item.get('date', 'undated')} | {item.get('visual_goal', '')} | {item.get('narration_goal', '')}"
        )
    lines.extend(["", "## Generated search queries"])
    for scene_id, queries in blueprint.get("scene_queries", {}).items():
        lines.append(f"- {scene_id}: {', '.join(queries)}")
    lines.extend(["", "## Media providers used"])
    for provider in discovery_priorities:
        lines.append(f"- {provider}")
    metrics = blueprint.get("scores", {})
    lines.extend(
        [
            "",
            "## Scores",
            f"- Visual diversity score: {metrics.get('visual_diversity_score', 0)}",
            f"- Storytelling score: {metrics.get('storytelling_score', 0)}",
            f"- Research score: {metrics.get('research_score', 0)}",
            f"- Originality score: {metrics.get('originality_score', 0)}",
            f"- Overall documentary quality: {metrics.get('overall_documentary_quality', 0)}",
        ]
    )
    return "\n".join(lines) + "\n"


def prepare_recycle_documentary(project_root: Path) -> dict[str, Any]:
    reference = load_reference_documentary(project_root)
    transcript = _normalize_transcript(reference.get("transcript", []))
    stored_path = str(reference.get("stored_path", ""))
    if not transcript and stored_path:
        transcript = _transcribe_with_whisper(project_root / stored_path)
        reference["transcript"] = transcript
        write_json(_reference_manifest_path(project_root), reference)
    if not transcript:
        raise RuntimeError(
            "The recycle workflow needs a transcript. Add one to the source, provide a sidecar transcript, or enable local transcription."
        )

    duration_seconds = _fallback_duration(project_root, reference, transcript)
    chapters = _normalize_chapters(reference.get("chapters", []), duration_seconds, transcript)
    visual = _visual_analysis(project_root, stored_path, chapters, duration_seconds)

    scenes = []
    observations = visual.get("scene_observations", {}) if isinstance(visual, dict) else {}
    for index, chapter in enumerate(chapters, start=1):
        chapter_id = str(chapter.get("id", f"ch{index:02}"))
        scene_observations = observations.get(chapter_id, []) if isinstance(observations, dict) else []
        scenes.append(_scene_payload(index, chapter, transcript, scene_observations))

    claims = []
    for scene in scenes:
        claims.extend(_claim_candidates(scene))
    claims = claims[:64]
    scene_queries = {scene["scene_id"]: _search_queries(scene) for scene in scenes}
    timeline = _timeline_events(claims, scenes)
    shot_plan = _shot_plan(timeline, {scene["scene_id"]: scene for scene in scenes}, scene_queries)
    verification_queue = [
        {
            "claim_id": f"recycle_claim_{index:03}",
            "statement": claim["statement"],
            "scene_id": claim["scene_id"],
            "verification_query": _verification_query(claim),
            "status": "pending_independent_verification",
            "confidence_score": round(0.55 + min(0.25, 0.05 * len(claim.get("dates", []))), 2),
        }
        for index, claim in enumerate(claims, start=1)
    ]

    score_base = min(100, 35 + len(scenes) * 4 + len(claims))
    blueprint = {
        "version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "title": reference.get("title", project_root.name),
        "duration_seconds": duration_seconds,
        "metadata": reference.get("metadata", {}),
        "chapters": chapters,
        "transcript": transcript,
        "visual_analysis": visual,
        "scenes": scenes,
        "claims": claims,
        "verification_queue": verification_queue,
        "timeline": timeline,
        "shot_plan": shot_plan,
        "scene_queries": scene_queries,
        "storyline": [scene.get("event_focus", "") for scene in scenes[:16]],
        "people": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("people", [])))[:16],
        "places": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("places", [])))[:16],
        "organizations": list(
            dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("organizations", []))
        )[:16],
        "historical_events": list(
            dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("historical_events", []))
        )[:16],
        "scores": {
            "visual_diversity_score": min(100, 45 + len({query for queries in scene_queries.values() for query in queries}) * 2),
            "storytelling_score": min(100, score_base),
            "research_score": min(100, 30 + len(verification_queue) * 2),
            "originality_score": 86,
            "overall_documentary_quality": min(
                100,
                round((score_base + min(100, 30 + len(verification_queue) * 2) + 86) / 3),
            ),
        },
        "media_provider_priorities": [
            "internet_archive",
            "wikimedia_commons",
            "wikimedia_commons_category",
            "research_source_pages",
            "pexels",
        ],
    }

    write_json(project_root / "manifests" / "recycle_blueprint.json", blueprint)
    write_json(project_root / "manifests" / "recycle_scene_understanding.json", {"version": 1, "scenes": scenes})
    write_json(project_root / "manifests" / "recycle_claims.json", {"version": 1, "claims": claims})
    write_json(project_root / "manifests" / "recycle_verification_queue.json", {"version": 1, "claims": verification_queue})
    write_json(project_root / "manifests" / "recycle_timeline.json", {"version": 1, "events": timeline})
    write_json(project_root / "manifests" / "recycle_shot_plan.json", {"version": 1, "events": shot_plan})
    write_json(project_root / "manifests" / "recycle_search_queries.json", {"version": 1, "scenes": scene_queries})
    report = _report_markdown(reference, blueprint, blueprint["media_provider_priorities"])
    (project_root / "manifests" / "recycle_engine_report.md").write_text(report, encoding="utf-8")

    workflow = load_manifest(project_root, "workflow.json")
    workflow.update(
        {
            "workflow_type": "recycle_documentary",
            "reference_documentary_loaded": True,
            "recycle_analysis_ready": True,
            "recycle_verification_ready": True,
            "recycle_reconstruction_ready": True,
            "recycle_report": "manifests/recycle_engine_report.md",
        }
    )
    save_manifest(project_root, "workflow.json", workflow)

    seed_recycle_research(project_root, blueprint)
    return blueprint


def approved_recycle_claims(project_root: Path) -> list[dict[str, Any]]:
    approved = approved_claims(project_root)
    return [claim for claim in approved if isinstance(claim, dict)]
