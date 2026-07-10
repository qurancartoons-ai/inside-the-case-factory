from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProviderResult:
    provider: str
    payload: dict[str, Any]
    cost_estimate_usd: float = 0.0
    requires_human_review: bool = True


class ResearchProvider(ABC):
    name: str

    @abstractmethod
    def research(self, topic: str) -> ProviderResult:
        raise NotImplementedError


class TextGenerationProvider(ABC):
    name: str

    @abstractmethod
    def generate(self, prompt: str) -> ProviderResult:
        raise NotImplementedError


class ImageGenerationProvider(ABC):
    name: str

    @abstractmethod
    def generate_image(self, prompt: str) -> ProviderResult:
        raise NotImplementedError


class VoiceOverProvider(ABC):
    name: str

    @abstractmethod
    def synthesize(self, script: str) -> ProviderResult:
        raise NotImplementedError
