from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class StageKind(StrEnum):
    TOPIC = "topic"
    REFERENCE_INTAKE = "reference_intake"
    RESEARCH = "research"
    FACT_CHECK = "fact_check"
    SCRIPT = "script"
    SCENE_PLAN = "scene_plan"
    PRODUCER_BLUEPRINT = "producer_blueprint"
    IMAGE_PROMPTS = "image_prompts"
    ASSET_GENERATION = "asset_generation"
    VOICE_OVER = "voice_over"
    EDIT_PLAN = "edit_plan"
    SUBTITLES = "subtitles"
    RENDER = "render"
    PACKAGE = "package"
    PUBLISH = "publish"


@dataclass(frozen=True)
class PipelineStage:
    kind: StageKind
    label: str
    output: str
    requires_review: bool
    expensive: bool = False


PIPELINE_STAGES: tuple[PipelineStage, ...] = (
    PipelineStage(StageKind.TOPIC, "Topic intake", "manifests/topic.json", False),
    PipelineStage(StageKind.REFERENCE_INTAKE, "Screenshot and interview clip intake", "manifests/reference_intent.json", True),
    PipelineStage(StageKind.RESEARCH, "Source-backed research dossier", "research/dossier.json", True),
    PipelineStage(StageKind.FACT_CHECK, "Claims and source map", "research/claims.json", True),
    PipelineStage(StageKind.SCRIPT, "Documentary script", "manifests/script.json", True),
    PipelineStage(StageKind.SCENE_PLAN, "Cinematic scene breakdown", "manifests/scenes.json", True),
    PipelineStage(StageKind.PRODUCER_BLUEPRINT, "Producer story rhythm blueprint", "manifests/producer_blueprint.json", False),
    PipelineStage(StageKind.IMAGE_PROMPTS, "Image generation prompts", "manifests/image_prompts.json", True),
    PipelineStage(StageKind.ASSET_GENERATION, "Images, clips, and motion assets", "assets/", True, True),
    PipelineStage(StageKind.VOICE_OVER, "Voice-over audio", "assets/audio/voiceover.wav", True, True),
    PipelineStage(StageKind.EDIT_PLAN, "Timing and visual edit plan", "manifests/edit_plan.json", True),
    PipelineStage(StageKind.SUBTITLES, "Subtitle file", "manifests/subtitles.srt", True),
    PipelineStage(StageKind.RENDER, "Rendered video", "exports/final_video.mp4", True),
    PipelineStage(StageKind.PACKAGE, "Thumbnail, title, and SEO metadata", "manifests/youtube_package.json", True),
    PipelineStage(StageKind.PUBLISH, "Upload and scheduling", "review/publish_approval.json", True, True),
)


def describe_pipeline() -> list[dict[str, object]]:
    return [
        {
            "stage": index,
            "kind": stage.kind.value,
            "label": stage.label,
            "output": stage.output,
            "requires_review": stage.requires_review,
            "expensive": stage.expensive,
        }
        for index, stage in enumerate(PIPELINE_STAGES, start=1)
    ]
