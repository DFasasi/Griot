"""Cross-platform song mapping: Spotify track / YouTube video / local file -> one recording.

Identity ladder (stop at the first that works):
  1. ISRC given by the source            -> catalog by ISRC
  2. Deezer search on title+artist(+len) -> ISRC, Deezer id, preview URL
  3. catalog by ISRC / Deezer id / normalised (artist, title) with duration ±5 s
YouTube titles are cleaned first ("Artist - Song (Official Video) [4K]" -> artist, song).
"""

from __future__ import annotations

import re
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

from griot_core.schema import ImportItem, Resolution
from griot_pipelines.sources import Deezer, SourceUnavailable, norm

Lookup = Callable[..., tuple[str, str] | None]  # -> (track_id, tier) or None

_NOISE_WORDS = re.compile(
    r"\b(official|video|audio|lyrics?|visuali[sz]er|hd|hq|4k|mv|m/v|clip|music video|explicit|clean|"
    r"remaster(ed)?|live session|performance)\b",
    re.I,
)
_VERSION_WORDS = re.compile(
    r"\b(remix|live|acoustic|sped up|slowed|instrumental|cover|extended|acapella)\b", re.I
)
_BRACKETS = re.compile(r"\s*[\(\[\{]([^\)\]\}]*)[\)\]\}]")
_QUOTED = re.compile(r"^(?P<artist>.*?)\s*[‘'\"“](?P<song>.+?)[’'\"”]\s*(?P<rest>.*)$")
_CHANNEL_NOISE = re.compile(r"\s*(-\s*topic|vevo|official|music|records)\s*$", re.I)


def _clean_brackets(t: str) -> str:
    """Drop "(Official Video)"-style groups but keep version info: "(Remix - Official Video)" -> "(Remix)"."""

    def repl(m: re.Match) -> str:
        inner = m.group(1)
        if not _NOISE_WORDS.search(inner):
            return m.group(0)
        versions = _VERSION_WORDS.findall(inner)
        return f" ({' '.join(v.title() for v in versions)})" if versions else ""

    return _BRACKETS.sub(repl, t).strip()


def _latin_alias(artist: str) -> str:
    """ "정국 (Jung Kook)" -> "Jung Kook"; catalogs index the romanised name."""
    m = re.fullmatch(r"(.*?)\s*\(([^)]+)\)", artist.strip())
    if m and not m.group(1).isascii() and m.group(2).isascii():
        return m.group(2).strip()
    return re.sub(r"\s*\([^)]*\)", "", artist).strip() or artist


def parse_youtube_title(title: str, channel: str | None) -> tuple[str | None, str]:
    """('Artist - Song (Official Video)', 'ArtistVEVO') -> ('Artist', 'Song').

    Also handles K-pop style "Artist 'Song' Official MV" and keeps version words.
    """
    t = re.sub(r"\s*\|.*$", "", title)  # "Song | Album Trailer"
    t = _clean_brackets(t)
    for sep in (" - ", " – ", " — ", " ~ "):
        if sep in t:
            artist, song = t.split(sep, 1)
            return _latin_alias(artist.strip()), song.strip().strip('"“”‘’')
    q = _QUOTED.match(t)
    if q and q.group("artist").strip() and not q.group("rest").strip(" ").replace(".", "").isalnum():
        rest = _NOISE_WORDS.sub("", q.group("rest")).strip()
        if not rest:
            return _latin_alias(q.group("artist")), q.group("song").strip()
    artist = _CHANNEL_NOISE.sub("", channel or "").strip() or None
    return artist, _NOISE_WORDS.sub("", t).strip().strip('"“”‘’').strip()


@dataclass
class Resolver:
    lookup: Lookup
    deezer: Deezer = field(default_factory=Deezer)
    _cache: dict[tuple, dict | None] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def resolve(self, item: ImportItem) -> Resolution:
        title, artist = item.title, item.artist
        if item.source == "youtube":
            artist_guess, title = parse_youtube_title(item.title, item.artist)
            artist = artist_guess or artist
        # Cache only the external identity (slow, rate-limited); catalog status changes as
        # analyses arrive, so it is looked up fresh every time.
        key = (norm(artist), norm(title), round(item.duration_s or 0), item.isrc)
        with self._lock:
            cached = key in self._cache
            d = self._cache.get(key)
        if not cached:
            try:
                d = self.deezer.by_isrc(item.isrc) if item.isrc else None
                if d is None and artist and title:
                    d = self.deezer.search(artist, title, item.duration_s)
            except SourceUnavailable:
                d = None  # leave uncached: the next re-check tries again
            else:
                with self._lock:
                    self._cache[key] = d

        res = Resolution(item=item, status="unmatched", title=title, artist=artist, isrc=item.isrc)
        if d is not None:
            res.title, res.artist = d.get("title") or title, (d.get("artist") or {}).get("name") or artist
            res.isrc = res.isrc or d.get("isrc")
            res.deezer_id = str(d["id"])
            res.preview_url = d.get("preview") or None
            res.confidence = 0.95 if item.isrc else 0.8

        hit = self.lookup(
            isrc=res.isrc,
            deezer_id=res.deezer_id,
            artist=res.artist,
            title=res.title,
            duration=item.duration_s,
        )
        if hit:
            res.track_id, tier = hit
            res.status = "preview" if tier == "B" else "full"
            res.confidence = max(res.confidence, 0.7)
        elif d is not None:
            res.status = "missing"
        return res
