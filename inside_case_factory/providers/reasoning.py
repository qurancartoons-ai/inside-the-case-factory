from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import sys
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from inside_case_factory.utils.files import read_json, write_json
from inside_case_factory.utils.text import compact_whitespace
from inside_case_factory.core.content_modes import mode_prompt
from inside_case_factory.core.narrative_quality import ARCHITECTURE_BEAT_FIELDS, ARCHITECTURE_FIELDS, STORY_ARCHITECTURE_SCHEMA


class ReasoningProviderError(RuntimeError):
    pass


def build_response_format(schema: dict[str, Any]) -> dict[str, Any]:
    """Build the single canonical response_format used by validation, tests and runtime."""
    schema_errors = validate_strict_response_schema(schema)
    if schema_errors:
        raise ReasoningProviderError("Invalid local response schema: " + "; ".join(schema_errors))
    return {"type": "json_schema", "name": schema["name"], "strict": True, "schema": schema["schema"]}


def _project_content_mode(project_root: Path) -> str:
    for name in ("workflow.json", "production_request.json", "production_plan.json"):
        path = project_root / "manifests" / name
        if path.exists():
            data = read_json(path)
            if isinstance(data, dict) and data.get("content_mode"):
                return str(data["content_mode"])
    return "factual_documentary"


def _project_spending_limit(project_root: Path, configured_limit: float) -> float:
    """Use a project's budget as standing authorization and as a hard upper bound."""
    path = project_root / "manifests" / "provider_config.json"
    if not path.exists():
        return configured_limit
    budget = float(read_json(path).get("budget_usd", 0) or 0)
    if budget <= 0:
        raise ReasoningProviderError("Project budget must be greater than 0 before paid AI calls can start.")
    return min(configured_limit, budget)


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
    def build_story_architecture(self, project_root: Path, research_plan: dict[str, Any], dossier: dict[str, Any], timeline: dict[str, Any], claims: list[dict[str, Any]], snapshots: list[dict[str, Any]], target_duration_minutes: int) -> dict[str, Any]:
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
        word_range: tuple[int, int] | None = None,
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

    def build_story_architecture(self, project_root: Path, research_plan: dict[str, Any], dossier: dict[str, Any], timeline: dict[str, Any], claims: list[dict[str, Any]], snapshots: list[dict[str, Any]], target_duration_minutes: int) -> dict[str, Any]:
        return self._blocked(project_root, "story_architecture", "Reasoning provider is disabled.")

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
                    "Detect every materially involved country and plan research in that country's most relevant language before considering the user's language.",
                    "Prioritize official records, courts, police, government and parliamentary reports; then national quality media; then international media; local media; and Dutch sources only as supplementary context.",
                    "Return specific research questions that must be answered before scripting.",
                    "If workflow_type is recycle_documentary, treat the reference documentary as a narrative blueprint only, never as a factual source.",
                    "If recycle_blueprint is present, independently verify each major event, correct unsupported claims, and fill missing context before scripting.",
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
            "Build a detailed continuous narrative architecture before scriptwriting. Return exactly the requested top-level and beat fields; only genuine narrative events belong in beats, while audit, closing, reflection, and metadata content belongs in its dedicated top-level field.",
            {
                "research_plan": research_plan, "dossier": dossier, "timeline": timeline,
                "claims": compact_claims, "extracted_source_count": len(snapshots), "target_duration_minutes": target_duration_minutes,
                "mode_instructions": mode_prompt(_project_content_mode(project_root)),
                "requirements": [
                    f"Return exactly these top-level fields: {', '.join(ARCHITECTURE_FIELDS)}.",
                    f"Every beat must contain exactly: {', '.join(ARCHITECTURE_BEAT_FIELDS)}.",
                    "Treat beat_id as an opaque stable identifier that only needs to match beat_ followed by exactly two digits; do not infer a separate contiguity rule.",
                    "For every beat provide: what happens, what the viewer learns, why it appears there, the carried curiosity question, supporting claim IDs, and high-value details to use.",
                    "Move primarily chronologically while slowing down at the decisive events, strongest evidence, investigation, and material contradictions identified by the research.",
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
                    "Extract 3-8 distinct atomic claims from every usable source when its text supports that many; one source may and normally should yield multiple claims.",
                    "Across the full research set, target at least 20 proposed claims and 30-60 when the evidence supports them; never return zero claims while relevant factual source passages are present.",
                    "Judge usability by relevance to the documentary topic, not by whether a source supports one preferred theory or angle. Official findings, rebuttals, and contradictory evidence are essential usable evidence.",
                    "Extract separate atomic claims for the chronology, named people, locations, official findings, forensic evidence, witness accounts, later inquiries, and clearly attributed allegations whenever the supplied text supports them.",
                    "Never collapse an official conclusion and an allegation into one claim. Preserve each proposition separately and describe the contradiction in contradiction_notes.",
                    "Set corroboration_status to corroborated only when exact evidence from at least two independent source IDs supports the same proposition; source_ids without matching evidence do not count.",
                    "Copy every exact_excerpt verbatim from supplied content; never paraphrase evidence. Each claim must include exact evidence, source IDs, a stable semantic canonical_key, and applicable research_question_ids.",
                    "Classify every claim as exactly one of: verified_fact, single_source_claim, allegation, witness_statement, official_explanation, alternative_explanation, disputed_claim, interpretation, speculation, unanswered_question.",
                    "Preserve supported dates, times, people, locations, and events; never invent precision.",
                    "Prefer primary and high-quality secondary sources. Flag weak single-source claims.",
                    "Do not create claims from raw fragments that are not factual and relevant to the production prompt.",
                    "Write claim text in the requested video language so the user never has to read a foreign source; preserve exact original-language excerpts as evidence.",
                    "Explicitly record contradictions between sources in contradiction_notes instead of merging incompatible accounts.",
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
        quality_report: dict[str, Any] | None = None,
        word_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        minimum_words, maximum_words = word_range or (0, 0)
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
                "narration_word_range": {"minimum": minimum_words, "maximum": maximum_words} if word_range else None,
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
        word_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        if word_range is None:
            from inside_case_factory.core.narrative_quality import script_word_targets
            contract = script_word_targets(target_duration_minutes, 125, 1.0)
            minimum_words, maximum_words = contract["minimum_words"], contract["maximum_words"]
        else:
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
        narration = str(result.get("narration", "")).strip()
        sections = result.get("sections", [])
        if narration and isinstance(sections, list) and sections:
            joined = " ".join(str(section.get("text", "")) for section in sections if isinstance(section, dict))
            if compact_whitespace(joined) != compact_whitespace(narration):
                sections[0]["text"] = narration
                for section in sections[1:]:
                    section["text"] = ""
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
        response_format = build_response_format(schema)
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
            "text": {"format": response_format},
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
        print("OpenAI Responses response_format=" + json.dumps(response_format, ensure_ascii=False, sort_keys=True), file=sys.stderr, flush=True)
        try:
            with urlopen(request, timeout=90) as response:
                response_data = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")
            raise ReasoningProviderError(f"OpenAI Responses API error {error.code}: {detail}") from error
        except URLError as error:
            raise ReasoningProviderError(f"OpenAI Responses API network error: {error}") from error

        parsed = parse_response_json(response_data)
        instance_errors = validate_response_instance(parsed, schema["schema"])
        if instance_errors:
            raise ReasoningProviderError(f"OpenAI response for {operation} did not match {schema['name']} schema: " + "; ".join(instance_errors))
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
        spending_limit = _project_spending_limit(project_root, self.config.per_project_spending_limit_usd)
        if current + estimated > spending_limit:
            raise ReasoningProviderError(
                f"Project reasoning budget would be exceeded before {operation}: "
                f"{current + estimated:.4f} > {spending_limit:.4f} USD."
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
    for operation in ("research_plan", "source_analysis", "story_architecture", "narrative_outline", "script", "scenes"):
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


def validate_response_instance(value: Any, schema: dict[str, Any], path: str = "$") -> list[str]:
    """Validate the strict Responses JSON-schema subset before domain parsing."""
    errors: list[str] = []
    allowed = schema.get("type")
    allowed_types = allowed if isinstance(allowed, list) else [allowed]
    type_matches = {
        "object": lambda item: isinstance(item, dict), "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str), "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool), "boolean": lambda item: isinstance(item, bool),
        "null": lambda item: item is None,
    }
    if allowed and not any(type_matches.get(kind, lambda item: True)(value) for kind in allowed_types):
        return [f"{path} must be {' or '.join(str(kind) for kind in allowed_types)}"]
    if isinstance(value, dict) and "object" in allowed_types:
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value: errors.append(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in properties: errors.append(f"{path}.{key} is not allowed")
        for key, child in properties.items():
            if key in value: errors.extend(validate_response_instance(value[key], child, f"{path}.{key}"))
    if isinstance(value, list) and "array" in allowed_types:
        if len(value) < int(schema.get("minItems", 0)): errors.append(f"{path} must contain at least {schema['minItems']} item(s)")
        for index, item in enumerate(value): errors.extend(validate_response_instance(item, schema.get("items", {}), f"{path}[{index}]"))
    if isinstance(value, str) and schema.get("pattern") and not re.fullmatch(str(schema["pattern"]), value):
        errors.append(f"{path} must match {schema['pattern']}")
    return errors


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
        spending_limit = _project_spending_limit(project_root, self.config.per_project_spending_limit_usd)
        if current_estimated_spend(project_root) + self.config.estimated_cost_per_call_usd > spending_limit:
            raise ReasoningProviderError(f"Project reasoning budget would be exceeded before {operation}.")

    def _json_response(
        self, project_root: Path, operation: str, instruction: str,
        payload: dict[str, Any], schema: dict[str, Any],
    ) -> dict[str, Any]:
        from inside_case_factory.providers.production import ProductionRequest as RoutedRequest
        build_response_format(schema)
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
    blueprint = request.get("recycle_blueprint", {}) if isinstance(request.get("recycle_blueprint"), dict) else {}
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
        "people": [str(item) for item in blueprint.get("people", []) if str(item).strip()],
        "locations": [str(item) for item in blueprint.get("places", []) if str(item).strip()],
        "dates": [str(item.get("date", "")) for item in blueprint.get("timeline", []) if isinstance(item, dict) and str(item.get("date", "")).strip()],
        "events": [str(item) for item in blueprint.get("historical_events", []) if str(item).strip()],
        "exclusions": [],
        "factual_questions": [str(item) for item in blueprint.get("verification_queries", []) if str(item).strip()],
        "involved_countries": [],
        "relevant_languages": [],
        "source_priorities": [],
        "coverage_targets": [],
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


def validate_strict_response_schema(format_schema: dict[str, Any]) -> list[str]:
    """Return local violations of the strict JSON-schema subset used by Responses."""
    errors: list[str] = []

    def visit(node: Any, path: str) -> None:
        if not isinstance(node, dict):
            return
        node_type = node.get("type")
        if node_type == "object" or isinstance(node_type, list) and "object" in node_type:
            properties = node.get("properties")
            if not isinstance(properties, dict):
                errors.append(f"{path}: object must define properties")
                properties = {}
            if node.get("additionalProperties") is not False:
                errors.append(f"{path}: additionalProperties must be false")
            required = node.get("required")
            if not isinstance(required, list):
                errors.append(f"{path}: required must be an array")
                required = []
            if set(required) != set(properties):
                errors.append(f"{path}: required must exactly match properties")
            for key, child in properties.items():
                visit(child, f"{path}.properties.{key}")
        if node_type == "array" or isinstance(node_type, list) and "array" in node_type:
            if "items" not in node:
                errors.append(f"{path}: array must define items")
            else:
                visit(node["items"], f"{path}.items")
        for keyword in ("anyOf", "oneOf", "allOf"):
            for index, child in enumerate(node.get(keyword, [])):
                visit(child, f"{path}.{keyword}[{index}]")

    if not isinstance(format_schema, dict) or "name" not in format_schema or "schema" not in format_schema:
        return ["response format must contain name and schema"]
    visit(format_schema["schema"], str(format_schema["name"]))
    return errors

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
            "involved_countries",
            "relevant_languages",
            "source_priorities",
            "coverage_targets",
        ],
        "properties": {
            "version": {"type": "integer"},
            "status": {"type": "string"},
            "exact_topic": {"type": "string"},
            "documentary_angle": {"type": ["string", "null"]},
            "requested_focus": {"type": "string"},
            "target_duration_minutes": {"type": "integer"},
            "video_language": {"type": "string"},
            "people": STRING_ARRAY,
            "locations": STRING_ARRAY,
            "dates": STRING_ARRAY,
            "events": STRING_ARRAY,
            "exclusions": STRING_ARRAY,
            "factual_questions": STRING_ARRAY,
            "involved_countries": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["country", "language", "reason"], "properties": {"country": {"type": "string"}, "language": {"type": "string"}, "reason": {"type": ["string", "null"]}}}},
            "relevant_languages": STRING_ARRAY,
            "source_priorities": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["level", "categories"], "properties": {"level": {"type": "integer"}, "categories": STRING_ARRAY}}},
            "coverage_targets": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["country", "minimum_percentage"], "properties": {"country": {"type": "string"}, "minimum_percentage": {"type": "integer"}}}},
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


RESPONSE_FORMAT_SCHEMAS = (
    RESEARCH_PLAN_SCHEMA, SOURCE_ANALYSIS_SCHEMA, CORROBORATION_SCHEMA,
    STORY_ARCHITECTURE_SCHEMA, NARRATIVE_OUTLINE_SCHEMA, SCRIPT_SCHEMA,
    SCRIPT_REPLACEMENTS_SCHEMA, SCENES_SCHEMA,
)
