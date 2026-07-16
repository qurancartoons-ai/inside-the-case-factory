from __future__ import annotations

from inside_case_factory.utils.text import compact_whitespace, title_case_topic


def build_sample_script(topic: str) -> dict[str, object]:
    title = title_case_topic(topic)
    sections = [
        {
            "id": "s01",
            "heading": "Cold open",
            "narration": (
                f"On a rain washed evening, the file marked {title} begins with an absence. "
                "A quiet street, a stopped clock, and a final message that seemed ordinary at the time."
            ),
        },
        {
            "id": "s02",
            "heading": "The last known trail",
            "narration": (
                "Investigators return to the same few landmarks again and again. "
                "A corner store camera. A bus shelter. A stretch of road where the lights thin out."
            ),
        },
        {
            "id": "s03",
            "heading": "Contradictions",
            "narration": (
                "The timeline looks simple until the witness statements are placed side by side. "
                "Minutes drift. Directions change. Small details begin to point in different ways."
            ),
        },
        {
            "id": "s04",
            "heading": "Evidence board",
            "narration": (
                "The strongest lead is not a single clue, but a pattern. "
                "Phone records, weather reports, and fragments of memory form a map of the unknown."
            ),
        },
        {
            "id": "s05",
            "heading": "Unanswered questions",
            "narration": (
                "By dawn, the case has become more than a disappearance. "
                "It is a question about who noticed, who waited, and who still knows more than they have said."
            ),
        },
    ]
    narration = " ".join(str(section["narration"]) for section in sections)
    return {
        "title": title,
        "topic": topic,
        "status": "sample_offline_generated",
        "disclaimer": "Demonstration narration only. Real cases require verified sources and claim review.",
        "sections": sections,
        "full_narration": compact_whitespace(narration),
    }


def build_visual_prompt(section: dict[str, object], topic_title: str, index: int) -> dict[str, object]:
    motifs = [
        "rain streaked suburban street, sodium vapor lights, distant house windows, wet asphalt reflections",
        "security camera perspective, empty bus shelter, timestamp overlay, long lens surveillance mood",
        "documents spread across a table, conflicting witness notes, red pencil marks, shallow depth of field",
        "investigation board, map pins, phone record fragments, thread connections, cinematic side light",
        "early morning exterior, pale horizon, unresolved case files, quiet police tape, restrained atmosphere",
    ]
    camera = [
        "slow push in, slight left drift, low contrast haze",
        "slow lateral pan, observational framing, subtle handheld tension",
        "macro glide over paper texture, controlled rack-focus feeling",
        "measured zoom toward the center of the board, investigative mood",
        "slow pull back, dawn light bloom, unresolved final frame",
    ]
    return {
        "scene_id": section["id"],
        "provider": "local_evidence_graphics",
        "prompt": (
            f"Cinematic true-crime documentary still for {topic_title}: "
            f"{motifs[(index - 1) % len(motifs)]}. {camera[(index - 1) % len(camera)]}."
        ),
        "negative_prompt": "cartoon, comedy, bright cheerful colors, illegible text, gore, exploitative imagery",
        "camera_motion": camera[(index - 1) % len(camera)],
        "replacement_note": "Owned local evidence graphic; external or licensed imagery may replace it when configured.",
    }
