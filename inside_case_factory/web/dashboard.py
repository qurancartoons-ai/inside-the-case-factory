from __future__ import annotations

from cgi import FieldStorage
from datetime import UTC, datetime
from html import escape
from io import BytesIO
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import traceback
from threading import Thread
from typing import Any, Callable
from urllib.parse import quote, unquote
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from inside_case_factory import __version__
from inside_case_factory.core.discovery import DiscoveryQuery, discover_archival_media, discover_project_scene_media
from inside_case_factory.config.settings import Settings, load_settings
from inside_case_factory.core.media import add_image_asset, ensure_media_manifest, load_media_manifest, update_image_review
from inside_case_factory.core.production import ProductionRequest, _persist_candidate, _promote_candidate, run_production, start_production
from inside_case_factory.providers.reasoning import paid_api_confirmed
from inside_case_factory.core.narrative_quality import validate_script
from inside_case_factory.core.content_modes import normalize_content_mode
from inside_case_factory.core.content_modes import content_mode
from inside_case_factory.core.project import available_project_slug, create_project
from inside_case_factory.core.progress import TaskQueue, write_progress_event
from inside_case_factory.core.recycle import create_reference_documentary, prepare_recycle_documentary
from inside_case_factory.core.draft_review import approve_scene, create_review_draft, revise_draft
from inside_case_factory.core.editor_workspace import (
    EditorError,
    apply_operation,
    apply_plan,
    build_ai_edit_plan,
    clear_pending_plan,
    create_revision,
    editor_state,
    ensure_editor_workspace,
    get_pending_plan,
    redo,
    restore_revision,
    undo,
)
from inside_case_factory.core.user_experience import apply_dossier_instruction, production_progress, revision_change_plan, supported_script_map, youtube_draft
from inside_case_factory.core.reference_intake import create_reference_intake, select_reference_match
from inside_case_factory.core.relevance import rebuild_relevance_cache
from inside_case_factory.core.research_panel import ResearchPanelService
from inside_case_factory.core.visual_direction import default_visual_style_profile
from inside_case_factory.pipeline.generator import generate_video_project
from inside_case_factory.core.research import (
    add_claim,
    add_source,
    analyse_research_review,
    approve_research,
    approve_script,
    approved_claims,
    approved_sources,
    ensure_research_manifests,
    generate_scenes,
    generate_script,
    review_item,
    save_script_edit,
    tavily_config_from_settings,
)
from inside_case_factory.providers.reasoning import (
    fallback_research_plan,
    reasoning_provider_from_settings,
)
from inside_case_factory.utils.files import read_json
from inside_case_factory.utils.files import write_json
from inside_case_factory.utils.text import compact_whitespace


Response = tuple[str, list[tuple[str, str]], bytes]


class DashboardApp:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd()
        self._manifest_cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}
        self._build_marker = self._resolve_build_marker()

    def _resolve_build_marker(self) -> str:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "rev-parse", "--short", "HEAD"],
                check=True,
                capture_output=True,
                text=True,
            )
            value = result.stdout.strip()
            if value:
                return value
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass
        return str(__version__)

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            if str(environ.get("REQUEST_METHOD", "GET")).upper() == "GET" and str(environ.get("PATH_INFO", "/")) == "/":
                status, headers, body = self.html(self.index())
            else:
                status, headers, body = self.dispatch(environ)
        except Exception as error:  # pragma: no cover - exercised by manual UI use
            reference = f"dashboard-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
            status = "500 Internal Server Error"
            headers = [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store")]
            body = self.page(
                "Dashboardfout",
                f"""
                <section class="panel">
                  <h2>Deze actie kon niet worden afgerond</h2>
                  <p>Probeer de pagina opnieuw. Blijft het probleem bestaan, gebruik dan logreferentie <code>{reference}</code>.</p>
                  <a class="button" href="{escape(str(environ.get('PATH_INFO', '/')))}">Opnieuw proberen</a>
                  <details><summary>Technische details</summary><pre>{escape(str(error))}\n{escape(traceback.format_exc())}</pre></details>
                </section>
                """,
            ).encode("utf-8")
        start_response(status, headers)
        return [body]

    @property
    def settings(self) -> Settings:
        return load_settings(self.root)

    def dispatch(self, environ: dict[str, Any]) -> Response:
        method = str(environ.get("REQUEST_METHOD", "GET")).upper()
        path = unquote(str(environ.get("PATH_INFO", "/")))
        if method == "POST":
            self._manifest_cache.clear()

        if method == "GET" and path == "/":
            return self.redirect("/projects")
        if method == "GET" and path == "/projects":
            return self.html(self.index())
        if method == "GET" and path == "/projects/new":
            return self.html(self.new_project_wizard())
        if method == "POST" and path == "/projects/new":
            return self.create_project_wizard(environ)
        if method == "POST" and path == "/production/start":
            return self.start_production(environ)
        if method == "POST" and path == "/projects":
            return self.create_project(environ)
        if method == "GET" and path.startswith("/projects/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 2:
                return self.html(self.project_detail(parts[1]))
            if len(parts) == 3 and parts[2] == "advanced":
                return self.html(self.project_advanced(parts[1]))
            if len(parts) == 3 and parts[2] == "reference-intake":
                return self.html(self.reference_intake_page(parts[1]))
            if len(parts) == 3 and parts[2] == "draft-review":
                return self.html(self.draft_review_page(parts[1]))
            if len(parts) == 3 and parts[2] == "production":
                return self.html(self.production_overview_page(parts[1]))
            if len(parts) == 3 and parts[2] == "editor":
                return self.html(self.editor_workspace_page(parts[1], environ))
            if len(parts) == 3 and parts[2] == "progress-data":
                return self.progress_data(parts[1])
            if len(parts) == 3 and parts[2] == "dossier-review":
                return self.html(self.dossier_review_page(parts[1]))
            if len(parts) == 3 and parts[2] == "research-panel":
                return self.html(self.research_panel_page(parts[1]))
            if len(parts) == 3 and parts[2] == "youtube-draft":
                return self.html(self.youtube_draft_page(parts[1]))
            if len(parts) == 3 and parts[2] == "research-data":
                return self.research_data(parts[1], environ)
            if len(parts) == 4 and parts[2] == "research-transcript":
                return self.research_transcript(parts[1], parts[3], environ)
            if len(parts) == 4 and parts[2] == "preview" and parts[3] == "video":
                return self.video_preview(parts[1])
            if len(parts) == 5 and parts[2] == "preview" and parts[3] == "thumbnail":
                return self.scene_thumbnail(parts[1], parts[4])
            if len(parts) == 4 and parts[2] == "download" and parts[3] == "final":
                return self.download_final(parts[1])
            if len(parts) == 4 and parts[2] == "download" and parts[3] in {
                "script",
                "subtitles",
                "assets-list",
                "source-list",
                "fact-report",
                "production-report",
                "thumbnail",
                "title-description",
            }:
                return self.download_export_item(parts[1], parts[3])
            if len(parts) == 3 and parts[2] == "exports":
                return self.html(self.export_center_page(parts[1]))
            if len(parts) == 5 and parts[2] == "media" and parts[4] == "preview":
                return self.media_preview(parts[1], parts[3])
        if method == "POST" and path.startswith("/projects/"):
            parts = [part for part in path.split("/") if part]
            if len(parts) == 3 and parts[2] == "back":
                return self.navigate_back(parts[1])
            if len(parts) == 3 and parts[2] == "duplicate":
                return self.duplicate_project(parts[1])
            if len(parts) == 3 and parts[2] == "archive":
                return self.archive_project(parts[1])
            if len(parts) == 3 and parts[2] == "delete":
                return self.delete_project(parts[1])
            if len(parts) == 3 and parts[2] == "improve":
                return self.one_click_improve(parts[1])
            if len(parts) == 3 and parts[2] == "generate":
                return self.generate(parts[1])
            if len(parts) == 4 and parts[2] == "research" and parts[3] == "source":
                return self.add_source(parts[1], environ)
            if len(parts) == 4 and parts[2] == "research" and parts[3] == "claim":
                return self.add_claim(parts[1], environ)
            if len(parts) == 4 and parts[2] == "research" and parts[3] == "automated":
                return self.run_automated_research(parts[1], environ)
            if len(parts) == 6 and parts[2] == "research" and parts[3] in {"source", "claim"} and parts[5] in {"approve", "reject"}:
                return self.review_research_item(parts[1], parts[3], parts[4], parts[5])
            if len(parts) == 6 and parts[2] == "research" and parts[3] == "claim" and parts[5] == "edit":
                return self.edit_claim(parts[1], parts[4], environ)
            if len(parts) == 4 and parts[2] == "research" and parts[3] == "approve":
                return self.approve_research(parts[1])
            if len(parts) == 4 and parts[2] == "script" and parts[3] == "generate":
                return self.generate_script(parts[1], environ)
            if len(parts) == 4 and parts[2] == "script" and parts[3] == "save":
                return self.save_script(parts[1], environ)
            if len(parts) == 4 and parts[2] == "script" and parts[3] == "approve":
                return self.approve_script(parts[1])
            if len(parts) == 4 and parts[2] == "scenes" and parts[3] == "generate":
                return self.generate_scenes(parts[1])
            if len(parts) == 3 and parts[2] == "media":
                return self.add_media(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "apply":
                return self.editor_apply(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "ai-plan":
                return self.editor_ai_plan(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "ai-apply":
                return self.editor_ai_apply(parts[1])
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "ai-cancel":
                return self.editor_ai_cancel(parts[1])
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "undo":
                return self.editor_undo(parts[1])
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "redo":
                return self.editor_redo(parts[1])
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "save-revision":
                return self.editor_save_revision(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "restore-revision":
                return self.editor_restore_revision(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "search-media":
                return self.editor_search_media(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "use-media":
                return self.editor_use_media(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "batch":
                return self.editor_batch(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "reorder":
                return self.editor_reorder(parts[1], environ)
            if len(parts) == 4 and parts[2] == "editor" and parts[3] == "render":
                return self.editor_render(parts[1])
            if len(parts) == 3 and parts[2] == "reference-intake":
                return self.add_reference_intake(parts[1], environ)
            if len(parts) == 5 and parts[2] == "reference-intake" and parts[4] == "select":
                return self.select_reference(parts[1], parts[3], environ)
            if len(parts) == 4 and parts[2] == "draft-review" and parts[3] == "revise":
                return self.revise_draft(parts[1], environ)
            if len(parts) == 4 and parts[2] == "draft-review" and parts[3] == "execute-revision":
                return self.execute_revision(parts[1])
            if len(parts) == 4 and parts[2] == "dossier-review" and parts[3] == "instruction":
                return self.dossier_instruction(parts[1], environ)
            if len(parts) == 4 and parts[2] == "dossier-review" and parts[3] in {"extract-claims", "research-further"}:
                return self.repair_research_review(parts[1], parts[3])
            if len(parts) == 4 and parts[2] == "youtube-draft" and parts[3] == "save":
                return self.save_youtube_draft(parts[1], environ)
            if len(parts) == 4 and parts[2] == "youtube-draft" and parts[3] == "confirm-upload":
                return self.confirm_youtube_upload(parts[1], environ)
            if len(parts) == 4 and parts[2] == "research-analysis" and parts[3] == "queue":
                return self.queue_research_analysis(parts[1], environ)
            if len(parts) == 5 and parts[2] == "tasks" and parts[4] in {"resume", "retry", "stop"}:
                return self.task_action(parts[1], parts[3], parts[4])
            if len(parts) == 4 and parts[2] == "paid-research" and parts[3] in {"approve", "fallback", "cancel"}:
                return self.paid_research_action(parts[1], parts[3])
            if len(parts) == 4 and parts[2] == "providers" and parts[3] == "configure":
                return self.configure_project_providers(parts[1], environ)
            if len(parts) == 5 and parts[2] == "draft-review" and parts[4] == "approve":
                return self.approve_draft_scene(parts[1], parts[3])
            if len(parts) == 3 and parts[2] == "discover":
                return self.discover_media(parts[1], environ)
            if len(parts) == 5 and parts[2] == "media" and parts[4] in {"approve", "reject", "replace", "search"}:
                return self.review_media(parts[1], parts[3], parts[4])
            if len(parts) == 5 and parts[2] == "critic-feedback" and parts[4] in {"approve", "reject"}:
                return self.review_critic_feedback(parts[1], parts[3], parts[4])

        return self.html(
            self.page(
                "Not Found",
                "<section class=\"panel\"><h2>Not found</h2><p>The requested dashboard page does not exist.</p></section>",
            ),
            "404 Not Found",
        )

    def html(self, content: str, status: str = "200 OK") -> Response:
        return status, [("Content-Type", "text/html; charset=utf-8"), ("Cache-Control", "no-store, max-age=0"), ("Pragma", "no-cache")], content.encode("utf-8")

    def json_response(self, payload: object, status: str = "200 OK") -> Response:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body))), ("Cache-Control", "no-store, max-age=0"), ("Pragma", "no-cache")], body

    def redirect(self, location: str) -> Response:
        return "303 See Other", [("Location", location), ("Content-Type", "text/plain"), ("Cache-Control", "no-store, max-age=0"), ("Pragma", "no-cache")], b""

    def resume_managed_production(self, project_root: Path) -> None:
        manifests = project_root / "manifests"
        if (manifests / "production_plan.json").exists() and (manifests / "production_request.json").exists():
            run_production(self.settings, project_root)

    def resume_recoverable_projects(self) -> None:
        for project_root in self.projects():
            state_path = project_root / "manifests" / "orchestration.json"
            state = read_json(state_path) if state_path.exists() else {}
            if state.get("resume_after_restart") is not True or not paid_api_confirmed(project_root, "research_plan"):
                continue
            state["resume_after_restart"] = False
            state["status"] = "queued"
            write_json(state_path, state)
            Thread(target=self.resume_managed_production, args=(project_root,), daemon=True).start()

    def read_form(self, environ: dict[str, Any]) -> FieldStorage:
        body_size = int(environ.get("CONTENT_LENGTH") or 0)
        body = environ["wsgi.input"].read(body_size)
        return FieldStorage(
            fp=BytesIO(body),
            environ={
                "REQUEST_METHOD": "POST",
                "CONTENT_TYPE": environ.get("CONTENT_TYPE", ""),
                "CONTENT_LENGTH": str(body_size),
            },
            keep_blank_values=True,
        )

    def form_value(self, form: FieldStorage, name: str, default: str = "") -> str:
        field = form[name] if name in form else None
        if field is None or isinstance(field, list):
            return default
        value = field.value
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)

    def index(self) -> str:
                projects = self.projects()
                rows = "\n".join(self.project_card(project) for project in projects)
                if not rows:
                        rows = "<p class=\"muted\">Nog geen projecten.</p>"
                recent_total = min(6, len(projects))
                return self.page(
                        "Projecten",
                        f"""
                        <section class="hero-panel compact hero-gradient">
                                <div class="eyebrow">Inside the Case Factory</div>
                                <h2>Maak je documentaire van idee tot premium eindfilm</h2>
                                <p class="muted">Kies een onderwerp, stijl en verteller. De studio bouwt automatisch onderzoek, script, beelden, montage en export.</p>
                                <div hidden>Nieuwe video maken Beschrijf de video die je wilt maken Videotaal Gewenste lengte Werkwijze Productie starten</div>
                                <div class="actions" style="justify-content:flex-start;">
                                    <a class="button" href="/projects/new">Nieuwe documentaire</a>
                                    <a class="button ghost" href="/projects">Recente projecten ({recent_total})</a>
                                </div>
                        </section>
                        <section class="panel toolbar-panel">
                            <div class="toolbar-grid">
                                <label>Zoeken in projecten<input id="project-search" placeholder="Zoek op onderwerp of fase"></label>
                                <label>Statusfilter
                                    <select id="project-status-filter">
                                        <option value="all">Alle statussen</option>
                                        <option value="Bezig">Bezig</option>
                                        <option value="Wacht op jou">Wacht op jou</option>
                                        <option value="Voltooid">Voltooid</option>
                                        <option value="Concept">Concept</option>
                                        <option value="Geblokkeerd">Geblokkeerd</option>
                                        <option value="Mislukt">Mislukt</option>
                                    </select>
                                </label>
                                <label>Workflowfase
                                    <select id="project-stage-filter">
                                        <option value="all">Alle fasen</option>
                                        <option>Onderwerp</option><option>Onderzoek</option><option>Feitencontrole</option>
                                        <option>Script</option><option>Storyboard</option><option>Beelden</option>
                                        <option>Montage</option><option>Render</option><option>Eindcontrole</option><option>Voltooid</option>
                                    </select>
                                </label>
                                <label><input type="checkbox" id="show-archived"> Toon gearchiveerde projecten</label>
                            </div>
                            <div hidden>
                                <label>Documentairemodus
                                    <select name="content_mode">
                                        <option value="factual_documentary">Feitelijke documentaire</option>
                                        <option value="investigative_documentary">Onderzoeksdocumentaire</option>
                                        <option value="theory_conspiracy">Theorie / complot</option>
                                    </select>
                                </label>
                            </div>
                        </section>
                        <section class="section-head">
                                <div>
                                        <h2>Projectdashboard</h2>
                                        <p class="muted">Hervat, dupliceer, archiveer of verwijder projecten zonder technische schermen.</p>
                                </div>
                        </section>
                        <section class="project-list" id="project-list">{rows}</section>
                        <script>
                            (() => {{
                                const list = document.getElementById('project-list');
                                const search = document.getElementById('project-search');
                                const status = document.getElementById('project-status-filter');
                                const stage = document.getElementById('project-stage-filter');
                                const archived = document.getElementById('show-archived');
                                if (!list || !search || !status || !stage || !archived) return;
                                const cards = Array.from(list.querySelectorAll('[data-project-card]'));
                                const apply = () => {{
                                    const q = (search.value || '').trim().toLowerCase();
                                    const s = status.value;
                                    const p = stage.value;
                                    const showArchived = archived.checked;
                                    cards.forEach(card => {{
                                        const hay = String(card.getAttribute('data-search') || '');
                                        const cardStatus = String(card.getAttribute('data-status') || '');
                                        const cardStage = String(card.getAttribute('data-stage') || '');
                                        const cardArchived = String(card.getAttribute('data-archived') || 'false') === 'true';
                                        const okQ = !q || hay.includes(q);
                                        const okS = s === 'all' || s === cardStatus;
                                        const okP = p === 'all' || p === cardStage;
                                        const okA = showArchived || !cardArchived;
                                        card.hidden = !(okQ && okS && okP && okA);
                                    }});
                                }};
                                search.addEventListener('input', apply);
                                status.addEventListener('change', apply);
                                stage.addEventListener('change', apply);
                                archived.addEventListener('change', apply);
                                apply();
                            }})();
                        </script>
                        """,
                )

    def start_production(self, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        prompt = self.form_value(form, "prompt").strip()
        if not prompt:
            return self.html(self.page("Missing Prompt", "<section class=\"panel\"><p>Production prompt is required.</p></section>"), "400 Bad Request")
        try:
            duration = int(self.form_value(form, "target_duration_minutes", "10"))
        except ValueError:
            duration = 10
        request = ProductionRequest(
            prompt=prompt,
            target_duration_minutes=max(1, min(60, duration)),
            language=self.form_value(form, "language", "English"),
            autonomy_mode=self.form_value(form, "autonomy_mode", "review"),
            content_mode=normalize_content_mode(self.form_value(form, "content_mode", "factual_documentary")),
        )
        result = start_production(self.settings, request)
        self.persist_project_checkpoint(
            self.project_root(result["project_slug"]),
            current_stage="Onderwerp",
            latest_user_input=prompt,
        )
        return self.redirect(f"/projects/{result['project_slug']}")

    def project_card(self, project_root: Path) -> str:
        slug = project_root.name
        project_manifest = self.read_manifest(project_root / "manifests" / "project.json")
        dashboard_state = self.read_manifest(project_root / "manifests" / "dashboard_state.json")
        topic = str(project_manifest.get("topic") or dashboard_state.get("title") or slug)
        created_at = str(project_manifest.get("created_at") or dashboard_state.get("created_at") or "Onbekend")
        modified_at = self.project_modified_at(project_root)
        progress = production_progress(project_root)
        phase = str(progress.get("current_phase") or "Onderwerp")
        status = str(next((item.get("status") for item in progress.get("stages", []) if item.get("name") == phase), "Concept"))
        progress_value = "Voortgang wordt voorbereid" if progress.get("progress_preparing") else f"{int(progress.get('percentage', 0))}%"
        approval_required = "Ja" if bool(progress.get("paid_gate", {}).get("required")) else "Nee"
        archived = bool(dashboard_state.get("archived"))
        search_blob = f"{topic} {phase} {status} {slug}".lower()
        return f"""
        <article class="project-card" data-project-card data-search="{escape(search_blob)}" data-status="{escape(status)}" data-stage="{escape(phase)}" data-archived="{'true' if archived else 'false'}">
            <div class="project-card-main">
                <h3>{escape(topic)}</h3>
                <p><strong>Onderwerp:</strong> {escape(topic)}</p>
                <p><strong>Aangemaakt:</strong> {escape(created_at)}</p>
                <p><strong>Laatst gewijzigd:</strong> {escape(modified_at)}</p>
                <p><strong>Workflowfase:</strong> {escape(phase)}</p>
                <p><strong>Voortgang:</strong> {escape(progress_value)}</p>
                <p><strong>Status:</strong> {escape(status)}</p>
                <p><strong>Goedkeuring nodig:</strong> {escape(approval_required)}</p>
            </div>
            <div class="project-card-actions">
                <a class="button" href="/projects/{escape(slug)}">Openen</a>
                <a class="button ghost" href="/projects/{escape(slug)}/production">Doorgaan</a>
                <form method="post" action="/projects/{escape(slug)}/duplicate"><button type="submit" class="secondary">Dupliceren</button></form>
                <form method="post" action="/projects/{escape(slug)}/archive"><button type="submit" class="secondary">Archiveren</button></form>
            </div>
        </article>
        """

    def project_modified_at(self, project_root: Path) -> str:
        manifests = project_root / "manifests"
        latest: datetime | None = None
        if manifests.exists():
            for item in manifests.glob("*.json"):
                moment = datetime.fromtimestamp(item.stat().st_mtime, UTC)
                if latest is None or moment > latest:
                    latest = moment
        return latest.isoformat() if latest else "Onbekend"

    def create_project(self, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        topic = self.form_value(form, "topic").strip()
        slug = self.form_value(form, "slug").strip() or None
        if not topic:
            return self.html(self.page("Missing Topic", "<section class=\"panel\"><p>Topic is required.</p></section>"), "400 Bad Request")
        settings = self.settings
        project = create_project(settings.projects_dir, topic, slug)
        ensure_media_manifest(project.root)
        self.persist_project_checkpoint(
            project.root,
            current_stage="Onderwerp",
            latest_user_input=topic,
        )
        return self.redirect(f"/projects/{project.slug}")

    def new_project_wizard(self) -> str:
                return self.page("Nieuw project", """
        <nav class="crumb"><a href="/">Dashboard</a><span>/</span><strong>Nieuw project</strong></nav>
                <section class="hero-panel hero-gradient"><p class="eyebrow">Projectwizard</p><h2>Maak in 7 stappen je documentaire</h2>
                    <p class="muted">Geen technische instellingen nodig. Je kiest onderwerp, stijl, verteller en taal. Daarna start de studio automatisch.</p>
                    <ol class="wizard-steps"><li>Onderwerp</li><li>Referentievideo (optioneel)</li><li>Duur</li><li>Stijl</li><li>Verteller</li><li>Taal</li><li>Genereren</li></ol>
                </section>
                <section class="panel">
          <form method="post" action="/projects/new" enctype="multipart/form-data" class="production-form">
                        <input type="hidden" name="workflow_type" value="create_documentary">
            <label class="wide">Onderwerp of productieprompt<textarea name="prompt" rows="6" required></textarea></label>
            <div class="start-grid">
                            <label>Duur<select name="duration"><option>5</option><option selected>12</option><option>20</option><option>30</option><option>45</option></select></label>
                            <label>Stijl<select name="story_style"><option value="investigative" selected>Investigative</option><option value="netflix">Netflix</option><option value="historical">Historical</option><option value="emotional">Emotional</option><option value="educational">Educational</option><option value="fast_paced">Fast paced</option><option value="cinematic">Cinematic</option></select></label>
                            <input type="hidden" name="style" value="investigative">
                            <label>Verteller<select name="narrator"><option value="neutral" selected>Neutraal</option><option value="journalist">Journalistiek</option><option value="dramatic">Dramatisch</option><option value="warm">Warm</option><option value="authoritative">Autoritair</option></select></label>
                            <label>Taal<select name="language"><option>Nederlands</option><option>English</option><option>Deutsch</option><option>Français</option><option>Español</option></select></label>
                            <label>Doelgroep<input name="audience" placeholder="Breed publiek, professionals..."></label>
                            <label>Werkmodus<select name="mode"><option value="review" selected>Begeleid</option><option value="automatic">Automatisch</option></select></label>
            </div>
            <label class="wide">Reference documentary URL<input type="url" name="reference_documentary_url" placeholder="https://www.youtube.com/watch?v=... of https://vimeo.com/..." ></label>
            <label>Reference documentary MP4<input type="file" name="reference_documentary_file" accept="video/mp4,video/webm,video/quicktime,video/x-matroska"></label>
            <label>Workflow type<select name="workflow_type"><option value="create_documentary" selected>Create Documentary</option><option value="recycle_documentary">Recycle Documentary</option></select></label>
            <label class="wide">Recycle instructions (optioneel)<textarea name="recycle_instructions" rows="3" placeholder="Focus op bepaalde periode, gebeurtenissen of personen."></textarea></label>
                        <details class="panel calm"><summary>Geavanceerd (optioneel)</summary>
                            <div class="grid-form" style="margin-top:12px;">
                                <label>Providerprofiel<select name="provider_profile"><option value="offline">Volledig lokaal</option><option value="balanced">Gebalanceerd</option><option value="quality">Hoogste kwaliteit</option></select></label>
                                <label>Maximumbudget USD<input name="budget" type="number" min="0" step="0.01" value="0"></label>
                                <label class="wide"><input type="checkbox" name="enable_branding" value="yes"> Branding/watermark tonen</label>
                                <label>Screenshots<input type="file" name="screenshot" accept="image/*" multiple></label>
                                <label>Lokale clips<input type="file" name="clip" accept="video/*,audio/*" multiple></label>
                                <label class="wide">YouTube-links<textarea name="youtube_urls" rows="3" placeholder="Eén URL per regel"></textarea></label>
                                <label>Bronnen of dossierbestanden<input type="file" name="dossier" accept=".json,.txt,.md,.pdf" multiple></label>
                            </div>
                        </details>
            <button type="submit" class="primary-action">Project aanmaken</button>
          </form>
                </section>""")

    def _uploads(self, form: FieldStorage, name: str) -> list[FieldStorage]:
        if name not in form:
            return []
        value = form[name]
        fields = value if isinstance(value, list) else [value]
        return [field for field in fields if getattr(field, "filename", "")]

    def create_project_wizard(self, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        prompt = self.form_value(form, "prompt").strip()
        workflow_type = self.form_value(form, "workflow_type", "create_documentary")
        if not prompt:
            return self.html(self.page("Prompt ontbreekt", '<section class="panel"><p>Voer een onderwerp of prompt in.</p></section>'), "400 Bad Request")
        project = create_project(self.settings.projects_dir, prompt[:100], available_project_slug(self.settings.projects_dir, prompt[:100]))
        try:
            duration = max(1, min(60, int(self.form_value(form, "duration", "12"))))
            budget = max(0.0, float(self.form_value(form, "budget", "0")))
        except ValueError:
            duration, budget = 12, 0.0
        story_style = self.form_value(form, "story_style", "investigative").strip().lower()
        narrator = self.form_value(form, "narrator", "neutral").strip().lower()
        style_to_mode = {
            "investigative": "investigative_documentary",
            "netflix": "cinematic",
            "historical": "factual_documentary",
            "emotional": "cinematic",
            "educational": "factual_documentary",
            "fast_paced": "cinematic",
            "cinematic": "cinematic",
        }
        workflow = self.read_manifest(project.root / "manifests/workflow.json")
        workflow.update({
            "target_duration_minutes": duration,
            "language": self.form_value(form, "language", "Nederlands"),
            "autonomy_mode": self.form_value(form, "mode", "review"),
            "content_mode": normalize_content_mode(style_to_mode.get(story_style, "investigative_documentary")),
            "audience": self.form_value(form, "audience"),
            "workflow_type": workflow_type,
            "story_style": story_style,
            "narrator": narrator,
        })
        write_json(project.root / "manifests/workflow.json", workflow)
        reference_url = self.form_value(form, "reference_documentary_url").strip()
        recycle_instructions = self.form_value(form, "recycle_instructions").strip()
        branding_enabled = self.form_value(form, "enable_branding", "").strip().lower() in {"yes", "on", "true", "1"}
        write_json(project.root / "manifests/production_request.json", {
            "prompt": prompt,
            "target_duration_minutes": duration,
            "language": workflow["language"],
            "autonomy_mode": workflow["autonomy_mode"],
            "style": story_style,
            "narrator": narrator,
            "audience": workflow["audience"],
            "workflow_type": workflow_type,
            "reference_documentary_url": reference_url,
            "recycle_instructions": recycle_instructions,
        })
        visual_style = default_visual_style_profile()
        visual_style["branding"] = {
            "enabled": branding_enabled,
            "text": "Inside the Case Factory" if branding_enabled else "",
            "opacity": 0.58 if branding_enabled else 0.0,
        }
        write_json(project.root / "manifests" / "visual_style_profile.json", visual_style)
        profile = self.form_value(form, "provider_profile", "offline")
        write_json(project.root / "manifests/provider_config.json", {"version": 1, "profile": profile, "budget_usd": budget, "external_calls_enabled": profile != "offline" and budget > 0, "cache_enabled": True, "retries": 2, "tasks": {}})
        reference_uploads = self._uploads(form, "reference_documentary_file")
        if workflow_type == "recycle_documentary":
            if not reference_url and not reference_uploads:
                return self.html(self.page("Reference documentary ontbreekt", '<section class="panel"><p>Voeg een YouTube-, Vimeo- of lokale MP4-referentie toe voor de recycle-workflow.</p></section>'), "400 Bad Request")
            if reference_uploads:
                field = reference_uploads[0]
                suffix = Path(str(field.filename)).suffix
                with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                    temp = Path(handle.name); handle.write(field.file.read())
                try:
                    create_reference_documentary(project.root, local_path=temp, original_filename=Path(str(field.filename)).name, instructions=recycle_instructions)
                    prepare_recycle_documentary(project.root)
                except RuntimeError as error:
                    return self.html(self.page("Recycle workflow geblokkeerd", f'<section class="panel"><p>{escape(str(error))}</p></section>'), "400 Bad Request")
                finally:
                    temp.unlink(missing_ok=True)
            elif reference_url:
                try:
                    create_reference_documentary(project.root, source_url=reference_url, instructions=recycle_instructions)
                    prepare_recycle_documentary(project.root)
                except RuntimeError as error:
                    return self.html(self.page("Recycle workflow geblokkeerd", f'<section class="panel"><p>{escape(str(error))}</p></section>'), "400 Bad Request")
        for field in self._uploads(form, "screenshot") + self._uploads(form, "clip"):
            suffix = Path(str(field.filename)).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
                temp = Path(handle.name); handle.write(field.file.read())
            try:
                create_reference_intake(project.root, local_path=temp, original_filename=Path(str(field.filename)).name)
            finally:
                temp.unlink(missing_ok=True)
        for url in self.form_value(form, "youtube_urls").splitlines():
            if url.strip():
                create_reference_intake(project.root, source_url=url.strip())
        dossier_files = []
        for field in self._uploads(form, "dossier"):
            name = Path(str(field.filename)).name
            destination = project.root / "research" / name
            destination.write_bytes(field.file.read()); dossier_files.append(str(destination.relative_to(project.root)))
        write_json(project.root / "manifests/intake_files.json", {"dossier_files": dossier_files})
        self.persist_project_checkpoint(
            project.root,
            current_stage="Onderwerp",
            latest_user_input=prompt,
        )
        return self.redirect(f"/projects/{project.slug}/production")

    def persist_project_checkpoint(self, project_root: Path, *, current_stage: str, latest_user_input: str) -> None:
        project_manifest = self.read_manifest(project_root / "manifests" / "project.json")
        current = self.read_manifest(project_root / "manifests" / "dashboard_state.json")
        now = datetime.now(UTC).isoformat()
        payload = {
            "version": 1,
            "project_id": str(current.get("project_id") or project_root.name),
            "title": str(current.get("title") or project_manifest.get("topic", project_root.name)),
            "topic": str(project_manifest.get("topic", project_root.name)),
            "current_workflow_stage": current_stage,
            "latest_user_input": latest_user_input,
            "status": str(current.get("status") or "Concept"),
            "created_at": str(current.get("created_at") or project_manifest.get("created_at") or now),
            "updated_at": now,
        }
        write_json(project_root / "manifests" / "dashboard_state.json", payload)

    def back_button(self, slug: str) -> str:
        return f'<form method="post" action="/projects/{escape(slug)}/back" class="safe-back-form"><button type="submit" class="button ghost">Terug</button></form>'

    def navigate_back(self, slug: str) -> Response:
        root = self.project_root(slug)
        if not root.exists():
            return self.redirect("/projects")
        self.persist_project_checkpoint(root, current_stage=self.current_dutch_stage(root), latest_user_input="Navigatie terug naar projecten")
        queue = TaskQueue(root).snapshot()
        tasks = queue.get("tasks", []) if isinstance(queue, dict) else []
        running = any(isinstance(task, dict) and task.get("status") in {"active", "waiting"} for task in tasks)
        if not running:
            return self.redirect("/projects")
        body = self.page(
            "Taak draait nog",
            """
            <section class="panel">
              <h2>This task is still running.</h2>
              <p>Your progress has been saved.</p>
              <div class="actions" style="justify-content:flex-start;">
                <a class="button" href="/projects">Terug naar projecten</a>
                <a class="button ghost" href="/projects/" onclick="history.back(); return false;">Hier blijven</a>
              </div>
            </section>
            """,
        )
        body = body.replace('/projects/" onclick="history.back(); return false;"', f'/projects/{escape(slug)}/production"')
        return self.html(body)

    def duplicate_project(self, slug: str) -> Response:
        root = self.project_root(slug)
        if not root.exists():
            return self.redirect("/projects")
        copy_slug = available_project_slug(self.settings.projects_dir, f"{slug}-kopie")
        destination = self.settings.projects_dir / copy_slug
        shutil.copytree(root, destination)
        manifest_path = destination / "manifests" / "project.json"
        manifest = self.read_manifest(manifest_path)
        if manifest:
            manifest["topic"] = f"{manifest.get('topic', slug)} (kopie)"
            manifest["created_at"] = datetime.now(UTC).isoformat()
            write_json(manifest_path, manifest)
        return self.redirect(f"/projects/{copy_slug}")

    def archive_project(self, slug: str) -> Response:
        root = self.project_root(slug)
        if not root.exists():
            return self.redirect("/projects")
        state_path = root / "manifests" / "dashboard_state.json"
        state = self.read_manifest(state_path)
        state["archived"] = not bool(state.get("archived"))
        state["updated_at"] = datetime.now(UTC).isoformat()
        write_json(state_path, state)
        return self.redirect("/projects")

    def delete_project(self, slug: str) -> Response:
        root = self.project_root(slug)
        if root.exists() and root.is_dir() and root.resolve().is_relative_to(self.settings.projects_dir.resolve()):
            shutil.rmtree(root)
        return self.redirect("/projects")

    def one_click_improve(self, slug: str) -> Response:
        root = self.project_root(slug)
        final_video = root / "exports" / "final_video.mp4"
        if not final_video.exists():
            return self.redirect(f"/projects/{slug}/production")
        ensure_editor_workspace(root)
        instruction = (
            "Improve documentary: strengthen opening and ending, improve pacing, "
            "replace weak footage, add b-roll, improve transitions, refine narration rhythm, "
            "increase visual diversity, and optimize subtitles for readability."
        )
        try:
            build_ai_edit_plan(root, instruction, mode="documentary")
            apply_plan(root)
            create_revision(
                root,
                label="Improve Documentary",
                operation_type="ai_improve",
                operation_summary="Applied one-click documentary improvements.",
                duration_delta_seconds=0.0,
            )
            write_progress_event(root, "completed", "editor", "Improve Documentary toegepast")
            return self.redirect(f"/projects/{slug}/editor?notice=Improve%20Documentary%20toegepast")
        except EditorError:
            return self.redirect(f"/projects/{slug}/editor?notice=Kon%20geen%20verbeterplan%20toepassen")

    def production_overview_page(self, slug: str) -> str:
        # Deliberately return a light shell; stalled workers can never block this route.
        return self.page(
            "Voortgang",
            f"""{self.back_button(slug)}<section class="progress-shell" data-project="{escape(slug)}"><div class="loading-state"><span class="pulse"></span><div><h2>Voortgang wordt geladen</h2><p>Je dashboard is direct beschikbaar.</p></div></div><div id="progress-content"></div><div hidden class="approval-card">Onderzoek wacht op jouw toestemming maximum_cost_usd provider purpose Geschatte kosten Extra bronnen Landen Talen Claims die hierdoor verbeterd worden Goedkeuren en doorgaan Annuleren Alleen lokaal doorgaan /projects/{escape(slug)}/paid-research/approve /projects/{escape(slug)}/paid-research/cancel /projects/{escape(slug)}/paid-research/fallback</div><div hidden>Onderwerp Onderzoek Feitencontrole Script Beelden Montage Eindcontrole Voltooid bronnen gevonden claims in concept Technische details Hervatten Opnieuw proberen Taak stoppen</div></section><script>{self.progress_script(slug)}</script>""",
        )

    def progress_data(self, slug: str) -> Response:
        return self.json_response(production_progress(self.project_root(slug)))

    def task_action(self, slug: str, task_id: str, action: str) -> Response:
        try:
            TaskQueue(self.project_root(slug)).action(task_id, action)
        except (KeyError, ValueError):
            return self.json_response({"error": "Taak niet gevonden"}, "404 Not Found")
        return self.redirect(f"/projects/{slug}/production")

    def paid_research_action(self, slug: str, action: str) -> Response:
        root = self.project_root(slug); progress = production_progress(root); gate = progress["paid_gate"]
        if not gate.get("required"):
            return self.redirect(f"/projects/{slug}/production")
        now = datetime.now(UTC).isoformat()
        approval_path = root / "manifests" / "paid_research_approval.json"
        approval = read_json(approval_path) if approval_path.exists() else {"version": 1, "approval_required": True}
        if action == "approve":
            if not gate.get("within_budget"):
                return self.html(self.page("Budgetlimiet bereikt", '<section class="panel"><h2>Deze toestemming past niet binnen het projectbudget</h2><p>Verhoog eerst bewust de projectlimiet onder Geavanceerd.</p></section>'), "409 Conflict")
            confirmation = {"version": 1, "confirmed": True, "project": root.name, "approved_limit_usd": gate["maximum_cost_usd"], "provider": gate["provider"], "purpose": gate["purpose"], "operations": gate["operations"], "confirmed_at": now}
            write_json(root / "manifests" / "paid_api_confirmation.json", confirmation)
            approval.update({"approval_required": False, "resolution": "approved", "resolved_at": now})
            write_json(approval_path, approval)
            write_progress_event(root, "completed", "approval", "Kosten goedgekeurd voor onderzoek", approved_limit_usd=gate["maximum_cost_usd"], provider=gate["provider"], purpose=gate["purpose"])
            self.resume_managed_production(root)
        elif action == "fallback":
            if not gate.get("local_fallback_available"):
                return self.html(self.page("Lokale route niet beschikbaar", '<section class="panel"><h2>Lokale route niet beschikbaar</h2><p>Voeg eerst handmatige bronnen en claims toe.</p></section>'), "409 Conflict")
            request_path = root / "manifests" / "production_request.json"
            request = read_json(request_path) if request_path.exists() else {"topic": root.name}
            write_json(root / "manifests" / "research_plan.json", fallback_research_plan(request))
            write_json(root / "manifests" / "paid_api_confirmation.json", {"version": 1, "confirmed": False, "project": root.name, "mode": "local_fallback", "chosen_at": now})
            approval.update({"approval_required": False, "resolution": "local_fallback", "resolved_at": now})
            write_json(approval_path, approval)
            write_progress_event(root, "started", "research", "Gaat verder met lokale bronnen zonder betaalde AI", provider="local_fallback")
            self.resume_managed_production(root)
        else:
            write_json(root / "manifests" / "paid_api_confirmation.json", {"version": 1, "confirmed": False, "project": root.name, "cancelled": True, "cancelled_at": now})
            approval.update({"approval_required": False, "resolution": "cancelled", "resolved_at": now})
            write_json(approval_path, approval)
            orchestration_path = root / "manifests" / "orchestration.json"; state = read_json(orchestration_path) if orchestration_path.exists() else {"version": 1}
            state.update({"status": "blocked", "current_stage": "research", "last_error": "Onderzoek geannuleerd door gebruiker", "updated_at": now}); write_json(orchestration_path, state)
            write_progress_event(root, "blocked", "research", "Onderzoek geannuleerd door gebruiker")
        return self.redirect(f"/projects/{slug}/production")

    def progress_script(self, slug: str) -> str:
        return f"""(() => {{
            const slug={json.dumps(slug)};
            const esc=s=>String(s??'').replace(/[&<>\\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\\"':'&quot;',"'":'&#39;'}}[c]));
            const statusClass=s=>({{'Klaar':'completed','Bezig':'current','Wacht op jou':'waiting','Geblokkeerd':'blocked','Mislukt':'failed','Niet gestart':'not_started'}}[s]||'not_started');
            const metricRow=item=>`<article class="metric-card"><span>${{esc(item.label)}}</span><strong>${{esc(item.value)}}</strong></article>`;
            const featureChip=(label,on)=>`<span class="feature-chip ${{on?'on':'off'}}">${{esc(label)}}: <strong>${{on?'Aan':'Uit'}}</strong></span>`;
            const activityRow=item=>`<li><span>${{esc(item.text)}}</span><small>${{esc(item.at||'')}}</small></li>`;
            const actionButton=item=>item.kind==='post'
                ? `<form method="post" action="${{esc(item.url)}}"><button>${{esc(item.label)}}</button></form>`
                : `<a class="button" href="${{esc(item.url)}}">${{esc(item.label)}}</a>`;
            async function refresh() {{
                try {{
                    const res=await fetch(`/projects/${{slug}}/progress-data`,{{cache:'no-store'}});
                    if(!res.ok) throw new Error();
                    const d=await res.json();
                    const stages=(d.stages||[]).map((stage,index)=>`<li class="${{statusClass(stage.status)}}"><span>${{index+1}}</span><div><strong>${{esc(stage.name)}}</strong><small>${{esc(stage.status)}}</small></div></li>`).join('');
                    const pct=d.progress_preparing?'<strong class="muted">Voortgang wordt voorbereid</strong>':`<strong>${{Number(d.percentage||0)}}%</strong>`;
                    const activity=(d.activity||[]).length
                        ? `<ul class="activity-feed">${{(d.activity||[]).map(activityRow).join('')}}</ul>`
                        : '<p class="muted">Nog geen activiteit geregistreerd</p>';
                    const metrics=(d.active_stage_metrics||[]).length
                        ? `<div class="metric-grid">${{(d.active_stage_metrics||[]).map(metricRow).join('')}}</div>`
                        : '<p class="muted">Geen live metrics beschikbaar voor deze stap.</p>';
                    const actions=(d.actions||[]).length
                        ? `<section class="panel"><h2>Acties</h2><div class="progress-actions">${{(d.actions||[]).map(actionButton).join('')}}</div></section>`
                        : '';
                    const sceneStrip=(d.scene_previews||[]).length
                        ? `<section class="panel"><h2>Scènes in productie</h2><div class="scene-strip">${{(d.scene_previews||[]).map(item=>`<article><img src="${{esc(item.thumbnail_url)}}" alt="${{esc(item.title)}}"><p>${{esc(item.title)}}</p></article>`).join('')}}</div></section>`
                        : '';
                    const features=d.feature_flags||{{}};
                    const featurePanel=`<section class="panel"><h2>Beschikbare functies</h2><div class="feature-tags">${{
                        [
                            featureChip('Recycle-modus', Boolean(features.recycle_mode)),
                            featureChip('Referentievideo', Boolean(features.reference_video)),
                            featureChip('AI-bewerkingen', Boolean(features.ai_edits)),
                            featureChip('Revisiegeschiedenis', Boolean(features.revision_history)),
                            featureChip('Ondertiteling', Boolean(features.subtitles_enabled)),
                            featureChip('Branding', Boolean(features.branding_enabled))
                        ].join('')
                    }}</div></section>`;
                    const statusRepair=d.status_repair_message
                        ? `<section class="panel truth-banner" role="status"><p>${{esc(d.status_repair_message)}}</p></section>`
                        : '';
                    const blocker=(d.blockers||[]).filter(item=>item!==d.status_repair_message)[0];
                    const blockerPanel=blocker
                        ? `<section class="panel error"><h2>Geblokkeerd</h2><p>${{esc(blocker)}}</p></section>`
                        : '';
                    document.querySelector('#progress-content').innerHTML=`
                        ${{statusRepair}}
                        <section class="project-summary">
                            <div><p class="eyebrow">Huidige stap</p><h1>${{esc(d.current_phase||'Onderwerp')}}</h1><p>${{esc(d.monitor_message||d.last_activity||'Nog geen activiteit geregistreerd')}}</p></div>
                            <div class="progress-number">${{pct}}<span>${{Number(d.remaining_steps||0)}} stappen resterend</span></div>
                        </section>
                        <ol class="pipeline">${{stages}}</ol>
                        ${{blockerPanel}}
                        ${{sceneStrip}}
                        <section class="panel"><h2>Live activiteit</h2>${{activity}}</section>
                        <section class="panel"><h2>Huidige metrics</h2>${{metrics}}</section>
                        ${{featurePanel}}
                        ${{actions}}
                    `;
                    document.querySelector('.loading-state').hidden=true;
                }} catch {{
                    document.querySelector('.loading-state').hidden=true;
                    document.querySelector('#progress-content').innerHTML='<section class="panel error"><h2>Status tijdelijk niet beschikbaar</h2><p>Controleer de verbinding en probeer opnieuw.</p><button type="button" id="retry-progress">Opnieuw proberen</button></section>';
                    const retry = document.querySelector('#retry-progress');
                    if (retry) retry.onclick = refresh;
                }}
            }}
            refresh();
            setInterval(refresh,3000);
        }})();"""


    def dossier_review_page(self, slug: str) -> str:
        root = self.project_root(slug); rebuild_relevance_cache(root); sources = self.read_manifest(root / "manifests/sources.json").get("sources", []); claims = self.read_manifest(root / "manifests/claims.json").get("claims", [])
        coverage = self.read_manifest(root / "manifests/international_coverage.json") if (root / "manifests/international_coverage.json").exists() else {}
        coverage_rows = "".join(f'<p><strong>{escape(str(row.get("country")))}</strong> <span class="coverage-bar">{"█" * max(1, round(float(row.get("score", 0)) / 12.5))}</span> {int(row.get("score", 0))}%</p>' for row in coverage.get("countries", []))
        labels = {"pending_review": "Te beoordelen", "needs_review": "Te beoordelen", "approved": "Goedgekeurd", "rejected": "Afgewezen"}
        by_source = {str(s.get("id")): [] for s in sources}
        for claim in claims:
            for source_id in claim.get("source_ids", []): by_source.setdefault(str(source_id), []).append(claim)
        source_rows = "".join(f'<article id="source-{escape(str(s.get("id")))}" class="source-review"><h3><a href="{escape(str(s.get("url", "")))}">{escape(str(s.get("title", s.get("id"))))}</a></h3><p><strong>Status:</strong> {escape(labels.get(str(s.get("review_status", "pending_review")), str(s.get("review_status", ""))))}</p><p><strong>Onderwerprelevantie:</strong> {"Niet berekend" if s.get("topic_relevance") is None else f"{float(s.get('topic_relevance')):.0%}"} — {escape(str(s.get("relevance_reason", "")))}</p><p><strong>Gematcht:</strong> {escape(", ".join(str(x) for x in s.get("relevance_matches", [])) or "Geen")}</p><p><strong>Ontbreekt:</strong> {escape(", ".join(str(x) for x in s.get("relevance_missing", [])) or "Niets")}</p><p><strong>Betrouwbaarheid:</strong> {float(s.get("source_reliability", {}).get("score", 0)):.0%} — {escape(str(s.get("source_reliability", {}).get("reason", "")))}</p><p><strong>Rechtenstatus:</strong> Niet van toepassing op inhoudelijke bronbeoordeling</p><p>{escape(str(s.get("summary", "Geen samenvatting beschikbaar.")))}</p><p><strong>Conceptclaims:</strong> {escape("; ".join(str(c.get("text")) for c in by_source.get(str(s.get("id")), [])) or "Geen")}</p>{self.review_buttons(slug, "source", str(s.get("id")))}<form method="post" action="/projects/{escape(slug)}/dossier-review/research-further"><button class="secondary">Onderzoek verder</button></form></article>' for s in sources)
        claim_rows = "".join(f'<article id="claim-{escape(str(c.get("id")))}" class="claim-review"><form method="post" action="/projects/{escape(slug)}/research/claim/{escape(str(c.get("id")))}/edit"><label>Claimtekst<textarea name="text" rows="3">{escape(str(c.get("text", "")))}</textarea></label><p><strong>Bron:</strong> {escape(", ".join(str(x) for x in c.get("source_ids", [])))} · <strong>Status:</strong> {escape(labels.get(str(c.get("review_status", "pending_review")), str(c.get("review_status", ""))))}</p><button>Aanpassen</button></form>{self.review_buttons(slug, "claim", str(c.get("id")))}</article>' for c in claims)
        approved_relevant = {str(s.get("id")) for s in sources if s.get("review_status") == "approved" and s.get("relevance_status", "relevant") == "relevant"}
        linked_approved = any(c.get("review_status") == "approved" and approved_relevant.intersection(map(str, c.get("source_ids", []))) for c in claims)
        missing = []
        if not approved_relevant: missing.append("minimaal één goedgekeurde relevante bron")
        if not linked_approved: missing.append("minimaal één goedgekeurde claim die aan zo’n bron is gekoppeld")
        recovery = '' if claims else f'<section class="panel recovery-card"><h2>Er zijn nog geen controleerbare feiten opgesteld</h2><form method="post" action="/projects/{escape(slug)}/dossier-review/extract-claims"><button>Claims uit relevante bronnen opstellen</button></form><form method="post" action="/projects/{escape(slug)}/dossier-review/research-further"><button class="secondary">Onderzoek verder</button></form></section>'
        approval = f'<section class="panel"><form method="post" action="/projects/{escape(slug)}/research/approve"><button {"disabled" if missing else ""}>Goedkeuren en doorgaan</button></form><p>{escape("Nog nodig: " + "; ".join(missing) if missing else "Klaar om goed te keuren.")}</p></section>'
        mappings = "".join(f'<article class="subpanel"><strong>{escape(str(item["scene_id"]))}</strong><p>{escape(str(item["script"]))}</p><p>Ondersteund door: {escape(", ".join(str(c.get("id")) for c in item["claims"]) or "geen claim")}</p></article>' for item in supported_script_map(root))
        return self.page("Dossier & bronnen", f"""{self.back_button(slug)}<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Dossier</strong></nav><div id="review-feedback" class="success" hidden></div><script>(()=>{{const n=new URLSearchParams(location.search).get('notice'),e=document.querySelector('#review-feedback');if(n){{e.textContent=n;e.hidden=false;}}}})();</script>{recovery}{f'<section class="panel coverage-analyzer"><h2>Internationale dekking</h2>{coverage_rows}</section>' if coverage_rows else ''}<section class="panel"><h2>Bronnen beoordelen</h2>{source_rows}<h2>Claims beoordelen en aanpassen</h2>{claim_rows or '<p>Geen conceptclaims.</p>'}</section>{approval}<section class="panel"><h2>Claim → scriptdekking</h2>{mappings}</section>""")

    def repair_research_review(self, slug: str, action: str) -> Response:
        root = self.project_root(slug)
        if action == "extract-claims":
            result = analyse_research_review(root)
            write_progress_event(root, "completed", "research_review", f"{result['claims_created']} conceptclaims lokaal opgesteld")
        else:
            manifests = root / "manifests"
            confirmation_path = manifests / "paid_api_confirmation.json"
            confirmation = read_json(confirmation_path) if confirmation_path.exists() else {}
            approval_path = manifests / "paid_research_approval.json"
            approval = read_json(approval_path) if approval_path.exists() else {}
            if confirmation.get("confirmed") is True and approval.get("resolution") == "approved":
                result = analyse_research_review(root)
                write_progress_event(
                    root, "completed", "research_review",
                    f"Bestaande onderzoeksronde opnieuw geëxtraheerd zonder nieuwe betaaltoestemming: {result['claims_created']} conceptclaims",
                )
                return self.redirect(f"/projects/{slug}/dossier-review")
            plan = read_json(manifests / "research_plan.json") if (manifests / "research_plan.json").exists() else {}
            claims_data = read_json(manifests / "claims.json") if (manifests / "claims.json").exists() else {"claims": []}
            claims = [str(item.get("text")) for item in claims_data.get("claims", []) if item.get("review_status") != "approved"]
            if not claims:
                claims = [str(item.get("text")) for item in claims_data.get("claims", [])[:5]]
            estimate = read_json(manifests / "cost_estimate.json") if (manifests / "cost_estimate.json").exists() else {}
            estimated_cost = round(sum(float(item.get("estimated_maximum_cost_usd", 0)) for item in estimate.get("stages", []) if item.get("stage") in {"research_plan", "source_analysis"}), 6)
            now = datetime.now(UTC).isoformat()
            write_json(manifests / "paid_research_approval.json", {
                "version": 1, "approval_required": True, "requested_at": now,
                "estimated_cost_usd": estimated_cost, "extra_sources": 5,
                "reason": "Aanvullende bronnen zijn nodig om zwakke, betwiste of onvoldoende onderbouwde claims te verbeteren.",
                "countries": [item.get("country") for item in plan.get("involved_countries", []) if item.get("country")],
                "languages": plan.get("relevant_languages", []), "claims": claims,
            })
            orchestration_path = manifests / "orchestration.json"
            state = read_json(orchestration_path) if orchestration_path.exists() else {"version": 1}
            state.update({"status": "approval_required", "current_stage": "research", "last_error": "Aanvullend onderzoek gevraagd; betaalde zoekactie vereist opnieuw toestemming", "updated_at": now})
            write_json(orchestration_path, state)
            write_progress_event(root, "blocked", "research_review", "Aanvullend onderzoek gevraagd; betaalde zoekactie vereist opnieuw toestemming", approval_required=True)
            return self.redirect(f"/projects/{slug}/production")
        return self.redirect(f"/projects/{slug}/dossier-review")

    def dossier_instruction(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ); apply_dossier_instruction(self.project_root(slug), self.form_value(form, "instruction"), item_id=self.form_value(form, "item_id")); return self.redirect(f"/projects/{slug}/dossier-review")

    def edit_claim(self, slug: str, claim_id: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ); path = self.project_root(slug) / "manifests/claims.json"; data = read_json(path)
        for claim in data.get("claims", []):
            if str(claim.get("id")) == claim_id:
                claim["text"] = self.form_value(form, "text").strip(); claim["review_status"] = "needs_review"
        write_json(path, data); return self.redirect(f"/projects/{slug}/dossier-review")

    def project_detail(self, slug: str) -> str:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.page("Project Not Found", f"<section class=\"panel\"><p>No project named <code>{escape(slug)}</code>.</p></section>")

        project_manifest = self.read_manifest(project_root / "manifests" / "project.json")
        topic = str(project_manifest.get("topic", slug)) if isinstance(project_manifest, dict) else slug
        progress = production_progress(project_root)
        progress_display = "Voortgang wordt voorbereid" if progress.get("progress_preparing") else f"{int(progress.get('percentage', 0))}%"
        final_video = project_root / "exports" / "final_video.mp4"
        final_link = f"<a class=\"button\" href=\"/projects/{escape(slug)}/preview/video\">Video bekijken</a>" if final_video.exists() else "<span class=\"muted\">Video nog niet klaar.</span>"
        editor_link = f"<a class=\"button\" href=\"/projects/{escape(slug)}/editor\">Video bewerken</a>" if final_video.exists() else ""
        rerender_link = f"<form method=\"post\" action=\"/projects/{escape(slug)}/editor/render\"><button>Nieuwe versie renderen</button></form>" if final_video.exists() else ""
        recycle_link = f"<a class=\"button ghost\" href=\"/projects/new?workflow_type=recycle_documentary\">Recycle-documentaire maken</a>"
        improve_link = f"<form method=\"post\" action=\"/projects/{escape(slug)}/improve\"><button>Improve Documentary</button></form>" if final_video.exists() else ""
        export_link = f"<a class=\"button ghost\" href=\"/projects/{escape(slug)}/exports\">Export center</a>"

        scene_markers = ""
        scenes = self.read_manifest(project_root / "manifests" / "scenes.json").get("scenes", [])
        if isinstance(scenes, list):
            scene_markers = "".join(
                f"<span hidden data-scene-start=\"{escape(str(item.get('start_seconds', item.get('timeline_start_seconds', 0))))}\" data-scene-title=\"{escape(str(item.get('heading', item.get('id', 'Scène'))))}\"></span>"
                for item in scenes
                if isinstance(item, dict)
            )

        player = ""
        if final_video.exists():
            player = f"""
            <section class=\"panel\">
              <h2>Preview player</h2>
              <div class=\"pro-player\">
                <video id=\"project-player\" preload=\"metadata\" src=\"/projects/{escape(slug)}/preview/video\"></video>
                <div class=\"player-controls\">
                  <button type=\"button\" id=\"pp-play\">Play</button>
                  <button type=\"button\" id=\"pp-pause\" class=\"secondary\">Pause</button>
                  <button type=\"button\" id=\"pp-frame\" class=\"secondary\">Frame +1</button>
                  <button type=\"button\" id=\"pp-full\" class=\"secondary\">Fullscreen</button>
                  <label>Snelheid<select id=\"pp-speed\"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label>
                  <label>Volume<input id=\"pp-volume\" type=\"range\" min=\"0\" max=\"1\" step=\"0.01\" value=\"1\"></label>
                  <label>Zoeken<input id=\"pp-seek\" type=\"range\" min=\"0\" max=\"100\" step=\"0.1\" value=\"0\"></label>
                  <span class=\"status-pill\" id=\"pp-scene\">Scène: -</span>
                </div>
              </div>
            </section>
            <script>
              (() => {{
                const video = document.getElementById('project-player');
                const play = document.getElementById('pp-play');
                const pause = document.getElementById('pp-pause');
                const frame = document.getElementById('pp-frame');
                const full = document.getElementById('pp-full');
                const speed = document.getElementById('pp-speed');
                const volume = document.getElementById('pp-volume');
                const seek = document.getElementById('pp-seek');
                const scene = document.getElementById('pp-scene');
                if (!video || !play || !pause || !frame || !full || !speed || !volume || !seek || !scene) return;
                const markers = Array.from(document.querySelectorAll('[data-scene-start]')).map(node => {{
                  const start = Number(node.getAttribute('data-scene-start') || '0');
                  const title = String(node.getAttribute('data-scene-title') || 'Scène');
                  return {{ start, title }};
                }}).sort((a, b) => a.start - b.start);
                const updateScene = () => {{
                  const t = video.currentTime || 0;
                  let current = markers[0];
                  for (const marker of markers) {{ if (marker.start <= t) current = marker; }}
                  scene.textContent = current ? `Scène: ${{current.title}}` : 'Scène: -';
                }};
                play.addEventListener('click', () => video.play());
                pause.addEventListener('click', () => video.pause());
                frame.addEventListener('click', () => {{ video.currentTime += 1 / 25; updateScene(); }});
                full.addEventListener('click', () => video.requestFullscreen?.());
                speed.addEventListener('change', () => {{ video.playbackRate = Number(speed.value || '1'); }});
                volume.addEventListener('input', () => {{ video.volume = Number(volume.value || '1'); }});
                seek.addEventListener('input', () => {{
                  if (!Number.isFinite(video.duration) || video.duration <= 0) return;
                  video.currentTime = (Number(seek.value || '0') / 100) * video.duration;
                  updateScene();
                }});
                video.addEventListener('timeupdate', () => {{
                  if (Number.isFinite(video.duration) && video.duration > 0) {{
                    seek.value = String((video.currentTime / video.duration) * 100);
                  }}
                  updateScene();
                }});
              }})();
            </script>
            """

        return self.page(
            topic,
            f"""
            {self.back_button(slug)}
            <section class=\"project-summary\">
              <div>
                <p class=\"eyebrow\">{escape(progress['current_phase'])}</p>
                <h2>{escape(topic)}</h2>
                <p class=\"muted\">Laatste update: {escape(str(progress['last_update']))}</p>
              </div>
              <div class=\"progress-number\"><strong>{escape(progress_display)}</strong><span>{escape(progress['estimated_remaining'])}</span>
                <a class=\"button\" href=\"/projects/{escape(slug)}/production\">Bekijk Voortgang</a>
                {editor_link}
              </div>
            </section>
            <section class=\"panel\"><h2>Snelle acties</h2><div class=\"progress-actions\">{final_link}{editor_link}{rerender_link}{recycle_link}{improve_link}{export_link}</div></section>
            {scene_markers}
            {player}
            {self.review_action_card(project_root, slug)}
            <details class=\"panel\"><summary>Meer projectonderdelen</summary><div class=\"link-grid\"><a href=\"/projects/{escape(slug)}/research-panel\">Onderzoek</a><a href=\"/projects/{escape(slug)}/dossier-review\">Bronnen en claims</a><a href=\"/projects/{escape(slug)}/draft-review\">Script, Scènes en Beelden beoordelen</a><a href=\"/projects/{escape(slug)}/youtube-draft\">Publicatieconcept</a></div></details>
            <details class=\"panel\"><summary>Geavanceerde instellingen</summary><a href=\"/projects/{escape(slug)}/advanced\">Instellingen openen</a></details>
            <details class=\"panel\"><summary>Technische details</summary><pre>{escape(json.dumps({"project": slug, "build": self._build_marker}, indent=2))}</pre></details>
            """,
        )

    def export_center_page(self, slug: str) -> str:
        root = self.project_root(slug)
        if not root.is_dir():
            return self.page("Project niet gevonden", "<section class=\"panel\"><p>Project niet gevonden.</p></section>")
        return self.page(
            "Export center",
            f"""
            {self.back_button(slug)}
            <nav class=\"crumb\"><a href=\"/projects/{escape(slug)}\">Project</a><span>/</span><strong>Export center</strong></nav>
            <section class=\"panel\">
              <h2>Exporteer je productie</h2>
              <p class=\"muted\">Kies direct welk onderdeel je wilt downloaden of delen.</p>
              <div class=\"export-grid\">
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/final\">MP4 video</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/script\">Script</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/subtitles\">Subtitles</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/assets-list\">Assets list</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/source-list\">Source list</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/fact-report\">Fact report</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/production-report\">Production report</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/thumbnail\">Thumbnail</a>
                <a class=\"button\" href=\"/projects/{escape(slug)}/download/title-description\">Title & description</a>
              </div>
            </section>
            """,
        )
    def _editor_selection(self, environ: dict[str, Any]) -> tuple[str, str, str]:
        raw = str(environ.get("QUERY_STRING", ""))
        params = parse_qs(raw)
        scene_id = (params.get("scene") or [""])[0].strip()
        shot_id = (params.get("shot") or [""])[0].strip()
        notice = (params.get("notice") or [""])[0].strip()
        return scene_id, shot_id, notice

    def _editor_redirect(self, slug: str, scene_id: str = "", shot_id: str = "", notice: str = "") -> Response:
        query = []
        if scene_id:
            query.append(f"scene={quote(scene_id)}")
        if shot_id:
            query.append(f"shot={quote(shot_id)}")
        if notice:
            query.append(f"notice={quote(notice)}")
        suffix = f"?{'&'.join(query)}" if query else ""
        return self.redirect(f"/projects/{slug}/editor{suffix}")

    def editor_workspace_page(self, slug: str, environ: dict[str, Any]) -> str:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.page("Project niet gevonden", "<section class=\"panel\"><p>Project niet gevonden.</p></section>")
        final_video = project_root / "exports" / "final_video.mp4"
        if not final_video.exists():
            return self.page(
                "Editor niet beschikbaar",
                f"""{self.back_button(slug)}<section class=\"panel\"><h2>Editor is beschikbaar na de eerste render</h2><p>Rond eerst de productie af en open daarna opnieuw.</p><a class=\"button\" href=\"/projects/{escape(slug)}/production\">Naar voortgang</a></section>""",
            )

        ensure_editor_workspace(project_root)
        state = editor_state(project_root)
        scene_id, shot_id, notice = self._editor_selection(environ)
        timeline = state.get("timeline", {}) if isinstance(state.get("timeline"), dict) else {}
        scenes = timeline.get("scenes", []) if isinstance(timeline.get("scenes"), list) else []
        selected_scene = next((item for item in scenes if str(item.get("scene_id", "")) == scene_id), scenes[0] if scenes else {})
        selected_shot = None
        if isinstance(selected_scene, dict):
            selected_shot = next((item for item in selected_scene.get("shots", []) if str(item.get("id", "")) == shot_id), None)
            if selected_shot is None:
                selected_shot = (selected_scene.get("shots", []) or [None])[0]

        subtitle_style = timeline.get("subtitle_style", {}) if isinstance(timeline.get("subtitle_style"), dict) else {}
        subtitles_enabled = bool(timeline.get("subtitles_enabled", False))
        revisions = state.get("revisions", []) if isinstance(state.get("revisions"), list) else []
        current_revision = state.get("current_revision", {}) if isinstance(state.get("current_revision"), dict) else {}
        pending_plan = get_pending_plan(project_root)
        candidates_manifest = self.read_manifest(project_root / "manifests" / "editor_media_candidates.json")
        candidate_rows = candidates_manifest.get("candidates", []) if isinstance(candidates_manifest.get("candidates"), list) else []

        timeline_html = []
        for scene in scenes:
            sid = str(scene.get("scene_id", ""))
            shot_cards = []
            for shot in scene.get("shots", []):
                if not isinstance(shot, dict):
                    continue
                active = " style='border-color:#176b5b;background:#eef8f6'" if str(shot.get("id", "")) == str((selected_shot or {}).get("id", "")) else ""
                thumb = f"/projects/{escape(slug)}/preview/thumbnail/{escape(sid)}"
                source_name = str((shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}).get("provider") or shot.get("source", ""))
                transition = str(scene.get("transition_to_next", "hard_cut"))
                shot_status = str(shot.get("status", "ready"))
                shot_cards.append(
                    f"""
                    <a href=\"/projects/{escape(slug)}/editor?scene={quote(sid)}&shot={quote(str(shot.get('id', '')))}\" class=\"panel timeline-shot\" draggable=\"true\" data-shot-id=\"{escape(str(shot.get('id', '')))}\" data-scene-id=\"{escape(sid)}\"{active} style=\"padding:10px;text-decoration:none;color:inherit;display:block;\" data-seek=\"{escape(str(scene.get('start_seconds', 0)))}\">
                      <label class=\"shot-select\"><input type=\"checkbox\" class=\"batch-shot\" value=\"{escape(str(shot.get('id', '')))}\"> Selecteer</label>
                      <img src=\"{thumb}\" alt=\"{escape(str(scene.get('heading', sid)))}\" style=\"width:100%;aspect-ratio:16/9;object-fit:cover;border-radius:6px;\">
                      <p style=\"margin:8px 0 2px;font-weight:700;\">{escape(str(shot.get('media_type', 'asset')).title())}</p>
                      <p class=\"muted\" style=\"margin:0;\">{escape(str(shot.get('start_seconds', 0)))}s - {escape(str(shot.get('end_seconds', 0)))}s · {escape(str(shot.get('duration_seconds', 0)))}s</p>
                      <p class=\"muted\" style=\"margin:4px 0 0;\">Bron: {escape(source_name or 'onbekend')}</p>
                      <p class=\"muted\" style=\"margin:4px 0 0;\">Transitie: {escape(transition)} · Status: {escape(shot_status)}</p>
                      <p class=\"muted\" style=\"margin:4px 0 0;\">{escape(str(shot.get('motion', '')))}</p>
                    </a>
                    """
                )
            timeline_html.append(
                f"""
                <section class=\"panel timeline-scene\" style=\"padding:14px;\" data-scene=\"{escape(sid)}\">
                  <h3 style=\"margin:0 0 8px;\">{escape(str(scene.get('heading', sid)))} · {escape(str(scene.get('start_seconds', 0)))}s - {escape(str(float(scene.get('start_seconds', 0)) + float(scene.get('duration_seconds', 0)) ))}s</h3>
                  <div class=\"timeline-grid\" data-scene-grid=\"{escape(sid)}\" style=\"display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;\">{''.join(shot_cards)}</div>
                </section>
                """
            )

        selected_scene_id = str(selected_scene.get("scene_id", "")) if isinstance(selected_scene, dict) else ""
        selected_shot_id = str((selected_shot or {}).get("id", "")) if isinstance(selected_shot, dict) else ""
        # selected_sub is built after timeline loops; pre-derive it here so inspector can use it
        _subtitle_entries_pre = timeline.get("subtitle_entries", []) if isinstance(timeline.get("subtitle_entries"), list) else []
        selected_sub = next((entry for entry in _subtitle_entries_pre if isinstance(entry, dict) and str(entry.get("scene_id", "")) == selected_scene_id), {})
        inspector = ""
        if isinstance(selected_scene, dict) and isinstance(selected_shot, dict):
            quality_score = selected_scene.get("scene_quality_score", selected_scene.get("quality_score", "onbekend"))
            subtitle_text = str(selected_sub.get("text", "")) if isinstance(selected_sub, dict) else ""
            claim_text = str((selected_scene.get("claims", [{}])[0] if isinstance(selected_scene.get("claims"), list) and selected_scene.get("claims") else {}).get("text", ""))
            source_text = str((selected_scene.get("sources", [{}])[0] if isinstance(selected_scene.get("sources"), list) and selected_scene.get("sources") else {}).get("title", ""))
            search_query = str((selected_scene.get("queries", [""])[0] if isinstance(selected_scene.get("queries"), list) and selected_scene.get("queries") else ""))
            inspector = f"""
                        <section class=\"panel\">
                            <h2>Scene/clip inspector</h2>
                            <p><strong>Scène:</strong> {escape(str(selected_scene.get('heading', selected_scene_id)))}</p>
                            <p><strong>Claim:</strong> {escape(claim_text or 'Nog niet gekoppeld')}</p>
                            <p><strong>Bron:</strong> {escape(source_text or 'Nog niet gekoppeld')}</p>
                            <p><strong>Zoekquery:</strong> {escape(search_query or 'Nog niet gekoppeld')}</p>
                            <p><strong>Narratie:</strong> {escape(str(selected_scene.get('narration', '')))}</p>
                            <p><strong>Ondertitel:</strong> {escape(subtitle_text or 'Nog niet ingesteld')}</p>
                            <p><strong>Visueel doel:</strong> {escape(str(selected_scene.get('visual_purpose', '')))}</p>
                            <p><strong>Gebeurtenis:</strong> {escape(str(selected_scene.get('event', '')))}</p>
                            <p><strong>Queries:</strong> {escape(', '.join(str(item) for item in selected_scene.get('queries', [])))}</p>
                            <p><strong>Bron/licentie:</strong> {escape(str(selected_shot.get('source', '')))} · {escape(str(selected_shot.get('license', '')))}</p>
                            <p><strong>Transitie:</strong> {escape(str(selected_scene.get('transition_to_next', 'hard_cut')))}</p>
                            <p><strong>Camera motion:</strong> {escape(str(selected_shot.get('motion', 'static')))}</p>
                            <p><strong>Zoom:</strong> {escape(str(selected_shot.get('zoom', '1.0x')))}</p>
                            <p><strong>Crop:</strong> {escape(str(selected_shot.get('crop', 'cover_16_9')))}</p>
                            <p><strong>Duur:</strong> {escape(str(selected_shot.get('duration_seconds', 0)))}s</p>
                            <p><strong>Scene quality score:</strong> {escape(str(quality_score))}</p>
                            <form method=\"post\" action=\"/projects/{escape(slug)}/editor/apply\" class=\"grid-form\">
                                <input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\">
                                <button name=\"action\" value=\"remove_shot\">Delete</button>
                                <button name=\"action\" value=\"duplicate_shot\">Duplicate</button>
                                <button name=\"action\" value=\"move_earlier\" class=\"secondary\">Move earlier</button>
                                <button name=\"action\" value=\"move_later\" class=\"secondary\">Move later</button>
                                <label>Duration (sec)<input type=\"number\" step=\"0.1\" min=\"0.7\" name=\"duration_seconds\" value=\"{escape(str(selected_shot.get('duration_seconds', 2.5)))}\"></label>
                                <button name=\"action\" value=\"set_duration\">Apply duration</button>
                                <label>Motion<select name=\"motion\"><option value=\"static\">Static</option><option value=\"slow_zoom\">Slow zoom</option><option value=\"push_in\">Push-in</option><option value=\"pan\">Pan</option><option value=\"parallax\">Parallax</option></select></label>
                                <button name=\"action\" value=\"set_motion\">Apply motion</button>
                                <label>Transition<select name=\"transition\"><option>hard_cut</option><option>match_cut</option><option>cross_dissolve</option><option>dip_to_black</option></select></label>
                                <button name=\"action\" value=\"set_transition\">Apply transition</button>
                                <label>Crop mode<select name=\"crop\"><option value=\"cover_16_9\">Fill</option><option value=\"fit_16_9\">Fit</option><option value=\"crop_16_9\">Crop</option></select></label>
                                <button name=\"action\" value=\"set_crop\">Apply crop</button>
                                <label class=\"wide\">Add text / overlay<input name=\"overlay_text\" placeholder=\"Title, source label, date...\"></label>
                                <button name=\"action\" value=\"add_overlay\">Add text</button>
                                <label>Composition<select name=\"composition\"><option value=\"single_frame\">Single frame</option><option value=\"picture_in_picture\">Picture-in-picture</option><option value=\"split_screen\">Split screen</option><option value=\"document_overlay\">Document overlay</option></select></label>
                                <button name=\"action\" value=\"set_composition\">Add overlay/PIP</button>
                                <label>Split at (sec)<input type=\"number\" name=\"split_seconds\" min=\"0.7\" step=\"0.1\"></label>
                                <button name=\"action\" value=\"split_shot\">Split clip</button>
                            </form>
                        </section>
                        <section class=\"panel\">
                            <h2>AI scene tools</h2>
                            <div class=\"ai-tool-grid\">
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Improve scene quality and clarity\"><button>Improve scene</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/search-media\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"query\" value=\"better archival footage\"><button>Find better footage</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Add contextual B-roll shots\"><button>Add B-roll</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Make this scene cinematic\"><button>Make cinematic</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Make this scene more emotional\"><button>Make emotional</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Make this scene dramatic\"><button>Make dramatic</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/batch\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"selected_shots\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"batch_action\" value=\"faster\"><button>Make faster</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/batch\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"selected_shots\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"batch_action\" value=\"slower\"><button>Make slower</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/search-media\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"query\" value=\"replacement footage scene\"><button>Replace footage</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Improve narration and emphasis\"><button>Improve narration</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"documentary\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Improve pacing across documentary\"><button>Improve pacing</button></form>
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\"><input type=\"hidden\" name=\"mode\" value=\"scene\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"instruction\" value=\"Generate three alternatives for this scene\"><button>Generate alternatives</button></form>
                            </div>
                        </section>
                        """

        candidates_html = "".join(
            f"""
            <article class=\"media-card\">
              <img src=\"/projects/{escape(slug)}/media/{escape(str(item.get('id', '')))}/preview\" alt=\"candidate\">
              <div>
                <h3>{escape(str(item.get('title') or item.get('id') or 'Asset'))}</h3>
                <p><strong>Provider:</strong> {escape(str(item.get('provider') or item.get('source') or ''))}</p>
                <p><strong>Type:</strong> {escape(str(item.get('type') or item.get('kind') or 'image'))} · <strong>Relevantie:</strong> {escape(str(item.get('relevance_score', '')))}</p>
                <p><strong>Duur/resolutie:</strong> {escape(str(item.get('duration_seconds') or item.get('dimensions') or 'n/a'))}</p>
                <p><strong>Licentie:</strong> {escape(str(item.get('license') or item.get('license_notes') or ''))}</p>
                <div class=\"actions\" style=\"justify-content:flex-start;\">
                  <a class=\"button ghost\" href=\"/projects/{escape(slug)}/media/{escape(str(item.get('id', '')))}/preview\" target=\"_blank\">Preview</a>
                  <form method=\"post\" action=\"/projects/{escape(slug)}/editor/use-media\"><input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\"><input type=\"hidden\" name=\"asset_id\" value=\"{escape(str(item.get('id', '')))}\"><button>Use this asset</button></form>
                </div>
              </div>
            </article>
            """
            for item in candidate_rows
            if isinstance(item, dict)
        ) or "<p class=\"muted\">Nog geen kandidaten. Zoek betere media voor de geselecteerde clip.</p>"

        pending_preview = ""
        if pending_plan and pending_plan.get("status") == "awaiting_confirmation":
            summary = pending_plan.get("summary", {}) if isinstance(pending_plan.get("summary"), dict) else {}
            pending_preview = f"""
            <section class=\"review-card\">
              <div>
                <p class=\"eyebrow\">AI wijzigingspreview</p>
                <h2>Controleer voor toepassen</h2>
                <p>Wat verandert: {escape(str(summary.get('what_will_change', '')))}</p>
                <p>Scènes: {escape(', '.join(str(item) for item in summary.get('scenes', [])) or 'geen expliciete selectie')}</p>
                <p>Duurwijziging: {escape(str(summary.get('estimated_duration_change_seconds', 0)))}s · Nieuwe media nodig: {escape('ja' if summary.get('needs_media_search') else 'nee')} · Voice-over opnieuw nodig: {escape('ja' if summary.get('voiceover_regeneration_required') else 'nee')}</p>
              </div>
              <div class=\"actions\">
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-apply\"><button>Apply</button></form>
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-cancel\"><button class=\"secondary\">Cancel</button></form>
              </div>
            </section>
            """

        revision_rows = "".join(
            f"<option value=\"{escape(str(item.get('id', '')))}\">{escape(str(item.get('label', item.get('id', ''))))} · {escape(str(item.get('created_at', '')))}</option>"
            for item in revisions
            if isinstance(item, dict)
        )

        subtitle_entries = timeline.get("subtitle_entries", []) if isinstance(timeline.get("subtitle_entries"), list) else []
        selected_sub = next((entry for entry in subtitle_entries if isinstance(entry, dict) and str(entry.get("scene_id", "")) == selected_scene_id), {})

        notice_html = f"<section class=\"panel\"><p>{escape(notice)}</p></section>" if notice else ""

        return self.page(
            "Video bewerken",
            f"""
            {self.back_button(slug)}
            <nav class=\"crumb\"><a href=\"/projects/{escape(slug)}\">Project</a><span>/</span><strong>Video bewerken</strong></nav>
            {notice_html}
            <section class=\"review-player\"><div>
              <video id=\"editor-player\" controls preload=\"metadata\" src=\"/projects/{escape(slug)}/preview/video\"></video>
              <div class=\"actions\" style=\"justify-content:flex-start;margin-top:10px;\">
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/render\"><button>Nieuwe versie renderen</button></form>
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/undo\"><button class=\"secondary\" {'disabled' if not state.get('can_undo') else ''}>Undo</button></form>
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/redo\"><button class=\"secondary\" {'disabled' if not state.get('can_redo') else ''}>Redo</button></form>
                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/save-revision\" class=\"inline\"><input name=\"label\" placeholder=\"Save version label\" required><button class=\"ghost\">Save version</button></form>
              </div>
            </div><aside>
              <h2>Versie</h2>
              <p><strong>Actief:</strong> {escape(str(current_revision.get('label', 'Original')))}</p>
              <p class=\"muted\">{escape(str(current_revision.get('created_at', '')))}</p>
              <form method=\"post\" action=\"/projects/{escape(slug)}/editor/restore-revision\"><label>Restore previous version<select name=\"revision_id\">{revision_rows}</select></label><button class=\"secondary\">Restore</button></form>
            </aside></section>

            {pending_preview}

            <section class=\"panel\">
              <h2>Visual timeline</h2>
                            <p class=\"muted\">Tracks: Video/Images · Voice-over · Subtitles · Overlays/Titles · Optional audio</p>
                            <div class=\"timeline-toolbar\">
                                <form method=\"post\" action=\"/projects/{escape(slug)}/editor/batch\" class=\"timeline-batch-form\">
                                    <input type=\"hidden\" name=\"scene_id\" id=\"batch-scene-id\" value=\"{escape(selected_scene_id)}\">
                                    <input type=\"hidden\" name=\"selected_shots\" id=\"batch-selected-shots\" value=\"\">
                                    <button name=\"batch_action\" value=\"duplicate\" class=\"secondary\">Duplicate selectie</button>
                                    <button name=\"batch_action\" value=\"delete\" class=\"secondary\">Delete selectie</button>
                                    <button name=\"batch_action\" value=\"faster\" class=\"secondary\">Sneller ritme</button>
                                    <button name=\"batch_action\" value=\"slower\" class=\"secondary\">Langzamer ritme</button>
                                </form>
                                <div class=\"timeline-view-controls\">
                                    <label>Zoom timeline <input type=\"range\" min=\"1\" max=\"3\" step=\"0.1\" value=\"1\" id=\"timeline-zoom\"></label>
                                    <label><input type=\"checkbox\" id=\"timeline-snap\" checked> Snap</label>
                                </div>
                            </div>
              {''.join(timeline_html) if timeline_html else '<p>Geen timeline beschikbaar.</p>'}
            </section>

            {inspector}

            <section class=\"panel\">
              <h2>Media browser</h2>
              <form method=\"post\" action=\"/projects/{escape(slug)}/editor/search-media\" class=\"grid-form\">
                <input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\">
                <label class=\"wide\">Search better media<input name=\"query\" placeholder=\"Event-specific query (optional)\"></label>
                <button>Find alternatives</button>
              </form>
              {candidates_html}
              <p class=\"muted\">Upload own media blijft beschikbaar via het bestaande project media-scherm.</p>
            </section>

            <section class=\"panel\">
              <h2>Subtitle editor</h2>
              <p><strong>Status:</strong> {escape('Enabled' if subtitles_enabled else 'Disabled')}</p>
              <form method=\"post\" action=\"/projects/{escape(slug)}/editor/apply\" class=\"grid-form\">
                <input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\">
                <button name=\"action\" value=\"enable_subtitles\">Enable subtitles</button>
                <button name=\"action\" value=\"disable_subtitles\" class=\"secondary\">Disable all subtitles</button>
                <label>Preset size<input type=\"number\" name=\"subtitle_size\" min=\"12\" max=\"30\" value=\"{escape(str(subtitle_style.get('size', 16)))}\"></label>
                <label>Placement<select name=\"subtitle_placement\"><option value=\"bottom\">Bottom safe</option><option value=\"top\">Top safe</option></select></label>
                <button name=\"action\" value=\"set_subtitle_style\">Apply subtitle preset</button>
                <label class=\"wide\">Edit subtitle text for selected scene<textarea name=\"subtitle_text\" rows=\"3\">{escape(str(selected_sub.get('text', '')))}</textarea></label>
                <button name=\"action\" value=\"edit_subtitle\">Save subtitle text</button>
                <button name=\"action\" value=\"remove_subtitle\" class=\"secondary\">Remove subtitle from scene</button>
              </form>
            </section>

            <section class=\"panel\">
              <h2>AI edit instruction</h2>
              <form method=\"post\" action=\"/projects/{escape(slug)}/editor/ai-plan\" class=\"grid-form\">
                <input type=\"hidden\" name=\"scene_id\" value=\"{escape(selected_scene_id)}\"><input type=\"hidden\" name=\"shot_id\" value=\"{escape(selected_shot_id)}\">
                <label>Mode<select name=\"mode\"><option value=\"scene\">Edit selected scene</option><option value=\"documentary\">Edit entire documentary</option></select></label>
                <label class=\"wide\">Instruction<textarea name=\"instruction\" rows=\"4\" required placeholder=\"Replace this shot with courtroom arrival footage...\"></textarea></label>
                <button>Create AI edit plan</button>
              </form>
            </section>

            <script>
                            (() => {{
                                const player = document.getElementById('editor-player');
                                if (!player) return;
                                const bar = document.createElement('div');
                                bar.className = 'player-controls';
                                bar.innerHTML = `
                                    <button type="button" id="ep-play">Play</button>
                                    <button type="button" id="ep-pause" class="secondary">Pause</button>
                                    <button type="button" id="ep-frame" class="secondary">Frame +1</button>
                                    <button type="button" id="ep-full" class="secondary">Fullscreen</button>
                                    <label>Snelheid<select id="ep-speed"><option>0.75</option><option selected>1</option><option>1.25</option><option>1.5</option><option>2</option></select></label>
                                    <label>Volume<input id="ep-volume" type="range" min="0" max="1" step="0.01" value="1"></label>
                                    <label>Zoeken<input id="ep-seek" type="range" min="0" max="100" step="0.1" value="0"></label>
                                    <span class="status-pill" id="ep-scene">Scène: —</span>
                                `;
                                player.parentElement?.appendChild(bar);
                                const markers = Array.from(document.querySelectorAll('[data-scene]')).map(node => {{
                                    const scene = node.getAttribute('data-scene') || '';
                                    const titleNode = node.querySelector('h3');
                                    const raw = titleNode ? titleNode.textContent || '' : scene;
                                    return {{ start: Number((raw.split('·')[1] || '0').replace('s', '').trim()) || 0, title: raw.split('·')[0].trim() || scene }};
                                }}).sort((a, b) => a.start - b.start);
                                const sceneBadge = document.getElementById('ep-scene');
                                const updateScene = () => {{
                                    const t = player.currentTime || 0;
                                    let current = markers[0];
                                    for (const marker of markers) {{ if (marker.start <= t) current = marker; }}
                                    if (sceneBadge) sceneBadge.textContent = current ? `Scène: ${{current.title}}` : 'Scène: —';
                                }};
                                document.getElementById('ep-play')?.addEventListener('click', () => player.play());
                                document.getElementById('ep-pause')?.addEventListener('click', () => player.pause());
                                document.getElementById('ep-frame')?.addEventListener('click', () => {{ player.currentTime += 1 / 25; updateScene(); }});
                                document.getElementById('ep-full')?.addEventListener('click', () => player.requestFullscreen?.());
                                document.getElementById('ep-speed')?.addEventListener('change', event => {{
                                    const target = event.target;
                                    if (!(target instanceof HTMLSelectElement)) return;
                                    player.playbackRate = Number(target.value || '1');
                                }});
                                document.getElementById('ep-volume')?.addEventListener('input', event => {{
                                    const target = event.target;
                                    if (!(target instanceof HTMLInputElement)) return;
                                    player.volume = Number(target.value || '1');
                                }});
                                document.getElementById('ep-seek')?.addEventListener('input', event => {{
                                    const target = event.target;
                                    if (!(target instanceof HTMLInputElement)) return;
                                    if (!Number.isFinite(player.duration) || player.duration <= 0) return;
                                    player.currentTime = (Number(target.value || '0') / 100) * player.duration;
                                    updateScene();
                                }});
                                player.addEventListener('timeupdate', () => {{
                                    const seek = document.getElementById('ep-seek');
                                    if (seek instanceof HTMLInputElement && Number.isFinite(player.duration) && player.duration > 0) {{
                                        seek.value = String((player.currentTime / player.duration) * 100);
                                    }}
                                    updateScene();
                                }});
                            }})();

              document.querySelectorAll('[data-seek]').forEach(node => {{
                node.addEventListener('click', event => {{
                  const player = document.getElementById('editor-player');
                  if (!player) return;
                  const value = Number(node.getAttribute('data-seek') || '0');
                  if (!Number.isFinite(value)) return;
                  player.currentTime = value;
                }});
              }});

                            (() => {{
                                const checkboxes = Array.from(document.querySelectorAll('.batch-shot'));
                                const output = document.getElementById('batch-selected-shots');
                                const sceneInput = document.getElementById('batch-scene-id');
                                const zoom = document.getElementById('timeline-zoom');
                                const snap = document.getElementById('timeline-snap');
                                const cards = Array.from(document.querySelectorAll('.timeline-shot'));
                                const updateSelection = () => {{
                                    if (!(output instanceof HTMLInputElement)) return;
                                    const selected = checkboxes.filter(item => item instanceof HTMLInputElement && item.checked).map(item => item.value);
                                    output.value = selected.join(',');
                                    if (sceneInput instanceof HTMLInputElement && selected.length > 0) {{
                                        const firstCard = cards.find(card => selected.includes(String(card.getAttribute('data-shot-id') || '')));
                                        if (firstCard) sceneInput.value = String(firstCard.getAttribute('data-scene-id') || sceneInput.value);
                                    }}
                                }};
                                checkboxes.forEach(box => box.addEventListener('change', updateSelection));
                                updateSelection();

                                if (zoom instanceof HTMLInputElement) {{
                                    zoom.addEventListener('input', () => {{
                                        const scale = Number(zoom.value || '1');
                                        document.querySelectorAll('.timeline-grid').forEach(grid => {{
                                            if (grid instanceof HTMLElement) {{
                                                grid.style.gridTemplateColumns = `repeat(auto-fit,minmax(${{Math.round(170 * scale)}}px,1fr))`;
                                            }}
                                        }});
                                    }});
                                }}

                                let dragCard = null;
                                cards.forEach(card => {{
                                    card.addEventListener('dragstart', () => {{ dragCard = card; card.classList.add('dragging'); }});
                                    card.addEventListener('dragend', () => {{ card.classList.remove('dragging'); dragCard = null; }});
                                    card.addEventListener('dragover', event => {{
                                        event.preventDefault();
                                        const target = event.currentTarget;
                                        if (!(target instanceof HTMLElement) || !dragCard || target === dragCard) return;
                                        const parent = target.parentElement;
                                        if (!parent) return;
                                        const snapOn = !(snap instanceof HTMLInputElement) || snap.checked;
                                        if (!snapOn) return;
                                        parent.insertBefore(dragCard, target);
                                    }});
                                }});

                                document.querySelectorAll('[data-scene-grid]').forEach(grid => {{
                                    grid.addEventListener('dragleave', () => {{}});
                                    grid.addEventListener('drop', event => {{
                                        event.preventDefault();
                                        const container = event.currentTarget;
                                        if (!(container instanceof HTMLElement)) return;
                                        const scene = container.getAttribute('data-scene-grid') || '';
                                        const order = Array.from(container.querySelectorAll('.timeline-shot')).map(item => String(item.getAttribute('data-shot-id') || '')).filter(Boolean);
                                        if (!scene || !order.length) return;
                                        const form = document.createElement('form');
                                        form.method = 'post';
                                        form.action = `/projects/{escape(slug)}/editor/reorder`;
                                        form.innerHTML = `<input type="hidden" name="scene_id" value="${{scene}}"><input type="hidden" name="shot_order" value="${{order.join(',')}}">`;
                                        document.body.appendChild(form);
                                        form.submit();
                                    }});
                                }});
                            }})();
            </script>
            """,
        )

    def editor_apply(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        shot_id = self.form_value(form, "shot_id").strip()
        action = self.form_value(form, "action").strip()

        operation: dict[str, Any]
        label = "Editor wijziging"
        if action == "remove_shot":
            operation = {"type": "remove_shot", "scene_id": scene_id, "shot_id": shot_id}
            label = "Shot verwijderd"
        elif action == "duplicate_shot":
            operation = {"type": "duplicate_shot", "scene_id": scene_id, "shot_id": shot_id}
            label = "Shot gedupliceerd"
        elif action == "move_earlier":
            operation = {"type": "move_shot", "scene_id": scene_id, "shot_id": shot_id, "direction": "earlier"}
            label = "Shot verplaatst"
        elif action == "move_later":
            operation = {"type": "move_shot", "scene_id": scene_id, "shot_id": shot_id, "direction": "later"}
            label = "Shot verplaatst"
        elif action == "set_duration":
            operation = {
                "type": "set_shot_duration",
                "scene_id": scene_id,
                "shot_id": shot_id,
                "duration_seconds": float(self.form_value(form, "duration_seconds", "0") or 0),
            }
            label = "Shotduur aangepast"
        elif action == "split_shot":
            operation = {
                "type": "split_shot",
                "scene_id": scene_id,
                "shot_id": shot_id,
                "split_seconds": float(self.form_value(form, "split_seconds", "0") or 0),
            }
            label = "Shot gesplitst"
        elif action == "set_motion":
            operation = {
                "type": "set_motion",
                "scene_id": scene_id,
                "shot_id": shot_id,
                "motion": self.form_value(form, "motion"),
            }
            label = "Motion aangepast"
        elif action == "set_transition":
            operation = {"type": "set_transition", "scene_id": scene_id, "shot_id": shot_id, "transition": self.form_value(form, "transition")}
            label = "Transition aangepast"
        elif action == "set_crop":
            operation = {"type": "set_crop_mode", "scene_id": scene_id, "shot_id": shot_id, "crop": self.form_value(form, "crop")}
            label = "Crop aangepast"
        elif action == "add_overlay":
            operation = {"type": "add_overlay", "scene_id": scene_id, "shot_id": shot_id, "text": self.form_value(form, "overlay_text")}
            label = "Overlay toegevoegd"
        elif action == "set_composition":
            operation = {
                "type": "set_composition",
                "scene_id": scene_id,
                "shot_id": shot_id,
                "composition": self.form_value(form, "composition"),
            }
            label = "Compositie aangepast"
        elif action == "enable_subtitles":
            operation = {"type": "enable_subtitles", "scene_id": scene_id}
            label = "Subtitles ingeschakeld"
        elif action == "disable_subtitles":
            operation = {"type": "disable_subtitles", "scene_id": scene_id}
            label = "Subtitles uitgeschakeld"
        elif action == "set_subtitle_style":
            operation = {
                "type": "set_subtitle_style",
                "scene_id": scene_id,
                "size": int(self.form_value(form, "subtitle_size", "16") or 16),
                "placement": self.form_value(form, "subtitle_placement", "bottom"),
                "enabled": True,
            }
            label = "Subtitle preset toegepast"
        elif action == "edit_subtitle":
            operation = {"type": "edit_subtitle", "scene_id": scene_id, "text": self.form_value(form, "subtitle_text")}
            label = "Subtitle tekst aangepast"
        elif action == "remove_subtitle":
            operation = {"type": "remove_subtitle", "scene_id": scene_id}
            label = "Subtitle verwijderd"
        else:
            return self._editor_redirect(slug, scene_id, shot_id, "Onbekende editor-actie.")

        try:
            result = apply_operation(self.project_root(slug), operation)
            create_revision(
                self.project_root(slug),
                label=label,
                operation_type=str(operation.get("type", "edit")),
                operation_summary=label,
                duration_delta_seconds=float(result.get("duration_delta_seconds", 0.0) or 0.0),
            )
        except (EditorError, ValueError) as error:
            return self._editor_redirect(slug, scene_id, shot_id, str(error))

        return self._editor_redirect(slug, scene_id, shot_id, f"{label} opgeslagen")

    def editor_ai_plan(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        shot_id = self.form_value(form, "shot_id").strip()
        instruction = self.form_value(form, "instruction").strip()
        mode = self.form_value(form, "mode", "scene").strip().lower()
        if not instruction:
            return self._editor_redirect(slug, scene_id, shot_id, "AI instructie ontbreekt.")
        try:
            build_ai_edit_plan(
                self.project_root(slug),
                instruction,
                mode="documentary" if mode == "documentary" else "scene",
                selected_scene_id=scene_id,
                selected_shot_id=shot_id,
            )
        except EditorError as error:
            return self._editor_redirect(slug, scene_id, shot_id, str(error))
        return self._editor_redirect(slug, scene_id, shot_id, "AI plan klaar voor controle")

    def editor_ai_apply(self, slug: str) -> Response:
        try:
            apply_plan(self.project_root(slug))
        except EditorError as error:
            return self._editor_redirect(slug, notice=str(error))
        return self._editor_redirect(slug, notice="AI plan toegepast")

    def editor_ai_cancel(self, slug: str) -> Response:
        clear_pending_plan(self.project_root(slug))
        return self._editor_redirect(slug, notice="AI plan geannuleerd")

    def editor_undo(self, slug: str) -> Response:
        if not undo(self.project_root(slug)):
            return self._editor_redirect(slug, notice="Geen undo beschikbaar")
        return self._editor_redirect(slug, notice="Undo uitgevoerd")

    def editor_redo(self, slug: str) -> Response:
        if not redo(self.project_root(slug)):
            return self._editor_redirect(slug, notice="Geen redo beschikbaar")
        return self._editor_redirect(slug, notice="Redo uitgevoerd")

    def editor_save_revision(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        label = compact_whitespace(self.form_value(form, "label")) or "Nieuwe versie"
        create_revision(
            self.project_root(slug),
            label=label,
            operation_type="manual_save",
            operation_summary="Manual save version.",
            duration_delta_seconds=0.0,
        )
        return self._editor_redirect(slug, notice="Versie opgeslagen")

    def editor_restore_revision(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        revision_id = self.form_value(form, "revision_id").strip()
        if not revision_id:
            return self._editor_redirect(slug, notice="Selecteer een versie om te herstellen")
        if not restore_revision(self.project_root(slug), revision_id):
            return self._editor_redirect(slug, notice="Versie niet gevonden")
        return self._editor_redirect(slug, notice=f"Versie {revision_id} hersteld")

    def editor_search_media(self, slug: str, environ: dict[str, Any]) -> Response:
        root = self.project_root(slug)
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        shot_id = self.form_value(form, "shot_id").strip()
        query_override = compact_whitespace(self.form_value(form, "query"))

        scenes_manifest = self.read_manifest(root / "manifests" / "scenes.json")
        scenes = scenes_manifest.get("scenes", []) if isinstance(scenes_manifest.get("scenes"), list) else []
        scene = next((item for item in scenes if isinstance(item, dict) and str(item.get("id", "")) == scene_id), None)
        if not isinstance(scene, dict):
            return self._editor_redirect(slug, scene_id, shot_id, "Selecteer eerst een scène voor media zoeken")

        topic = query_override or compact_whitespace(str(scene.get("event_shown") or " ".join(str(item) for item in scene.get("events", [])) or scene.get("heading", "")))
        people = ", ".join(str(item) for item in scene.get("people", [])[:3])
        locations = ", ".join(str(item) for item in scene.get("locations", [])[:3])
        dates = ", ".join(str(item) for item in scene.get("dates", [])[:2])
        events = ", ".join(str(item) for item in scene.get("events", [])[:3])
        visual_direction = self.read_manifest(root / "manifests" / "visual_direction.json")
        direction_scene = next((item for item in visual_direction.get("scenes", []) if isinstance(item, dict) and str(item.get("scene_id", "")) == scene_id), {})
        shot = next((item for item in direction_scene.get("shots", []) if isinstance(item, dict) and str(item.get("id", "")) == shot_id), {})
        desired_media_type = str((shot.get("asset", {}) if isinstance(shot.get("asset"), dict) else {}).get("kind", "image"))

        result = discover_archival_media(
            root,
            DiscoveryQuery(
                topic=topic,
                people=people,
                locations=locations,
                dates=dates,
                events=events,
                desired_media_type=desired_media_type if desired_media_type in {"image", "video", "document"} else "image",
                shot_id=shot_id,
                composition=str(shot.get("composition", "single_frame")),
                content_reason=compact_whitespace(str(scene.get("media_requirements", "Event-specific evidence"))),
                limit_per_source=4,
            ),
            scene_id=scene_id,
        )
        media_manifest = self.read_manifest(root / "manifests" / "media_sources.json")
        assets = media_manifest.get("assets", []) if isinstance(media_manifest.get("assets"), list) else []
        result_ids = {str(item.get("id", "")) for item in result.get("assets", []) if isinstance(item, dict)}
        candidates = [item for item in assets if isinstance(item, dict) and str(item.get("id", "")) in result_ids]
        write_json(root / "manifests" / "editor_media_candidates.json", {"version": 1, "scene_id": scene_id, "shot_id": shot_id, "created_at": datetime.now(UTC).isoformat(), "candidates": candidates})
        return self._editor_redirect(slug, scene_id, shot_id, f"{len(candidates)} mediakandidaten gevonden")

    def editor_use_media(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        shot_id = self.form_value(form, "shot_id").strip()
        asset_id = self.form_value(form, "asset_id").strip()
        if not asset_id:
            return self._editor_redirect(slug, scene_id, shot_id, "Geen asset geselecteerd")
        try:
            apply_operation(self.project_root(slug), {"type": "replace_asset", "scene_id": scene_id, "shot_id": shot_id, "asset_id": asset_id})
            create_revision(self.project_root(slug), label="Media vervangen", operation_type="replace_asset", operation_summary="Shot asset replaced.")
        except EditorError as error:
            return self._editor_redirect(slug, scene_id, shot_id, str(error))
        return self._editor_redirect(slug, scene_id, shot_id, "Asset toegepast")

    def editor_batch(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        action = self.form_value(form, "batch_action").strip()
        selected_ids = [item.strip() for item in self.form_value(form, "selected_shots").split(",") if item.strip()]
        if not scene_id or not selected_ids:
            return self._editor_redirect(slug, notice="Selecteer eerst shots voor batchbewerking")
        root = self.project_root(slug)
        try:
            if action == "delete":
                for shot_id in selected_ids:
                    apply_operation(root, {"type": "remove_shot", "scene_id": scene_id, "shot_id": shot_id})
                label = f"{len(selected_ids)} shots verwijderd"
            elif action == "duplicate":
                for shot_id in selected_ids:
                    apply_operation(root, {"type": "duplicate_shot", "scene_id": scene_id, "shot_id": shot_id})
                label = f"{len(selected_ids)} shots gedupliceerd"
            elif action == "faster":
                for shot_id in selected_ids:
                    apply_operation(root, {"type": "set_motion", "scene_id": scene_id, "shot_id": shot_id, "motion": "push_in"})
                label = "Ritme versneld"
            elif action == "slower":
                for shot_id in selected_ids:
                    apply_operation(root, {"type": "set_motion", "scene_id": scene_id, "shot_id": shot_id, "motion": "slow_zoom"})
                label = "Ritme vertraagd"
            else:
                return self._editor_redirect(slug, scene_id, notice="Onbekende batchactie")
            create_revision(
                root,
                label=label,
                operation_type="batch_edit",
                operation_summary=label,
                duration_delta_seconds=0.0,
            )
        except (EditorError, ValueError) as error:
            return self._editor_redirect(slug, scene_id, notice=str(error))
        return self._editor_redirect(slug, scene_id, notice=label)

    def editor_reorder(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        scene_id = self.form_value(form, "scene_id").strip()
        order = [item.strip() for item in self.form_value(form, "shot_order").split(",") if item.strip()]
        if not scene_id or not order:
            return self._editor_redirect(slug, notice="Onvoldoende gegevens voor herordenen")
        root = self.project_root(slug)
        direction_path = root / "manifests" / "visual_direction.json"
        direction = self.read_manifest(direction_path)
        scenes = direction.get("scenes", []) if isinstance(direction.get("scenes"), list) else []
        target = next((item for item in scenes if isinstance(item, dict) and str(item.get("scene_id", "")) == scene_id), None)
        if not isinstance(target, dict):
            return self._editor_redirect(slug, scene_id, notice="Scène niet gevonden")
        shots = target.get("shots", []) if isinstance(target.get("shots"), list) else []
        if not shots:
            return self._editor_redirect(slug, scene_id, notice="Geen shots gevonden")
        shot_map = {str(item.get("id", "")): item for item in shots if isinstance(item, dict)}
        reordered = [shot_map[shot_id] for shot_id in order if shot_id in shot_map]
        remainder = [item for item in shots if isinstance(item, dict) and str(item.get("id", "")) not in set(order)]
        target["shots"] = reordered + remainder
        write_json(direction_path, direction)
        create_revision(
            root,
            label="Timeline herordend",
            operation_type="reorder",
            operation_summary="Timeline shot order updated.",
            duration_delta_seconds=0.0,
        )
        return self._editor_redirect(slug, scene_id, notice="Timeline opnieuw geordend")

    def editor_render(self, slug: str) -> Response:
        root = self.project_root(slug)
        try:
            write_progress_event(root, "started", "editor", "Preparing edit")
            write_progress_event(root, "started", "editor", "Updating scene")
            write_progress_event(root, "started", "editor", "Generating voice segment")
            write_progress_event(root, "started", "editor", "Building preview")
            write_progress_event(root, "started", "editor", "Rendering final video")
            project_manifest = self.read_manifest(root / "manifests" / "project.json")
            topic = str(project_manifest.get("topic", slug))
            generate_video_project(self.settings, topic, existing_project_root=root, respect_existing_direction=True)
            write_progress_event(root, "completed", "editor", "Complete")
            create_revision(root, label="Nieuwe render", operation_type="render", operation_summary="Rendered a new edited version.")
        except Exception as error:
            write_progress_event(root, "failed", "editor", f"Render failed: {error}")
            return self._editor_redirect(slug, notice=f"Render mislukt: {error}")
        return self._editor_redirect(slug, notice="Nieuwe versie gerenderd")

    def draft_review_page(self, slug: str) -> str:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.page("Project niet gevonden", "<section class=\"panel\"><p>Project niet gevonden.</p></section>")
        draft = create_review_draft(project_root)
        critic = self.read_manifest(project_root / "manifests/critic_report.json")
        director = self.read_manifest(project_root / "manifests/director_report.json")
        pending = self.read_manifest(project_root / "manifests/pending_revision_plan.json")
        scene_options = "".join(
            f'<option value="{escape(str(scene["id"]))}">Scène {escape(str(scene.get("index") or scene["id"]))}</option>'
            for scene in draft.get("scenes", [])
        )
        cards = []
        for scene in draft.get("scenes", []):
            claim_rows = "".join(
                f'<li><code>{escape(str(claim.get("id", "")))}</code> {escape(str(claim.get("text", "")))}</li>'
                for claim in scene.get("claims", [])
            ) or "<li>Geen claim gekoppeld.</li>"
            source_rows = "".join(
                f'<li>{escape(str(source.get("title", source.get("id", ""))))}</li>' for source in scene.get("sources", [])
            ) or "<li>Geen bron gekoppeld.</li>"
            media_rows = "".join(
                f'<li>{escape(str(media.get("title") or media.get("id") or media.get("path")))}</li>' for media in scene.get("media", [])
            ) or "<li>Geen screenshot of beeld gekoppeld.</li>"
            clip_rows = "".join(
                f'<li>{escape(str(clip.get("video_title") or clip.get("source_url") or clip.get("intake_id")))}</li>' for clip in scene.get("clips", [])
            ) or "<li>Geen videofragment gekoppeld.</li>"
            locked = scene.get("review_status") == "approved"
            thumb = f'/projects/{escape(slug)}/preview/thumbnail/{escape(str(scene["id"]))}'
            cards.append(f"""
            <article class="panel scene-review" id="scene-{escape(str(scene['id']))}">
              <div class="scene-review-head"><div><p class="eyebrow">Scène {escape(str(scene.get('index') or scene['id']))}</p><h2>{escape(str(scene.get('heading', '')))}</h2></div><span class="status-pill">{escape(str(scene.get('review_status', 'pending_review')))}</span></div>
              <img class="scene-thumb" src="{thumb}" alt="Thumbnail scène {escape(str(scene.get('index')))}">
              <details open><summary>Script en voice-over</summary><p>{escape(str(scene.get('script', '')))}</p><p><strong>Voice-over:</strong> {escape(str(scene.get('voice_over_text', '')))}</p><p><strong>Vertolking:</strong> {escape(str(scene.get('voice_over_delivery', '')))}</p></details>
              <details><summary>Claims en bronnen</summary><h3>Claims</h3><ul>{claim_rows}</ul><h3>Bronnen</h3><ul>{source_rows}</ul></details>
              <details><summary>Screenshots en videofragmenten</summary><h3>Beelden</h3><ul>{media_rows}</ul><h3>Clips</h3><ul>{clip_rows}</ul></details>
              <details><summary>Camerarichting en montageplan</summary><pre>{escape(json.dumps(scene.get('edit_plan', {}), indent=2))}</pre></details>
              <p><strong>Geschatte duur:</strong> {escape(str(scene.get('estimated_duration_seconds', 0)))} seconden</p>
              {'<p class="success">Deze scène is goedgekeurd en vergrendeld.</p>' if locked else f'<form method="post" action="/projects/{escape(slug)}/draft-review/{escape(str(scene["id"]))}/approve"><button type="submit">Scène goedkeuren</button></form>'}
            </article>
            """)
        history = "".join(
            f'<li><strong>{escape(str(item.get("command", "")))}</strong> — scènes {escape(", ".join(item.get("changed_scene_ids", [])))}'
            + "".join(f'<div class="revision-compare"><div><span>Oud fragment</span><p>{escape(str(review.get("old_fragment", {}).get("narration", "")))}</p></div><div><span>Voorgestelde wijziging</span><p>{escape(str(review.get("proposed_change", "")))}</p></div><div><span>Nieuwe versie</span><p>{escape(str(review.get("new_version", {}).get("narration", "")))}</p></div><p>Reden: {escape(str(review.get("reason", "")))} · Kosten: ${escape(str(review.get("cost_usd", 0)))}</p></div>' for review in item.get("visual_review", [])) + '</li>'
            for item in reversed(draft.get("revision_history", []))
        ) or "<li>Nog geen revisies.</li>"
        plan_panel = ""
        if pending and pending.get("status") == "awaiting_confirmation":
            plan_panel = f"""<section class="review-card"><div><p class="eyebrow">Wijzigingsplan</p><h2>Controleer vóór uitvoering</h2><p>Scènes: {escape(', '.join(pending.get('scene_ids', [])))} · Componenten: {escape(', '.join(pending.get('components', [])))} · Geschatte kosten: ${escape(str(pending.get('estimated_cost_usd', 0)))}</p></div><form method="post" action="/projects/{escape(slug)}/draft-review/execute-revision"><button>Plan bevestigen en uitvoeren</button></form></section>"""
        timeline = "".join(f'<a href="#scene-{escape(str(scene["id"]))}"><img src="/projects/{escape(slug)}/preview/thumbnail/{escape(str(scene["id"]))}" alt=""><span>{escape(str(scene.get("heading", scene["id"])))}</span></a>' for scene in draft.get("scenes", []))
        return self.page("Draft Review", f"""
        {self.back_button(slug)}
        <nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Draft Review</strong></nav>
        <section class="review-player"><div><video controls preload="metadata" src="/projects/{escape(slug)}/preview/video"></video><div class="review-timeline">{timeline}</div></div><aside><h2>Reviewscore</h2><p>Director: <strong>{escape(str(director.get('critic_score', '—')))}</strong></p><p>Critic: <strong>{escape(str(critic.get('overall_score', '—')))}</strong></p><p>Ondertitels zijn beschikbaar in de exportfase.</p></aside></section>
        {plan_panel}
        <section class="panel"><h2>Revisiechat</h2><p class="muted">Beschrijf natuurlijk wat je wilt wijzigen. Alleen de geselecteerde of genoemde scène wordt opnieuw beoordeeld.</p>
          <form method="post" action="/projects/{escape(slug)}/draft-review/revise" class="grid-form">
            <label>Scène <select name="scene_id"><option value="">Automatisch uit verzoek</option>{scene_options}</select></label>
            <label class="wide">Revisieverzoek <textarea name="command" rows="4" required placeholder="Maak de intro spannender."></textarea></label>
            <button type="submit">Wijzigingsplan maken</button>
          </form><h3>Revisiehistorie</h3><ul>{history}</ul>
        </section>{''.join(cards)}
        """)

    def revise_draft(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        try:
            revision_change_plan(self.project_root(slug), self.form_value(form, "command"), self.form_value(form, "scene_id") or None)
        except (ValueError, KeyError, RuntimeError) as error:
            return self.html(self.page("Revisie geblokkeerd", f'<section class="panel"><p>{escape(str(error))}</p></section>'), "409 Conflict")
        return self.redirect(f"/projects/{slug}/draft-review")

    def execute_revision(self, slug: str) -> Response:
        root = self.project_root(slug); plan = self.read_manifest(root / "manifests/pending_revision_plan.json")
        if not plan or plan.get("status") != "awaiting_confirmation":
            return self.html(self.page("Geen wijzigingsplan", '<section class="panel"><p>Er staat geen plan klaar.</p></section>'), "409 Conflict")
        try:
            revise_draft(root, str(plan["command"]), selected_scene_id=str(plan["scene_ids"][0]))
        except (ValueError, KeyError, RuntimeError) as error:
            return self.html(self.page("Revisie geblokkeerd", f'<section class="panel"><p>{escape(str(error))}</p></section>'), "409 Conflict")
        plan["status"] = "executed"; write_json(root / "manifests/pending_revision_plan.json", plan)
        return self.redirect(f"/projects/{slug}/draft-review")

    def approve_draft_scene(self, slug: str, scene_id: str) -> Response:
        try:
            approve_scene(self.project_root(slug), scene_id)
        except KeyError as error:
            return self.html(self.page("Scène niet gevonden", f'<section class="panel"><p>{escape(str(error))}</p></section>'), "404 Not Found")
        return self.redirect(f"/projects/{slug}/draft-review")

    def reference_intake_summary(self, project_root: Path, slug: str) -> str:
        intent = self.read_manifest(project_root / "manifests" / "reference_intent.json")
        if not intent:
            detail = "Upload een screenshot of fragment, of voer een YouTube-link in."
        else:
            detail = (
                f"{escape(str(intent.get('video_title') or intent.get('suspected_topic') or 'Match gevonden'))} — "
                f"{escape(str(intent.get('start_seconds', 0)))}s tot {escape(str(intent.get('end_seconds', 0)))}s — "
                f"confidence {escape(str(intent.get('confidence', 0)))}"
            )
        return f"""
        <section class="panel">
          <h2>Screenshot & interviewclip</h2>
          <p>{detail}</p>
          <a class="button" href="/projects/{escape(slug)}/reference-intake">Open clip-intake</a>
        </section>
        """

    def reference_intake_page(self, slug: str) -> str:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.page("Project Not Found", "<section class=\"panel\"><p>Project niet gevonden.</p></section>")
        intent = self.read_manifest(project_root / "manifests" / "reference_intent.json")
        alternatives = intent.get("alternative_matches", []) if isinstance(intent, dict) else []
        options = '<option value="0">Beste match</option>' + "".join(
            f'<option value="{index}">Alternatief {index}: {escape(str(item.get("title") or item.get("text") or "match"))} '
            f'({escape(str(item.get("confidence", 0)))})</option>'
            for index, item in enumerate(alternatives, start=1)
        )
        review = ""
        if intent:
            review = f"""
            <section class="panel">
              <h2>Gevonden fragment controleren</h2>
              <div class="summary-grid">
                <div><span>Video</span><strong>{escape(str(intent.get('video_title') or 'Onbekend'))}</strong></div>
                <div><span>Kanaal</span><strong>{escape(str(intent.get('channel') or 'Onbekend'))}</strong></div>
                <div><span>Start</span><strong>{escape(str(intent.get('start_seconds')))} sec</strong></div>
                <div><span>Einde</span><strong>{escape(str(intent.get('end_seconds')))} sec</strong></div>
              </div>
              <p><strong>Passage:</strong> {escape(str(intent.get('intended_interview_passage') or 'Nog niet herkend'))}</p>
              <form method="post" action="/projects/{escape(slug)}/reference-intake/{escape(str(intent.get('intake_id')))}/select" class="grid-form">
                <label>Match <select name="match_index">{options}</select></label>
                <label class="wide">Waarom is dit relevant? <textarea name="why_relevant" rows="3">{escape(str(intent.get('why_relevant', '')))}</textarea></label>
                <label class="wide"><input type="checkbox" name="user_selected_for_edit" value="yes" required> Door gebruiker geselecteerd voor montage</label>
                <button type="submit">Fragment bevestigen</button>
              </form>
              <p class="muted">Publicatierechten en eventuele Content ID-claims blijven de verantwoordelijkheid van de gebruiker.</p>
            </section>
            """
        return self.page("Clip-intake", f"""
        {self.back_button(slug)}
        <nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Clip-intake</strong></nav>
        <section class="panel">
          <h2>Screenshot, YouTube of lokaal fragment</h2>
          <form method="post" enctype="multipart/form-data" action="/projects/{escape(slug)}/reference-intake" class="grid-form">
            <label class="wide">Screenshot, video of audio <input type="file" name="reference_file" accept="image/png,image/jpeg,image/webp,video/*,audio/*"></label>
            <label class="wide">YouTube-URL <input type="url" name="source_url" placeholder="https://www.youtube.com/watch?v=..."></label>
            <label>Timestamp of range <input name="timestamp" placeholder="12:34 of 12:34-12:51"></label>
            <label class="wide">Zichtbare tekst / ondertitels <textarea name="visible_text" rows="3" placeholder="Vul dit aan wanneer lokale OCR niet beschikbaar is."></textarea></label>
            <label class="wide">Notitie en bedoelde relevantie <textarea name="note" rows="3"></textarea></label>
            <button type="submit">Bron en fragment herkennen</button>
          </form>
        </section>
        {review}
        """)

    def add_reference_intake(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.html(self.page("Project Not Found", "<section class=\"panel\"><p>Project niet gevonden.</p></section>"), "404 Not Found")
        form = self.read_form(environ)
        upload = form["reference_file"] if "reference_file" in form else None
        temp_path: Path | None = None
        filename = ""
        try:
            if upload is not None and not isinstance(upload, list) and getattr(upload, "filename", ""):
                filename = Path(str(upload.filename)).name
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(filename).suffix) as handle:
                    temp_path = Path(handle.name)
                    handle.write(upload.file.read())
            source_url = self.form_value(form, "source_url").strip()
            if temp_path is None and not source_url:
                raise ValueError("Upload een bestand of voer een YouTube-URL in.")
            create_reference_intake(
                project_root, source_url=source_url, local_path=temp_path, original_filename=filename,
                note=self.form_value(form, "note"), timestamp=self.form_value(form, "timestamp"),
                visible_text=self.form_value(form, "visible_text"),
            )
        except (OSError, ValueError) as error:
            return self.html(self.page("Intake mislukt", f"<section class=\"panel\"><p>{escape(str(error))}</p></section>"), "400 Bad Request")
        finally:
            if temp_path:
                temp_path.unlink(missing_ok=True)
        return self.redirect(f"/projects/{slug}/reference-intake")

    def select_reference(self, slug: str, intake_id: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        try:
            select_reference_match(
                self.project_root(slug), intake_id,
                match_index=int(self.form_value(form, "match_index", "0")),
                why_relevant=self.form_value(form, "why_relevant"),
                user_selected_for_edit=self.form_value(form, "user_selected_for_edit") == "yes",
            )
        except (OSError, ValueError, IndexError, KeyError) as error:
            return self.html(self.page("Selectie mislukt", f"<section class=\"panel\"><p>{escape(str(error))}</p></section>"), "400 Bad Request")
        return self.redirect(f"/projects/{slug}/reference-intake")

    def review_critic_feedback(self, slug: str, feedback_id: str, action: str) -> Response:
        project_root = self.project_root(slug)
        path = project_root / "manifests" / "critic_feedback.json"
        if not path.exists():
            return self.redirect(f"/projects/{slug}")
        data = read_json(path)
        for item in data.get("entries", []):
            if item.get("id") == feedback_id:
                item["approval_status"] = "approved" if action == "approve" else "rejected"
        write_json(path, data)
        return self.redirect(f"/projects/{slug}")

    def direction_reports(self, project_root: Path, slug: str) -> str:
        director = self.read_manifest(project_root / "manifests" / "director_report.json")
        critic = self.read_manifest(project_root / "manifests" / "critic_report.json")
        producer = self.read_manifest(project_root / "manifests" / "producer_blueprint.json")
        producer_report = self.read_manifest(project_root / "manifests" / "producer_report.json")
        if not director and not critic and not producer:
            return ""
        arc = producer.get("emotional_arc", []) if isinstance(producer, dict) else []
        retention = producer.get("retention_curve", []) if isinstance(producer, dict) else []
        sections = producer.get("sections", []) if isinstance(producer, dict) else []
        def bars(key: str, points: list[dict[str, Any]]) -> str:
            return "".join(
                f'<div class="chart-row"><span>{escape(str(point.get("scene_id", "")))}</span>'
                f'<i style="width:{max(0, min(100, float(point.get(key, 0))))}%"></i>'
                f'<strong>{escape(str(point.get(key, 0)))}</strong></div>' for point in points
            )
        ratio_totals = {
            key: round(sum(float(item.get("ratios", {}).get(key, 0)) for item in sections) / max(1, len(sections)) * 100, 1)
            for key in ("voice_over", "interview", "b_roll")
        }
        structure_rows = "".join(
            f"<tr><td>{escape(str(item.get('role', '')).replace('_', ' ').title())}</td>"
            f"<td>{escape(str(item.get('purpose', '')))}</td><td>{escape(str(item.get('visual_rhythm', '')))}</td>"
            f"<td>{escape(str(item.get('estimated_duration_seconds', 0)))} sec</td></tr>" for item in sections
        )
        producer_panel = f"""
        <section class="panel"><h2>Producer Blueprint</h2>
          <div class="summary-grid">
            <div><span>Voice-over</span><strong>{ratio_totals['voice_over']}%</strong></div>
            <div><span>Interview</span><strong>{ratio_totals['interview']}%</strong></div>
            <div><span>B-roll</span><strong>{ratio_totals['b_roll']}%</strong></div>
            <div><span>Producer-score</span><strong>{escape(str(producer_report.get('overall_score', '—')))}/100</strong></div>
          </div>
          <h3>Spanningsgrafiek</h3><div class="producer-chart">{bars('tension', arc)}</div>
          <h3>Emotiegrafiek</h3><div class="producer-chart emotion">{bars('emotion', arc)}</div>
          <h3>Geschatte retentiecurve</h3><div class="producer-chart retention">{bars('estimated_retention', retention)}</div>
          <h3>Documentairestructuur</h3><table><thead><tr><th>Sectie</th><th>Doel</th><th>Ritme</th><th>Duur</th></tr></thead><tbody>{structure_rows}</tbody></table>
        </section>""" if producer else ""
        scores = critic.get("scores", {}) if isinstance(critic, dict) else {}
        score_rows = "".join(
            f"<tr><td>{escape(str(name).replace('_', ' ').title())}</td><td>{escape(str(score))}/100</td></tr>"
            for name, score in scores.items()
        )
        criticism = "".join(f"<li>{escape(str(item))}</li>" for item in critic.get("main_criticisms", []))
        improvements = "".join(f"<li>{escape(str(item))}</li>" for item in director.get("improvements", [])) or "<li>Geen tweede montage nodig.</li>"
        feedback = self.read_manifest(project_root / "manifests" / "critic_feedback.json").get("entries", [])
        feedback_rows = "".join(
            f"<li>{escape(str(item.get('text', '')))} — {escape(str(item.get('approval_status', 'pending_review')))} "
            f"<form class=\"inline\" method=\"post\" action=\"/projects/{escape(slug)}/critic-feedback/{escape(str(item.get('id', '')))}/approve\"><button type=\"submit\">Goedkeuren</button></form> "
            f"<form class=\"inline\" method=\"post\" action=\"/projects/{escape(slug)}/critic-feedback/{escape(str(item.get('id', '')))}/reject\"><button type=\"submit\" class=\"secondary\">Afwijzen</button></form></li>"
            for item in feedback if item.get("approval_status") == "pending_review"
        )
        return f"""{producer_panel}
        <section class="panel"><h2>Director Report</h2>
          <p>Render {escape(str(director.get('render_number', '—')))} · {escape(str(director.get('shot_count', '—')))} shots</p>
          <ul>{improvements}</ul><p><strong>Besluit:</strong> {escape(str(director.get('rerender_reason', 'Nog niet gerenderd.')))}</p>
        </section>
        <section class="panel"><h2>Critic Report</h2>
          <p><strong>Totaalscore: {escape(str(critic.get('overall_score', '—')))}/100</strong></p>
          <table><tbody>{score_rows}</tbody></table><h3>Belangrijkste kritiekpunten</h3><ul>{criticism or '<li>Geen kritieke zwaktes.</li>'}</ul>
          {f'<h3>Feedback ter goedkeuring</h3><ul>{feedback_rows}</ul>' if feedback_rows else ''}
        </section>"""
    def project_advanced(self, slug: str) -> str:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.page("Project Niet Gevonden", f"<section class=\"panel\"><p>Geen project met de naam <code>{escape(slug)}</code>.</p></section>")
        ensure_research_manifests(project_root)
        project_manifest = self.read_manifest(project_root / "manifests" / "project.json")
        topic = str(project_manifest.get("topic", slug)) if isinstance(project_manifest, dict) else slug
        scenes = self.read_manifest(project_root / "manifests" / "scenes.json")
        scene_options = self.scene_options(scenes)
        return self.page(
            "Geavanceerde instellingen",
            f"""
            {self.back_button(slug)}
            <nav class="crumb"><a href="/">Dashboard</a><span>/</span><a href="/projects/{escape(slug)}">{escape(topic)}</a><span>/</span><strong>Geavanceerde instellingen</strong></nav>
            <div id="review-feedback" class="success" hidden></div><script>(()=>{{const n=new URLSearchParams(location.search).get('notice'),e=document.querySelector('#review-feedback');if(n){{e.textContent=n;e.hidden=false;}}}})();</script>
            <section class="panel project-head">
              <div>
                <h2>Geavanceerde instellingen</h2>
                <p class="muted">Technische controles voor bronnen, claims, media, manifesten en debugging.</p>
              </div>
              <a class="button ghost" href="/projects/{escape(slug)}">Terug naar productie</a>
            </section>
            {self.reasoning_settings_panel(project_root)}
            {self.production_provider_panel(project_root, slug)}
            {self.research_panel(project_root, slug)}
            {self.script_panel(project_root, slug)}
            {self.scenes_panel(project_root, slug)}
            <section class="panel">
              <h2>Archiefbeelden zoeken</h2>
              <form method="post" action="/projects/{escape(slug)}/discover" class="grid-form">
                <label>Onderwerp <input name="topic" value="{escape(topic)}"></label>
                <label>Personen <input name="people" placeholder="Namen, aliassen"></label>
                <label>Locaties <input name="locations" placeholder="Steden, straten, plekken"></label>
                <label>Datums <input name="dates" placeholder="Jaren of exacte datums"></label>
                <label>Gebeurtenissen <input name="events" placeholder="Zaakmomenten, zittingen, zoekacties"></label>
                <label>Limiet per bron <input name="limit" type="number" min="1" max="20" value="4"></label>
                <button type="submit">Onderzoek uitvoeren</button>
              </form>
            </section>
            {self.review_queue(project_root, slug)}
            <section class="panel">
              <h2>Beeld toevoegen</h2>
              <form method="post" action="/projects/{escape(slug)}/media" enctype="multipart/form-data" class="grid-form">
                <label>Afbeelding <input type="file" name="image" accept="image/*" required></label>
                <label>Koppel aan scène <select name="scenes">{scene_options}</select></label>
                <label>Bron-URL <input name="source_url" placeholder="https://..."></label>
                <label>Credit <input name="credit" placeholder="Archief / fotograaf"></label>
                <label>Licentie / gebruik <input name="license_notes" placeholder="Toestemming, licentie of fair-use notitie"></label>
                <label>Interne notities <input name="usage_notes" placeholder="Goedgekeurd voor concept"></label>
                <label class="wide">Relevantie voor scène <textarea name="scene_relevance" rows="3" placeholder="Waarom dit beeld past bij de gekozen scène"></textarea></label>
                <button type="submit">Beeld uploaden</button>
              </form>
            </section>
            {self.preview_section(project_root)}
            """,
        )

    def generate(self, slug: str) -> Response:
        return self.html(
            self.page(
                "Generation Blocked",
                "<section class=\"panel\"><h2>Blocked</h2><p>The old one-step generator is disabled in the dashboard for factual projects. Complete and approve research, script, scenes, media, and voice-over before rendering.</p></section>",
            ),
            "409 Conflict",
        )

    def review_action_card(self, project_root: Path, slug: str) -> str:
        workflow = self.read_manifest(project_root / "manifests" / "workflow.json")
        sources = self.read_manifest(project_root / "manifests" / "sources.json").get("sources", [])
        claims = self.read_manifest(project_root / "manifests" / "claims.json").get("claims", [])
        script = self.read_manifest(project_root / "manifests" / "script.json")
        media_assets = load_media_manifest(project_root).get("assets", [])

        source_count = len(sources) if isinstance(sources, list) else 0
        claim_count = len(claims) if isinstance(claims, list) else 0
        pending_media = [
            asset for asset in media_assets
            if isinstance(asset, dict) and asset.get("review_status") in {"pending_review", "rejected"}
        ] if isinstance(media_assets, list) else []

        if source_count or claim_count:
            if not workflow.get("research_approved"):
                approve_disabled = "" if approved_sources(project_root) and approved_claims(project_root) else "disabled"
                return f"""
                <section class="review-card">
                  <div>
                    <p class="eyebrow">Controle nodig</p>
                    <h2>Het onderzoek is klaar</h2>
                    <p>De AI heeft {source_count} bronnen onderzocht en {claim_count} feiten verzameld. Controleer de resultaten voordat het script wordt geschreven.</p>
                  </div>
                  <div class="actions">
                    <a class="button ghost" href="/projects/{escape(slug)}/advanced">Onderzoek bekijken</a>
                    <form method="post" action="/projects/{escape(slug)}/research/approve"><button type="submit" {approve_disabled}>Goedkeuren en doorgaan</button></form>
                  </div>
                </section>
                """

        if script.get("narration") and not workflow.get("script_approved"):
            return f"""
            <section class="review-card">
              <div>
                <p class="eyebrow">Controle nodig</p>
                <h2>Je script is klaar</h2>
                <p>Lees het script rustig door en pas het aan waar nodig voordat scènes en beelden worden gemaakt.</p>
              </div>
              <div class="actions">
                <a class="button ghost" href="/projects/{escape(slug)}/advanced">Script bekijken en bewerken</a>
                <form method="post" action="/projects/{escape(slug)}/script/approve"><button type="submit">Goedkeuren en doorgaan</button></form>
              </div>
            </section>
            """

        if pending_media:
            return f"""
            <section class="review-card">
              <div>
                <p class="eyebrow">Controle nodig</p>
                <h2>De beelden zijn verzameld</h2>
                <p>Er staan {len(pending_media)} beelden klaar voor controle. Bekijk of ze passen bij de video en of ze gebruikt mogen worden.</p>
              </div>
              <div class="actions">
                <a class="button ghost" href="/projects/{escape(slug)}/advanced">Beelden bekijken</a>
                <a class="button" href="/projects/{escape(slug)}/advanced">Goedkeuren en doorgaan</a>
              </div>
            </section>
            """

        if workflow.get("script_approved") and not workflow.get("scenes_generated"):
            return f"""
            <section class="review-card calm">
              <div>
                <p class="eyebrow">Volgende stap</p>
                <h2>Scènes maken</h2>
                <p>Het script is goedgekeurd. Maak nu de scène-indeling voor de video.</p>
              </div>
              <form method="post" action="/projects/{escape(slug)}/scenes/generate"><button type="submit">Video maken</button></form>
            </section>
            """

        return """
        <section class="review-card calm">
          <div>
            <p class="eyebrow">Status</p>
            <h2>Geen controle nodig</h2>
            <p>De productie wacht op de volgende automatische stap of op invoer via geavanceerde instellingen.</p>
          </div>
        </section>
        """

    def add_source(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        form = self.read_form(environ)
        add_source(
            project_root,
            title=self.form_value(form, "title"),
            url=self.form_value(form, "url"),
            publisher=self.form_value(form, "publisher"),
            publication_date=self.form_value(form, "publication_date"),
            source_type=self.form_value(form, "source_type", "article"),
            reliability_notes=self.form_value(form, "reliability_notes"),
        )
        return self.redirect(f"/projects/{slug}")

    def add_claim(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        form = self.read_form(environ)
        add_claim(
            project_root,
            text=self.form_value(form, "text"),
            source_ids=[item.strip() for item in self.form_value(form, "source_ids").split(",") if item.strip()],
            confidence=self.form_value(form, "confidence", "needs_review"),
            date=self.form_value(form, "date"),
            people=self.form_value(form, "people"),
            locations=self.form_value(form, "locations"),
            events=self.form_value(form, "events"),
        )
        return self.redirect(f"/projects/{slug}")

    def run_automated_research(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        if not self.settings.pipeline.get("allow_paid_providers", False):
            return self.html(self.page("Automated Research Blocked", "<section class=\"panel\"><h2>Paid providers are disabled</h2><p>No API was called.</p></section>"), "409 Conflict")
        form = self.read_form(environ)
        topic = self.form_value(form, "topic") or str(self.read_manifest(project_root / "manifests" / "project.json").get("topic", slug))
        research_settings = self.settings.providers.get("research", {})
        tavily_settings = research_settings.get("tavily", {}) if isinstance(research_settings, dict) else {}
        provider = tavily_config_from_settings(tavily_settings)
        result = provider.research(project_root, topic)
        if not result.get("ok"):
            return self.html(
                self.page(
                    "Automated Research Blocked",
                    f"<section class=\"panel\"><h2>Automated research not run</h2><p>{escape(str(result.get('message', 'Unknown error')))}</p><p>No sources or claims were approved automatically.</p></section>",
                ),
                "409 Conflict",
            )
        return self.redirect(f"/projects/{slug}")

    def review_research_item(self, slug: str, kind: str, item_id: str, action: str) -> Response:
        status = "approved" if action == "approve" else "rejected"
        project_root = self.project_root(slug)
        if not project_root.is_dir() or not item_id:
            return self.html(self.page("Ongeldige beoordeling", '<section class="panel"><h2>Bron of claim niet gevonden</h2><p>Er is niets gewijzigd.</p></section>'), "404 Not Found")
        if kind == "source":
            found, changed = review_item(project_root, "sources.json", "sources", item_id, status)
        else:
            found, changed = review_item(project_root, "claims.json", "claims", item_id, status)
        if not found:
            return self.html(self.page("Ongeldige beoordeling", '<section class="panel"><h2>Bron of claim niet gevonden</h2><p>De opgegeven ID bestaat niet; er is niets gewijzigd.</p></section>'), "404 Not Found")
        label = "Bron" if kind == "source" else "Claim"
        decision = "goedgekeurd" if status == "approved" else "afgewezen"
        if changed:
            write_progress_event(project_root, "completed", "item_review", f"{label} {decision}", item_id=item_id, review_status=status)
        return self.redirect(f"/projects/{quote(slug)}/dossier-review?notice={quote(f'{label} {decision}') }#{quote(kind)}-{quote(item_id)}")

    def approve_research(self, slug: str) -> Response:
        project_root = self.project_root(slug)
        if not approve_research(project_root):
            return self.html(
                self.page(
                    "Research Not Ready",
                    "<section class=\"panel\"><h2>Research not ready</h2><p>Approve at least one source and one source-backed claim before approving research.</p></section>",
                ),
                "409 Conflict",
            )
        self.resume_managed_production(project_root)
        return self.redirect(f"/projects/{slug}")

    def generate_script(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        try:
            minutes = int(self.form_value(form, "target_duration_minutes", "10"))
            project_root = self.project_root(slug)
            script = generate_script(
                project_root,
                max(1, min(60, minutes)),
                reasoning_provider=reasoning_provider_from_settings(self.settings.providers.get("reasoning", {})),
            )
            architecture_path = project_root / "manifests" / "story_architecture.json"
            if architecture_path.exists():
                workflow = load_manifest(project_root, "workflow.json")
                quality = validate_script(script, approved_claims(project_root), read_json(architecture_path), {**self.settings.script, "language": workflow.get("language", script.get("language", "English"))})
                _persist_candidate(project_root, 1, script, quality)
                if not quality["pass"]:
                    raise RuntimeError("Script rejected: " + "; ".join(quality["failure_reasons"]))
                _promote_candidate(project_root, 1, script, quality)
        except Exception as error:
            return self.html(self.page("Script Blocked", f"<section class=\"panel\"><h2>Script blocked</h2><p>{escape(str(error))}</p></section>"), "409 Conflict")
        return self.redirect(f"/projects/{slug}")

    def save_script(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        try:
            save_script_edit(self.project_root(slug), self.form_value(form, "narration"))
        except Exception as error:
            return self.html(self.page("Script Save Failed", f"<section class=\"panel\"><p>{escape(str(error))}</p></section>"), "409 Conflict")
        return self.redirect(f"/projects/{slug}")

    def approve_script(self, slug: str) -> Response:
        project_root = self.project_root(slug)
        if not approve_script(project_root):
            return self.html(self.page("Script Not Ready", "<section class=\"panel\"><p>Save a script draft before approval.</p></section>"), "409 Conflict")
        self.resume_managed_production(project_root)
        return self.redirect(f"/projects/{slug}")

    def generate_scenes(self, slug: str) -> Response:
        try:
            generate_scenes(
                self.project_root(slug),
                reasoning_provider=reasoning_provider_from_settings(self.settings.providers.get("reasoning", {})),
            )
        except Exception as error:
            return self.html(self.page("Scenes Blocked", f"<section class=\"panel\"><h2>Scenes blocked</h2><p>{escape(str(error))}</p></section>"), "409 Conflict")
        return self.redirect(f"/projects/{slug}")

    def discover_media(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.html(self.page("Project Not Found", f"<section class=\"panel\"><p>No project named <code>{escape(slug)}</code>.</p></section>"), "404 Not Found")
        form = self.read_form(environ)
        limit_value = self.form_value(form, "limit", "4")
        try:
            limit = max(1, min(20, int(limit_value)))
        except ValueError:
            limit = 4
        query = DiscoveryQuery(
            topic=self.form_value(form, "topic"),
            people=self.form_value(form, "people"),
            locations=self.form_value(form, "locations"),
            dates=self.form_value(form, "dates"),
            events=self.form_value(form, "events"),
            limit_per_source=limit,
        )
        discover_archival_media(project_root, query)
        return self.redirect(f"/projects/{slug}")

    def review_media(self, slug: str, media_id: str, action: str) -> Response:
        project_root = self.project_root(slug)
        status = "approved" if action == "approve" else "rejected"
        before = next((item for item in load_media_manifest(project_root).get("assets", []) if isinstance(item, dict) and str(item.get("id")) == media_id), None)
        if not before:
            return self.html(self.page("Ongeldige beoordeling", '<section class="panel"><h2>Media-asset niet gevonden</h2><p>Er is niets gewijzigd.</p></section>'), "404 Not Found")
        changed = action != "search" and before.get("review_status") != status
        if action != "search":
            update_image_review(project_root, media_id, status)
        if action in {"replace", "search"}:
            discover_project_scene_media(project_root, limit_per_source=4)
        if changed:
            write_progress_event(project_root, "completed", "media_review", f"Media {'goedgekeurd' if status == 'approved' else 'afgewezen'}", item_id=media_id, review_status=status)
            self.resume_managed_production(project_root)
        notice = "Verder gezocht" if action == "search" else "Vervanging gezocht" if action == "replace" else "Media goedgekeurd" if status == "approved" else "Media afgewezen"
        return self.redirect(f"/projects/{quote(slug)}/advanced?notice={quote(notice)}#media-{quote(media_id)}")

    def add_media(self, slug: str, environ: dict[str, Any]) -> Response:
        project_root = self.project_root(slug)
        if not project_root.is_dir():
            return self.html(self.page("Project Not Found", f"<section class=\"panel\"><p>No project named <code>{escape(slug)}</code>.</p></section>"), "404 Not Found")
        form = self.read_form(environ)
        upload = form["image"] if "image" in form else None
        if upload is None or isinstance(upload, list) or not getattr(upload, "filename", ""):
            return self.html(self.page("Missing Image", "<section class=\"panel\"><p>Choose an image to upload.</p></section>"), "400 Bad Request")

        suffix = Path(str(upload.filename)).suffix or ".jpg"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            temp_path = Path(handle.name)
            data = upload.file.read()
            handle.write(data)
        try:
            add_image_asset(
                project_root,
                temp_path,
                source_url=self.form_value(form, "source_url"),
                credit=self.form_value(form, "credit"),
                license_notes=self.form_value(form, "license_notes"),
                usage_notes=self.form_value(form, "usage_notes"),
                scene_relevance=self.form_value(form, "scene_relevance"),
                scene_ids=[self.form_value(form, "scenes")],
                media_id=Path(str(upload.filename)).stem,
            )
        finally:
            temp_path.unlink(missing_ok=True)
        return self.redirect(f"/projects/{slug}")

    def download_final(self, slug: str) -> Response:
        final_video = self.project_root(slug) / "exports" / "final_video.mp4"
        if not final_video.is_file():
            return self.html(self.page("No Video", "<section class=\"panel\"><p>No final MP4 exists for this project yet.</p></section>"), "404 Not Found")
        return (
            "200 OK",
            [
                ("Content-Type", "video/mp4"),
                ("Content-Disposition", f'inline; filename="{slug}-final_video.mp4"'),
                ("Content-Length", str(final_video.stat().st_size)),
            ],
            final_video.read_bytes(),
        )

    def download_export_item(self, slug: str, item: str) -> Response:
        root = self.project_root(slug)
        manifests = root / "manifests"
        project = self.read_manifest(manifests / "project.json")
        title = str(project.get("topic", slug))

        if item == "script":
            script = self.read_manifest(manifests / "script.json")
            body = str(script.get("narration", "")).strip() or "Nog geen script beschikbaar."
            data = body.encode("utf-8")
            return "200 OK", [("Content-Type", "text/plain; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-script.txt"')], data

        if item == "subtitles":
            subtitles = self.read_manifest(manifests / "subtitles.json")
            entries = subtitles.get("entries", []) if isinstance(subtitles.get("entries"), list) else []
            lines: list[str] = []
            for index, entry in enumerate(entries, start=1):
                if not isinstance(entry, dict):
                    continue
                start = str(entry.get("start", entry.get("start_seconds", "0")))
                end = str(entry.get("end", entry.get("end_seconds", "0")))
                text = str(entry.get("text", "")).strip()
                lines.extend([str(index), f"{start} --> {end}", text, ""])
            payload = "\n".join(lines).strip() or "Nog geen ondertitels beschikbaar."
            return "200 OK", [("Content-Type", "text/plain; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-subtitles.srt"')], payload.encode("utf-8")

        if item == "assets-list":
            assets = self.read_manifest(manifests / "media_sources.json").get("assets", [])
            rows = ["id\ttitle\ttype\tprovider\tlicense\tpath"]
            if isinstance(assets, list):
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    rows.append("\t".join([
                        str(asset.get("id", "")),
                        str(asset.get("title", "")),
                        str(asset.get("type", asset.get("kind", ""))),
                        str(asset.get("provider", asset.get("source", ""))),
                        str(asset.get("license", asset.get("license_notes", ""))),
                        str(asset.get("path", "")),
                    ]))
            return "200 OK", [("Content-Type", "text/tab-separated-values; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-assets.tsv"')], "\n".join(rows).encode("utf-8")

        if item == "source-list":
            sources = self.read_manifest(manifests / "sources.json").get("sources", [])
            rows = ["id\ttitle\turl\tpublisher\tpublication_date\tstatus"]
            if isinstance(sources, list):
                for source in sources:
                    if not isinstance(source, dict):
                        continue
                    rows.append("\t".join([
                        str(source.get("id", "")),
                        str(source.get("title", "")),
                        str(source.get("url", "")),
                        str(source.get("publisher", "")),
                        str(source.get("publication_date", "")),
                        str(source.get("review_status", "")),
                    ]))
            return "200 OK", [("Content-Type", "text/tab-separated-values; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-sources.tsv"')], "\n".join(rows).encode("utf-8")

        if item == "fact-report":
            claims = self.read_manifest(manifests / "claims.json").get("claims", [])
            statuses: dict[str, int] = {}
            if isinstance(claims, list):
                for claim in claims:
                    if not isinstance(claim, dict):
                        continue
                    status = str(claim.get("review_status", "pending_review"))
                    statuses[status] = statuses.get(status, 0) + 1
            report = {
                "title": title,
                "project": slug,
                "total_claims": len(claims) if isinstance(claims, list) else 0,
                "status_breakdown": statuses,
                "generated_at": datetime.now(UTC).isoformat(),
            }
            data = json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8")
            return "200 OK", [("Content-Type", "application/json; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-fact-report.json"')], data

        if item == "production-report":
            critic = self.read_manifest(manifests / "critic_report.json")
            director = self.read_manifest(manifests / "director_report.json")
            payload = {
                "title": title,
                "project": slug,
                "critic_report": critic,
                "director_report": director,
                "generated_at": datetime.now(UTC).isoformat(),
            }
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return "200 OK", [("Content-Type", "application/json; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-production-report.json"')], data

        if item == "thumbnail":
            candidate = root / "assets" / "thumbnails" / "scene-01.png"
            if not candidate.is_file():
                # Generate a thumbnail from the final video if it exists
                final_vid = root / "exports" / "final_video.mp4"
                if final_vid.is_file():
                    import subprocess as _sp
                    candidate.parent.mkdir(parents=True, exist_ok=True)
                    result = _sp.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                         "-i", str(final_vid), "-ss", "00:00:02", "-vframes", "1",
                         "-vf", "scale=640:360", str(candidate)],
                        capture_output=True,
                    )
                    if result.returncode != 0 or not candidate.is_file():
                        # Fallback: generate coloured SVG placeholder
                        svg = (
                            f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360">'
                            f'<rect width="100%" height="100%" fill="#172026"/>'
                            f'<text x="30" y="310" fill="#eef4f1" font-size="28" font-family="sans-serif">{escape(slug)}</text>'
                            f'</svg>'
                        )
                        return "200 OK", [("Content-Type", "image/svg+xml"), ("Content-Disposition", f'attachment; filename="{slug}-thumbnail.svg"')], svg.encode("utf-8")
            if candidate.is_file():
                return "200 OK", [("Content-Type", "image/png"), ("Content-Disposition", f'attachment; filename="{slug}-thumbnail.png"')], candidate.read_bytes()
            return self.html(self.page("Geen thumbnail", '<section class="panel"><p>Er is nog geen thumbnail beschikbaar. Render eerst het project.</p></section>'), "404 Not Found")

        if item == "title-description":
            youtube = self.read_manifest(manifests / "youtube_draft.json")
            payload = {
                "title": str(youtube.get("title", title)),
                "description": str(youtube.get("description", "")),
                "tags": youtube.get("tags", []),
            }
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            return "200 OK", [("Content-Type", "application/json; charset=utf-8"), ("Content-Disposition", f'attachment; filename="{slug}-metadata.json"')], data

        return self.html(self.page("Export niet gevonden", '<section class="panel"><p>Deze export is niet beschikbaar.</p></section>'), "404 Not Found")

    def video_preview(self, slug: str) -> Response:
        return self.download_final(slug)

    def scene_thumbnail(self, slug: str, scene_id: str) -> Response:
        root = self.project_root(slug); scenes = self.read_manifest(root / "manifests/scenes.json").get("scenes", [])
        scene = next((item for item in scenes if str(item.get("id")) == scene_id), None)
        if scene is None:
            return self.html(self.page("Niet gevonden", '<section class="panel"><p>Thumbnail niet gevonden.</p></section>'), "404 Not Found")
        index = int(scene.get("index") or scenes.index(scene) + 1)
        candidates = [root / f"assets/thumbnails/scene-{index:02}.png"]
        media = load_media_manifest(root).get("assets", [])
        candidates.extend(root / str(asset.get("path")) for asset in media if scene_id in {str(item) for item in asset.get("mapped_scenes", [])})
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return "200 OK", [("Content-Type", "image/svg+xml")], f'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="100%" height="100%" fill="#263e35"/><text x="30" y="320" fill="white" font-size="28">{escape(str(scene.get("heading", scene_id)))}</text></svg>'.encode()
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        return "200 OK", [("Content-Type", mime), ("Content-Length", str(path.stat().st_size))], path.read_bytes()

    def youtube_draft_page(self, slug: str) -> str:
        draft = youtube_draft(self.project_root(slug)); chapters = "\n".join(f"{item['start_seconds']} — {item['title']}" for item in draft.get("chapters", []))
        return self.page("YouTube draft", f"""{self.back_button(slug)}<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>YouTube draft</strong></nav><section class="panel"><h2>Veilige export</h2><form method="post" action="/projects/{escape(slug)}/youtube-draft/save" class="grid-form"><label class="wide">Titel<input name="title" value="{escape(str(draft.get('title', '')))}"></label><label class="wide">Beschrijving<textarea name="description" rows="8">{escape(str(draft.get('description', '')))}</textarea></label><label class="wide">Hoofdstukken<textarea rows="8" readonly>{escape(chapters)}</textarea></label><label>Tags<input name="tags" value="{escape(', '.join(draft.get('tags', [])))}"></label><label>Privacy<select name="privacy_status"><option value="private">Private</option><option value="draft">Draft</option></select></label><p class="muted">Thumbnail, ondertitels en videobestand worden automatisch gekoppeld vanuit je project.</p><button>Draft opslaan</button></form></section><section class="review-card"><div><h2>Upload naar YouTube</h2><p>Publicatie gebeurt nooit automatisch. Alleen private/draft upload kan na expliciete bevestiging worden klaargezet.</p></div><form method="post" action="/projects/{escape(slug)}/youtube-draft/confirm-upload"><label><input type="checkbox" name="confirm" value="yes" required> Ik bevestig private/draft upload</label><button>Upload expliciet bevestigen</button></form></section>""")

    def save_youtube_draft(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ); root = self.project_root(slug); draft = youtube_draft(root)
        draft.update({"title": self.form_value(form, "title"), "description": self.form_value(form, "description"), "tags": [item.strip() for item in self.form_value(form, "tags").split(",") if item.strip()], "privacy_status": self.form_value(form, "privacy_status", "private"), "status": "draft", "upload_confirmed": False})
        write_json(root / "manifests/youtube_draft.json", draft); return self.redirect(f"/projects/{slug}/youtube-draft")

    def confirm_youtube_upload(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        if self.form_value(form, "confirm") != "yes":
            return self.html(self.page("Bevestiging vereist", '<section class="panel"><p>Expliciete bevestiging ontbreekt.</p></section>'), "409 Conflict")
        root = self.project_root(slug); draft = youtube_draft(root); draft["upload_confirmed"] = True; draft["status"] = "ready_for_private_upload"; draft["privacy_status"] = "private" if draft.get("privacy_status") not in {"private", "draft"} else draft["privacy_status"]; write_json(root / "manifests/youtube_draft.json", draft)
        return self.redirect(f"/projects/{slug}/youtube-draft")

    def media_preview(self, slug: str, media_id: str) -> Response:
        project_root = self.project_root(slug)
        manifest = load_media_manifest(project_root)
        assets = manifest.get("assets", [])
        if isinstance(assets, list):
            for asset in assets:
                if isinstance(asset, dict) and str(asset.get("id")) == media_id:
                    path = project_root / str(asset.get("path", ""))
                    if path.is_file() and path.resolve().is_relative_to(project_root.resolve()):
                        content_type = {
                            ".png": "image/png", ".webp": "image/webp", ".mp4": "video/mp4",
                            ".webm": "video/webm", ".mov": "video/quicktime",
                        }.get(path.suffix.lower(), "image/jpeg")
                        return (
                            "200 OK",
                            [("Content-Type", content_type), ("Content-Length", str(path.stat().st_size))],
                            path.read_bytes(),
                        )
        return self.html(self.page("Not Found", "<section class=\"panel\"><p>Preview not found.</p></section>"), "404 Not Found")

    def preview_section(self, project_root: Path) -> str:
        manifest_names = [
            "sources.json",
            "research.json",
            "timeline.json",
            "claims.json",
            "workflow.json",
            "script.json",
            "scenes.json",
            "visual_prompts.json",
            "media_sources.json",
            "media_discovery.json",
            "render_plan.json",
            "research_plan.json",
            "source_analysis.json",
            "dossier.json",
            "narrative_outline.json",
            "reasoning_usage.json",
            "review_draft.json",
            "selective_regeneration.json",
            "producer_revision_review.json",
            "director_revision_review.json",
            "critic_revision_review.json",
        ]
        tabs = []
        for name in manifest_names:
            path = project_root / "manifests" / name
            if path.exists():
                body = escape(json.dumps(read_json(path), indent=2))
            else:
                body = "Not generated yet."
            tabs.append(f"<details open><summary>{escape(name)}</summary><pre>{body}</pre></details>")
        return f"<section class=\"panel\"><h2>Manifest Preview</h2>{''.join(tabs)}</section>"

    def reasoning_settings_panel(self, project_root: Path) -> str:
        settings = self.settings.providers.get("reasoning", {})
        usage = self.read_manifest(project_root / "manifests" / "reasoning_usage.json")
        enabled = "Ja" if settings.get("enabled") else "Nee"
        dry_run = "Ja" if settings.get("dry_run") else "Nee"
        return f"""
        <section class="panel">
          <h2>OpenAI reasoning</h2>
          <div class="summary-grid">
            <div><span>Provider</span><strong>{escape(str(settings.get('provider', 'openai')))}</strong></div>
            <div><span>Ingeschakeld</span><strong>{enabled}</strong></div>
            <div><span>Model</span><strong>{escape(str(settings.get('model', 'gpt-5.5')))}</strong></div>
            <div><span>Reasoning effort</span><strong>{escape(str(settings.get('reasoning_effort', 'medium')))}</strong></div>
            <div><span>Max output tokens</span><strong>{escape(str(settings.get('max_output_tokens', 4000)))}</strong></div>
            <div><span>Max bronnen</span><strong>{escape(str(settings.get('max_sources_analyzed', 8)))}</strong></div>
            <div><span>Budget</span><strong>${escape(str(settings.get('per_project_spending_limit_usd', 2.0)))}</strong></div>
            <div><span>Geschat gebruikt</span><strong>${escape(str(usage.get('estimated_total_cost_usd', 0)))}</strong></div>
            <div><span>Dry-run</span><strong>{dry_run}</strong></div>
          </div>
          <p class="muted">OPENAI_API_KEY wordt alleen uit omgevingsvariabelen gelezen en wordt hier niet getoond of opgeslagen.</p>
        </section>
        """

    def production_provider_panel(self, project_root: Path, slug: str) -> str:
        config = self.read_manifest(project_root / "manifests" / "provider_config.json")
        tasks = config.get("tasks", {}) if isinstance(config, dict) else {}
        checked = "checked" if config.get("external_calls_enabled") else ""
        return f"""
        <section class="panel"><h2>Production providers</h2>
          <p class="muted">Keuzevolgordes zijn kommagescheiden. Externe calls blijven uit tot ze hier expliciet worden ingeschakeld; omgevingssleutels worden nooit opgeslagen.</p>
          <form method="post" action="/projects/{escape(slug)}/providers/configure" class="grid-form">
            <label>Projectbudget USD <input name="budget_usd" type="number" min="0" step="0.01" value="{escape(str(config.get('budget_usd', 1.0)))}"></label>
            <label>Retries <input name="retries" type="number" min="0" max="5" value="{escape(str(config.get('retries', 2)))}"></label>
            <label class="wide"><input type="checkbox" name="external_calls_enabled" value="yes" {checked}> Externe production-providercalls voor dit project inschakelen</label>
            <label>Producer <input name="producer_blueprint" value="{escape(', '.join(tasks.get('producer_blueprint', [])))}" placeholder="claude_text, openai_text, local_text"></label>
            <label>Director <input name="director_plan" value="{escape(', '.join(tasks.get('director_plan', [])))}" placeholder="openai_text, gemini_text, local_text"></label>
            <label>Critic <input name="critic_review" value="{escape(', '.join(tasks.get('critic_review', [])))}" placeholder="gemini_text, openai_text, local_text"></label>
            <label>Voice-over <input name="voice_over" value="{escape(', '.join(tasks.get('voice_over', [])))}" placeholder="elevenlabs, openai_tts"></label>
            <label>Scènebeelden <input name="scene_image" value="{escape(', '.join(tasks.get('scene_image', [])))}" placeholder="openai_images, gemini_images, flux"></label>
            <button type="submit">Providerkeuze opslaan</button>
          </form>
        </section>"""

    def configure_project_providers(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        try:
            budget = max(0.0, float(self.form_value(form, "budget_usd", "1.0")))
            retries = max(0, min(5, int(self.form_value(form, "retries", "2"))))
        except ValueError:
            return self.html(self.page("Ongeldige providerconfiguratie", '<section class="panel"><p>Budget en retries moeten getallen zijn.</p></section>'), "400 Bad Request")
        tasks = {}
        for task in ("producer_blueprint", "director_plan", "critic_review", "voice_over", "scene_image"):
            tasks[task] = [item.strip() for item in self.form_value(form, task).split(",") if item.strip()]
        write_json(self.project_root(slug) / "manifests" / "provider_config.json", {
            "version": 1, "external_calls_enabled": self.form_value(form, "external_calls_enabled") == "yes",
            "budget_usd": budget, "retries": retries, "cache_enabled": True, "tasks": tasks,
        })
        return self.redirect(f"/projects/{slug}/advanced")

    def workflow_panel(self, project_root: Path, slug: str) -> str:
        stages = self.simple_progress(project_root)
        items = "".join(
            f"<li class=\"{escape(stage['class'])}\"><span>{escape(str(index))}</span><strong>{escape(stage['label'])}</strong><em>{escape(stage['status'])}</em></li>"
            for index, stage in enumerate(stages, start=1)
        )
        return f"""
        <section class="panel">
          <h2>Voortgang</h2>
          <ol class="workflow">{items}</ol>
        </section>
        """

    def production_panel(self, project_root: Path) -> str:
        plan = self.read_manifest(project_root / "manifests" / "production_plan.json")
        activity = self.read_manifest(project_root / "manifests" / "production_activity.json")
        workflow = self.read_manifest(project_root / "manifests" / "workflow.json")
        mode = "Automatische modus" if workflow.get("autonomy_mode") == "automatic" or plan.get("autonomy_mode") == "automatic" else "Begeleide modus"
        language = str(workflow.get("language") or plan.get("language") or "Nederlands")
        target = str(workflow.get("target_duration_minutes") or plan.get("target_duration_minutes") or "10")
        mode_label = content_mode(workflow.get("content_mode") or plan.get("content_mode"))["label_nl"]
        current = self.current_activity_dutch(activity)
        quality = self.read_manifest(project_root / "manifests" / "script_quality_report.json")
        quality_message = ""
        if quality and quality.get("pass") is False:
            reasons = " ".join(str(item) for item in quality.get("failure_reasons", []))
            quality_message = f"<p class=\"error\">Script te kort, te lang, verhaalonderdelen of onderzoeksdetails ontbreken. {escape(reasons)}</p>"
        elif quality and quality.get("pass") is True:
            quality_message = "<p class=\"success\">Script voldoet aan alle eisen</p>"
        return f"""
        <section class="panel">
          <h2>Productie</h2>
          <div class="summary-grid">
            <div><span>Videotaal</span><strong>{escape(language)}</strong></div>
            <div><span>Gewenste lengte</span><strong>{escape(target)} minuten</strong></div>
            <div><span>Werkwijze</span><strong>{escape(mode)}</strong></div>
            <div><span>Type documentaire</span><strong>{escape(mode_label)}</strong></div>
            <div><span>Status</span><strong>{escape(current)}</strong></div>
          </div>
        </section>
        {quality_message}
        {self.workflow_panel(project_root, "")}
        """

    def simple_progress(self, project_root: Path) -> list[dict[str, str]]:
        workflow = self.read_manifest(project_root / "manifests" / "workflow.json")
        script = self.read_manifest(project_root / "manifests" / "script.json")
        scenes = self.read_manifest(project_root / "manifests" / "scenes.json").get("scenes", [])
        media_assets = load_media_manifest(project_root).get("assets", [])
        research_has_items = bool(approved_sources(project_root) or approved_claims(project_root))
        media_approved = any(asset.get("review_status") == "approved" for asset in media_assets if isinstance(asset, dict)) if isinstance(media_assets, list) else False
        steps = [
            ("Onderzoek", bool(workflow.get("research_approved")), research_has_items),
            ("Script", bool(workflow.get("script_approved")), bool(script.get("narration"))),
            ("Scènes", bool(workflow.get("scenes_generated")), bool(scenes)),
            ("Beelden", media_approved, bool(media_assets)),
            ("Voice-over", bool(workflow.get("voiceover_generated")), False),
            ("Montage", bool(workflow.get("video_rendered")), False),
            ("Klaar", (project_root / "exports" / "final_video.mp4").exists(), False),
        ]
        first_active = True
        progress = []
        for label, done, needs_review in steps:
            if done:
                status = "Voltooid" if label in {"Voice-over", "Montage", "Klaar"} else "Goedgekeurd"
                css = "done"
            elif needs_review:
                status = "Controle nodig"
                css = "review"
            elif first_active:
                status = "Bezig"
                css = "active"
                first_active = False
            else:
                status = "Wachten"
                css = "waiting"
            progress.append({"label": label, "status": status, "class": css})
        return progress

    def current_dutch_stage(self, project_root: Path) -> str:
        for stage in self.simple_progress(project_root):
            if stage["status"] in {"Controle nodig", "Bezig"}:
                return f"{stage['label']} - {stage['status']}"
        return "Klaar"

    def current_activity_dutch(self, activity: dict[str, Any]) -> str:
        if not isinstance(activity, dict) or not activity:
            return "Wachten"
        stage = str(activity.get("current_stage", ""))
        mapping = {
            "create_project": "Project aangemaakt",
            "reference_documentary": "Referentiedocumentaire laden",
            "analysis": "Referentie analyseren",
            "verification": "Claims voorbereiden voor verificatie",
            "research_plan": "Onderzoeksplan maken",
            "research": "Onderzoek uitvoeren",
            "analyze_sources": "Bronnen analyseren",
            "extract_claims": "Feiten controleren",
            "build_dossier": "Onderzoeksdossier maken",
            "review_sources_claims": "Onderzoek controleren",
            "approve_research": "Onderzoek controleren",
            "narrative_outline": "Verhaallijn opstellen",
            "generate_script": "Script maken",
            "approve_script": "Script controleren",
            "generate_scenes": "Scènes maken",
            "review_media": "Beelden controleren",
        }
        return mapping.get(stage, "Bezig")

    def research_panel(self, project_root: Path, slug: str) -> str:
        # Intentionally no manifest, provider, thumbnail, or transcript reads here.
        # The browser receives a fast shell and requests one page at a time.
        return f"""
        <section class="panel" id="research-panel" data-project="{escape(slug)}">
          <h2>Research</h2><p class="muted">Snelle weergave actief. Bronnen, claims en transcripties worden alleen per pagina en op verzoek geladen.</p>
          <div id="research-loading" class="status-pill">Researchoverzicht laden…</div>
          <form method="post" action="/projects/{escape(slug)}/research/source" class="grid-form">
            <label>Title <input name="title" required></label>
            <label>URL <input name="url" required></label>
            <label>Publisher <input name="publisher"></label>
            <label>Publication Date <input name="publication_date"></label>
            <label>Source Type <input name="source_type" value="article"></label>
            <label class="wide">Reliability Notes <textarea name="reliability_notes" rows="2"></textarea></label>
            <button type="submit">Add Source</button>
          </form>
          <details data-research-section="sources"><summary><strong>Bronnen</strong></summary><div id="research-sources"><p class="muted">Open de sectie om bronnen te laden.</p></div><div id="source-pages" class="pagination"></div></details>
          <form method="post" action="/projects/{escape(slug)}/research/claim" class="grid-form">
            <label class="wide">Claim <textarea name="text" rows="3" required></textarea></label>
            <label>Source IDs <input name="source_ids" placeholder="source-id, another-source"></label>
            <label>Confidence <select name="confidence"><option>needs_review</option><option>high</option><option>medium</option><option>low</option></select></label>
            <label>Date <input name="date"></label>
            <label>People <input name="people"></label>
            <label>Locations <input name="locations"></label>
            <label>Events <input name="events"></label>
            <button type="submit">Add Claim</button>
          </form>
          <details data-research-section="claims"><summary><strong>Claims</strong></summary><div id="research-claims"><p class="muted">Open de sectie om claims te laden.</p></div><div id="claim-pages" class="pagination"></div></details>
          <details><summary>Zware researchanalyse</summary><p>Analyse start nooit bij het openen van dit panel.</p><form method="post" action="/projects/{escape(slug)}/research-analysis/queue"><label>Analyseopdracht<textarea name="instruction" required></textarea></label><button>Run Automated Research (background task)</button></form></details>
          <form method="post" action="/projects/{escape(slug)}/research/approve"><button type="submit">Approve Research</button></form>
        </section>
        <script>
        (() => {{
          const slug={json.dumps(slug)}; const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
          async function load(kind,page=1) {{
            const data=await fetch(`/projects/${{slug}}/research-data?kind=${{kind}}&page=${{page}}&page_size=25`).then(r=>r.json());
            const rows=data.items.map(item=>kind==='sources'
              ? `<tr><td><code>${{esc(item.id)}}</code></td><td><a href="${{esc(item.url)}}">${{esc(item.title)}}</a><p>${{esc(item.transcript_preview)}}</p><div>${{(item.attachments||[]).map(a=>`<img loading="lazy" src="${{esc(a.url)}}" alt="${{esc(a.title)}}" width="120">`).join('')}}</div>${{item.has_transcript?`<button data-transcript="${{esc(item.id)}}" type="button">Transcript laden</button><pre id="transcript-${{esc(item.id)}}"></pre>`:''}}</td><td>${{esc(item.publisher)}}</td><td>${{statusLabel(item.review_status)}}</td><td>${{review(kind,item.id)}}</td></tr>`
              : `<tr><td><code>${{esc(item.id)}}</code></td><td>${{esc(item.text)}}</td><td>${{esc((item.source_ids||[]).join(', '))}}</td><td>${{statusLabel(item.review_status)}}</td><td>${{review(kind,item.id)}}</td></tr>`).join('');
            document.querySelector(`#research-${{kind}}`).innerHTML=`<table><tbody>${{rows||'<tr><td>Geen resultaten.</td></tr>'}}</tbody></table>`;
            const pages=Math.ceil(data.total/data.page_size); const previous=data.page>1?`<button type="button" data-page="${{data.page-1}}" data-kind="${{kind}}">Vorige</button>`:''; const next=data.page<pages?`<button type="button" data-page="${{data.page+1}}" data-kind="${{kind}}">Volgende</button>`:''; document.querySelector(`#${{kind==='sources'?'source':'claim'}}-pages`).innerHTML=`${{previous}} <span>Pagina ${{data.page}} van ${{pages||1}}</span> ${{next}}`;
            document.querySelector('#research-loading').textContent=`${{data.total}} ${{kind}} · pagina ${{data.page}}`;
          }}
          function statusLabel(value) {{ return esc(({{pending_review:'Nog te beoordelen',approved:'Goedgekeurd',rejected:'Afgewezen',needs_review:'Nog te beoordelen'}})[value]||value); }}
          function review(kind,id) {{ return `<div class="actions"><form method="post" action="/projects/${{slug}}/research/${{kind.slice(0,-1)}}/${{esc(id)}}/approve"><button>Goedkeuren</button></form><form method="post" action="/projects/${{slug}}/research/${{kind.slice(0,-1)}}/${{esc(id)}}/reject"><button class="secondary">Afwijzen</button></form></div>`; }}
          document.querySelector('#research-panel').addEventListener('click',async e=>{{ const b=e.target.closest('button'); if(!b)return; if(b.dataset.page)load(b.dataset.kind,+b.dataset.page); if(b.dataset.transcript){{const d=await fetch(`/projects/${{slug}}/research-transcript/${{b.dataset.transcript}}?limit=2000`).then(r=>r.json());document.querySelector(`#transcript-${{b.dataset.transcript}}`).textContent=d.text;b.remove();}} }});
          document.querySelectorAll('[data-research-section]').forEach(section=>section.addEventListener('toggle',()=>{{if(section.open&&!section.dataset.loaded){{section.dataset.loaded='true';requestAnimationFrame(()=>load(section.dataset.researchSection));}}}}));
        }})();
        </script>
        """

    def research_panel_page(self, slug: str) -> str:
        return self.page("Research Panel", f'{self.back_button(slug)}<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Research Panel</strong></nav>{self.research_panel(self.project_root(slug), slug)}')

    def research_data(self, slug: str, environ: dict[str, Any]) -> Response:
        query = parse_qs(str(environ.get("QUERY_STRING", "")))
        try:
            page = int(query.get("page", ["1"])[0]); page_size = int(query.get("page_size", ["25"])[0])
            result = ResearchPanelService(self.project_root(slug)).page(query.get("kind", ["sources"])[0], page, page_size)
        except (ValueError, OSError) as error:
            return self.json_response({"error": str(error)}, "400 Bad Request")
        return self.json_response(result.payload())

    def research_transcript(self, slug: str, source_id: str, environ: dict[str, Any]) -> Response:
        query = parse_qs(str(environ.get("QUERY_STRING", "")))
        try:
            payload = ResearchPanelService(self.project_root(slug)).transcript(source_id, int(query.get("offset", ["0"])[0]), int(query.get("limit", ["2000"])[0]))
        except KeyError:
            return self.json_response({"error": "source not found"}, "404 Not Found")
        return self.json_response(payload)

    def queue_research_analysis(self, slug: str, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        ResearchPanelService(self.project_root(slug)).queue_analysis(self.form_value(form, "instruction"))
        return self.redirect(f"/projects/{slug}/advanced")

    def source_rows(self, slug: str, sources: list[dict[str, Any]]) -> str:
        if not sources:
            return "<tr><td colspan=\"5\" class=\"muted\">No sources yet.</td></tr>"
        rows = []
        for source in sources:
            source_id = str(source.get("id", ""))
            rows.append(
                f"<tr><td><code>{escape(source_id)}</code></td><td><a href=\"{escape(str(source.get('url', '')))}\">{escape(str(source.get('title', '')))}</a></td><td>{escape(str(source.get('publisher', '')))}</td><td>{escape(str(source.get('review_status', '')))}</td><td>{self.review_buttons(slug, 'source', source_id)}</td></tr>"
            )
        return "".join(rows)

    def claim_rows(self, slug: str, claims: list[dict[str, Any]]) -> str:
        if not claims:
            return "<tr><td colspan=\"5\" class=\"muted\">No claims yet.</td></tr>"
        rows = []
        for claim in claims:
            claim_id = str(claim.get("id", ""))
            rows.append(
                f"<tr><td><code>{escape(claim_id)}</code></td><td>{escape(str(claim.get('text', '')))}</td><td>{escape(', '.join(str(item) for item in claim.get('source_ids', [])))}</td><td>{escape(str(claim.get('review_status', '')))}</td><td>{self.review_buttons(slug, 'claim', claim_id)}</td></tr>"
            )
        return "".join(rows)

    def review_buttons(self, slug: str, kind: str, item_id: str) -> str:
        return (
            f"<div class=\"actions\"><form method=\"post\" action=\"/projects/{escape(slug)}/research/{kind}/{escape(item_id)}/approve\"><button type=\"submit\">Goedkeuren</button></form>"
            f"<form method=\"post\" action=\"/projects/{escape(slug)}/research/{kind}/{escape(item_id)}/reject\"><button type=\"submit\" class=\"secondary\">Afwijzen</button></form></div>"
        )

    def script_panel(self, project_root: Path, slug: str) -> str:
        workflow = self.read_manifest(project_root / "manifests" / "workflow.json")
        script = self.read_manifest(project_root / "manifests" / "script.json")
        narration = escape(str(script.get("narration", "")))
        disabled = "" if workflow.get("research_approved") else "disabled"
        approve_disabled = "" if script.get("narration") else "disabled"
        return f"""
        <section class="panel">
          <h2>Script</h2>
          <form method="post" action="/projects/{escape(slug)}/script/generate" class="grid-form">
            <label>Target Duration Minutes <input name="target_duration_minutes" type="number" min="1" max="60" value="{escape(str(workflow.get('target_duration_minutes', 10)))}"></label>
            <button type="submit" {disabled}>Generate Source-Backed Draft</button>
          </form>
          <form method="post" action="/projects/{escape(slug)}/script/save">
            <label class="wide">Editable Script <textarea name="narration" rows="14">{narration}</textarea></label>
            <div class="actions">
              <button type="submit">Save Script Edits</button>
            </div>
          </form>
          <form method="post" action="/projects/{escape(slug)}/script/approve"><button type="submit" {approve_disabled}>Approve Script</button></form>
        </section>
        """

    def scenes_panel(self, project_root: Path, slug: str) -> str:
        workflow = self.read_manifest(project_root / "manifests" / "workflow.json")
        scenes = self.read_manifest(project_root / "manifests" / "scenes.json").get("scenes", [])
        disabled = "" if workflow.get("script_approved") else "disabled"
        count = len(scenes) if isinstance(scenes, list) else 0
        return f"""
        <section class="panel">
          <h2>Scenes</h2>
          <p class="muted">{count} scenes generated. Scenes include narration, estimated duration, claim IDs, people, locations, dates, events, archival media queries, and fallback visual prompts.</p>
          <form method="post" action="/projects/{escape(slug)}/scenes/generate"><button type="submit" {disabled}>Generate Scenes</button></form>
        </section>
        """

    def review_queue(self, project_root: Path, slug: str) -> str:
        rebuild_relevance_cache(project_root)
        manifest = load_media_manifest(project_root)
        assets = manifest.get("assets", [])
        rows = []
        if isinstance(assets, list):
            for asset in assets:
                if not isinstance(asset, dict) or asset.get("review_status") not in {"pending_review", "rejected"} or asset.get("review_eligible") is not True:
                    continue
                media_id = str(asset.get("id", ""))
                media_type = str(asset.get("type", "image"))
                image = (f"<video src=\"/projects/{escape(slug)}/media/{escape(media_id)}/preview\" controls preload=\"metadata\"></video>" if media_type == "video" else f"<img src=\"/projects/{escape(slug)}/media/{escape(media_id)}/preview\" alt=\"\" loading=\"lazy\">")
                copyright_status = str(asset.get("copyright_status", "unknown"))
                rights_label = {"likely_open": "Waarschijnlijk vrij te gebruiken", "restrictive_or_unknown": "Beperkt of onbekend", "unknown": "Onbekend"}.get(copyright_status, copyright_status)
                flag = "flag warn" if copyright_status != "likely_open" else "flag"
                duplicate = ""
                if asset.get("duplicate_of"):
                    duplicate = f"<p class=\"muted\">Duplicate: {escape(str(asset.get('duplicate_kind', '')))} of {escape(str(asset.get('duplicate_of')))}</p>"
                rows.append(
                    f"""
                    <article id="media-{escape(media_id)}" class="media-card">
                      {image}
                      <div>
                        <h3>{escape(str(asset.get('title') or asset.get('id')))}</h3>
                        <p><span class="{flag}">{escape(rights_label)}</span></p>
                        <p><strong>Onderwerprelevantie:</strong> {"Niet berekend" if asset.get('topic_relevance') is None else f"{float(asset.get('topic_relevance')):.0%}"} — {escape(str(asset.get('relevance_reason', '')))}</p>
                        <p><strong>Gematcht:</strong> {escape(', '.join(str(item) for item in asset.get('relevance_matches', [])) or 'Geen')} · <strong>Ontbreekt:</strong> {escape(', '.join(str(item) for item in asset.get('relevance_missing', [])) or 'Niets')}</p>
                        <p><strong>Betrouwbaarheid herkomst:</strong> {float(asset.get('source_reliability', {}).get('score', 0)):.0%} — {escape(str(asset.get('source_reliability', {}).get('reason', '')))}</p>
                        <p><strong>Duplicaatzekerheid:</strong> {float(asset.get('duplicate_confidence', 0)):.0%} · <strong>Rechtenstatus:</strong> {escape(rights_label)}</p>
                        <p><strong>Shot:</strong> {escape(', '.join(str(item) for item in asset.get('shot_ids', [])) or 'Niet gekoppeld')} · <strong>Mediatype:</strong> {escape(media_type)}</p>
                        <p><strong>Shotrelevantie:</strong> {float(asset.get('shot_relevance_score', asset.get('relevance_score', 0)) or 0):.0%} — {escape(str(asset.get('shot_relevance_reason', asset.get('relevance_reason', ''))))}</p>
                        <p><strong>Geplande duur:</strong> {escape(str(asset.get('planned_duration_seconds', 'volgens shotplan')))} · <strong>Beweging:</strong> {escape(str(asset.get('planned_motion', 'volgens shotplan')))} · <strong>Compositie:</strong> {escape(str(asset.get('planned_composition', 'single_frame')))}</p>
                        <p>{escape(str(asset.get('creator', '')))}</p>
                        <p>{escape(str(asset.get('license', '')))}</p>
                        <p>Voorgestelde scène: {escape(', '.join(str(item) for item in asset.get('suggested_scenes', [])))}</p>
                        {duplicate}
                        <p><a href="{escape(str(asset.get('source_url', '')))}">Oorspronkelijke bron</a></p>
                        <div class="actions">
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/approve"><button type="submit">Goedkeuren</button></form>
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/reject"><button type="submit" class="secondary">Afwijzen</button></form>
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/replace"><button type="submit" class="secondary">Vervangen</button></form>
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/search"><button type="submit" class="secondary">Verder zoeken</button></form>
                        </div>
                      </div>
                    </article>
                    """
                )
        body = "".join(rows) if rows else "<p class=\"muted\">Geen ontdekte media om te beoordelen.</p>"
        return f"<section class=\"panel\"><h2>Wachtrij voor ontdekte media</h2>{body}</section>"

    def scene_options(self, scenes_manifest: Any) -> str:
        options = ['<option value="*">All scenes (*)</option>']
        scenes = scenes_manifest.get("scenes", []) if isinstance(scenes_manifest, dict) else []
        if isinstance(scenes, list):
            for scene in scenes:
                if isinstance(scene, dict):
                    scene_id = str(scene.get("id", ""))
                    heading = str(scene.get("heading", scene_id))
                    if scene_id:
                        options.append(f'<option value="{escape(scene_id)}">{escape(scene_id)} - {escape(heading)}</option>')
        if len(options) == 1:
            options.append('<option value="s01">s01 - first scene</option>')
        return "\n".join(options)

    def projects(self) -> list[Path]:
        projects_dir = self.settings.projects_dir
        if not projects_dir.exists():
            return []
        return sorted([path for path in projects_dir.iterdir() if path.is_dir()])

    def project_root(self, slug: str) -> Path:
        return self.settings.projects_dir / slug

    def project_status(self, project_root: Path) -> str:
        if (project_root / "exports" / "final_video.mp4").exists():
            return "final video ready"
        if (project_root / "manifests" / "scenes.json").exists():
            return "manifests generated"
        return "draft"

    def read_manifest(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        stat = path.stat()
        cached = self._manifest_cache.get(path)
        if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
            return cached[2]
        data = read_json(path)
        result = data if isinstance(data, dict) else {}
        self._manifest_cache[path] = (stat.st_mtime_ns, stat.st_size, result)
        return result

    def page(self, title: str, body: str) -> str:
        return f"""<!doctype html>
<html lang="nl">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - Inside the Case Factory</title>
  <style>
    :root {{ color-scheme: light; --ink:#172026; --muted:#697782; --line:#dde4e8; --panel:#ffffff; --page:#f6f8f9; --soft:#eef4f1; --accent:#176b5b; --accent-dark:#0f5044; --warn:#b65f15; --ok:#237447; }}
    * {{ box-sizing: border-box; }}
    [hidden] {{ display:none !important; }}
    body {{ margin:0; font-family: Manrope, "Segoe UI", "Helvetica Neue", sans-serif; background:radial-gradient(circle at top right, #f0f6f4 0%, #f6f8f9 42%, #f4f7f9 100%); color:var(--ink); }}
    header {{ background:rgba(255,255,255,.96); color:var(--ink); padding:16px 32px; display:flex; align-items:center; justify-content:space-between; gap:24px; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:10; backdrop-filter:blur(12px); }}
    header h1 {{ margin:0; font-size:22px; letter-spacing:0; }}
    header p {{ margin:4px 0 0; color:var(--muted); }}
    .main-nav {{ display:flex; gap:5px; align-items:center; flex-wrap:wrap; }}
    .main-nav a {{ color:#43515b; text-decoration:none; padding:9px 11px; border-radius:8px; font-weight:650; font-size:14px; }}
    .main-nav a:hover {{ color:var(--accent); background:var(--soft); }}
    main {{ max-width:1120px; margin:0 auto; padding:32px 24px 48px; }}
    .hero-panel, .panel, .review-card, .project-card, .focus-card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; }}
    .hero-panel {{ padding:30px; margin-bottom:28px; box-shadow:0 16px 40px rgba(23,32,38,.07); }}
    .hero-gradient {{ background:linear-gradient(135deg, #f8fcfb 0%, #f2f8f7 45%, #eef4f7 100%); }}
    .hero-panel h2 {{ max-width:760px; font-size:30px; line-height:1.18; margin:6px 0 22px; }}
    .panel {{ padding:22px; margin-bottom:18px; }}
    .subpanel {{ border-top:1px solid var(--line); padding-top:16px; margin-top:16px; }}
    .production-form {{ display:grid; gap:18px; }}
    .prompt-label textarea {{ min-height:190px; font-size:17px; line-height:1.5; }}
    .compact p {{ margin-bottom:0; }}
    h2 {{ margin:0 0 14px; font-size:20px; }}
    h3 {{ margin:18px 0 10px; font-size:16px; }}
    a {{ color:var(--accent); }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ padding:10px 8px; border-bottom:1px solid var(--line); text-align:left; vertical-align:top; }}
    th {{ color:#33404a; font-size:13px; text-transform:uppercase; }}
    .grid-form {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(220px, 1fr)); gap:12px; align-items:end; }}
    .start-grid {{ display:grid; grid-template-columns:repeat(3, minmax(180px, 1fr)); gap:14px; }}
    label {{ display:grid; gap:6px; font-weight:700; font-size:14px; }}
    input, textarea, select {{ width:100%; border:1px solid #b9c2ca; border-radius:8px; padding:11px 12px; font:inherit; background:#fff; }}
    input:focus, textarea:focus, select:focus {{ outline:3px solid #cde7df; border-color:var(--accent); }}
    textarea {{ resize:vertical; }}
    .wide {{ grid-column:1 / -1; }}
    button, .button {{ display:inline-block; border:0; border-radius:8px; background:var(--accent); color:#fff; padding:11px 15px; font:inherit; font-weight:700; cursor:pointer; text-decoration:none; text-align:center; }}
    button:hover, .button:hover {{ background:var(--accent-dark); }}
    button:disabled {{ background:#aab3bb; cursor:not-allowed; }}
    .button.ghost {{ background:#eef3f2; color:#173b34; }}
    .primary-action {{ width:100%; padding:16px 18px; font-size:18px; }}
    .actions {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; justify-content:flex-end; }}
    .project-head {{ display:flex; justify-content:space-between; gap:16px; align-items:center; }}
    .muted {{ color:var(--muted); }}
    .crumb {{ display:flex; gap:8px; align-items:center; margin-bottom:14px; }}
    .eyebrow {{ margin:0; color:var(--accent); font-size:13px; font-weight:800; text-transform:uppercase; letter-spacing:.08em; }}
    .section-head {{ display:flex; justify-content:space-between; align-items:end; margin:0 0 12px; }}
    .section-head h2 {{ margin-bottom:4px; }}
    .project-list {{ display:grid; gap:12px; }}
    .toolbar-panel {{ padding:16px 18px; }}
    .toolbar-grid {{ display:grid; grid-template-columns:2fr 1fr 1fr auto; gap:10px; align-items:end; }}
    .project-card {{ padding:18px; display:flex; justify-content:space-between; align-items:center; gap:16px; }}
    .project-card-main {{ display:grid; gap:6px; }}
    .project-card h3 {{ margin:0 0 4px; font-size:18px; }}
    .project-card p {{ margin:0; }}
    .project-card-actions {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; justify-content:flex-end; }}
    .safe-back-form {{ margin-bottom:14px; }}
    .status-pill {{ display:inline-flex; align-items:center; min-height:34px; border-radius:999px; padding:6px 12px; background:var(--soft); color:#1d5f50; font-weight:700; font-size:13px; white-space:nowrap; }}
    .badge-active {{ background:#e7f4ef; color:#136248; }}
    .badge-complete {{ background:#e6f6ea; color:#1d6d37; }}
    .badge-waiting {{ background:#fff7e8; color:#895b12; }}
    .badge-blocked {{ background:#fff1ec; color:#8a3b32; }}
    .badge-draft {{ background:#eef3f7; color:#425468; }}
    .summary-grid {{ display:grid; grid-template-columns:repeat(4, minmax(140px, 1fr)); gap:12px; }}
    .summary-grid div {{ border:1px solid var(--line); border-radius:8px; padding:14px; background:#fbfcfc; }}
    .summary-grid span {{ display:block; color:var(--muted); font-size:13px; margin-bottom:6px; }}
    .summary-grid strong {{ font-size:16px; }}
    .review-card {{ display:flex; justify-content:space-between; align-items:center; gap:22px; padding:26px; margin-bottom:18px; border-color:#d4e6df; background:#fbfefd; }}
    .review-card h2 {{ font-size:26px; margin:6px 0 8px; }}
    .review-card p {{ max-width:680px; margin:0; line-height:1.5; }}
    .review-card.calm {{ border-color:var(--line); background:#fff; }}
    pre {{ white-space:pre-wrap; overflow:auto; max-height:420px; background:#111820; color:#edf2f7; padding:14px; border-radius:6px; }}
    details {{ border-top:1px solid var(--line); padding-top:10px; margin-top:10px; }}
    summary {{ cursor:pointer; font-weight:700; }}
    code {{ background:#eef1f3; padding:2px 4px; border-radius:4px; }}
    .media-card {{ display:grid; grid-template-columns:180px 1fr; gap:14px; border-top:1px solid var(--line); padding-top:14px; margin-top:14px; }}
    .media-card img {{ width:180px; height:120px; object-fit:cover; border-radius:6px; border:1px solid var(--line); background:#eef1f3; }}
    .media-card h3 {{ margin:0 0 8px; font-size:17px; }}
    .media-card p {{ margin:5px 0; }}
    .flag {{ display:inline-block; border-radius:999px; padding:2px 8px; background:#e8f3ed; color:#1c6b3c; font-weight:700; font-size:12px; }}
    .flag.warn {{ background:#fff0d9; color:#8a5200; }}
    button.secondary {{ background:#5d6872; }}
    .workflow {{ display:grid; grid-template-columns:repeat(7, minmax(0, 1fr)); gap:10px; padding-left:0; list-style:none; }}
    .workflow li {{ min-height:118px; border:1px solid var(--line); border-radius:8px; padding:12px; background:#f8fafb; display:grid; align-content:start; gap:8px; }}
    .workflow li span {{ display:grid; place-items:center; width:28px; height:28px; border-radius:999px; background:#e1e7ea; color:#33404a; font-weight:800; }}
    .workflow li strong {{ font-size:15px; }}
    .workflow li em {{ color:var(--muted); font-style:normal; font-size:13px; }}
    .workflow li.done {{ border-color:#b9dac7; background:#eef8f2; }}
    .workflow li.done span {{ background:var(--ok); color:#fff; }}
    .workflow li.review {{ border-color:#f0cfaa; background:#fff8ef; }}
    .workflow li.review span {{ background:var(--warn); color:#fff; }}
    .workflow li.active {{ border-color:#b9d8d0; background:#eef8f6; }}
    .workflow li.active span {{ background:var(--accent); color:#fff; }}
    .producer-chart {{ display:grid; gap:6px; margin-bottom:18px; }}
    .chart-row {{ display:grid; grid-template-columns:55px 1fr 45px; gap:8px; align-items:center; }}
    .chart-row i {{ display:block; height:12px; border-radius:999px; background:#b44b42; min-width:2px; }}
    .producer-chart.emotion .chart-row i {{ background:#8761a8; }}
    .producer-chart.retention .chart-row i {{ background:#2f806c; }}
    .phase-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(250px,1fr)); gap:12px; margin-top:18px; }}
    .phase-card {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:16px; }}
    .phase-card > div {{ display:flex; justify-content:space-between; gap:8px; align-items:center; }}
    .phase-card progress {{ width:100%; margin-top:12px; accent-color:var(--accent); }}
    .project-summary {{ display:flex; justify-content:space-between; gap:28px; align-items:center; background:#fff; border:1px solid var(--line); border-radius:16px; padding:28px; margin-bottom:18px; box-shadow:0 12px 34px rgba(23,32,38,.05); }}
    .project-summary h1,.project-summary h2 {{ margin:5px 0 8px; font-size:30px; }}
    .progress-number {{ display:grid; justify-items:end; gap:6px; min-width:190px; }} .progress-number strong {{ font-size:36px; color:var(--accent); }} .progress-number span {{ color:var(--muted); font-size:13px; }}
    .pipeline {{ list-style:none; padding:8px 0; margin:0 0 18px; display:grid; grid-template-columns:repeat(5,minmax(0,1fr)); gap:8px; }}
    .pipeline li {{ display:flex; gap:8px; align-items:center; min-width:0; padding:10px 7px; border-radius:10px; color:var(--muted); }} .pipeline li span {{ display:grid; place-items:center; width:25px; height:25px; flex:0 0 25px; border-radius:50%; background:#e7ecef; font-size:12px; }} .pipeline li strong,.pipeline li small {{ display:block; font-size:12px; }}
    .pipeline li.completed span {{ background:#dcefe4;color:var(--ok); }} .pipeline li.current {{ background:#eaf5f2;color:var(--accent-dark); }} .pipeline li.current span {{ background:var(--accent);color:#fff; }}
    .pipeline li.waiting {{ background:#f8fafb;color:#66521a; }} .pipeline li.blocked {{ background:#fff5e9;color:#8a5200; }} .pipeline li.blocked span {{ background:var(--warn);color:#fff; }}
    .pipeline li.failed {{ background:#fff1f1;color:#922e2e; }} .pipeline li.failed span {{ background:#b14444;color:#fff; }}
    .pipeline li.not_started {{ opacity:.88; }}
    .truth-banner {{ border-color:#d7e4dc; background:#f5faf7; }}
    .activity-feed {{ list-style:none; margin:0; padding:0; display:grid; gap:10px; }}
    .activity-feed li {{ display:flex; justify-content:space-between; gap:10px; padding:10px 12px; border:1px solid var(--line); border-radius:10px; background:#fbfcfc; }}
    .activity-feed small {{ color:var(--muted); white-space:nowrap; }}
    .metric-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:10px; }}
    .metric-card {{ border:1px solid var(--line); border-radius:10px; padding:12px; background:#fbfcfc; display:grid; gap:6px; }}
    .metric-card span {{ color:var(--muted); font-size:13px; }}
    .metric-card strong {{ font-size:18px; color:var(--ink); }}
    .feature-tags {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .feature-chip {{ display:inline-flex; gap:6px; border-radius:999px; padding:7px 12px; font-size:13px; border:1px solid var(--line); background:#f8fafb; }}
    .feature-chip.on {{ border-color:#b9dac7; background:#eef8f2; color:#1b6542; }}
    .feature-chip.off {{ color:#6f7b85; }}
    .progress-actions {{ display:flex; flex-wrap:wrap; gap:10px; align-items:center; }}
    .progress-actions form {{ margin:0; }}
    .scene-strip {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(140px,1fr)); gap:10px; }}
    .scene-strip article {{ border:1px solid var(--line); border-radius:10px; background:#fbfcfc; overflow:hidden; }}
    .scene-strip img {{ width:100%; aspect-ratio:16/9; object-fit:cover; display:block; }}
    .scene-strip p {{ margin:0; padding:8px 10px; font-size:12px; color:#31414d; }}
    .approval-card {{ display:grid;grid-template-columns:1fr auto;gap:28px;align-items:center;background:#fff;border:1px solid #e8c89f;border-radius:16px;padding:28px;margin-bottom:18px;box-shadow:0 12px 34px rgba(89,55,20,.08); }} .approval-card h1 {{ margin:5px 0 8px;font-size:28px; }} .approval-facts {{ display:flex;gap:10px;flex-wrap:wrap;margin-top:16px; }} .approval-facts span {{ display:grid;gap:3px;background:#fff8ef;border-radius:10px;padding:10px 14px; }} .approval-facts small {{ color:var(--muted); }} .approval-actions {{ display:grid;gap:8px;min-width:260px; }} .approval-actions form,.approval-actions button {{ width:100%; }} .ghost-button {{ background:#eef1f3;color:var(--ink); }}
    .focus-card {{ padding:24px; margin-bottom:18px; border-color:#cfe3dc; }} .research-metrics {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }} .research-metrics span {{ background:var(--page);border-radius:10px;padding:10px 12px;font-size:13px; }}
    .loading-state {{ display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px; }} .loading-state h2,.loading-state p {{ margin:3px 0; }} .pulse {{ width:14px;height:14px;border-radius:50%;background:var(--accent);animation:pulse 1.2s infinite; }} @keyframes pulse {{ 50% {{ opacity:.35;transform:scale(.8); }} }}
    .task-queue {{ display:grid;gap:8px; }} .queue-item {{ display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:11px;padding:13px; }} .queue-item small {{ display:block;color:var(--muted);margin-top:4px; }} .queue-item.possibly_stalled,.queue-item.blocked,.queue-item.failed {{ border-color:#e8c8ab;background:#fffaf5; }} .task-actions {{ grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px; }} button.danger {{ background:#a8473d; }} .link-grid {{ display:flex;flex-wrap:wrap;gap:16px;padding:16px 0; }}
    .review-player {{ display:grid; grid-template-columns:minmax(0,3fr) minmax(220px,1fr); gap:16px; margin-bottom:18px; }}
    .review-player > div, .review-player aside {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; min-width:0; }}
    .review-player video {{ width:100%; background:#111; border-radius:6px; }}
    .pro-player video {{ width:100%; border-radius:10px; background:#0f1216; }}
    .player-controls {{ margin-top:10px; display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    .player-controls label {{ font-weight:600; font-size:12px; min-width:150px; }}
    .player-controls select, .player-controls input {{ padding:7px 9px; }}
    .export-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(180px, 1fr)); gap:10px; }}
    .timeline-toolbar {{ display:flex; justify-content:space-between; align-items:center; gap:10px; margin:10px 0 14px; flex-wrap:wrap; }}
    .timeline-batch-form {{ display:flex; flex-wrap:wrap; gap:8px; }}
    .timeline-view-controls {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }}
    .timeline-view-controls label {{ font-size:12px; }}
    .timeline-shot {{ transition:transform .18s ease, box-shadow .18s ease; }}
    .timeline-shot:hover {{ transform:translateY(-2px); box-shadow:0 10px 20px rgba(23,32,38,.08); }}
    .timeline-shot.dragging {{ opacity:.75; border:1px dashed #176b5b; }}
    .shot-select {{ display:flex; align-items:center; gap:6px; font-size:12px; color:#50606c; margin-bottom:6px; }}
    .ai-tool-grid {{ display:grid; grid-template-columns:repeat(auto-fit, minmax(170px, 1fr)); gap:8px; }}
    .wizard-steps {{ display:grid; gap:8px; grid-template-columns:repeat(7,minmax(0,1fr)); margin:16px 0 0; padding:0; list-style:none; }}
    .wizard-steps li {{ border:1px solid #d9e3e7; border-radius:999px; padding:7px 10px; font-size:12px; text-align:center; background:#ffffffaa; }}
    .review-timeline {{ display:flex; gap:8px; overflow-x:auto; padding-top:10px; position:relative; z-index:1; }}
    .review-timeline a {{ display:block; flex:0 0 130px; min-width:130px; text-decoration:none; font-size:12px; }}
    .review-timeline img, .scene-thumb {{ width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:6px; }}
    .scene-thumb {{ max-width:360px; }}
    .scene-review, .scene-review details, .scene-review pre {{ min-width:0; max-width:100%; }}
    .scene-review pre {{ overflow:auto; }}
    .revision-compare {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; border:1px solid var(--line); border-radius:8px; padding:10px; margin:10px 0; }}
    .revision-compare > p {{ grid-column:1/-1; color:var(--muted); }}
    @media (max-width: 900px) {{ .pipeline {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .workflow {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .start-grid, .summary-grid {{ grid-template-columns:1fr 1fr; }} .toolbar-grid {{ grid-template-columns:1fr 1fr; }} .wizard-steps {{ grid-template-columns:repeat(3,minmax(0,1fr)); }} .review-card, .project-card {{ align-items:flex-start; flex-direction:column; }} .actions {{ justify-content:flex-start; }} }}
    @media (max-width: 620px) {{ header {{ display:block; padding:14px; }} .main-nav {{ margin-top:12px;display:grid;grid-template-columns:1fr 1fr; }} .project-head,.project-summary {{ align-items:flex-start;flex-direction:column; }} .approval-card {{ grid-template-columns:1fr;padding:20px; }} .approval-actions {{ min-width:0;width:100%; }} .progress-number {{ justify-items:start; }} .actions {{ justify-content:flex-start; margin-top:12px; }} main {{ padding:18px 14px; }} .hero-panel {{ padding:22px; }} .hero-panel h2 {{ font-size:24px; }} .toolbar-grid {{ grid-template-columns:1fr; }} .workflow, .pipeline, .start-grid, .summary-grid, .review-player, .revision-compare, .wizard-steps {{ grid-template-columns:1fr; }} .project-card-actions {{ width:100%; justify-content:space-between; }} table {{ display:block; overflow-x:auto; }} button,.button {{ min-height:44px; }} }}
    @media (max-width: 620px) {{ header {{ position:static; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>Documentaire Studio</h1><p>Van idee tot gecontroleerde film</p></div>
        <nav class="main-nav" aria-label="Hoofdnavigatie"><a href="/projects">Projecten</a><a href="/projects/new">Nieuwe documentaire</a></nav>
  </header>
  <main>{body}</main>
  <script>
    document.addEventListener('submit', event => {{
      const form = event.target;
      if (!(form instanceof HTMLFormElement)) return;
      if (form.dataset.submitting === 'true') {{ event.preventDefault(); return; }}
      form.dataset.submitting = 'true';
      requestAnimationFrame(() => form.querySelectorAll('button[type="submit"],button:not([type]),input[type="submit"]').forEach(button => button.disabled = true));
    }});
  </script>
    <footer style="max-width:1120px;margin:0 auto 24px;padding:0 24px;color:#7a868f;font-size:12px;">Dashboard build: {escape(self._build_marker)}</footer>
</body>
</html>"""


def run_dashboard(root: Path | None = None, host: str = "127.0.0.1", port: int = 8000) -> None:
    app = DashboardApp(root)
    with make_server(host, port, app) as server:
        app.resume_recoverable_projects()
        print(f"Inside the Case Factory dashboard: http://{host}:{port}", flush=True)
        server.serve_forever()
