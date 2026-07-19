from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from inside_case_factory.core.review_demo import create_offline_review_demo
from inside_case_factory.utils.files import read_json, write_json


SLUG = "release-candidate-demo"


def prepare(repo: Path, runtime: Path) -> Path:
    if runtime.exists():
        shutil.rmtree(runtime)
    (runtime / "config").mkdir(parents=True)
    shutil.copy2(repo / "config/defaults.toml", runtime / "config/defaults.toml")
    shutil.copy2(repo / "config/providers.toml", runtime / "config/providers.toml")
    root = create_offline_review_demo(runtime / "projects", slug=SLUG)
    # Exercise paid-consent UI without making an external call.
    write_json(root / "manifests/paid_research_approval.json", {
        "version": 1, "approval_required": True, "estimated_cost_usd": 0.04,
        "extra_sources": 6, "reason": "Een internationale bron ontbreekt nog.",
        "countries": ["Nederland", "België"], "languages": ["Nederlands", "Frans"],
        "claims": ["De dienstregeling en de getuigenverklaring beter vergelijken."],
    })
    write_json(root / "manifests/cost_estimate.json", {
        "maximum_total_cost_usd": 0.04,
        "stages": [{"stage": "research_plan", "provider": "offline-mock", "estimated_maximum_cost_usd": 0.04}],
    })
    write_json(root / "manifests/provider_config.json", {
        "version": 1, "profile": "offline", "budget_usd": 1.0,
        "external_calls_enabled": False, "cache_enabled": True, "retries": 0, "tasks": {},
    })
    return root


def report(repo: Path, runtime: Path) -> Path:
    evidence_path = runtime / "acceptance-evidence.json"
    evidence = read_json(evidence_path) if evidence_path.exists() else {"steps": [], "result": "FAIL"}
    out = repo / "release_candidate"
    out.mkdir(exist_ok=True)
    screenshots = out / "screenshots"
    screenshots.mkdir(exist_ok=True)
    source_shots = runtime / "screenshots"
    if source_shots.exists():
        for image in source_shots.glob("*.png"):
            shutil.copy2(image, screenshots / image.name)
    rows = "".join(
        f"<tr><td>{item['name']}</td><td>{item['status']}</td><td>{item.get('ms', 0)} ms</td></tr>"
        for item in evidence.get("steps", [])
    )
    artefacts = [
        runtime / "projects" / SLUG / "exports/final_video.mp4",
        runtime / "projects" / SLUG / "manifests/selective_regeneration.json",
        runtime / "projects" / SLUG / "manifests/youtube_draft.json",
    ]
    html = f"""<!doctype html><html lang='nl'><meta charset='utf-8'><title>RC acceptance</title>
<style>body{{font:15px system-ui;max-width:1100px;margin:40px auto;color:#172026}}table{{border-collapse:collapse;width:100%}}td,th{{padding:9px;border-bottom:1px solid #ddd;text-align:left}}.PASS{{color:#08783e;font-weight:bold}}img{{max-width:320px;margin:8px}}</style>
<h1>Inside the Case Factory — release-candidate acceptance</h1><p>Resultaat: <strong class='{evidence.get('result')}'>{evidence.get('result')}</strong> · project: {SLUG} · volledig offline.</p>
<table><thead><tr><th>Stap</th><th>Resultaat</th><th>Tijd</th></tr></thead><tbody>{rows}</tbody></table>
<h2>Artefacten</h2><ul>{''.join(f'<li>{p}: {"aanwezig" if p.exists() else "ontbreekt"}</li>' for p in artefacts)}</ul>
<h2>Screenshots</h2>{''.join(f"<img src='screenshots/{p.name}' alt='{p.stem}'>" for p in sorted(screenshots.glob('*.png')))}</html>"""
    target = out / "acceptance-report.html"
    target.write_text(html, encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("prepare", "report"))
    parser.add_argument("--runtime", type=Path, default=Path(".release-candidate-runtime"))
    args = parser.parse_args()
    repo = Path.cwd()
    print(prepare(repo, args.runtime) if args.command == "prepare" else report(repo, args.runtime))


if __name__ == "__main__":
    main()
