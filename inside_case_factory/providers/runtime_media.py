from __future__ import annotations

from pathlib import Path
import subprocess

from inside_case_factory.providers.production import ProductionProviderRouter, ProductionRequest


class RoutedVoiceOverProvider:
    name = "production_router"

    def __init__(self, router: ProductionProviderRouter, project_root: Path) -> None:
        self.router = router
        self.project_root = project_root

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        response = self.router.execute(ProductionRequest("voice", "voice_over", text, self.project_root))
        temporary = output_path.with_suffix(".provider-audio")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_bytes(response.data)
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(temporary), "-ar", "44100", "-ac", "1", str(output_path)], check=True)
        temporary.unlink(missing_ok=True)
        return output_path


class FailoverVoiceOverProvider:
    name = "production_router_with_local_fallback"

    def __init__(self, primary: object, fallback: object) -> None:
        self.primary = primary
        self.fallback = fallback

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        try:
            return self.primary.synthesize_to_file(text, output_path, text_path)  # type: ignore[attr-defined]
        except Exception:
            output_path.unlink(missing_ok=True)
            return self.fallback.synthesize_to_file(text, output_path, text_path)  # type: ignore[attr-defined]


class RoutedImageProvider:
    name = "production_router"

    def __init__(self, router: ProductionProviderRouter, project_root: Path) -> None:
        self.router = router
        self.project_root = project_root

    def generate_to_file(self, prompt: str, output_path: Path) -> Path:
        response = self.router.execute(ProductionRequest("image", "scene_image", prompt, self.project_root, output_path=output_path))
        if not response.data:
            raise RuntimeError(f"{response.provider} did not return completed image bytes.")
        return output_path
