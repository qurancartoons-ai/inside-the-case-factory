from __future__ import annotations

from html import escape
from pathlib import Path
import subprocess

from inside_case_factory.core.autonomous_direction import CriticEngine, DirectorEngine
from inside_case_factory.core.draft_review import create_review_draft, revise_draft
from inside_case_factory.core.producer import ProducerEngine
from inside_case_factory.core.project import create_project
from inside_case_factory.utils.files import read_json, write_json


DEMO_SLUG = "offline-review-demo"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def create_offline_review_demo(projects_dir: Path, *, slug: str = DEMO_SLUG, topic: str = "De Verdwenen Nachtbus — Offline Reviewdemo") -> Path:
    project = create_project(projects_dir, topic, slug)
    root = project.root
    sources = [
        {"id": "src01", "title": "Gemeentelijk vervoersrapport 2019", "url": "https://example.invalid/transport-report", "publisher": "Demo Archief", "review_status": "approved"},
        {"id": "src02", "title": "Getuigeninterview — lokale omroep", "url": "https://example.invalid/interview", "publisher": "Demo Nieuws", "review_status": "approved"},
    ]
    claims = [
        {"id": "c01", "text": "De laatste nachtbus vertrok volgens het dienstlogboek om 00:42.", "source_ids": ["src01"], "review_status": "approved"},
        {"id": "c02", "text": "Een getuige verklaarde dat de bus kort bij het oude depot stopte.", "source_ids": ["src02"], "review_status": "approved"},
        {"id": "c03", "text": "Het rapport laat een onverklaarde onderbreking van negen minuten zien.", "source_ids": ["src01"], "review_status": "approved"},
        {"id": "c04", "text": "De beschikbare documenten geven geen definitief antwoord op de oorzaak.", "source_ids": ["src01", "src02"], "review_status": "approved"},
    ]
    narrations = [
        "Om 00:42 vertrekt de laatste nachtbus. Negen minuten later ontbreekt ieder spoor in het logboek.",
        "Het gemeentelijke rapport bevestigt de route, maar niet wat er bij het oude depot gebeurde.",
        "Een getuige beschrijft een korte stop. Die verklaring is belangrijk, maar staat niet gelijk aan een bewezen feit.",
        "De documenten laten één conclusie toe: de onderbreking bestaat, maar de oorzaak blijft onopgelost.",
    ]
    headings = ["De ontbrekende minuten", "Het dienstlogboek", "De getuige", "Wat overeind blijft"]
    scenes = [{
        "id": f"s{i:02}", "index": i, "heading": headings[i - 1], "narration": narrations[i - 1],
        "duration_seconds": 9.0, "start_seconds": (i - 1) * 9.0, "end_seconds": i * 9.0,
        "claim_ids": [f"c{i:02}"], "dates": ["2019"], "events": [headings[i - 1]], "locations": ["oud depot"] if i in {2, 3} else [],
    } for i in range(1, 5)]
    write_json(root / "manifests/sources.json", {"version": 1, "sources": sources})
    write_json(root / "manifests/claims.json", {"version": 1, "claims": claims})
    write_json(root / "manifests/dossier.json", {"version": 1, "status": "draft", "summary": "Een lokale, fictieve fixturezaak over negen ontbrekende minuten in een nachtbuslogboek.", "key_facts": [claim["text"] for claim in claims], "unresolved_questions": ["Waarom stopte de registratie?", "Wat gebeurde er bij het depot?"]})
    write_json(root / "manifests/script.json", {"version": 1, "title": project.topic, "status": "approved", "narration": "\n\n".join(narrations), "sections": [{"id": scene["id"], "text": scene["narration"], "claim_ids": scene["claim_ids"]} for scene in scenes]})
    write_json(root / "manifests/scenes.json", {"version": 1, "status": "draft", "scenes": scenes})
    workflow = read_json(root / "manifests/workflow.json")
    workflow.update({"research_approved": True, "script_approved": True, "scenes_generated": True, "voiceover_generated": True, "video_rendered": True, "stage": "draft_review", "language": "Nederlands"})
    write_json(root / "manifests/workflow.json", workflow)

    thumbs = root / "assets/thumbnails"; thumbs.mkdir(parents=True, exist_ok=True)
    media = []
    colors = ("173b34", "263e51", "5b3d33", "292d36")
    for i, (scene, color) in enumerate(zip(scenes, colors, strict=True), start=1):
        path = thumbs / f"scene-{i:02}.png"
        _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"color=c=0x{color}:s=640x360:d=1", "-vf", f"drawtext=text='SCENE {i}  {headings[i-1]}':fontcolor=white:fontsize=24:x=30:y=300", "-frames:v", "1", str(path)])
        media.append({"id": f"demo-media-{i}", "title": f"Offline scènebeeld {i}", "type": "image", "path": str(path.relative_to(root)), "mapped_scenes": [scene["id"]], "review_status": "approved", "rights_status": "owned", "license": "Offline fixture; owned"})
    write_json(root / "manifests/media_sources.json", {"version": 1, "assets": media})
    write_json(root / "manifests/clip_sources.json", {"version": 1, "clips": [{"intake_id": "demo-interview", "video_title": "Offline getuigeninterview", "channel": "Demo Nieuws", "scene_ids": ["s03"], "timestamp": {"start_seconds": 4, "end_seconds": 10}, "rights_status": "fixture"}]})

    ProducerEngine().plan(root, scenes)
    DirectorEngine().plan(root, scenes, width=640, height=360)
    subtitles = "\n".join(f"{i}\n00:00:{(i-1)*9:02},000 --> 00:00:{i*9:02},000\n{scene['narration']}\n" for i, scene in enumerate(scenes, 1))
    (root / "manifests/subtitles.srt").write_text(subtitles, encoding="utf-8")
    voice = root / "assets/audio/voiceover.wav"; voice.parent.mkdir(parents=True, exist_ok=True)
    voice_text = root / "workspace/demo_voice.txt"; voice_text.write_text(" ".join(narrations), encoding="utf-8")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"flite=textfile={voice_text}:voice=kal", "-ar", "44100", "-ac", "1", str(voice)])
    final = root / "exports/final_video.mp4"; final.parent.mkdir(parents=True, exist_ok=True)
    concat = root / "workspace/demo_images.txt"
    concat.write_text("".join(f"file '{(thumbs / f'scene-{i:02}.png').resolve()}'\nduration 9\n" for i in range(1, 5)) + f"file '{(thumbs / 'scene-04.png').resolve()}'\n", encoding="utf-8")
    _run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-i", str(voice), "-vf", "scale=640:360,format=yuv420p", "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", "-shortest", str(final)])
    write_json(root / "manifests/narration_timing.json", {"provider": "ffmpeg_flite", "segments": [{"scene_id": s["id"], "start_seconds": s["start_seconds"], "end_seconds": s["end_seconds"], "text": s["narration"]} for s in scenes]})
    CriticEngine().analyze(root, render_number=1, duration_seconds=36)
    create_review_draft(root)
    plan = {"command": "Maak de intro spannender en gebruik meer close-ups.", "scene_ids": ["s01"], "components": ["script", "voice_over", "producer", "director"], "estimated_cost_usd": 0.0, "status": "confirmed_offline_demo"}
    write_json(root / "manifests/pending_revision_plan.json", plan)
    revise_draft(root, plan["command"])
    create_review_draft(root)
    write_json(root / "manifests/provider_selection.json", {"version": 1, "selections": {task: {"provider": "offline/local", "model": "built_in"} for task in ("producer_blueprint", "director_plan", "critic_review", "voice_over", "scene_image")}})
    write_json(root / "manifests/provider_usage.json", {"version": 1, "spent_usd": 0.0, "calls": []})
    write_json(root / "manifests/youtube_draft.json", {"version": 1, "status": "draft", "title": project.topic, "description": "Offline reviewdemo — niet publiceren.", "chapters": [{"start_seconds": s["start_seconds"], "title": s["heading"]} for s in scenes], "tags": ["offline demo", "documentaire"], "thumbnail": "assets/thumbnails/scene-01.png", "subtitles": "manifests/subtitles.srt", "video": "exports/final_video.mp4", "privacy_status": "private", "upload_confirmed": False})
    report = root / "review/demo_review_report.html"
    draft = read_json(root / "manifests/review_draft.json")
    report.write_text("<!doctype html><meta charset='utf-8'><title>Offline reviewdemo</title><style>body{font:16px Arial;max-width:960px;margin:40px auto;background:#f4f6f7;color:#172026}article{background:white;padding:20px;margin:15px 0;border-radius:10px}img{width:320px}code{background:#eee;padding:3px}</style><h1>Inside the Case Factory — Offline Reviewdemo</h1><p>Volledig lokaal · kosten $0 · revisie: <code>Maak de intro spannender.</code></p>" + "".join(f"<article><h2>Scène {escape(str(s['index']))}: {escape(str(s['heading']))}</h2><img src='../assets/thumbnails/scene-{int(s['index']):02}.png'><p>{escape(str(s['script']))}</p><p>Status: {escape(str(s['review_status']))}</p></article>" for s in draft["scenes"]), encoding="utf-8")
    return root
