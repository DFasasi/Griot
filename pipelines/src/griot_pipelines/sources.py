"""Thin, rate-limited clients for the public catalog sources Griot enriches from."""

from __future__ import annotations

import os
import re
import threading
import time

import httpx

USER_AGENT = "Griot/0.1 (https://github.com/DFasasi/Griot)"


class RateLimiter:
    def __init__(self, per_second: float) -> None:
        self.interval = 1.0 / per_second
        self.next = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            if now < self.next:
                time.sleep(self.next - now)
            self.next = max(now, self.next) + self.interval


class Source:
    base: str
    rate: float

    def __init__(self, client: httpx.Client | None = None) -> None:
        self.http = client or httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=20)
        self.limit = RateLimiter(self.rate)

    def _get(self, path: str, **params) -> dict | list | None:
        for attempt in range(3):
            self.limit.wait()
            r = self.http.get(self.base + path, params=params or None)
            if r.status_code == 404:
                return None
            if r.status_code in (429, 503):
                time.sleep(2**attempt)
                continue
            r.raise_for_status()
            return r.json()
        return None


_FEAT = re.compile(r"\s*[\(\[]?\b(feat\.?|ft\.?|featuring)\b.*$", re.I)
_SUFFIX = re.compile(r"\s+-\s+.*\b(remaster(ed)?|version|edit|mix|live|mono|stereo|single)\b.*$", re.I)


def norm(s: str | None) -> str:
    """Comparable form of a title/artist: no featuring credits, edition suffixes or punctuation."""
    s = _SUFFIX.sub("", _FEAT.sub("", s or ""))
    s = re.sub(r"\s*[\(\[].*?[\)\]]", "", s)
    s = re.sub(r"[^\w ]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


_VERSION = re.compile(
    r"\b(remix|rmx|live|acoustic|sped[ -]?up|slowed|reverb|nightcore|instrumental|karaoke|cover|"
    r"demo|reprise|extended|club mix|radio edit|a cappella|acapella|8d)\b",
    re.I,
)


def _versions(title: str) -> set[str]:
    return {m.lower().replace("-", " ") for m in _VERSION.findall(title or "")}


def _same(a: str, b: str) -> bool:
    return bool(a and b) and (a == b or a in b or b in a)


class Deezer(Source):
    base = "https://api.deezer.com"
    rate = 8.0  # documented quota: 50 requests / 5 s

    def by_isrc(self, isrc: str) -> dict | None:
        d = self._get(f"/track/isrc:{isrc}")
        return None if not d or "error" in d else d

    def search(self, artist: str, title: str, duration: float | None = None) -> dict | None:
        """Best catalog match for a title/artist, or None rather than a wrong version.

        Plain search ranks viral edits and tributes highly (e.g. dozens of sped-up "Bloody
        Mary"s outrank Lady Gaga's original), so when it finds nothing acceptable we look in
        the artist's own top tracks.
        """
        a_norms = [norm(a) for a in re.split(r",|&| x | and ", artist)] or [norm(artist)]
        d = self._get("/search", q=f"{artist} {title}", limit=10)
        best, score = self._best((d or {}).get("data", []), title, a_norms, duration)
        if best is None or score < 1.5:
            best, score = self._best(self._artist_top(artist, a_norms), title, a_norms, duration)
        if best is None or score < 1.5:
            return None
        return self._get(f"/track/{best['id']}")  # full record has bpm, isrc, gain

    @staticmethod
    def _best(cands: list[dict], title: str, a_norms: list[str], duration: float | None):
        best, best_score = None, 0.0
        t_norm, wanted_versions = norm(title), _versions(title)
        for x in cands:
            cand = f"{x['title']} {x.get('title_version') or ''}"
            score = float(_same(norm(x["title"]), t_norm))
            score += float(any(_same(norm(x["artist"]["name"]), a) for a in a_norms))
            if duration and abs(x.get("duration", 0) - duration) <= 5:
                score += 0.5
            # A remix / live / sped-up cut has different seams: never trade the original for it.
            if _versions(cand) - wanted_versions:
                score -= 1.0
            if x["title"].strip().lower() == title.strip().lower():
                score += 0.25
            if score > best_score:
                best, best_score = x, score
        return best, best_score

    def _artist_top(self, artist: str, a_norms: list[str]) -> list[dict]:
        found = (self._get("/search/artist", q=artist, limit=3) or {}).get("data", [])
        match = next((a for a in found if norm(a["name"]) in a_norms), None)
        if match is None:
            return []
        return (self._get(f"/artist/{match['id']}/top", limit=100) or {}).get("data", [])

    def lookup(self, artist: str, title: str, isrc: str | None, duration: float | None) -> dict | None:
        return (isrc and self.by_isrc(isrc)) or self.search(artist, title, duration)


class LrcLib(Source):
    base = "https://lrclib.net/api"
    rate = 5.0

    def get(self, artist: str, title: str, duration: float | None = None) -> dict | None:
        params = {"artist_name": artist, "track_name": title}
        if duration:
            params["duration"] = int(round(duration))
        hit = self._get("/get", **params)
        if hit:
            return hit
        res = self._get("/search", artist_name=artist, track_name=title) or []
        if duration:
            res = [x for x in res if abs((x.get("duration") or 0) - duration) <= 5]
        return res[0] if res else None


class ListenBrainz(Source):
    base = "https://api.listenbrainz.org/1"
    rate = 2.0

    def listens(self, mbids: list[str]) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in range(0, len(mbids), 100):
            self.limit.wait()
            r = self.http.post(
                f"{self.base}/popularity/recording", json={"recording_mbids": mbids[i : i + 100]}
            )
            r.raise_for_status()
            for row in r.json():
                if row.get("total_listen_count") is not None:
                    out[row["recording_mbid"]] = int(row["total_listen_count"])
        return out


class LastFm(Source):
    base = "https://ws.audioscrobbler.com/2.0/"
    rate = 4.0

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        super().__init__(client)
        self.key = api_key or os.environ.get("LASTFM_API_KEY")

    def playcount(self, artist: str, title: str, mbid: str | None = None) -> int | None:
        if not self.key:
            return None
        params = {"method": "track.getInfo", "api_key": self.key, "format": "json", "autocorrect": 1}
        params |= {"mbid": mbid} if mbid else {"artist": artist, "track": title}
        d = self._get("", **params)
        if (not d or "error" in d) and mbid:  # MBID lookups miss often on Last.fm
            d = self._get("", **(params | {"mbid": None, "artist": artist, "track": title}))
        try:
            return int(d["track"]["playcount"])
        except (TypeError, KeyError, ValueError):
            return None


class MusicBrainz(Source):
    base = "https://musicbrainz.org/ws/2"
    rate = 1.0  # MusicBrainz hard limit

    def by_isrc(self, isrc: str) -> str | None:
        d = self._get(f"/isrc/{isrc}", fmt="json")
        recs = (d or {}).get("recordings", [])
        return recs[0]["id"] if recs else None
