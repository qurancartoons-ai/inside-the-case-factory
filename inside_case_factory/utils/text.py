from __future__ import annotations

import html
import re


def compact_whitespace(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_case_topic(topic: str) -> str:
    small = {"a", "an", "and", "as", "at", "for", "in", "of", "on", "or", "the", "to", "with"}
    words = compact_whitespace(topic).split(" ")
    titled: list[str] = []
    for index, word in enumerate(words):
        lower = word.lower()
        titled.append(lower if index and lower in small else lower.capitalize())
    return " ".join(titled)


def svg_escape(value: str) -> str:
    return html.escape(value, quote=True)


def ffmpeg_drawtext_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
