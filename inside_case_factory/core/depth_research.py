from __future__ import annotations

from collections import defaultdict
import re
from typing import Any

from inside_case_factory.core.research import normalized_domain
from inside_case_factory.core.content_modes import normalize_content_mode, content_mode


RESEARCH_QUESTIONS: list[dict[str, str]] = [
    {"id": "health_final_days", "question": "What is reliably documented about Michael Jackson's health and activities in his final days?"},
    {"id": "final_24_hours", "question": "What happened during Michael Jackson's final 24 hours, in supported chronological order?"},
    {"id": "medications", "question": "Which medications were administered, in what documented amounts and sequence?"},
    {"id": "murray_timeline", "question": "What actions did Conrad Murray take, and when?"},
    {"id": "emergency_response", "question": "When was emergency help requested and what did dispatchers and paramedics document?"},
    {"id": "hospital_autopsy", "question": "When was Jackson declared dead and what did the autopsy document?"},
    {"id": "cause_manner", "question": "What were the official cause and manner of death?"},
    {"id": "police_investigation", "question": "What did the police investigation establish and what evidence was collected?"},
    {"id": "criminal_charges", "question": "What criminal charges were filed and on what alleged conduct?"},
    {"id": "trial_evidence", "question": "What material evidence and testimony were presented at trial?"},
    {"id": "verdict_sentence", "question": "What were the verdict and sentence, with dates?"},
    {"id": "disputes", "question": "Which material points remain disputed, uncertain, or dependent on conflicting testimony?"},
    {"id": "aftermath", "question": "What documented aftermath and wider impact followed the death and trial?"},
]


FOCUSED_QUERIES: list[dict[str, Any]] = [
    {"query": "Michael Jackson final rehearsals June 24 2009 health final days contemporaneous report", "question_ids": ["health_final_days", "final_24_hours"]},
    {"query": "Michael Jackson final 24 hours timeline June 25 2009 paramedics UCLA", "question_ids": ["health_final_days", "final_24_hours", "emergency_response", "hospital_autopsy"]},
    {"query": "Los Angeles County coroner Michael Jackson autopsy propofol cause manner death PDF", "question_ids": ["medications", "hospital_autopsy", "cause_manner"]},
    {"query": "Conrad Murray police affidavit Michael Jackson medications timeline June 25 2009", "question_ids": ["medications", "murray_timeline", "police_investigation"]},
    {"query": "Michael Jackson 911 call paramedic testimony emergency response timeline", "question_ids": ["emergency_response", "trial_evidence", "disputes"]},
    {"query": "People v Conrad Murray court trial evidence testimony propofol records", "question_ids": ["criminal_charges", "trial_evidence", "murray_timeline", "verdict_sentence"]},
    {"query": "Conrad Murray charged involuntary manslaughter complaint verdict sentence official", "question_ids": ["criminal_charges", "verdict_sentence", "aftermath"]},
    {"query": "Michael Jackson death investigation LAPD official statement Conrad Murray search warrant", "question_ids": ["police_investigation", "disputes"]},
    {"query": "Conrad Murray trial disputed timeline phone calls CPR propofol expert testimony", "question_ids": ["trial_evidence", "disputes", "murray_timeline"]},
    {"query": "Michael Jackson death aftermath medical regulation propofol legacy reputable retrospective", "question_ids": ["aftermath", "cause_manner"]},
]


SECOND_ROUND_QUERIES: dict[str, str] = {
    "health_final_days": "Michael Jackson health final week June 2009 rehearsal witnesses reputable news",
    "emergency_response": "Los Angeles paramedic testimony Michael Jackson 911 response exact times",
    "police_investigation": "LAPD Michael Jackson death investigation affidavit evidence official",
    "trial_evidence": "California court Conrad Murray trial exhibits testimony transcript",
    "disputes": "Conrad Murray trial conflicting testimony disputed timeline expert witnesses",
    "aftermath": "Michael Jackson death documented aftermath medical practice propofol impact",
}


def select_authoritative_results(results: list[dict[str, Any]], *, limit: int = 16, mode: str = "factual_documentary") -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    domain_counts: defaultdict[str, int] = defaultdict(int)
    ordered = sorted(results, key=lambda x: (-float(x.get("quality_score", 0)), -float(x.get("score", 0) or 0)))
    for item in ordered:
        url = str(item.get("url", ""))
        domain = normalized_domain(url)
        title = str(item.get("title", "")).lower()
        source_type = str(item.get("source_type", ""))
        if not url or url in seen_urls:
            continue
        if normalize_content_mode(mode) == "factual_documentary" and source_type == "video" and not any(word in title for word in ("official", "interview", "testimony", "911")):
            continue
        if normalize_content_mode(mode) == "factual_documentary" and source_type in {"blog"}:
            continue
        if domain_counts[domain] >= (1 if normalize_content_mode(mode) == "factual_documentary" else 2):
            continue
        selected.append(item)
        seen_urls.add(url)
        domain_counts[domain] += 1
        if len(selected) >= limit:
            break
    return selected


def mode_query_guidance(mode: str) -> str:
    selected = content_mode(mode)
    if normalize_content_mode(mode) == "factual_documentary":
        return "Prioritize official records, court documents, coroner findings, and reputable contemporaneous reporting."
    if normalize_content_mode(mode) == "investigative_documentary":
        return "Search official records plus attributed allegations, witness accounts, contradictions, competing explanations, and unresolved questions."
    return "Search theory origins, proponents, cited anomalies, alleged motives and connections, counterarguments, and conventional explanations; retain controversial sources with attribution."


def merge_semantically_equivalent_proposals(proposals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, dict[str, Any]] = {}
    for index, claim in enumerate(proposals):
        if not isinstance(claim, dict):
            continue
        key = str(claim.get("canonical_key") or claim.get("text") or index).strip().casefold()
        candidate_key = key
        if candidate_key in groups:
            stop = {"the", "a", "an", "of", "to", "and", "in", "on", "was", "were", "is", "that", "his", "her"}
            left = set(re.findall(r"[a-z0-9]+", str(groups[candidate_key].get("text", "")).casefold())) - stop
            right = set(re.findall(r"[a-z0-9]+", str(claim.get("text", "")).casefold())) - stop
            similarity = len(left & right) / max(1, len(left | right))
            if similarity < 0.45:
                candidate_key = f"{key}::{index}"
        if candidate_key not in groups:
            groups[candidate_key] = dict(claim)
            groups[candidate_key]["evidence"] = list(claim.get("evidence", []))
            continue
        existing = groups[candidate_key]
        existing["evidence"].extend(claim.get("evidence", []))
        existing["source_ids"] = sorted(set(map(str, existing.get("source_ids", []))) | set(map(str, claim.get("source_ids", []))))
        existing["contradiction_notes"] = "; ".join(filter(None, [str(existing.get("contradiction_notes", "")), str(claim.get("contradiction_notes", ""))]))
    return list(groups.values())


QUESTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "health_final_days": ("health", "rehears", "insomnia", "sleep", "final days"),
    "final_24_hours": ("june 24", "june 25", "final hours", "that morning", "midnight"),
    "medications": ("propofol", "lorazepam", "midazolam", "valium", "sedative", "drug", "medication"),
    "murray_timeline": ("murray", "physician", "doctor"),
    "emergency_response": ("911", "paramedic", "ambulance", "resuscitation", "cpr", "emergency"),
    "hospital_autopsy": ("ucla", "hospital", "autopsy", "pronounced", "declared dead"),
    "cause_manner": ("cause of death", "manner of death", "homicide", "intoxication"),
    "police_investigation": ("police", "lapd", "search warrant", "investigation", "detective"),
    "criminal_charges": ("charged", "complaint", "involuntary manslaughter"),
    "trial_evidence": ("trial", "testified", "testimony", "jury", "evidence"),
    "verdict_sentence": ("verdict", "convicted", "sentenced", "sentence"),
    "disputes": ("disputed", "conflict", "claimed", "account", "defense", "prosecution"),
    "aftermath": ("after his death", "afterward", "legacy", "impact", "released", "license"),
}


def _canonical_key(text: str) -> str:
    lower = text.casefold()
    rules = [
        (("cause of death", "propofol"), "official_cause_propofol"),
        (("manner of death", "homicide"), "official_manner_homicide"),
        (("pronounced dead", "ucla"), "hospital_declaration"),
        (("declared dead", "ucla"), "hospital_declaration"),
        (("convicted", "involuntary manslaughter"), "murray_verdict"),
        (("sentenced", "four years"), "murray_sentence"),
        (("911", "call"), "emergency_call"),
        (("25 milligrams", "propofol"), "propofol_25mg"),
    ]
    for terms, key in rules:
        if all(term in lower for term in terms):
            return key
    words = re.findall(r"[a-z0-9]+", lower)
    return "_".join(words[:10])


def extract_atomic_sentence_proposals(snapshots: list[dict[str, Any]], *, limit: int = 60, per_source: int = 8) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for snapshot in snapshots:
        content = str(snapshot.get("content", ""))
        source_id = str(snapshot.get("source_id", ""))
        added = 0
        for sentence in re.split(r"(?<=[.!?])\s+|\n+", content):
            sentence = " ".join(sentence.split()).strip()
            lower = sentence.casefold()
            if len(sentence) < 55 or len(sentence) > 320 or "?" in sentence:
                continue
            question_ids = [qid for qid, words in QUESTION_KEYWORDS.items() if any(word in lower for word in words)]
            if not question_ids or not re.search(r"\b(was|were|had|did|died|gave|administered|called|arrived|pronounced|declared|found|charged|convicted|sentenced|testified|reported|ruled|concluded)\b", lower):
                continue
            dates = re.findall(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2}(?:,\s+\d{4})?|\b(?:19|20)\d{2}\b", sentence)
            times = re.findall(r"\b\d{1,2}:\d{2}\s*(?:a\.m\.|p\.m\.|am|pm|PST|BST)?", sentence, re.I)
            proposals.append({
                "text": sentence, "canonical_key": _canonical_key(sentence), "research_question_ids": question_ids,
                "source_ids": [source_id],
                "evidence": [{"source_id": source_id, "exact_excerpt": sentence, "start": content.find(sentence), "end": content.find(sentence) + len(sentence), "searchable_text": sentence}],
                "relevance_score": 0.8, "confidence": "medium", "source_quality": "unreviewed",
                "corroboration_status": "single_source", "people": [], "locations": [], "dates": [*dates, *times],
                "events": [], "contradiction_notes": "", "review_status": "pending_review",
            })
            added += 1
            if added >= per_source or len(proposals) >= limit:
                break
        if len(proposals) >= limit:
            break
    return proposals


def build_coverage(questions: list[dict[str, str]], query_records: list[dict[str, Any]], sources: list[dict[str, Any]], claims: list[dict[str, Any]], *, mode: str = "factual_documentary") -> dict[str, Any]:
    source_ids = {str(source.get("id")) for source in sources}
    entries = []
    for question in questions:
        qid = question["id"]
        query_sources = sorted({sid for record in query_records if qid in record.get("question_ids", []) for sid in record.get("source_ids", []) if sid in source_ids})
        matched = [claim for claim in claims if qid in claim.get("research_question_ids", [])]
        contradictions = [str(claim.get("contradiction_notes")) for claim in matched if claim.get("contradiction_notes")]
        gaps = [] if matched else ["No validated atomic claim answers this question."]
        entries.append({
            "question_id": qid, "question": question["question"], "source_ids": query_sources,
            "validated_claim_ids": [claim["id"] for claim in matched], "gaps": gaps,
            "contradictions": contradictions, "more_search_required": not bool(matched),
        })
    return {"version": 1, "content_mode": normalize_content_mode(mode), "mode_policy": mode_query_guidance(mode), "questions": entries, "requires_more_search": any(item["more_search_required"] for item in entries)}
