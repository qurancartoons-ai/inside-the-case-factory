from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import tomllib
from typing import Any

from inside_case_factory.config.env import load_dotenv


@dataclass(frozen=True)
class Settings:
    root: Path
    app: dict[str, Any]
    paths: dict[str, Any]
    video: dict[str, Any]
    pipeline: dict[str, Any]
    review_gates: dict[str, Any]
    script: dict[str, Any]
    providers: dict[str, Any]

    @property
    def projects_dir(self) -> Path:
        return self.root / str(self.paths.get("projects_dir", "projects"))

    @property
    def default_project(self) -> str:
        return str(self.paths.get("default_project", "example_case"))


def _load_toml(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def load_settings(root: Path | None = None) -> Settings:
    project_root = root or Path.cwd()
    load_dotenv(project_root)
    defaults = _load_toml(project_root / "config" / "defaults.toml")
    providers = _load_toml(project_root / "config" / "providers.toml")

    return Settings(
        root=project_root,
        app=defaults.get("app", {}),
        paths=defaults.get("paths", {}),
        video=defaults.get("video", {}),
        pipeline=defaults.get("pipeline", {}),
        review_gates=defaults.get("review_gates", {}),
        script=defaults.get("script", {}),
        providers=providers,
    )
