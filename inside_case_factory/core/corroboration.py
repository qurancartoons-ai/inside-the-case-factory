from __future__ import annotations

from collections import defaultdict
import re
from typing import Any
from urllib.parse import urlparse

from inside_case_factory.utils.files import write_json


def _domain(url: str) -> str:
    host = urlparse(url).netloc.lower().split("@")[ -1].split(":")[0]
    return host.removeprefix("www.")


def build_corroboration_report(project_root, claims: list[dict[str, Any]], sources: list[dict[str, Any]], ai: dict[str, Any]) -> dict[str, Any]:
    claim_by_id = {str(claim.get("id")): claim for claim in claims}
    source_by_id = {str(source.get("id")): source for source in sources}
    groups: list[dict[str, Any]] = []
    assigned: set[str] = set()
    rejected_groups: list[dict[str, Any]] = []
    for index, candidate in enumerate(ai.get("groups", []) if isinstance(ai, dict) else [], start=1):
        if not isinstance(candidate, dict):
            continue
        member_ids = [str(item) for item in candidate.get("member_claim_ids", [])]
        member_ids = list(dict.fromkeys(item for item in member_ids if item in claim_by_id and item not in assigned))
        if len(member_ids) < 2:
            rejected_groups.append({"candidate": candidate, "reason": "A corroboration group requires at least two valid member claims."})
            continue
        member_claims = [claim_by_id[item] for item in member_ids]
        if len(member_ids) > 8 or not _claims_are_specific_equivalents(member_claims):
            rejected_groups.append({"candidate": candidate, "reason": "Member propositions are too broad or textually dissimilar for a safe equivalence group."})
            continue
        source_ids = sorted({str(source_id) for claim in member_claims for source_id in claim.get("source_ids", []) if str(source_id) in source_by_id})
        domains = {_domain(str(source_by_id[source_id].get("url", ""))) for source_id in source_ids}
        hashes = {str(snapshot.get("content_hash")) for snapshot in _snapshots(project_root) if str(snapshot.get("source_id")) in source_ids}
        independent = len(domains) >= 2 and len(hashes) >= 2
        evidence = [
            {"claim_id": claim["id"], "source_ids": claim.get("source_ids", []), "evidence": claim.get("evidence", []), "text": claim.get("text", "")}
            for claim in member_claims
        ]
        nuances = [str(item) for item in candidate.get("disagreements_or_nuances", []) if str(item)]
        conflicting = any(word in " ".join(nuances).casefold() for word in ("conflict", "contradict", "disagree", "uncertain"))
        group = {
            "group_id": f"cg{len(groups)+1:03}",
            "canonical_proposition": str(candidate.get("canonical_proposition", "")),
            "member_claim_ids": member_ids,
            "independent_source_ids": source_ids if independent else [],
            "supporting_evidence": evidence,
            "corroboration_status": "conflicting" if conflicting else ("corroborated" if independent else "single_source"),
            "confidence": "high" if independent and not conflicting else ("medium" if not conflicting else "low"),
            "disagreements_or_nuances": nuances,
        }
        groups.append(group); assigned.update(member_ids)
    for claim in claims:
        claim_id = str(claim.get("id"))
        if claim_id in assigned:
            continue
        groups.append({
            "group_id": f"cg{len(groups)+1:03}", "canonical_proposition": str(claim.get("text", "")),
            "member_claim_ids": [claim_id], "independent_source_ids": [],
            "supporting_evidence": [{"claim_id": claim_id, "source_ids": claim.get("source_ids", []), "evidence": claim.get("evidence", []), "text": claim.get("text", "")}],
            "corroboration_status": "single_source", "confidence": "medium", "disagreements_or_nuances": [],
        })
    report = {
        "version": 1, "status": "validated", "total_claim_groups": len(groups),
        "corroborated_groups": [group["group_id"] for group in groups if group["corroboration_status"] == "corroborated"],
        "single_source_groups": [group["group_id"] for group in groups if group["corroboration_status"] == "single_source"],
        "conflicting_groups": [group["group_id"] for group in groups if group["corroboration_status"] == "conflicting"],
        "strongest_core_facts": [group["group_id"] for group in groups if group["corroboration_status"] == "corroborated" and group["confidence"] == "high"],
        "important_facts_needing_corroboration": [group["group_id"] for group in groups if group["corroboration_status"] == "single_source"],
        "rejected_ai_groups": rejected_groups, "groups": groups,
    }
    write_json(project_root / "manifests" / "corroboration_report.json", report)
    return report


def _claims_are_specific_equivalents(claims: list[dict[str, Any]]) -> bool:
    token_sets = []
    stop = {"the", "a", "an", "of", "to", "and", "in", "on", "was", "were", "is", "that", "his", "her", "with", "for"}
    for claim in claims:
        token_sets.append(set(re.findall(r"[a-z0-9]+", str(claim.get("text", "")).casefold())) - stop)
    if len(token_sets) < 2:
        return False
    similarities = []
    for index, left in enumerate(token_sets):
        for right in token_sets[index + 1:]:
            similarities.append(len(left & right) / max(1, len(left | right)))
    return bool(similarities) and sum(similarities) / len(similarities) >= 0.35


def _snapshots(project_root) -> list[dict[str, Any]]:
    from inside_case_factory.utils.files import read_json
    path = project_root / "manifests" / "source_snapshots.json"
    if not path.exists():
        return []
    data = read_json(path)
    return data.get("snapshots", []) if isinstance(data, dict) else []


def update_group_references(project_root, report: dict[str, Any], claims: list[dict[str, Any]], dossier: dict[str, Any], timeline: dict[str, Any]) -> None:
    claim_to_group = {claim_id: group["group_id"] for group in report["groups"] for claim_id in group["member_claim_ids"]}
    dossier["corroboration_group_ids"] = report["corroborated_groups"]
    dossier["validated_claim_ids"] = [claim["id"] for claim in claims]
    dossier["key_facts"] = [{"claim_id": claim["id"], "group_id": claim_to_group.get(claim["id"]), "statement": claim["text"]} for claim in claims]
    timeline["corroboration_group_ids"] = report["corroborated_groups"]
    timeline["events"] = [{**event, "group_id": claim_to_group.get(str(event.get("claim_id")))} for event in timeline.get("events", [])]
    write_json(project_root / "manifests" / "dossier.json", dossier)
    write_json(project_root / "manifests" / "timeline.json", timeline)
