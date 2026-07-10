from __future__ import annotations

import shutil
import subprocess


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def ffmpeg_version() -> str:
    if not ffmpeg_available():
        return "ffmpeg not found"
    completed = subprocess.run(
        ["ffmpeg", "-version"],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()[0]
