"""Lyrics -> opening/closing/full embeddings + theme tags.

Only derived features are stored (vectors and tags); lyric text is never persisted.
"""

from __future__ import annotations

import re
from functools import cached_property
from typing import Literal

import numpy as np
from pydantic import BaseModel, Field

EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"  # 384-d, 50+ languages
THEME_MODEL = "claude-haiku-4-5"
EDGE_LINES = 4

Theme = Literal[
    "love", "heartbreak", "longing", "desire", "celebration", "party", "defiance",
    "empowerment", "struggle", "grief", "nostalgia", "faith", "wealth", "social commentary",
    "self-reflection", "freedom", "loneliness", "home",
]  # fmt: skip

_TS = re.compile(r"^\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)$")


def parse_lyrics(synced: str | None, plain: str | None) -> list[tuple[float | None, str]]:
    """Lines as (time_s or None, text), dropping blanks and instrumental markers."""
    lines: list[tuple[float | None, str]] = []
    if synced:
        for raw in synced.splitlines():
            m = _TS.match(raw.strip())
            if m and m.group(3).strip() and m.group(3).strip() not in {"♪", "..."}:
                lines.append((int(m.group(1)) * 60 + float(m.group(2)), m.group(3).strip()))
    if not lines and plain:
        lines = [(None, x.strip()) for x in plain.splitlines() if x.strip() and not x.startswith("[")]
    return lines


def edges(lines: list[tuple[float | None, str]], n: int = EDGE_LINES) -> tuple[str, str, str]:
    texts = [t for _, t in lines]
    return " / ".join(texts[:n]), " / ".join(texts[-n:]), "\n".join(texts)


class ThemeTags(BaseModel):
    themes: list[Theme] = Field(description="1-3 dominant themes, most central first")
    opening_mood: Literal["positive", "neutral", "negative"]
    closing_mood: Literal["positive", "neutral", "negative"]


THEME_PROMPT = """Classify the song lyrics below.
Pick 1-3 dominant themes from the allowed list (most central first), and the emotional
tone of the opening lines and of the closing lines.

<lyrics>
{lyrics}
</lyrics>"""


class LyricsModel:
    @cached_property
    def encoder(self):
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(EMBED_MODEL)

    @cached_property
    def claude(self):
        import anthropic

        return anthropic.Anthropic()

    def embed(self, texts: list[str]) -> np.ndarray:
        return self.encoder.encode(texts, normalize_embeddings=True, convert_to_numpy=True)

    def themes(self, lyrics: str) -> ThemeTags | None:
        import anthropic

        try:
            resp = self.claude.messages.parse(
                model=THEME_MODEL,
                max_tokens=256,
                messages=[{"role": "user", "content": THEME_PROMPT.format(lyrics=lyrics[:6000])}],
                output_format=ThemeTags,
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
            raise
        except anthropic.APIError:
            return None
        if resp.stop_reason == "refusal":
            return None
        return resp.parsed_output
