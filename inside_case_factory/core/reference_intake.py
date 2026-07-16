from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

from inside_case_factory.utils.files import read_json, write_json


SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
SUPPORTED_MEDIA_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm", ".mp3", ".wav", ".m4a", ".aac"}
AVOIDANCE_TERMS = ("content id bypass", "content-id bypass", "evade detection", "detectie omzeilen")


def parse_timestamp(value: str | float | int | None) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, (float, int)):
        return max(0.0, float(value))
    text = str(value).strip().replace(",", ".")
    if text.isdigit() or re.fullmatch(r"\d+(?:\.\d+)", text):
        return max(0.0, float(text))
    parts = text.split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError(f"Invalid timestamp: {value}")
    try:
        numbers = [float(part) for part in parts]
    except ValueError as error:
        raise ValueError(f"Invalid timestamp: {value}") from error
    seconds = 0.0
    for number in numbers:
        seconds = seconds * 60 + number
    return max(0.0, seconds)


def parse_time_range(value: str | None) -> tuple[float | None, float | None]:
    if not value or not value.strip():
        return None, None
    for separator in ("-->", "–", "—", "-"):
        if separator in value:
            start, end = value.split(separator, 1)
            parsed_start, parsed_end = parse_timestamp(start), parse_timestamp(end)
            if parsed_start is not None and parsed_end is not None and parsed_end <= parsed_start:
                raise ValueError("End timestamp must be after start timestamp")
            return parsed_start, parsed_end
    point = parse_timestamp(value)
    return point, None


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


def _tokens(text: str) -> set[str]:
    return {token for token in re.findall(r"[\w'-]+", text.lower()) if len(token) > 2}


def _overlap_score(query: str, candidate: str) -> float:
    wanted = _tokens(query)
    if not wanted:
        return 0.0
    return len(wanted & _tokens(candidate)) / len(wanted)


def match_transcript(
    query: str,
    transcript: Iterable[dict[str, Any]],
    *,
    hint_seconds: float | None = None,
    limit: int = 3,
) -> list[dict[str, Any]]:
    segments = [dict(segment) for segment in transcript]
    candidates: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        start = float(segment.get("start", 0.0))
        duration = max(0.5, float(segment.get("duration", 4.0)))
        window = segments[max(0, index - 1): min(len(segments), index + 2)]
        text = " ".join(str(item.get("text", "")) for item in window).strip()
        primary_lexical = _overlap_score(query, str(segment.get("text", "")))
        context_lexical = _overlap_score(query, text)
        lexical = primary_lexical * 0.9 + context_lexical * 0.1
        proximity = 0.0 if hint_seconds is None else max(0.0, 1.0 - abs(start - hint_seconds) / 120.0)
        confidence = min(0.99, lexical * 0.85 + proximity * 0.15)
        if confidence > 0:
            candidates.append({
                "start_seconds": start,
                "end_seconds": start + duration,
                "text": str(segment.get("text", "")).strip(),
                "context": text,
                "speaker": segment.get("speaker"),
                "confidence": round(confidence, 3),
            })
    candidates.sort(key=lambda item: (-float(item["confidence"]), float(item["start_seconds"])))
    return candidates[:limit]


def _clean_clip_bounds(match: dict[str, Any], chapters: Iterable[dict[str, Any]]) -> tuple[float, float]:
    start = max(0.0, float(match.get("start_seconds", 0.0)) - 1.0)
    end = max(start + 1.0, float(match.get("end_seconds", start + 8.0)) + 1.0)
    # Interview clips should remain concise; a long supplied transcript window is trimmed.
    end = min(end, start + 45.0)
    for chapter in chapters:
        title = str(chapter.get("title", "")).lower()
        if any(term in title for term in ("intro", "sponsor", "outro", "advert")):
            chapter_start = float(chapter.get("start", 0.0))
            chapter_end = float(chapter.get("end", chapter_start))
            if start < chapter_end and end > chapter_start:
                start = max(start, chapter_end)
    return round(start, 3), round(max(start + 1.0, end), 3)


def _stable_id(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()[:16]


def create_reference_intake(
    project_root: Path,
    *,
    source_url: str = "",
    local_path: Path | None = None,
    original_filename: str = "",
    note: str = "",
    timestamp: str = "",
    visible_text: str = "",
    metadata: dict[str, Any] | None = None,
    transcript: Iterable[dict[str, Any]] = (),
    possible_sources: Iterable[dict[str, Any]] = (),
    screenshot_analyzer: Callable[[Path], dict[str, Any]] | None = None,
    source_resolver: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    metadata = dict(metadata or {})
    transcript = list(transcript)
    possible_sources = list(possible_sources)
    if source_url and source_resolver is not None:
        resolved = source_resolver(source_url)
        metadata = {**resolved.get("metadata", resolved), **metadata}
        transcript = transcript or list(resolved.get("transcript", []))
        possible_sources = possible_sources or list(resolved.get("possible_sources", []))
    if local_path and local_path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES and screenshot_analyzer is not None:
        analysis = screenshot_analyzer(local_path)
        visible_text = visible_text or str(analysis.get("visible_text", ""))
        metadata = {**analysis.get("metadata", {}), **metadata}
        possible_sources = possible_sources or list(analysis.get("possible_sources", []))
    # Offline fixtures and local exports can carry source data alongside media.
    if local_path:
        sidecar = local_path.with_suffix(local_path.suffix + ".json")
        if sidecar.exists():
            local_data = read_json(sidecar)
            metadata = {**local_data.get("metadata", {}), **metadata}
            transcript = transcript or list(local_data.get("transcript", []))
            possible_sources = possible_sources or list(local_data.get("possible_sources", []))
    start_hint, end_hint = parse_time_range(timestamp)
    suffix = (local_path.suffix if local_path else Path(original_filename).suffix).lower()
    if local_path and suffix not in SUPPORTED_IMAGE_SUFFIXES | SUPPORTED_MEDIA_SUFFIXES:
        raise ValueError(f"Unsupported intake file: {suffix or 'unknown type'}")
    input_kind = "youtube" if youtube_video_id(source_url) else (
        "screenshot" if suffix in SUPPORTED_IMAGE_SUFFIXES else "local_media" if local_path else "source_reference"
    )
    signature = {
        "source_url": source_url.strip(), "filename": original_filename or (local_path.name if local_path else ""),
        "note": note.strip(), "timestamp": timestamp.strip(), "visible_text": visible_text.strip(),
    }
    intake_id = _stable_id(signature)
    intake_dir = project_root / "workspace" / "reference_intake" / intake_id
    manifest_path = intake_dir / "intake.json"
    if manifest_path.exists():
        return read_json(manifest_path)
    intake_dir.mkdir(parents=True, exist_ok=True)
    stored_path = ""
    if local_path:
        destination = intake_dir / f"input{suffix}"
        shutil.copy2(local_path, destination)
        stored_path = str(destination.relative_to(project_root))

    query = " ".join(part for part in (visible_text, note, metadata.get("title", "")) if part).strip()
    matches = match_transcript(query, transcript, hint_seconds=start_hint)
    source_candidates = [dict(item) for item in possible_sources]
    if source_url or metadata:
        source_candidates.insert(0, {
            "url": source_url, "video_id": youtube_video_id(source_url), "title": metadata.get("title", ""),
            "channel": metadata.get("channel", ""), "confidence": 1.0 if source_url else 0.75,
        })
    alternatives: list[dict[str, Any]] = []
    for source in source_candidates:
        source_score = float(source.get("confidence", 0.5))
        source_transcript = source.get("transcript", transcript)
        for transcript_match in match_transcript(query, source_transcript, hint_seconds=start_hint, limit=2):
            candidate = {**source, **transcript_match}
            candidate["confidence"] = round(source_score * float(transcript_match["confidence"]), 3)
            alternatives.append(candidate)
    alternatives.extend(matches)
    alternatives.sort(key=lambda item: -float(item.get("confidence", 0.0)))
    best = alternatives[0] if alternatives else {}
    if start_hint is not None:
        best = {**best, "start_seconds": start_hint, "end_seconds": end_hint or best.get("end_seconds", start_hint + 12.0)}
    if start_hint is not None and end_hint is not None:
        clip_start, clip_end = start_hint, end_hint
    else:
        clip_start, clip_end = _clean_clip_bounds(best, metadata.get("chapters", []))
    topic = str(metadata.get("topic") or note or metadata.get("title") or visible_text).strip()
    speaker = str(best.get("speaker") or metadata.get("speaker") or "Unknown speaker")
    confidence = float(best.get("confidence", 0.35 if (source_url or local_path) else 0.0))
    intent = {
        "version": 1, "intake_id": intake_id, "status": "needs_user_selection",
        "suspected_topic": topic, "intended_event": str(metadata.get("event", "")),
        "intended_interview_passage": str(best.get("text") or visible_text),
        "why_relevant": note.strip(), "source_url": str(best.get("url") or source_url),
        "channel": str(best.get("channel") or metadata.get("channel", "")),
        "video_title": str(best.get("title") or metadata.get("title", "")),
        "speaker": speaker, "start_seconds": clip_start, "end_seconds": clip_end,
        "confidence": round(confidence, 3), "alternative_matches": alternatives[1:4],
        "hypotheses": {"people": metadata.get("people", []), "locations": metadata.get("locations", []), "events": metadata.get("events", [])},
    }
    payload = {
        "version": 1, "id": intake_id, "created_at": datetime.now(UTC).isoformat(), "input_kind": input_kind,
        "source_url": source_url, "stored_path": stored_path, "original_filename": signature["filename"],
        "user_note": note, "visible_text": visible_text, "timestamp_hint": timestamp,
        "metadata": metadata, "reference_intent": intent, "processing": {"state": "resolved", "attempts": 1},
    }
    write_json(manifest_path, payload)
    write_json(project_root / "manifests" / "reference_intent.json", intent)
    return payload


def select_reference_match(
    project_root: Path,
    intake_id: str,
    *,
    match_index: int = 0,
    why_relevant: str = "",
    user_selected_for_edit: bool = False,
) -> dict[str, Any]:
    path = project_root / "workspace" / "reference_intake" / intake_id / "intake.json"
    payload = read_json(path)
    intent = dict(payload["reference_intent"])
    if match_index:
        alternatives = intent.get("alternative_matches", [])
        if match_index - 1 >= len(alternatives):
            raise IndexError("Reference match does not exist")
        chosen = dict(alternatives[match_index - 1])
        for key in ("url", "channel", "title", "speaker", "start_seconds", "end_seconds", "confidence", "text"):
            if key in chosen:
                target = {"url": "source_url", "title": "video_title", "text": "intended_interview_passage"}.get(key, key)
                intent[target] = chosen[key]
    intent["why_relevant"] = why_relevant.strip() or intent.get("why_relevant", "")
    intent["user_selected_for_edit"] = bool(user_selected_for_edit)
    intent["status"] = "selected" if user_selected_for_edit else "reviewed"
    intent["selected_at"] = datetime.now(UTC).isoformat()
    payload["reference_intent"] = intent
    write_json(path, payload)
    write_json(project_root / "manifests" / "reference_intent.json", intent)
    if user_selected_for_edit:
        integrate_selected_reference(project_root, intent, payload.get("stored_path", ""))
    return intent


def integrate_selected_reference(project_root: Path, intent: dict[str, Any], stored_path: str = "") -> None:
    duration = max(0.0, float(intent["end_seconds"]) - float(intent["start_seconds"]))
    source_entry = {
        "intake_id": intent["intake_id"], "source_url": intent.get("source_url", ""),
        "channel": intent.get("channel", ""), "video_title": intent.get("video_title", ""),
        "speaker": intent.get("speaker", "Unknown speaker"), "timestamp": {
            "start_seconds": intent["start_seconds"], "end_seconds": intent["end_seconds"]},
        "used_duration_seconds": round(duration, 3), "project_context": intent.get("why_relevant", ""),
        "local_fallback_path": stored_path, "rights_status": "user_responsibility",
        "user_selected_for_edit": True,
    }
    source_path = project_root / "manifests" / "clip_sources.json"
    source_manifest = read_json(source_path) if source_path.exists() else {"version": 1, "clips": []}
    clips = [clip for clip in source_manifest.get("clips", []) if clip.get("intake_id") != intent["intake_id"]]
    clips.append(source_entry)
    source_manifest["clips"] = clips
    write_json(source_path, source_manifest)

    research_path = project_root / "manifests" / "reference_research.json"
    write_json(research_path, {
        "version": 1, "research_direction": intent.get("suspected_topic", ""),
        "event_context": intent.get("intended_event", ""), "interview_statement": {
            "speaker": intent.get("speaker", "Unknown speaker"), "statement": intent.get("intended_interview_passage", ""),
            "epistemic_status": "speaker_statement_not_verified_fact", "requires_corroboration": True,
        }, "why_relevant": intent.get("why_relevant", ""),
    })

    speaker = intent.get("speaker") or "de spreker"
    statement = intent.get("intended_interview_passage", "")
    script_insert = {
        "version": 1, "intake_id": intent["intake_id"], "story_role": "primary_source_interview",
        "before_clip": f"In dit interview zegt {speaker} het volgende. Dit is diens verklaring, niet op zichzelf een bewezen feit.",
        "clip": {"speaker": speaker, "statement": statement, "attribution": "speaker_statement"},
        "after_clip": "Deze passage geeft richting aan het onderzoek, maar moet naast onafhankelijke bronnen en de oorspronkelijke context worden gelegd.",
    }
    write_json(project_root / "manifests" / "reference_script_integration.json", script_insert)

    edit_plan = {
        "version": 1, "intake_id": intent["intake_id"], "placement": "at_related_script_passage",
        "source_in": intent["start_seconds"], "source_out": intent["end_seconds"],
        "context_card": {"enabled": True, "speaker": speaker, "source": intent.get("video_title") or intent.get("channel", "")},
        "audio": {"normalize_lufs": -16, "preserve_intelligibility": True, "j_cut_seconds": 0.6, "l_cut_seconds": 0.8},
        "subtitles": {"enabled": True, "speaker_labels": True, "text": statement},
        "trimming": {"remove_silence": True, "remove_irrelevant_context": True, "preserve_statement_context": True},
        "b_roll": {"allowed_over_interview": True, "never_obscure_essential_visual_context": True},
        "voice_over": {"before": script_insert["before_clip"], "after": script_insert["after_clip"]},
        "safety": {"content_id_evasion": False, "identity_claim": False, "misleading_recut": False, "alter_words": False},
        "rights": {"decision_maker": "user", "blocks_edit": False},
    }
    write_json(project_root / "manifests" / "reference_edit_plan.json", edit_plan)


def apply_reference_to_script(project_root: Path, script: dict[str, Any]) -> dict[str, Any]:
    """Attach a confirmed interview beat to a generated script without presenting it as fact."""
    integration_path = project_root / "manifests" / "reference_script_integration.json"
    if not integration_path.exists():
        return script
    integration = read_json(integration_path)
    intake_id = integration.get("intake_id")
    existing = script.get("reference_interviews", [])
    if any(item.get("intake_id") == intake_id for item in existing if isinstance(item, dict)):
        return script
    beat = {
        "intake_id": intake_id,
        "story_role": integration.get("story_role"),
        "before_clip": integration.get("before_clip"),
        "clip": integration.get("clip"),
        "after_clip": integration.get("after_clip"),
    }
    script["reference_interviews"] = [*existing, beat]
    script.setdefault("research_directions", []).append(
        "Corroborate the attributed interview statement and preserve its original context."
    )
    return script


def validate_reference_safety(project_root: Path) -> list[str]:
    errors: list[str] = []
    for name in ("reference_intent.json", "reference_script_integration.json", "reference_edit_plan.json"):
        path = project_root / "manifests" / name
        if not path.exists():
            continue
        lowered = path.read_text(encoding="utf-8").lower()
        if any(term in lowered for term in AVOIDANCE_TERMS):
            errors.append(f"Forbidden Content ID avoidance instruction in {name}")
    edit_path = project_root / "manifests" / "reference_edit_plan.json"
    if edit_path.exists() and read_json(edit_path).get("safety", {}).get("content_id_evasion") is not False:
        errors.append("Edit plan must explicitly disable Content ID evasion")
    return errors
