from __future__ import annotations

from cgi import FieldStorage
from datetime import UTC, datetime
from html import escape
from io import BytesIO
import json
from pathlib import Path
import tempfile
import traceback
from threading import Thread
from typing import Any, Callable
from urllib.parse import quote, unquote
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

from inside_case_factory import __version__
from inside_case_factory.core.discovery import DiscoveryQuery, discover_archival_media
from inside_case_factory.config.settings import Settings, load_settings
from inside_case_factory.core.media import add_image_asset, ensure_media_manifest, load_media_manifest, update_image_review
from inside_case_factory.core.production import ProductionRequest, _persist_candidate, _promote_candidate, run_production, start_production
from inside_case_factory.providers.reasoning import paid_api_confirmed
from inside_case_factory.core.narrative_quality import validate_script
from inside_case_factory.core.content_modes import normalize_content_mode
from inside_case_factory.core.content_modes import content_mode
from inside_case_factory.core.project import create_project
from inside_case_factory.core.progress import TaskQueue, write_progress_event
from inside_case_factory.core.draft_review import approve_scene, create_review_draft, revise_draft
from inside_case_factory.core.user_experience import apply_dossier_instruction, production_progress, revision_change_plan, supported_script_map, youtube_draft
from inside_case_factory.core.reference_intake import create_reference_intake, select_reference_match
from inside_case_factory.core.relevance import rebuild_relevance_cache
from inside_case_factory.core.research_panel import ResearchPanelService
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


Response = tuple[str, list[tuple[str, str]], bytes]


class DashboardApp:
    def __init__(self, root: Path | None = None) -> None:
        self.root = root or Path.cwd()
        self._manifest_cache: dict[Path, tuple[int, int, dict[str, Any]]] = {}

    def __call__(self, environ: dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        try:
            status, headers, body = self.dispatch(environ)
        except Exception as error:  # pragma: no cover - exercised by manual UI use
            status = "500 Internal Server Error"
            headers = [("Content-Type", "text/html; charset=utf-8")]
            body = self.page(
                "Dashboard Error",
                f"""
                <section class="panel">
                  <h2>Something failed</h2>
                  <p>{escape(str(error))}</p>
                  <pre>{escape(traceback.format_exc())}</pre>
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

        if method == "GET" and path == "/":
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
            if len(parts) == 5 and parts[2] == "media" and parts[4] == "preview":
                return self.media_preview(parts[1], parts[3])
        if method == "POST" and path.startswith("/projects/"):
            parts = [part for part in path.split("/") if part]
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
            if len(parts) == 5 and parts[2] == "media" and parts[4] in {"approve", "reject"}:
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
        return status, [("Content-Type", "text/html; charset=utf-8")], content.encode("utf-8")

    def json_response(self, payload: object, status: str = "200 OK") -> Response:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return status, [("Content-Type", "application/json; charset=utf-8"), ("Content-Length", str(len(body)))], body

    def redirect(self, location: str) -> Response:
        return "303 See Other", [("Location", location), ("Content-Type", "text/plain")], b""

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
        rows = "\n".join(self.project_card(project) for project in projects[:6])
        if not rows:
            rows = "<p class=\"muted\">Nog geen projecten.</p>"
        return self.page(
            "Dashboard",
            f"""
            <section class="hero-panel">
              <div class="eyebrow">Nieuwe video maken</div>
              <h2>Beschrijf je documentaire. De productie volgt daarna stap voor stap.</h2>
              <p><a class="button" href="/projects/new">Open de volledige projectwizard</a></p>
              <form method="post" action="/production/start" class="production-form">
                <label class="wide prompt-label">Beschrijf de video die je wilt maken
                  <textarea name="prompt" rows="8" required placeholder="Bijvoorbeeld: Maak een feitelijke documentaire over een onopgeloste zaak. Focus op de tijdlijn, betrokken personen, belangrijke vragen en betrouwbare bronnen."></textarea>
                </label>
                <div class="start-grid">
                  <label>Videotaal
                    <select name="language">
                      <option value="Nederlands">Nederlands</option>
                      <option value="English">Engels</option>
                      <option value="Arabic">Arabic</option>
                      <option value="French">French</option>
                      <option value="German">German</option>
                      <option value="Spanish">Spanish</option>
                    </select>
                  </label>
                  <label>Gewenste lengte
                    <select name="target_duration_minutes">
                      <option value="5">5 minuten</option>
                      <option value="8">8 minuten</option>
                      <option value="12" selected>12 minuten</option>
                      <option value="20">20 minuten</option>
                      <option value="30">30 minuten</option>
                    </select>
                  </label>
                  <label>Werkwijze
                    <select name="autonomy_mode">
                      <option value="review">Begeleide modus</option>
                      <option value="automatic">Automatische modus</option>
                    </select>
                  </label>
                  <label>Type documentaire
                    <select name="content_mode">
                      <option value="factual_documentary">Feitelijke documentaire</option>
                      <option value="investigative_documentary">Onderzoeksdocumentaire</option>
                      <option value="theory_conspiracy">Theorie / complot</option>
                    </select>
                    <small>Feitelijke documentaire: gecontroleerde feiten en onzekerheid. Onderzoeksdocumentaire: controverses en concurrerende verklaringen. Theorie / complot: theorieën én tegenargumenten met bronattributie.</small>
                  </label>
                </div>
                <button type="submit" class="primary-action">Productie starten</button>
              </form>
            </section>
            <section class="section-head">
              <div>
                <h2>Projecten</h2>
                <p class="muted">Open een project om de productie voortgang en controles te bekijken.</p>
              </div>
            </section>
            <section class="project-list">{rows}</section>
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
        return self.redirect(f"/projects/{result['project_slug']}")

    def project_card(self, project_root: Path) -> str:
        manifest = self.read_manifest(project_root / "manifests" / "project.json")
        slug = project_root.name
        topic = str(manifest.get("topic", slug)) if isinstance(manifest, dict) else slug
        final_video = project_root / "exports" / "final_video.mp4"
        status = "Klaar" if final_video.exists() else self.current_dutch_stage(project_root)
        progress = production_progress(project_root)
        current = next((phase["name"] for phase in progress["phases"] if phase["status"] == "active"), "Afgerond")
        return f"""
        <article class="project-card">
          <div>
            <h3>{escape(topic)}</h3>
            <p>Huidige stap: <strong>{escape(current)}</strong></p>
            <p class="muted">{progress['percentage']}% afgerond · laatste update {escape(str(progress['last_update']))}</p>
          </div>
          <div class="project-card-actions">
            <span class="status-pill">{escape(status)}</span>
            <a class="button ghost" href="/projects/{escape(slug)}">Openen</a>
          </div>
        </article>
        """

    def create_project(self, environ: dict[str, Any]) -> Response:
        form = self.read_form(environ)
        topic = self.form_value(form, "topic").strip()
        slug = self.form_value(form, "slug").strip() or None
        if not topic:
            return self.html(self.page("Missing Topic", "<section class=\"panel\"><p>Topic is required.</p></section>"), "400 Bad Request")
        settings = self.settings
        project = create_project(settings.projects_dir, topic, slug)
        ensure_media_manifest(project.root)
        return self.redirect(f"/projects/{project.slug}")

    def new_project_wizard(self) -> str:
        return self.page("Nieuw project", """
        <nav class="crumb"><a href="/">Dashboard</a><span>/</span><strong>Nieuw project</strong></nav>
        <section class="hero-panel"><p class="eyebrow">Projectwizard</p><h2>Van onderwerp naar reviewbare documentaire</h2>
          <form method="post" action="/projects/new" enctype="multipart/form-data" class="production-form">
            <label class="wide">Onderwerp of productieprompt<textarea name="prompt" rows="6" required></textarea></label>
            <div class="start-grid">
              <label>Duur<select name="duration"><option>5</option><option selected>12</option><option>20</option><option>30</option></select></label>
              <label>Taal<select name="language"><option>Nederlands</option><option>English</option><option>Deutsch</option><option>Français</option></select></label>
              <label>Documentairestijl<select name="style"><option value="factual_documentary">Feitelijk</option><option value="investigative_documentary">Onderzoekend</option><option value="cinematic">Cinematisch</option></select></label>
              <label>Doelgroep<input name="audience" placeholder="Breed publiek, professionals..."></label>
              <label>Providerprofiel<select name="provider_profile"><option value="offline">Volledig lokaal</option><option value="balanced">Gebalanceerd</option><option value="quality">Hoogste kwaliteit</option></select></label>
              <label>Maximumbudget USD<input name="budget" type="number" min="0" step="0.01" value="0"></label>
              <label>Modus<select name="mode"><option value="review">Reviewmodus</option><option value="automatic">Automatisch</option></select></label>
            </div>
            <label>Screenshots<input type="file" name="screenshot" accept="image/*" multiple></label>
            <label>Lokale clips<input type="file" name="clip" accept="video/*,audio/*" multiple></label>
            <label class="wide">YouTube-links<textarea name="youtube_urls" rows="3" placeholder="Eén URL per regel"></textarea></label>
            <label>Bronnen of dossierbestanden<input type="file" name="dossier" accept=".json,.txt,.md,.pdf" multiple></label>
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
        if not prompt:
            return self.html(self.page("Prompt ontbreekt", '<section class="panel"><p>Voer een onderwerp of prompt in.</p></section>'), "400 Bad Request")
        project = create_project(self.settings.projects_dir, prompt[:100])
        try:
            duration = max(1, min(60, int(self.form_value(form, "duration", "12"))))
            budget = max(0.0, float(self.form_value(form, "budget", "0")))
        except ValueError:
            duration, budget = 12, 0.0
        workflow = self.read_manifest(project.root / "manifests/workflow.json")
        workflow.update({"target_duration_minutes": duration, "language": self.form_value(form, "language", "Nederlands"), "autonomy_mode": self.form_value(form, "mode", "review"), "content_mode": normalize_content_mode(self.form_value(form, "style", "factual_documentary")), "audience": self.form_value(form, "audience")})
        write_json(project.root / "manifests/workflow.json", workflow)
        write_json(project.root / "manifests/production_request.json", {"prompt": prompt, "target_duration_minutes": duration, "language": workflow["language"], "autonomy_mode": workflow["autonomy_mode"], "style": self.form_value(form, "style"), "audience": workflow["audience"]})
        profile = self.form_value(form, "provider_profile", "offline")
        write_json(project.root / "manifests/provider_config.json", {"version": 1, "profile": profile, "budget_usd": budget, "external_calls_enabled": profile != "offline" and budget > 0, "cache_enabled": True, "retries": 2, "tasks": {}})
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
        return self.redirect(f"/projects/{project.slug}/production")

    def production_overview_page(self, slug: str) -> str:
        # Deliberately return a light shell; stalled workers can never block this route.
        return self.page("Voortgang", f"""<section class="progress-shell" data-project="{escape(slug)}"><div class="loading-state"><span class="pulse"></span><div><h2>Voortgang wordt geladen</h2><p>Je dashboard is direct beschikbaar.</p></div></div><div id="progress-content"></div></section><script>{self.progress_script(slug)}</script>""")

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
        return f"""(() => {{ const slug={json.dumps(slug)}, esc=s=>String(s??'').replace(/[&<>\"']/g,c=>({{'&':'&amp;','<':'&lt;','>':'&gt;','\"':'&quot;',\"'\":'&#39;'}}[c]));
        const labels={{active:'Actief',waiting:'Wachtend',completed:'Afgerond',blocked:'Geblokkeerd',failed:'Fout',possibly_stalled:'Mogelijk vastgelopen',stopped:'Gestopt'}};
        const task=t=>`<article class="queue-item ${{esc(t.status)}}"><div><strong>${{esc(t.label)}}</strong><small>Start: ${{esc(t.started_at||'Nog niet gestart')}} · duur: ${{t.duration_seconds||0}} sec · pogingen: ${{t.retries||0}}</small>${{t.reason?`<p>${{esc(t.reason)}}</p>`:''}}</div><span class="status-pill">${{labels[t.status]||esc(t.status)}}</span>${{['possibly_stalled','blocked','failed'].includes(t.status)?`<div class="task-actions"><form method="post" action="/projects/${{slug}}/tasks/${{esc(t.id)}}/resume"><button>Hervatten</button></form><form method="post" action="/projects/${{slug}}/tasks/${{esc(t.id)}}/retry"><button class="secondary">Opnieuw proberen</button></form><a class="button ghost" href="#events">Log bekijken</a><form method="post" action="/projects/${{slug}}/tasks/${{esc(t.id)}}/stop"><button class="danger">Taak stoppen</button></form></div>`:''}}</article>`;
        async function refresh() {{ const d=await fetch(`/projects/${{slug}}/progress-data`).then(r=>r.json()), phases=d.phases.map((p,i)=>`<li class="${{esc(p.status)}}"><span>${{i+1}}</span><div><strong>${{esc(p.name)}}</strong><small>${{esc(p.status)}}</small></div></li>`).join(''), q=d.queue.tasks.map(task).join('')||'<p class="muted">Er staan geen taken in de wachtrij.</p>', r=d.research;
        const list=items=>`<ul>${{(items||[]).map(x=>`<li>${{esc(x)}}</li>`).join('')}}</ul>`, g=d.paid_gate, gateTitle=g.title||'Onderzoek wacht op jouw toestemming', gate=g.required?`<section class="approval-card" role="alert" aria-labelledby="approval-title"><div><p class="eyebrow">Toestemming nodig</p><h1 id="approval-title">${{esc(gateTitle)}}</h1><p><strong>Waarom:</strong> ${{esc(g.purpose)}}</p><div class="approval-facts"><span><small>Geschatte kosten</small><strong>$${{Number(g.maximum_cost_usd).toFixed(4)}}</strong></span><span><small>Extra bronnen</small><strong>${{Number(g.extra_sources||0)}}</strong></span><span><small>Landen</small><strong>${{esc((g.countries||[]).join(', '))}}</strong></span><span><small>Talen</small><strong>${{esc((g.languages||[]).join(', '))}}</strong></span></div><div class="approval-claims"><strong>Claims die hierdoor verbeterd worden</strong>${{list(g.claims)}}</div>${{g.within_budget?'':'<p class="error">Deze zoekactie overschrijdt de ingestelde projectlimiet; pas eerst het budget aan.</p>'}}</div><div class="approval-actions"><form method="post" action="/projects/${{slug}}/paid-research/approve"><button ${{g.within_budget?'':'disabled'}}>Goedkeuren en doorgaan</button></form><form method="post" action="/projects/${{slug}}/paid-research/cancel"><button class="ghost-button">Annuleren</button></form><form method="post" action="/projects/${{slug}}/paid-research/fallback"><button class="secondary" ${{g.local_fallback_available?'':'disabled'}}>Alleen lokaal doorgaan</button></form></div></section>`:'';
        document.querySelector('#progress-content').innerHTML=`${{gate}}<section class="project-summary"><div><p class="eyebrow">Huidige stap</p><h1>${{esc(d.current_phase)}}</h1><p>${{esc(d.last_activity)}}</p></div><div class="progress-number"><strong>${{d.percentage}}%</strong><span>${{d.remaining_steps}} stappen resterend${{d.estimated_remaining?' · '+esc(d.estimated_remaining):''}}</span></div></section><ol class="pipeline">${{phases}}</ol>${{g.required?'':`<section class="focus-card"><p class="eyebrow">Onderzoek nu</p><h2>${{esc(r.current_task)}}</h2><div class="research-metrics"><span><strong>${{r.sources_found}}</strong> bronnen gevonden</span><span><strong>${{r.sources_processed}}</strong> verwerkt</span><span><strong>${{r.draft_claims}}</strong> claims in concept</span><span><strong>${{esc(r.estimated_remaining)}}</strong> resterend</span></div><p>Laatst afgerond: ${{esc(r.last_completed)}} · Laatste voortgang: ${{esc(r.waiting_since||'Nog geen')}} · Actieve dienst: ${{esc(r.provider)}}</p>${{r.last_error?`<p class="error">${{esc(r.last_error)}}</p>`:''}}</section>`}}<section class="panel"><div class="section-head"><div><p class="eyebrow">Taakwachtrij</p><h2>Actief, wachtend en afgerond</h2></div></div><div class="task-queue">${{q}}</div></section><details id="events" class="panel"><summary>Geavanceerd: logs en technische details</summary><div class="event-list">${{d.events.map(e=>`<p><span class="flag">${{esc(e.event)}}</span> ${{esc(e.message)}}</p>`).join('')||'<p>Nog geen events.</p>'}}</div></details>`; document.querySelector('.loading-state').hidden=true; }} refresh(); setInterval(refresh,3000); }})();"""

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
        return self.page("Dossier & bronnen", f"""<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Dossier</strong></nav><div id="review-feedback" class="success" hidden></div><script>(()=>{{const n=new URLSearchParams(location.search).get('notice'),e=document.querySelector('#review-feedback');if(n){{e.textContent=n;e.hidden=false;}}}})();</script>{recovery}{f'<section class="panel coverage-analyzer"><h2>Internationale dekking</h2>{coverage_rows}</section>' if coverage_rows else ''}<section class="panel"><h2>Bronnen beoordelen</h2>{source_rows}<h2>Claims beoordelen en aanpassen</h2>{claim_rows or '<p>Geen conceptclaims.</p>'}</section>{approval}<section class="panel"><h2>Claim → scriptdekking</h2>{mappings}</section>""")

    def repair_research_review(self, slug: str, action: str) -> Response:
        root = self.project_root(slug)
        if action == "extract-claims":
            result = analyse_research_review(root)
            write_progress_event(root, "completed", "research_review", f"{result['claims_created']} conceptclaims lokaal opgesteld")
        else:
            manifests = root / "manifests"
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
        final_video = project_root / "exports" / "final_video.mp4"
        final_link = (
            f"<a class=\"button\" href=\"/projects/{escape(slug)}/download/final\">Video openen</a>"
            if final_video.exists()
            else "<span class=\"muted\">Video nog niet klaar.</span>"
        )
        return self.page(
            topic,
            f"""
            <section class="project-summary">
              <div>
                <p class="eyebrow">{escape(progress['current_phase'])}</p>
                <h2>{escape(topic)}</h2>
                <p class="muted">Laatste update: {escape(str(progress['last_update']))}</p>
              </div>
              <div class="progress-number"><strong>{progress['percentage']}%</strong><span>{escape(progress['estimated_remaining'])}</span>
                <a class="button" href="/projects/{escape(slug)}/production">Bekijk voortgang</a>
              </div>
            </section>
            {self.review_action_card(project_root, slug)}
            <details class="panel"><summary>Meer projectonderdelen</summary><div class="link-grid"><a href="/projects/{escape(slug)}/research-panel">Onderzoek</a><a href="/projects/{escape(slug)}/dossier-review">Bronnen en claims</a><a href="/projects/{escape(slug)}/draft-review">Script, Scènes en Beelden beoordelen</a><a href="/projects/{escape(slug)}/youtube-draft">Publicatieconcept</a>{final_link}</div></details>
            <details class="panel"><summary>Geavanceerd</summary><a href="/projects/{escape(slug)}/advanced">Geavanceerde instellingen, kosten en bestanden</a></details>
            """,
        )

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
        <nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Draft Review</strong></nav>
        <section class="review-player"><div><video controls preload="metadata" src="/projects/{escape(slug)}/preview/video"></video><div class="review-timeline">{timeline}</div></div><aside><h2>Reviewscore</h2><p>Director: <strong>{escape(str(director.get('critic_score', '—')))}</strong></p><p>Critic: <strong>{escape(str(critic.get('overall_score', '—')))}</strong></p><p>Ondertitels: <code>manifests/subtitles.srt</code></p></aside></section>
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
        changed = before.get("review_status") != status
        update_image_review(project_root, media_id, status)
        if changed:
            write_progress_event(project_root, "completed", "media_review", f"Media {'goedgekeurd' if status == 'approved' else 'afgewezen'}", item_id=media_id, review_status=status)
            self.resume_managed_production(project_root)
        return self.redirect(f"/projects/{quote(slug)}/advanced?notice={quote('Media goedgekeurd' if status == 'approved' else 'Media afgewezen')}#media-{quote(media_id)}")

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
        return self.page("YouTube draft", f"""<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>YouTube draft</strong></nav><section class="panel"><h2>Veilige export</h2><form method="post" action="/projects/{escape(slug)}/youtube-draft/save" class="grid-form"><label class="wide">Titel<input name="title" value="{escape(str(draft.get('title', '')))}"></label><label class="wide">Beschrijving<textarea name="description" rows="8">{escape(str(draft.get('description', '')))}</textarea></label><label class="wide">Hoofdstukken<textarea rows="8" readonly>{escape(chapters)}</textarea></label><label>Tags<input name="tags" value="{escape(', '.join(draft.get('tags', [])))}"></label><label>Privacy<select name="privacy_status"><option value="private">Private</option><option value="draft">Draft</option></select></label><p>Thumbnail: <code>{escape(str(draft.get('thumbnail')))}</code><br>Ondertitels: <code>{escape(str(draft.get('subtitles')))}</code><br>Video: <code>{escape(str(draft.get('video')))}</code></p><button>Draft opslaan</button></form></section><section class="review-card"><div><h2>Upload naar YouTube</h2><p>Publicatie gebeurt nooit automatisch. Alleen private/draft upload kan na expliciete bevestiging worden klaargezet.</p></div><form method="post" action="/projects/{escape(slug)}/youtube-draft/confirm-upload"><label><input type="checkbox" name="confirm" value="yes" required> Ik bevestig private/draft upload</label><button>Upload expliciet bevestigen</button></form></section>""")

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
                        return (
                            "200 OK",
                            [("Content-Type", "image/jpeg"), ("Content-Length", str(path.stat().st_size))],
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
        return self.page("Research Panel", f'<nav class="crumb"><a href="/projects/{escape(slug)}">Project</a><span>/</span><strong>Research Panel</strong></nav>{self.research_panel(self.project_root(slug), slug)}')

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
                image = f"<img src=\"/projects/{escape(slug)}/media/{escape(media_id)}/preview\" alt=\"\" loading=\"lazy\">"
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
                        <p>{escape(str(asset.get('creator', '')))}</p>
                        <p>{escape(str(asset.get('license', '')))}</p>
                        <p>Voorgestelde scène: {escape(', '.join(str(item) for item in asset.get('suggested_scenes', [])))}</p>
                        {duplicate}
                        <p><a href="{escape(str(asset.get('source_url', '')))}">Oorspronkelijke bron</a></p>
                        <div class="actions">
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/approve"><button type="submit">Goedkeuren</button></form>
                          <form method="post" action="/projects/{escape(slug)}/media/{escape(media_id)}/reject"><button type="submit" class="secondary">Afwijzen</button></form>
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
    body {{ margin:0; font-family: Arial, sans-serif; background:var(--page); color:var(--ink); }}
    header {{ background:rgba(255,255,255,.96); color:var(--ink); padding:16px 32px; display:flex; align-items:center; justify-content:space-between; gap:24px; border-bottom:1px solid var(--line); position:sticky; top:0; z-index:10; backdrop-filter:blur(12px); }}
    header h1 {{ margin:0; font-size:22px; letter-spacing:0; }}
    header p {{ margin:4px 0 0; color:var(--muted); }}
    .main-nav {{ display:flex; gap:5px; align-items:center; flex-wrap:wrap; }}
    .main-nav a {{ color:#43515b; text-decoration:none; padding:9px 11px; border-radius:8px; font-weight:650; font-size:14px; }}
    .main-nav a:hover {{ color:var(--accent); background:var(--soft); }}
    main {{ max-width:1120px; margin:0 auto; padding:32px 24px 48px; }}
    .hero-panel, .panel, .review-card, .project-card, .focus-card {{ background:var(--panel); border:1px solid var(--line); border-radius:14px; }}
    .hero-panel {{ padding:30px; margin-bottom:28px; box-shadow:0 16px 40px rgba(23,32,38,.07); }}
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
    .project-card {{ padding:18px; display:flex; justify-content:space-between; align-items:center; gap:16px; }}
    .project-card h3 {{ margin:0 0 4px; font-size:18px; }}
    .project-card p {{ margin:0; }}
    .project-card-actions {{ display:flex; align-items:center; gap:10px; }}
    .status-pill {{ display:inline-flex; align-items:center; min-height:34px; border-radius:999px; padding:6px 12px; background:var(--soft); color:#1d5f50; font-weight:700; font-size:13px; white-space:nowrap; }}
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
    .pipeline {{ list-style:none; padding:8px 0; margin:0 0 18px; display:grid; grid-template-columns:repeat(10,1fr); gap:6px; }}
    .pipeline li {{ display:flex; gap:8px; align-items:center; min-width:0; padding:10px 7px; border-radius:10px; color:var(--muted); }} .pipeline li span {{ display:grid; place-items:center; width:25px; height:25px; flex:0 0 25px; border-radius:50%; background:#e7ecef; font-size:12px; }} .pipeline li strong,.pipeline li small {{ display:block; font-size:12px; }}
    .pipeline li.afgerond span {{ background:#dcefe4;color:var(--ok); }} .pipeline li.actief {{ background:#eaf5f2;color:var(--accent-dark); }} .pipeline li.actief span {{ background:var(--accent);color:#fff; }}
    .pipeline li.approval_required,.pipeline li.blocked {{ background:#fff5e9;color:#8a5200; }} .pipeline li.approval_required span,.pipeline li.blocked span {{ background:var(--warn);color:#fff; }}
    .approval-card {{ display:grid;grid-template-columns:1fr auto;gap:28px;align-items:center;background:#fff;border:1px solid #e8c89f;border-radius:16px;padding:28px;margin-bottom:18px;box-shadow:0 12px 34px rgba(89,55,20,.08); }} .approval-card h1 {{ margin:5px 0 8px;font-size:28px; }} .approval-facts {{ display:flex;gap:10px;flex-wrap:wrap;margin-top:16px; }} .approval-facts span {{ display:grid;gap:3px;background:#fff8ef;border-radius:10px;padding:10px 14px; }} .approval-facts small {{ color:var(--muted); }} .approval-actions {{ display:grid;gap:8px;min-width:260px; }} .approval-actions form,.approval-actions button {{ width:100%; }} .ghost-button {{ background:#eef1f3;color:var(--ink); }}
    .focus-card {{ padding:24px; margin-bottom:18px; border-color:#cfe3dc; }} .research-metrics {{ display:flex; flex-wrap:wrap; gap:10px; margin:16px 0; }} .research-metrics span {{ background:var(--page);border-radius:10px;padding:10px 12px;font-size:13px; }}
    .loading-state {{ display:flex;align-items:center;gap:14px;background:#fff;border:1px solid var(--line);border-radius:14px;padding:24px; }} .loading-state h2,.loading-state p {{ margin:3px 0; }} .pulse {{ width:14px;height:14px;border-radius:50%;background:var(--accent);animation:pulse 1.2s infinite; }} @keyframes pulse {{ 50% {{ opacity:.35;transform:scale(.8); }} }}
    .task-queue {{ display:grid;gap:8px; }} .queue-item {{ display:grid;grid-template-columns:1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:11px;padding:13px; }} .queue-item small {{ display:block;color:var(--muted);margin-top:4px; }} .queue-item.possibly_stalled,.queue-item.blocked,.queue-item.failed {{ border-color:#e8c8ab;background:#fffaf5; }} .task-actions {{ grid-column:1/-1;display:flex;flex-wrap:wrap;gap:8px; }} button.danger {{ background:#a8473d; }} .link-grid {{ display:flex;flex-wrap:wrap;gap:16px;padding:16px 0; }}
    .review-player {{ display:grid; grid-template-columns:minmax(0,3fr) minmax(220px,1fr); gap:16px; margin-bottom:18px; }}
    .review-player > div, .review-player aside {{ background:#fff; border:1px solid var(--line); border-radius:8px; padding:14px; }}
    .review-player video {{ width:100%; background:#111; border-radius:6px; }}
    .review-timeline {{ display:flex; gap:8px; overflow-x:auto; padding-top:10px; }}
    .review-timeline a {{ flex:0 0 130px; text-decoration:none; font-size:12px; }}
    .review-timeline img, .scene-thumb {{ width:100%; aspect-ratio:16/9; object-fit:cover; border-radius:6px; }}
    .scene-thumb {{ max-width:360px; }}
    .revision-compare {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:8px; border:1px solid var(--line); border-radius:8px; padding:10px; margin:10px 0; }}
    .revision-compare > p {{ grid-column:1/-1; color:var(--muted); }}
    @media (max-width: 900px) {{ .pipeline {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .workflow {{ grid-template-columns:repeat(2, minmax(0, 1fr)); }} .start-grid, .summary-grid {{ grid-template-columns:1fr 1fr; }} .review-card, .project-card {{ align-items:flex-start; flex-direction:column; }} .actions {{ justify-content:flex-start; }} }}
    @media (max-width: 620px) {{ header {{ display:block; padding:14px; }} .main-nav {{ margin-top:12px;display:grid;grid-template-columns:1fr 1fr; }} .project-head,.project-summary {{ align-items:flex-start;flex-direction:column; }} .approval-card {{ grid-template-columns:1fr;padding:20px; }} .approval-actions {{ min-width:0;width:100%; }} .progress-number {{ justify-items:start; }} .actions {{ justify-content:flex-start; margin-top:12px; }} main {{ padding:18px 14px; }} .hero-panel {{ padding:22px; }} .hero-panel h2 {{ font-size:24px; }} .workflow, .pipeline, .start-grid, .summary-grid, .review-player, .revision-compare {{ grid-template-columns:1fr; }} .project-card-actions {{ width:100%; justify-content:space-between; }} table {{ display:block; overflow-x:auto; }} button,.button {{ min-height:44px; }} }}
  </style>
</head>
<body>
  <header>
    <div><h1>Documentaire Studio</h1><p>Van idee tot gecontroleerde film</p></div>
    <nav class="main-nav" aria-label="Hoofdnavigatie"><a href="/">Projecten</a><a href="/projects/new">Nieuwe documentaire</a><a href="/">Voortgang</a><a href="/">Review</a><a href="/#settings">Instellingen</a></nav>
  </header>
  <main>{body}</main>
</body>
</html>"""


def run_dashboard(root: Path | None = None, host: str = "127.0.0.1", port: int = 8000) -> None:
    app = DashboardApp(root)
    with make_server(host, port, app) as server:
        app.resume_recoverable_projects()
        print(f"Inside the Case Factory dashboard: http://{host}:{port}", flush=True)
        server.serve_forever()
