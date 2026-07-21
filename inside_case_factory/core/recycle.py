from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import re
import shutil
from typing import Any
from urllib.parse import urlparse

from inside_case_factory.core.project import slugify
from inside_case_factory.core.research import add_claim, add_source, approved_claims, load_manifest, save_manifest
from inside_case_factory.rendering.probe import media_duration_seconds
from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace


SUPPORTED_REFERENCE_VIDEO_SUFFIXES = {".mp4", ".mov", ".mkv", ".webm"}
SUPPORTED_REFERENCE_AUDIO_SUFFIXES = {".mp3", ".wav", ".m4a", ".aac"}
SCENE_WINDOW_SECONDS = 45.0


def youtube_video_id(url: str) -> str | None:
    parsed = urlparse(url.strip())
    host = parsed.netloc.lower().removeprefix("www.")
    if host == "youtu.be":
        return parsed.path.strip("/").split("/")[0] or None
    if host in {"youtube.com", "m.youtube.com"}:
        if parsed.path == "/watch":
            query = dict(item.split("=", 1) for item in parsed.query.split("&") if "=" in item)
            return query.get("v")
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
            if isinstance(item, dict):
                start = float(item.get("start", 0.0) or 0.0)
                duration = max(0.5, float(item.get("duration", item.get("end", start + 4.0)) or 4.0))
                if "end" in item:
                    duration = max(0.5, float(item.get("end", start + duration)) - start)
                text = compact_whitespace(str(item.get("text", "")))
                if not text:
                    continue
                segments.append({
                    "id": str(item.get("id", f"seg{index + 1:03}")),
                    "start": round(start, 3),
                    "duration": round(duration, 3),
                    "end": round(start + duration, 3),
                    "text": text,
                    "speaker": compact_whitespace(str(item.get("speaker", ""))),
                })
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
            chapters.append({
                "id": str(item.get("id", f"ch{index + 1:02}")),
                "title": compact_whitespace(str(item.get("title", f"Chapter {index + 1}"))),
                "start": round(start, 3),
                "end": round(max(start + 1.0, end), 3),
            })
        if chapters:
            return chapters
    if not transcript:
        return [{"id": "ch01", "title": "Reference documentary", "start": 0.0, "end": max(1.0, duration_seconds)}]
    chapters = []
    start = 0.0
    cursor = 1
    while start < duration_seconds:
        end = min(duration_seconds, start + SCENE_WINDOW_SECONDS)
        chapters.append({
            "id": f"ch{cursor:02}",
            "title": f"Scene block {cursor}",
            "start": round(start, 3),
            "end": round(max(start + 1.0, end), 3),
        })
        cursor += 1
        start = end
    return chapters or [{"id": "ch01", "title": "Reference documentary", "start": 0.0, "end": max(1.0, duration_seconds)}]


def _transcribe_with_whisper(local_path: Path) -> list[dict[str, Any]]:
    try:
        import whisper  # type: ignore[import-not-found]
    except ImportError as error:
        raise RuntimeError(
            "No transcript was supplied and Whisper is not installed. Add a sidecar transcript JSON or install the whisper package for local transcription."
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
        transcript.append({
            "id": f"seg{index + 1:03}",
            "start": round(start, 3),
            "duration": round(max(0.5, end - start), 3),
            "end": round(max(start + 0.5, end), 3),
            "text": text,
            "speaker": "",
        })
    return transcript


def create_reference_documentary(
    project_root: Path,
    *,
    source_url: str = "",
    local_path: Path | None = None,
    original_filename: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = infer_reference_input_kind(source_url, local_path)
    sidecar = _sidecar_payload(local_path)
    payload_metadata = {**sidecar.get("metadata", {}), **(metadata or {})}
    storage_dir = _reference_storage_dir(project_root)
    storage_dir.mkdir(parents=True, exist_ok=True)
    stored_path = ""
    if local_path is not None:
        destination = storage_dir / f"reference{local_path.suffix.lower()}"
        shutil.copy2(local_path, destination)
        stored_path = str(destination.relative_to(project_root))
    document = {
        "version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "input_kind": kind,
        "source_url": source_url.strip(),
        "stored_path": stored_path,
        "original_filename": original_filename or (local_path.name if local_path else ""),
        "metadata": payload_metadata,
        "chapters": sidecar.get("chapters", []),
        "transcript": sidecar.get("transcript", []),
        "duration_seconds": float(sidecar.get("duration_seconds", payload_metadata.get("duration_seconds", 0.0)) or 0.0),
        "title": compact_whitespace(str(payload_metadata.get("title") or original_filename or Path(source_url).name or project_root.name)),
    }
    write_json(_reference_manifest_path(project_root), document)
    workflow = load_manifest(project_root, "workflow.json")
    workflow.update({
        "workflow_type": "recycle_documentary",
        "reference_documentary_loaded": True,
        "reference_documentary_title": document["title"],
    })
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
    filtered = [item for item in matches if item.lower() not in {"The", "This", "That", "Later", "Chapter", "Scene"}]
    return list(dict.fromkeys(filtered))[:8]


def _derive_event_focus(chapter_title: str, text: str) -> str:
    candidates = _sentence_split(" ".join(part for part in (chapter_title, text) if part))
    if candidates:
        sentence = candidates[0]
        tokens = sentence.split()
        return " ".join(tokens[: min(8, len(tokens))])
    return compact_whitespace(chapter_title) or "Reference event"


def _required_visuals(event_focus: str, entities: dict[str, list[str]], text: str) -> list[str]:
    visuals = [event_focus]
    visuals.extend(entities.get("places", []))
    visuals.extend(entities.get("organizations", []))
    if entities.get("dates"):
        visuals.append(f"date marker {entities['dates'][0]}")
    for sentence in _sentence_split(text)[:2]:
        tokens = [token for token in re.findall(r"[A-Za-z0-9'-]{4,}", sentence) if token.lower() not in {"with", "from", "that", "this", "after", "before", "their", "there", "because", "while"}]
        if tokens:
            visuals.append(" ".join(tokens[:3]))
    return list(dict.fromkeys(compact_whitespace(item) for item in visuals if compact_whitespace(item)))[:6]


def _scene_entities(chapter_title: str, text: str) -> dict[str, list[str]]:
    capitalized = _extract_capitalized_phrases(f"{chapter_title} {text}")
    dates = _extract_dates(text)
    people = [item for item in capitalized if len(item.split()) <= 3][:4]
    places = [item for item in capitalized if any(token in item.lower() for token in ("city", "county", "street", "hospital", "ranch", "cape", "london", "paris", "houston", "southampton"))][:4]
    organizations = [item for item in capitalized if any(token in item.lower() for token in ("agency", "court", "government", "police", "company", "archive", "administration", "nasa", "bbc", "pepsi"))][:4]
    if not places and len(capitalized) > 2:
        places = capitalized[1:3]
    return {
        "people": list(dict.fromkeys(people)),
        "places": list(dict.fromkeys(places)),
        "organizations": list(dict.fromkeys(organizations)),
        "dates": dates,
        "historical_events": list(dict.fromkeys([_derive_event_focus(chapter_title, text)])),
        "objects": [],
    }


def _scene_payload(index: int, chapter: dict[str, Any], transcript: list[dict[str, Any]]) -> dict[str, Any]:
    start = float(chapter.get("start", 0.0) or 0.0)
    end = float(chapter.get("end", start + SCENE_WINDOW_SECONDS) or start + SCENE_WINDOW_SECONDS)
    matching = [item for item in transcript if float(item.get("start", 0.0) or 0.0) < end and float(item.get("end", 0.0) or 0.0) > start]
    narration = compact_whitespace(" ".join(str(item.get("text", "")) for item in matching))
    event_focus = _derive_event_focus(str(chapter.get("title", "")), narration)
    entities = _scene_entities(str(chapter.get("title", "")), narration)
    purpose = f"Explain the event '{event_focus}' and move the chronology forward."
    viewer = f"Understand what happened during '{event_focus}' and why it matters to the broader timeline."
    return {
        "scene_id": f"scene_{index:02}",
        "chapter_id": str(chapter.get("id", f"ch{index:02}")),
        "title": compact_whitespace(str(chapter.get("title", f"Scene {index}"))),
        "start_seconds": round(start, 3),
        "end_seconds": round(end, 3),
        "duration_seconds": round(max(1.0, end - start), 3),
        "narration": narration,
        "event_focus": event_focus,
        "purpose": purpose,
        "viewer_understanding": viewer,
        "required_visuals": _required_visuals(event_focus, entities, narration),
        "entities": entities,
        "why_shown": purpose,
    }


def _claim_candidates(scene: dict[str, Any]) -> list[dict[str, Any]]:
    statements = []
    for sentence in _sentence_split(str(scene.get("narration", ""))):
        if len(sentence) < 35 or len(sentence) > 240 or "?" in sentence:
            continue
        if not re.search(r"\b(is|was|were|had|did|announced|released|landed|departed|died|won|started|began|ended|burned|founded|launched|returned|trial|arrested)\b", sentence, re.I):
            continue
        dates = _extract_dates(sentence) or list(scene.get("entities", {}).get("dates", []))[:1]
        statements.append({
            "statement": sentence,
            "scene_id": scene["scene_id"],
            "dates": dates,
            "people": list(scene.get("entities", {}).get("people", [])),
            "places": list(scene.get("entities", {}).get("places", [])),
            "organizations": list(scene.get("entities", {}).get("organizations", [])),
            "event_focus": str(scene.get("event_focus", "")),
        })
        if len(statements) >= 5:
            break
    return statements


def _search_queries(scene: dict[str, Any]) -> list[str]:
    subject = scene.get("entities", {}).get("people", [])[:2]
    places = scene.get("entities", {}).get("places", [])[:2]
    organizations = scene.get("entities", {}).get("organizations", [])[:2]
    dates = scene.get("entities", {}).get("dates", [])[:2]
    event_focus = compact_whitespace(str(scene.get("event_focus", "")))
    title = compact_whitespace(str(scene.get("title", "")))
    base_terms = [item for item in [event_focus, title, *subject, *places, *organizations, *dates] if compact_whitespace(str(item))]
    queries = []
    if subject and event_focus:
        queries.append(compact_whitespace(f"{subject[0]} {event_focus}"))
    if subject and places:
        queries.append(compact_whitespace(f"{subject[0]} {places[0]} {event_focus}"))
    if subject and organizations:
        queries.append(compact_whitespace(f"{subject[0]} {organizations[0]} {event_focus}"))
    if event_focus and dates:
        queries.append(compact_whitespace(f"{event_focus} {dates[0]} archive"))
    if places and event_focus:
        queries.append(compact_whitespace(f"{places[0]} {event_focus} historical footage"))
    if organizations and event_focus:
        queries.append(compact_whitespace(f"{organizations[0]} {event_focus} documents"))
    if len(base_terms) >= 2:
        queries.append(compact_whitespace(" ".join(base_terms[:4])))
    deduped = []
    seen: set[str] = set()
    for query in queries:
        cleaned = compact_whitespace(query)
        lowered = cleaned.lower()
        if len(cleaned.split()) < 2 or lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(cleaned)
    return deduped[:8]


def _timeline_events(claims: list[dict[str, Any]], scenes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for index, claim in enumerate(claims, start=1):
        raw_dates = claim.get("dates", [])
        date = raw_dates[0] if isinstance(raw_dates, list) and raw_dates else ""
        events.append({
            "event_id": f"event_{index:02}",
            "date": date,
            "label": compact_whitespace(str(claim.get("event_focus", "")) or str(claim.get("statement", ""))[:72]),
            "summary": str(claim.get("statement", "")),
            "scene_id": str(claim.get("scene_id", "")),
        })
    if not events:
        for index, scene in enumerate(scenes, start=1):
            events.append({
                "event_id": f"event_{index:02}",
                "date": (scene.get("entities", {}).get("dates") or [""])[0],
                "label": str(scene.get("event_focus", f"Scene {index}")),
                "summary": str(scene.get("viewer_understanding", "")),
                "scene_id": str(scene.get("scene_id", "")),
            })
    events.sort(key=lambda item: str(item.get("date", "9999")) or "9999")
    return events


def _shot_plan(events: list[dict[str, Any]], scenes_by_id: dict[str, dict[str, Any]], search_queries: dict[str, list[str]]) -> list[dict[str, Any]]:
    plan = []
    for event in events:
        scene = scenes_by_id.get(str(event.get("scene_id", "")), {})
        visuals = list(scene.get("required_visuals", []))
        plan.append({
            "event_id": str(event.get("event_id", "")),
            "scene_id": str(event.get("scene_id", "")),
            "date": str(event.get("date", "")),
            "narration_goal": str(event.get("summary", "")),
            "visual_goal": visuals[0] if visuals else str(event.get("label", "")),
            "emotion": "focused curiosity" if event != events[-1] else "resolution",
            "supporting_media": visuals[:4],
            "search_queries": list(search_queries.get(str(event.get("scene_id", "")), [])),
        })
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
    claims_manifest = load_manifest(project_root, "claims.json")
    existing_sources = {str(item.get("url", "")) for item in sources_manifest.get("sources", []) if isinstance(item, dict)}
    source_ids: list[str] = []
    reference = load_reference_documentary(project_root)
    reference_url = str(reference.get("source_url", ""))
    source_title = str(reference.get("title", project_root.name))
    if reference_url and reference_url not in existing_sources:
        source = add_source(
            project_root,
            title=f"Reference documentary blueprint: {source_title}",
            url=reference_url,
            publisher="reference_documentary_blueprint",
            source_type="video",
            reliability_notes="Blueprint only. Do not treat this reference documentary as a factual source; independently verify every claim.",
        )
        source["relevance_status"] = "relevant"
        source["review_status"] = "rejected"
        source["blueprint_only"] = True
        sources = load_manifest(project_root, "sources.json")
        for item in sources.get("sources", []):
            if isinstance(item, dict) and item.get("id") == source["id"]:
                item.update(source)
                source_ids.append(str(item["id"]))
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
        "verification_queries": [item.get("verification_query", "") for item in blueprint.get("claims", [])[:12]],
    }
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
    lines.extend([
        "",
        "## Claims extracted",
    ])
    for claim in blueprint.get("claims", []):
        lines.append(f"- {claim.get('statement', '')}")
    lines.extend([
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
    ])
    for item in blueprint.get("shot_plan", []):
        lines.append(f"- {item.get('date', 'undated')} | {item.get('visual_goal', '')} | {item.get('narration_goal', '')}")
    lines.extend([
        "",
        "## Generated search queries",
    ])
    for scene_id, queries in blueprint.get("scene_queries", {}).items():
        lines.append(f"- {scene_id}: {', '.join(queries)}")
    lines.extend([
        "",
        "## Media providers used",
    ])
    for provider in discovery_priorities:
        lines.append(f"- {provider}")
    metrics = blueprint.get("scores", {})
    lines.extend([
        "",
        "## Scores",
        f"- Visual diversity score: {metrics.get('visual_diversity_score', 0)}",
        f"- Storytelling score: {metrics.get('storytelling_score', 0)}",
        f"- Research score: {metrics.get('research_score', 0)}",
        f"- Originality score: {metrics.get('originality_score', 0)}",
        f"- Overall documentary quality: {metrics.get('overall_documentary_quality', 0)}",
    ])
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
        raise RuntimeError("The recycle workflow needs a transcript. Supply one in sidecar JSON or install Whisper for local transcription.")
    duration_seconds = _fallback_duration(project_root, reference, transcript)
    chapters = _normalize_chapters(reference.get("chapters", []), duration_seconds, transcript)
    scenes = [_scene_payload(index, chapter, transcript) for index, chapter in enumerate(chapters, start=1)]
    claims = []
    for scene in scenes:
        claims.extend(_claim_candidates(scene))
    claims = claims[:48]
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
        "scenes": scenes,
        "claims": claims,
        "verification_queue": verification_queue,
        "timeline": timeline,
        "shot_plan": shot_plan,
        "scene_queries": scene_queries,
        "storyline": [scene.get("event_focus", "") for scene in scenes[:12]],
        "people": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("people", [])))[:12],
        "places": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("places", [])))[:12],
        "organizations": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("organizations", [])))[:12],
        "historical_events": list(dict.fromkeys(name for scene in scenes for name in scene.get("entities", {}).get("historical_events", [])))[:12],
        "scores": {
            "visual_diversity_score": min(100, 45 + len({query for queries in scene_queries.values() for query in queries}) * 2),
            "storytelling_score": min(100, score_base),
            "research_score": min(100, 30 + len(verification_queue) * 2),
            "originality_score": 85,
            "overall_documentary_quality": min(100, round((score_base + min(100, 30 + len(verification_queue) * 2) + 85) / 3)),
        },
        "media_provider_priorities": [
            "internet_archive",
            "nasa",
            "library_of_congress",
            "europeana",
            "wikimedia_commons",
            "pexels",
            "pixabay",
            "public_domain_archives",
            "government_archives",
            "historical_archives",
            "licensed_news_archives",
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
    workflow.update({
        "workflow_type": "recycle_documentary",
        "reference_documentary_loaded": True,
        "recycle_analysis_ready": True,
        "recycle_verification_ready": True,
        "recycle_reconstruction_ready": True,
        "recycle_report": "manifests/recycle_engine_report.md",
    })
    save_manifest(project_root, "workflow.json", workflow)
    seed_recycle_research(project_root, blueprint)
    return blueprint


def approved_recycle_claims(project_root: Path) -> list[dict[str, Any]]:
    approved = approved_claims(project_root)
    return [claim for claim in approved if isinstance(claim, dict)]