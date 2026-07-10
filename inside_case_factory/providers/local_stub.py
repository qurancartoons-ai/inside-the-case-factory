from __future__ import annotations

from datetime import UTC, datetime

from inside_case_factory.providers.base import ProviderResult, ResearchProvider, TextGenerationProvider


class LocalStubResearchProvider(ResearchProvider):
    name = "local_stub"

    def research(self, topic: str) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            payload={
                "topic": topic,
                "status": "not_started",
                "message": "Offline stub only. Add verified sources before scripting.",
                "sources": [],
                "created_at": datetime.now(UTC).isoformat(),
            },
        )


class LocalStubTextProvider(TextGenerationProvider):
    name = "local_stub"

    def generate(self, prompt: str) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            payload={
                "prompt": prompt,
                "status": "not_generated",
                "message": "Offline stub only. No paid model was called.",
            },
        )
