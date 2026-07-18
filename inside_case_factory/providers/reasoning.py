from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace
from inside_case_factory.core.content_modes import mode_prompt


class ReasoningProviderError(RuntimeError):
    pass


def _project_content_mode(project_root: Path) -> str:
    for name in ("workflow.json", "production_request.json", "production_plan.json"):
        path = project_root / "manifests" / name
        if path.exists():
            data = read_json(path)
            if isinstance(data, dict) and data.get("content_mode"):
                return str(data["content_mode"])
    return "factual_documentary"


@dataclass(frozen=True)
class ReasoningConfig:
    provider: str = "openai"
    enabled: bool = False
    model: str = "gpt-4.1-mini"
    strategy: str = "low_cost"
    reasoning_effort: str = "medium"
    max_output_tokens: int = 4000
    max_sources_analyzed: int = 8
    max_source_text_length: int = 18000
    per_project_spending_limit_usd: float = 0.25
    estimated_cost_per_call_usd: float = 0.05
    estimated_input_cost_per_1k_tokens_usd: float = 0.0
    estimated_output_cost_per_1k_tokens_usd: float = 0.0
    dry_run: bool = False
    require_explicit_confirmation: bool = False
    stages: dict[str, dict[str, Any]] | None = None

    def stage(self, operation: str) -> dict[str, Any]:
        configured = (self.stages or {}).get(operation, {})
        return {
            "model": str(configured.get("model", self.model)),
            "max_input_tokens": int(configured.get("max_input_tokens", 0)),
            "max_output_tokens": int(configured.get("max_output_tokens", self.max_output_tokens)),
            "input_cost_per_million_tokens_usd": float(
                configured.get("input_cost_per_million_tokens_usd", self.estimated_input_cost_per_1k_tokens_usd * 1000)
            ),
            "output_cost_per_million_tokens_usd": float(
                configured.get("output_cost_per_million_tokens_usd", self.estimated_output_cost_per_1k_tokens_usd * 1000)
            ),
        }


class ReasoningProvider(ABC):
    name: str

    @property
    @abstractmethod
    def available(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def analyze_request(self, project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def analyze_sources(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        sources: list[dict[str, Any]],
        tavily_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def create_narrative_outline(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
    ) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def write_script(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        narrative_outline: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        raise NotImplementedError

    def rewrite_script_passages(
        self, project_root: Path, repair_plan: dict[str, Any],
        target_duration_minutes: int, language: str,
    ) -> dict[str, Any]:
        raise NotImplementedError("This reasoning provider does not support surgical passage rewriting.")

    @abstractmethod
    def generate_scenes(
        self,
        project_root: Path,
        script: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
    ) -> dict[str, Any]:
        raise NotImplementedError


class DisabledReasoningProvider(ReasoningProvider):
    name = "disabled"

    @property
    def available(self) -> bool:
        return False

    def _blocked(self, project_root: Path, operation: str, message: str) -> dict[str, Any]:
        payload = {
            "version": 1,
            "provider": self.name,
            "operation": operation,
            "status": "blocked",
            "message": message,
            "created_at": datetime.now(UTC).isoformat(),
        }
        write_json(project_root / "manifests" / f"{operation}.json", payload)
        return payload

    def analyze_request(self, project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        return self._blocked(project_root, "research_plan", "Reasoning provider is disabled.")

    def analyze_sources(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        sources: list[dict[str, Any]],
        tavily_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self._blocked(project_root, "source_analysis", "Reasoning provider is disabled.")

    def create_narrative_outline(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
        quality_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._blocked(project_root, "narrative_outline", "Reasoning provider is disabled.")

    def write_script(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        narrative_outline: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
    ) -> dict[str, Any]:
        return self._blocked(project_root, "script", "Reasoning provider is disabled.")

    def generate_scenes(
        self,
        project_root: Path,
        script: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
    ) -> dict[str, Any]:
        return self._blocked(project_root, "scenes", "Reasoning provider is disabled.")


class OpenAIReasoningProvider(ReasoningProvider):
    name = "openai"
    endpoint = "https://api.openai.com/v1/responses"

    def __init__(self, config: ReasoningConfig, api_key: str | None = None) -> None:
        self.config = config
        self.api_key = api_key or os.environ.get("OPENAI_API_KEY")

    @property
    def available(self) -> bool:
        return bool(self.config.enabled and self.api_key and not self.config.dry_run)

    def analyze_request(self, project_root: Path, request: dict[str, Any]) -> dict[str, Any]:
        if self.config.dry_run:
            payload = fallback_research_plan(request, "Dry-run mode is enabled; OpenAI was not called.")
            write_json(project_root / "manifests" / "research_plan.json", payload)
            return payload
        result = self._json_response(
            project_root,
            "research_plan",
            "Convert the user's documentary request into a precise factual research plan.",
            {
                "production_request": request,
                "content_mode_instructions": mode_prompt(request.get("content_mode")),
                "requirements": [
                    "Keep dashboard language separate from generated video language.",
                    "Return specific research questions that must be answered before scripting.",
                    "Do not invent facts.",
                ],
            },
            RESEARCH_PLAN_SCHEMA,
        )
        write_json(project_root / "manifests" / "research_plan.json", result)
        return result

    def group_claims(self, project_root: Path, claims: list[dict[str, Any]]) -> dict[str, Any]:
        """Identify equivalent propositions without changing claim evidence."""
        compact_claims = [
            {
                "id": claim.get("id"), "text": claim.get("text"),
                "source_ids": claim.get("source_ids", []),
                "research_question_ids": claim.get("research_question_ids", []),
                "confidence": claim.get("confidence", ""),
            }
            for claim in claims
        ]
        result = self._json_response(
            project_root,
            "corroboration",
            "Group only claims that express the same specific factual proposition. Do not group claims merely because they share a person, topic, medication, or event. Preserve distinctions such as receiving a drug versus a drug being detected, allegation versus established fact, and event versus consequence.",
            {
                "claims": compact_claims,
                "rules": [
                    "Every member must retain its original claim ID and evidence outside this response.",
                    "A group must contain at least two claims with genuinely equivalent propositions.",
                    "Use canonical propositions that are specific enough to reject broad-topic matches.",
                    "Identify disagreements or nuances instead of forcing a group.",
                ],
            },
            CORROBORATION_SCHEMA,
        )
        write_json(project_root / "manifests" / "corroboration_ai.json", result)
        return result

    def build_story_architecture(self, project_root: Path, research_plan: dict[str, Any], dossier: dict[str, Any], timeline: dict[str, Any], claims: list[dict[str, Any]], snapshots: list[dict[str, Any]], target_duration_minutes: int) -> dict[str, Any]:
        compact_claims = [{"id": c.get("id"), "text": c.get("text"), "source_ids": c.get("source_ids", []), "dates": c.get("date", ""), "classification": c.get("evidence_classification", "verified_fact")} for c in claims]
        result = self._json_response(
            project_root,
            "story_architecture",
            "Build a detailed continuous narrative architecture before scriptwriting. Only genuine narrative events belong in beats; all audit, closing, reflection, and metadata content belongs in its dedicated top-level field.",
            {
                "research_plan": research_plan, "dossier": dossier, "timeline": timeline,
                "claims": compact_claims, "extracted_source_count": len(snapshots), "target_duration_minutes": target_duration_minutes,
                "mode_instructions": mode_prompt(_project_content_mode(project_root)),
                "requirements": [
                    "For every beat provide: what happens, what the viewer learns, why it appears there, the carried curiosity question, supporting claim IDs, and high-value details to use.",
                    "Move primarily chronologically while slowing down at the emergency, medical evidence, investigation, and trial contradictions.",
                    "Audit unused high-value research details and identify unsupported or uncertain areas; never invent missing events.",
                ],
            }, STORY_ARCHITECTURE_SCHEMA,
        )
        from inside_case_factory.core.narrative_quality import validate_architecture_file
        report = validate_architecture_file(project_root, result)
        if not report["valid"]:
            raise ReasoningProviderError("Malformed story architecture: " + "; ".join(report["errors"]))
        write_json(project_root / "manifests/story_architecture.json", result)
        return result

    def analyze_sources(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        sources: list[dict[str, Any]],
        tavily_results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        limited_results = []
        source_lookup = {str(source.get("id", "")): source for source in sources}
        for result in tavily_results:
            if len(limited_results) >= self.config.max_sources_analyzed:
                break
            source_id = str(result.get("source_id", ""))
            source = source_lookup.get(source_id)
            if not source:
                continue
            content = str(result.get("raw_content") or result.get("content") or "")
            limited_results.append(
                {
                    "source": source,
                    "snapshot": {key: result.get(key) for key in ("extraction_method", "content_hash", "content_length")},
                    "content": content[: self.config.max_source_text_length],
                }
            )
        result = self._json_response(
            project_root,
            "source_analysis",
            "Clean, analyze, and extract only evidence-supported documentary claims from Tavily source content.",
            {
                "research_plan": research_plan,
                "content_mode_instructions": mode_prompt(research_plan.get("content_mode")),
                "sources": limited_results,
                "rules": [
                    "Reject navigation, captions, lyrics, transcript filler, ads, UI fragments, unrelated descriptions, and unsupported statements.",
                    "Extract multiple atomic claims from every usable source; each claim must be one independently verifiable factual statement.",
                    "Across the full research set, target 30-60 proposed claims when the evidence supports them.",
                    "Each claim must include exact evidence, source IDs, a stable semantic canonical_key, and applicable research_question_ids.",
                    "Classify every claim as exactly one of: verified_fact, single_source_claim, allegation, witness_statement, official_explanation, alternative_explanation, disputed_claim, interpretation, speculation, unanswered_question.",
                    "Preserve supported dates, times, people, locations, and events; never invent precision.",
                    "Prefer primary and high-quality secondary sources. Flag weak single-source claims.",
                    "Do not create claims from raw fragments that are not factual and relevant to the production prompt.",
                ],
            },
            SOURCE_ANALYSIS_SCHEMA,
        )
        write_json(project_root / "manifests" / "source_analysis.json", result)
        return result

    def create_narrative_outline(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
    ) -> dict[str, Any]:
        result = self._json_response(
            project_root,
            "narrative_outline",
            "Create a compelling documentary narrative plan from approved research only. " + mode_prompt(_project_content_mode(project_root)),
            {
                "research_plan": research_plan,
                "dossier": dossier,
                "approved_claims": approved_claims,
                "target_duration_minutes": target_duration_minutes,
                "video_language": language,
            },
            NARRATIVE_OUTLINE_SCHEMA,
        )
        write_json(project_root / "manifests" / "narrative_outline.json", result)
        return result

    def write_script(
        self,
        project_root: Path,
        research_plan: dict[str, Any],
        dossier: dict[str, Any],
        narrative_outline: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
        language: str,
        quality_report: dict[str, Any] | None = None,
        word_range: tuple[int, int] = (1500, 1700),
    ) -> dict[str, Any]:
        minimum_words, maximum_words = word_range
        result = self._json_response(
            project_root,
            "script",
            f"Write the final {language} voice-over narration at {minimum_words}-{maximum_words} words using the complete story architecture. Map every narration section to one or more stable beat_ids and cover all required beats. Write natural spoken {language}, not formal prose or a translation from English. Use a restrained documentary tone, concrete language, varied sentence rhythm, and transitions that sound natural aloud. Avoid awkward inversions, inflated vocabulary, generic suspense clichés, invented emotion, repetitive rhetorical questions, and translated-English constructions. Every paragraph must add new factual or narrative value. Preserve all evidence provenance and attribution. " + mode_prompt(_project_content_mode(project_root)),
            {
                "story_architecture": read_json(project_root / "manifests/story_architecture.json") if (project_root / "manifests/story_architecture.json").exists() else {},
                "approved_claims": approved_claims,
                "previous_quality_report": quality_report or {},
                "target_duration_minutes": target_duration_minutes,
                "video_language": language,
                "rules": [
                    "No invented thoughts, dialogue, motives, events, or unsupported details.",
                    f"For this final revision, write {minimum_words}-{maximum_words} words, with a strong opening hook, chronological sections, attributed allegations and disputed interpretations, and a case-connected ending.",
                    "Every script section must include beat_ids, and every required architecture beat must be represented.",
                    "Suspenseful but factual.",
                    "Preserve citations internally with claim IDs.",
                    "Avoid repetitive wording.",
                    "Write natural Dutch for voice-over when video_language is Dutch or Nederlands: clear spoken Dutch, restrained and concrete, with varied sentence lengths and no literal English syntax.",
                    "Do not use generic suspense language, invented emotion, repetitive rhetorical questions, awkward inversions, inflated vocabulary, or bureaucratic abstractions.",
                    "Avoid parenthetical and citation-like wording in spoken narration; express dates and numbers as a Dutch speaker would say them aloud.",
                    "Each paragraph must add new factual or narrative value; do not repeat a summary or reuse the same transition or sentence template.",
                    f"The narration alone must contain at least {minimum_words} and at most {maximum_words} words. Count it before returning JSON; section metadata does not count.",
                    "Keep claim_ids and beat_ids only in their structured section fields. Never put claim IDs, beat IDs, source markers, citations, or parenthetical metadata in narration or section text.",
                    "Keep every spoken sentence at 32 words or fewer. Split complex clauses into natural voice-over sentences.",
                    "Before returning, proofread the Dutch aloud for idiom, agreement, tense consistency, compound number spelling, concrete wording, and natural transitions. Replace literal, bureaucratic, or generic moralizing phrases.",
                    "Do not use rhetorical questions. State the supported uncertainty directly and concretely.",
                    "Do not end with a broad lesson, social value, new standard, restored trust, or claimed effect unless an approved claim explicitly supports it. End on the last concrete supported consequence.",
                    "Tell one continuous unfolding story rather than listing research categories. Every paragraph must either change the situation, complicate an explanation, reveal a consequence, or create a specific forward question.",
                    "Use the story architecture beats in order and perform its research-utilization audit; do not omit high-value supported details without reason.",
                ],
            },
            SCRIPT_SCHEMA,
        )
        return result

    def rewrite_script_passages(
        self, project_root: Path, repair_plan: dict[str, Any],
        target_duration_minutes: int, language: str,
    ) -> dict[str, Any]:
        return self._json_response(
            project_root,
            "script",
            "Return one minimal replacement passage per target_id. Never return or rewrite a complete script. Change only the stated original passage, address only its concrete validator errors, and use only the approved claims embedded in that repair item.",
            {
                "repairs": repair_plan.get("repairs", []),
                "target_duration_minutes": target_duration_minutes,
                "video_language": language,
                "rules": [
                    "Return exactly one target_id and replacement_passage for every supplied repair.",
                    "Do not return narration, sections, metadata, commentary, or any non-target text.",
                    "Do not introduce a name, date, year, number, event, or conclusion absent from that repair's approved_claims.",
                    "If a repair item is a word-count shortfall, expand only that passage with approved-claim detail until the full script reaches the requested minimum.",
                    "If a repair item is a word-count excess, compress only that passage without changing any other passage.",
                    "An empty replacement_passage is allowed when the target must be removed.",
                ],
            },
            SCRIPT_REPLACEMENTS_SCHEMA,
        )

    def generate_scenes(
        self,
        project_root: Path,
        script: dict[str, Any],
        dossier: dict[str, Any],
        approved_claims: list[dict[str, Any]],
        target_duration_minutes: int,
    ) -> dict[str, Any]:
        result = self._json_response(
            project_root,
            "scenes",
            "Create detailed documentary scenes from the approved script and dossier while preserving epistemic labels. " + mode_prompt(_project_content_mode(project_root)),
            {
                "script": script,
                "dossier": dossier,
                "approved_claims": approved_claims,
                "target_duration_minutes": target_duration_minutes,
                "rules": [
                    "Create 35-60 distinct visual beats, not one scene per script section. Each beat must have narration, estimated_duration_seconds, claim_ids, people, locations, dates, events, media_requirements, archival_media_queries, alternative_media_queries, camera_movement, transition_notes, on_screen_text, and ai_visual_prompt.",
                    "Use archival photographs, news footage, documents, headlines, court images, maps, timeline graphics, and text cards before fallback AI visuals. Do not search for or generate media in this call.",
                    "Every scene must map to approved claim IDs where factual.",
                    "Use fallback AI visual prompts only when real archival media is unavailable.",
                    "Include media requirements and archival search queries.",
                ],
            },
            SCENES_SCHEMA,
        )
        write_json(project_root / "manifests" / "scenes.json", result)
        return result

    def _json_response(
        self,
        project_root: Path,
        operation: str,
        instruction: str,
        payload: dict[str, Any],
        schema: dict[str, Any],
    ) -> dict[str, Any]:
        self._ensure_callable(project_root, operation)
        stage = self.config.stage(operation)
        request_payload = {
            "model": stage["model"],
            "max_output_tokens": stage["max_output_tokens"],
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are the runtime reasoning layer for a factual documentary production system. "
                        "Return only valid JSON matching the requested schema. Preserve provenance. "
                        "Reject junk content and unsupported claims."
                    ),
                },
                {"role": "user", "content": json.dumps({"instruction": instruction, "input": payload}, ensure_ascii=False)},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema["name"],
                    "strict": True,
                    "schema": schema["schema"],
                }
            },
        }
        if not str(stage["model"]).startswith("gpt-4.1"):
            request_payload["reasoning"] = {"effort": self.config.reasoning_effort}
        body = json.dumps(request_payload).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.api_key or ''}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=90) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ReasoningProviderError(f"OpenAI Responses API error {error.code}: {detail}") from error
        except URLError as error:
            raise ReasoningProviderError(f"OpenAI Responses API network error: {error}") from error

        parsed = parse_response_json(response_data)
        self._record_usage(project_root, operation, response_data)
        return parsed

    def _ensure_callable(self, project_root: Path, operation: str) -> None:
        if not self.config.enabled:
            raise ReasoningProviderError("OpenAI reasoning is not enabled.")
        if self.config.dry_run:
            raise ReasoningProviderError("OpenAI reasoning dry-run mode is enabled.")
        if not self.api_key:
            raise ReasoningProviderError("OPENAI_API_KEY is not set.")
        estimated = self._preflight_estimated_cost(operation)
        if self.config.require_explicit_confirmation and not paid_api_confirmed(project_root, operation, estimated):
            estimate = estimate_reasoning_cost(self.config)
            raise ReasoningProviderError(
                "Paid API call not confirmed. Review manifests/cost_estimate.json, then create "
                "manifests/paid_api_confirmation.json with confirmed=true explicitly for this project. "
                f"Estimated maximum reasoning cost: ${estimate['estimated_maximum_cost_usd']:.4f}."
            )
        current = current_estimated_spend(project_root)
        if current + estimated > self.config.per_project_spending_limit_usd:
            raise ReasoningProviderError(
                f"Project reasoning budget would be exceeded before {operation}: "
                f"{current + estimated:.4f} > {self.config.per_project_spending_limit_usd:.4f} USD."
            )

    def _preflight_estimated_cost(self, operation: str) -> float:
        stage = self.config.stage(operation)
        token_based = (
            (stage["max_input_tokens"] / 1_000_000.0) * stage["input_cost_per_million_tokens_usd"]
            + (stage["max_output_tokens"] / 1_000_000.0) * stage["output_cost_per_million_tokens_usd"]
        )
        return token_based

    def _record_usage(self, project_root: Path, operation: str, response_data: dict[str, Any]) -> None:
        usage = response_data.get("usage", {}) if isinstance(response_data, dict) else {}
        input_tokens = int(usage.get("input_tokens", 0) or usage.get("prompt_tokens", 0) or 0) if isinstance(usage, dict) else 0
        output_tokens = int(usage.get("output_tokens", 0) or usage.get("completion_tokens", 0) or 0) if isinstance(usage, dict) else 0
        stage = self.config.stage(operation)
        cost = ((input_tokens / 1_000_000.0) * stage["input_cost_per_million_tokens_usd"]
            + (output_tokens / 1_000_000.0) * stage["output_cost_per_million_tokens_usd"])
        path = project_root / "manifests" / "reasoning_usage.json"
        if path.exists():
            manifest = read_json(path)
            if not isinstance(manifest, dict):
                manifest = {"version": 1, "calls": []}
        else:
            manifest = {"version": 1, "calls": []}
        calls = manifest.setdefault("calls", [])
        if not isinstance(calls, list):
            calls = []
            manifest["calls"] = calls
        calls.append(
            {
                "at": datetime.now(UTC).isoformat(),
                "provider": self.name,
                "operation": operation,
                "model": stage["model"],
                "reasoning_effort": self.config.reasoning_effort,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "token_based_estimated_cost_usd": round(cost, 6),
                "actual_provider_billing_usd": None,
            }
        )
        manifest["token_based_estimated_total_cost_usd"] = round(
            sum(float(call.get("token_based_estimated_cost_usd", 0) or 0) for call in calls if isinstance(call, dict)),
            6,
        )
        manifest["actual_provider_billing_total_usd"] = None
        manifest["spending_limit_usd"] = self.config.per_project_spending_limit_usd
        write_json(path, manifest)


def current_estimated_spend(project_root: Path) -> float:
    path = project_root / "manifests" / "reasoning_usage.json"
    if not path.exists():
        return 0.0
    data = read_json(path)
    if not isinstance(data, dict):
        return 0.0
    return float(data.get("token_based_estimated_total_cost_usd", data.get("estimated_total_cost_usd", 0)) or 0)


def paid_api_confirmed(project_root: Path, operation: str = "", required_cost_usd: float = 0.0) -> bool:
    path = project_root / "manifests" / "paid_api_confirmation.json"
    if not path.exists():
        return False
    data = read_json(path)
    if not isinstance(data, dict) or data.get("confirmed") is not True:
        return False
    # Legacy confirmations remain readable for existing projects. New dashboard
    # confirmations are deliberately limited by project, operation and amount.
    if "project" not in data:
        return True
    if data.get("project") != project_root.name:
        return False
    operations = data.get("operations", [])
    if operation and operation not in operations:
        return False
    return float(data.get("approved_limit_usd", 0)) + 1e-9 >= max(0.0, required_cost_usd)


def estimate_reasoning_cost(config: ReasoningConfig) -> dict[str, Any]:
    stages = []
    for operation in ("research_plan", "source_analysis", "narrative_outline", "script", "scenes"):
        stage = config.stage(operation)
        cost = (
            stage["max_input_tokens"] / 1_000_000.0 * stage["input_cost_per_million_tokens_usd"]
            + stage["max_output_tokens"] / 1_000_000.0 * stage["output_cost_per_million_tokens_usd"]
        )
        stages.append({"stage": operation, "model": stage["model"], "estimated_maximum_cost_usd": round(cost, 6)})
    total = sum(item["estimated_maximum_cost_usd"] for item in stages)
    return {
        "version": 1,
        "strategy": config.strategy,
        "planning_assumptions_only": True,
        "dry_run": config.dry_run,
        "paid_calls_confirmed": False,
        "project_budget_usd": config.per_project_spending_limit_usd,
        "estimated_maximum_cost_usd": round(total, 6),
        "within_budget": total <= config.per_project_spending_limit_usd,
        "stages": stages,
    }


def parse_response_json(response_data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response_data.get("output_text"), str):
        return json.loads(response_data["output_text"])
    output = response_data.get("output", [])
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            content = item.get("content", [])
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict):
                        text = part.get("text")
                        if isinstance(text, str) and text.strip():
                            return json.loads(text)
    raise ReasoningProviderError("OpenAI response did not contain parseable JSON text.")


def reasoning_config_from_settings(settings: dict[str, Any]) -> ReasoningConfig:
    return ReasoningConfig(
        provider=str(settings.get("provider", "openai")),
        enabled=bool(settings.get("enabled", False)),
        model=str(settings.get("model", "gpt-4.1-mini")),
        strategy=str(settings.get("strategy", "low_cost")),
        reasoning_effort=str(settings.get("reasoning_effort", "low")),
        max_output_tokens=int(settings.get("max_output_tokens", 4000)),
        max_sources_analyzed=int(settings.get("max_sources_analyzed", 8)),
        max_source_text_length=int(settings.get("max_source_text_length", 18000)),
        per_project_spending_limit_usd=float(settings.get("per_project_spending_limit_usd", 0.25)),
        estimated_cost_per_call_usd=float(settings.get("estimated_cost_per_call_usd", 0.05)),
        estimated_input_cost_per_1k_tokens_usd=float(settings.get("estimated_input_cost_per_1k_tokens_usd", 0.0)),
        estimated_output_cost_per_1k_tokens_usd=float(settings.get("estimated_output_cost_per_1k_tokens_usd", 0.0)),
        dry_run=bool(settings.get("dry_run", False)),
        require_explicit_confirmation=bool(settings.get("require_explicit_confirmation", True)),
        stages=settings.get("stages", {}) if isinstance(settings.get("stages", {}), dict) else {},
    )


def reasoning_provider_from_settings(settings: dict[str, Any]) -> ReasoningProvider:
    config = reasoning_config_from_settings(settings)
    if config.provider == "openai":
        return OpenAIReasoningProvider(config)
    if config.provider in {"gemini", "claude", "local"}:
        return StructuredTextReasoningProvider(config)
    return DisabledReasoningProvider()


class StructuredTextReasoningProvider(OpenAIReasoningProvider):
    """Runs the existing schema-driven documentary operations on non-OpenAI text providers."""

    def __init__(self, config: ReasoningConfig) -> None:
        from inside_case_factory.providers.production import (
            ClaudeTextProvider, GeminiTextProvider, LocalTextProvider, ProviderConfig,
        )
        self.config = config
        mapping = {
            "gemini": (GeminiTextProvider, "GEMINI_API_KEY", "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"),
            "claude": (ClaudeTextProvider, "ANTHROPIC_API_KEY", "https://api.anthropic.com/v1/messages"),
            "local": (LocalTextProvider, "", str((config.stages or {}).get("endpoint", "http://127.0.0.1:11434/v1/chat/completions"))),
        }
        provider_class, key_env, endpoint = mapping[config.provider]
        model = config.model
        endpoint = endpoint.format(model=model)
        self.adapter = provider_class(ProviderConfig(
            name=f"{config.provider}_text", kind="text", model=model, enabled=config.enabled,
            api_key_env=key_env, endpoint=endpoint, estimated_cost_usd=config.estimated_cost_per_call_usd,
        ))
        self.name = config.provider

    @property
    def available(self) -> bool:
        return self.adapter.available and not self.config.dry_run

    def _ensure_callable(self, project_root: Path, operation: str) -> None:
        if not self.available:
            raise ReasoningProviderError(f"{self.name} reasoning is not available.")
        if self.name != "local" and self.config.require_explicit_confirmation and not paid_api_confirmed(project_root, operation, self.config.estimated_cost_per_call_usd):
            raise ReasoningProviderError("Paid API call not confirmed for this project.")
        if current_estimated_spend(project_root) + self.config.estimated_cost_per_call_usd > self.config.per_project_spending_limit_usd:
            raise ReasoningProviderError(f"Project reasoning budget would be exceeded before {operation}.")

    def _json_response(
        self, project_root: Path, operation: str, instruction: str,
        payload: dict[str, Any], schema: dict[str, Any],
    ) -> dict[str, Any]:
        from inside_case_factory.providers.production import ProductionRequest as RoutedRequest
        self._ensure_callable(project_root, operation)
        prompt = json.dumps({
            "system": "Return only valid JSON matching the supplied JSON Schema. Preserve provenance and never invent facts.",
            "instruction": instruction, "input": payload, "json_schema": schema["schema"],
        }, ensure_ascii=False)
        try:
            response = self.adapter.generate(RoutedRequest("text", operation, prompt, project_root, options={"max_tokens": self.config.max_output_tokens}))
            text = response.content.strip()
            if text.startswith("```"):
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
            parsed = json.loads(text)
        except (ValueError, json.JSONDecodeError, RuntimeError) as error:
            raise ReasoningProviderError(f"{self.name} returned invalid structured output: {error}") from error
        usage_path = project_root / "manifests" / "reasoning_usage.json"
        usage = read_json(usage_path) if usage_path.exists() else {"version": 1, "calls": [], "estimated_total_cost_usd": 0.0}
        usage["calls"].append({"operation": operation, "provider": self.name, "model": self.config.model, "estimated_cost_usd": response.cost_usd})
        usage["estimated_total_cost_usd"] = round(float(usage.get("estimated_total_cost_usd", 0)) + response.cost_usd, 6)
        write_json(usage_path, usage)
        return parsed


def fallback_research_plan(request: dict[str, Any], message: str = "") -> dict[str, Any]:
    prompt = compact_whitespace(str(request.get("prompt") or request.get("topic") or ""))
    return {
        "version": 1,
        "provider": "local_fallback",
        "status": "draft" if not message else "blocked",
        "message": message,
        "exact_topic": prompt[:160] or "Untitled documentary",
        "documentary_angle": "",
        "requested_focus": prompt,
        "target_duration_minutes": int(request.get("target_duration_minutes", 10) or 10),
        "video_language": str(request.get("language", "English")),
        "people": [],
        "locations": [],
        "dates": [],
        "events": [],
        "exclusions": [],
        "factual_questions": [],
        "created_at": datetime.now(UTC).isoformat(),
    }


def fallback_dossier() -> dict[str, Any]:
    return {
        "version": 1,
        "status": "draft",
        "summary": "",
        "key_facts": [],
        "corroborated_claim_ids": [],
        "single_source_claim_ids": [],
        "weak_source_claim_ids": [],
        "contradictions": [],
        "source_quality_notes": [],
        "primary_evidence": [],
        "secondary_evidence": [],
        "tertiary_evidence": [],
        "unresolved_questions": [],
    }


STRING_ARRAY = {"type": "array", "items": {"type": "string"}}

RESEARCH_PLAN_SCHEMA = {
    "name": "research_plan",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "version",
            "status",
            "exact_topic",
            "documentary_angle",
            "requested_focus",
            "target_duration_minutes",
            "video_language",
            "people",
            "locations",
            "dates",
            "events",
            "exclusions",
            "factual_questions",
        ],
        "properties": {
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "exact_topic": {"type": "string"},
            "documentary_angle": {"type": "string"},
            "requested_focus": {"type": "string"},
            "target_duration_minutes": {"type": "integer"},
            "video_language": {"type": "string"},
            "people": STRING_ARRAY,
            "locations": STRING_ARRAY,
            "dates": STRING_ARRAY,
            "events": STRING_ARRAY,
            "exclusions": STRING_ARRAY,
            "factual_questions": STRING_ARRAY,
        },
    },
}

CLAIM_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "text",
        "evidence_classification",
        "canonical_key",
        "research_question_ids",
        "source_ids",
        "evidence",
        "relevance_score",
        "confidence",
        "source_quality",
        "corroboration_status",
        "people",
        "locations",
        "dates",
        "events",
        "contradiction_notes",
        "review_status",
    ],
    "properties": {
                    "text": {"type": "string"},
        "evidence_classification": {"type": "string"},
        "canonical_key": {"type": "string"},
        "research_question_ids": STRING_ARRAY,
        "source_ids": STRING_ARRAY,
        "evidence": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["source_id", "exact_excerpt", "start", "end", "searchable_text"],
                "properties": {
                    "source_id": {"type": "string"}, "exact_excerpt": {"type": "string"},
                    "start": {"type": "integer"}, "end": {"type": "integer"}, "searchable_text": {"type": "string"}
                }
            }
        },
        "relevance_score": {"type": "number"},
        "confidence": {"type": "string"},
        "source_quality": {"type": "string"},
        "corroboration_status": {"type": "string"},
        "people": STRING_ARRAY,
        "locations": STRING_ARRAY,
        "dates": STRING_ARRAY,
        "events": STRING_ARRAY,
        "contradiction_notes": {"type": "string"},
        "review_status": {"type": "string"},
    },
}

SOURCE_ANALYSIS_SCHEMA = {
    "name": "source_analysis",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "status", "source_analysis", "claims", "dossier", "timeline"],
        "properties": {
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "source_analysis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "source_id",
                        "relevant",
                        "usable",
                        "source_type",
                        "source_quality",
                        "summary",
                        "evidence_excerpts",
                        "rejection_reason",
                    ],
                    "properties": {
                        "source_id": {"type": "string"},
                        "relevant": {"type": "boolean"},
                        "usable": {"type": "boolean"},
                        "source_type": {"type": "string"},
                        "source_quality": {"type": "string"},
                        "summary": {"type": "string"},
                        "evidence_excerpts": STRING_ARRAY,
                        "rejection_reason": {"type": "string"},
                    },
                },
            },
            "claims": {"type": "array", "items": CLAIM_SCHEMA},
            "dossier": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "version",
                    "status",
                    "summary",
                    "key_facts",
                    "corroborated_claim_ids",
                    "single_source_claim_ids",
                    "weak_source_claim_ids",
                    "contradictions",
                    "source_quality_notes",
                    "primary_evidence",
                    "secondary_evidence",
                    "tertiary_evidence",
                    "unresolved_questions",
                ],
                "properties": {
                    "version": {"type": "integer"},
                    "status": {"type": "string"},
                    "summary": {"type": "string"},
                    "key_facts": STRING_ARRAY,
                    "corroborated_claim_ids": STRING_ARRAY,
                    "single_source_claim_ids": STRING_ARRAY,
                    "weak_source_claim_ids": STRING_ARRAY,
                    "contradictions": STRING_ARRAY,
                    "source_quality_notes": STRING_ARRAY,
                    "primary_evidence": STRING_ARRAY,
                    "secondary_evidence": STRING_ARRAY,
                    "tertiary_evidence": STRING_ARRAY,
                    "unresolved_questions": STRING_ARRAY,
                },
            },
            "timeline": {
                "type": "object",
                "additionalProperties": False,
                "required": ["version", "events"],
                "properties": {
                    "version": {"type": "integer"},
                    "events": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "required": ["date", "summary", "claim_ids", "source_ids"],
                            "properties": {
                                "date": {"type": "string"},
                                "summary": {"type": "string"},
                                "claim_ids": STRING_ARRAY,
                                "source_ids": STRING_ARRAY,
                            },
                        },
                    },
                },
            },
        },
    },
}

CORROBORATION_SCHEMA = {
    "name": "corroboration",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["version", "status", "groups", "ungrouped_claim_ids"],
        "properties": {
            "version": {"type": "integer"}, "status": {"type": "string"},
            "ungrouped_claim_ids": STRING_ARRAY,
            "groups": {"type": "array", "items": {
                "type": "object", "additionalProperties": False,
                "required": ["canonical_proposition", "member_claim_ids", "disagreements_or_nuances", "confidence"],
                "properties": {
                    "canonical_proposition": {"type": "string"},
                    "member_claim_ids": STRING_ARRAY,
                    "disagreements_or_nuances": STRING_ARRAY,
                    "confidence": {"type": "string"},
                },
            }},
        },
    },
}

STORY_ARCHITECTURE_SCHEMA = {
    "name": "story_architecture",
    "schema": {
        "type": "object", "additionalProperties": False,
        "required": ["version", "status", "beats", "research_utilization_audit", "unused_high_value_details", "coverage_gaps", "final_reflection", "closing_requirements", "supplementary_metadata"],
        "properties": {
            "version": {"type": "integer"}, "status": {"type": "string"},
            "beats": {"type": "array", "minItems": 1, "items": {
                "type": "object", "additionalProperties": False,
                "required": ["beat_id", "what_happens", "viewer_learns", "why_here", "curiosity_forward", "claim_ids", "high_value_details"],
                "properties": {
                    "beat_id": {"type": "string", "pattern": "^beat_[0-9]{2}$"}, "what_happens": {"type": "string"}, "viewer_learns": {"type": "string"},
                    "why_here": {"type": "string"}, "curiosity_forward": {"type": "string"}, "claim_ids": STRING_ARRAY, "high_value_details": STRING_ARRAY,
                },
            }},
            "research_utilization_audit": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["detail", "claim_ids", "use_or_omit_reason"], "properties": {"detail": {"type": "string"}, "claim_ids": STRING_ARRAY, "use_or_omit_reason": {"type": "string"}}}},
            "unused_high_value_details": STRING_ARRAY, "coverage_gaps": STRING_ARRAY,
            "final_reflection": {"type": "string"}, "closing_requirements": STRING_ARRAY,
            "supplementary_metadata": {"type": "object"},
        },
    },
}

NARRATIVE_OUTLINE_SCHEMA = {
    "name": "narrative_outline",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "version",
            "status",
            "opening_hook",
            "documentary_thesis",
            "acts",
            "chronological_structure",
            "tension_points",
            "transitions",
            "factual_boundaries",
            "unresolved_questions",
            "conclusion_approach",
        ],
        "properties": {
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "opening_hook": {"type": "string"},
            "documentary_thesis": {"type": "string"},
            "acts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "title", "purpose", "claim_ids"],
                    "properties": {
                        "id": {"type": "string"},
                        "title": {"type": "string"},
                        "purpose": {"type": "string"},
                        "claim_ids": STRING_ARRAY,
                    },
                },
            },
            "chronological_structure": STRING_ARRAY,
            "tension_points": STRING_ARRAY,
            "transitions": STRING_ARRAY,
            "factual_boundaries": STRING_ARRAY,
            "unresolved_questions": STRING_ARRAY,
            "conclusion_approach": {"type": "string"},
        },
    },
}

SCRIPT_SCHEMA = {
    "name": "script",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": [
            "version",
            "title",
            "target_duration_minutes",
            "language",
            "status",
            "generated_from",
            "opening_hook",
            "narration",
            "sections",
        ],
        "properties": {
            "version": {"type": "integer"},
            "title": {"type": "string"},
            "target_duration_minutes": {"type": "integer"},
            "language": {"type": "string"},
            "status": {"type": "string"},
            "generated_from": STRING_ARRAY,
            "opening_hook": {"type": "string"},
            "narration": {"type": "string"},
            "sections": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["id", "heading", "claim_ids", "beat_ids", "text"],
                    "properties": {
                        "id": {"type": "string"},
                        "heading": {"type": "string"},
                        "claim_ids": STRING_ARRAY,
                        "beat_ids": STRING_ARRAY,
                        "text": {"type": "string"},
                    },
                },
            },
        },
    },
}

SCRIPT_REPLACEMENTS_SCHEMA = {
    "name": "script_replacements",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["replacements"],
        "properties": {
            "replacements": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["target_id", "replacement_passage"],
                    "properties": {
                        "target_id": {"type": "string"},
                        "replacement_passage": {"type": "string"},
                    },
                },
            },
        },
    },
}

SCENES_SCHEMA = {
    "name": "scenes",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["version", "status", "scenes"],
        "properties": {
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "id",
                        "index",
                        "narration",
                        "estimated_duration_seconds",
                        "claim_ids",
                        "media_requirements",
                        "archival_media_queries",
                        "alternative_media_queries",
                        "people",
                        "locations",
                        "dates",
                        "events",
                        "camera_movement",
                        "on_screen_text",
                        "transition_notes",
                        "ai_visual_prompt",
                    ],
                    "properties": {
                        "id": {"type": "string"},
                        "index": {"type": "integer"},
                        "narration": {"type": "string"},
                        "estimated_duration_seconds": {"type": "integer"},
                        "claim_ids": STRING_ARRAY,
                        "media_requirements": STRING_ARRAY,
                        "archival_media_queries": STRING_ARRAY,
                        "alternative_media_queries": STRING_ARRAY,
                        "people": STRING_ARRAY,
                        "locations": STRING_ARRAY,
                        "dates": STRING_ARRAY,
                        "events": STRING_ARRAY,
                        "camera_movement": {"type": "string"},
                        "on_screen_text": {"type": "string"},
                        "transition_notes": {"type": "string"},
                        "ai_visual_prompt": {"type": "string"},
                    },
                },
            },
        },
    },
}
