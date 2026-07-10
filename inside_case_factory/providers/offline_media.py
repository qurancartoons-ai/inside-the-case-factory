from __future__ import annotations

from pathlib import Path
import subprocess


class FFmpegFliteVoiceOverProvider:
    name = "ffmpeg_flite"

    def synthesize_to_file(self, text: str, output_path: Path, text_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
        subprocess.run(
            [
                "ffmpeg",
                "-hide_banner",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"flite=textfile={text_path}:voice=kal",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(output_path),
            ],
            check=True,
        )
        return output_path


class SVGPlaceholderImageProvider:
    name = "local_svg_placeholder"

    def write_svg(self, output_path: Path, svg: str) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(svg, encoding="utf-8")
        return output_path
