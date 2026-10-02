"""Identify local audio files: tags, content hash, Chromaprint fingerprint, AcoustID -> MBID."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import mutagen

AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".flac", ".wav", ".aiff", ".aif", ".ogg", ".opus", ".alac"}
ACOUSTID_URL = "https://api.acoustid.org/v2/lookup"


@dataclass
class FileIdentity:
    path: str
    sha1: str
    title: str
    artist: str
    album: str | None = None
    year: int | None = None
    isrc: str | None = None
    mbid: str | None = None  # recording MBID
    artist_mbids: list[str] = field(default_factory=list)
    duration_s: float | None = None
    fingerprint: str | None = None


def iter_audio(root: Path):
    for dirpath, _, files in os.walk(root):
        for f in files:
            p = Path(dirpath) / f
            if p.suffix.lower() in AUDIO_EXTS and not f.startswith("._"):
                yield p


def content_hash(path: Path) -> str:
    """Fast stable id: size + first and last MiB. Enough to detect duplicates/moves."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with path.open("rb") as f:
        h.update(f.read(1 << 20))
        if size > 2 << 20:
            f.seek(-(1 << 20), os.SEEK_END)
            h.update(f.read(1 << 20))
    return h.hexdigest()


def _first(tags, *keys) -> str | None:
    for k in keys:
        v = tags.get(k)
        if v:
            v = v[0] if isinstance(v, list) else v
            return str(v).strip() or None
    return None


def read_tags(path: Path) -> dict:
    try:
        f = mutagen.File(path, easy=True)
    except Exception:
        f = None
    tags = dict(f.tags or {}) if f is not None and f.tags is not None else {}
    year = _first(tags, "date", "originaldate")
    mbid = _first(tags, "musicbrainz_trackid")
    return {
        "title": _first(tags, "title") or path.stem,
        "artist": _first(tags, "artist", "albumartist") or "Unknown Artist",
        "album": _first(tags, "album"),
        "year": int(year[:4]) if year and year[:4].isdigit() else None,
        "isrc": _first(tags, "isrc"),
        "mbid": mbid,
        "artist_mbids": [a for a in (tags.get("musicbrainz_artistid") or []) if a],
        "duration_s": float(f.info.length) if f is not None and getattr(f, "info", None) else None,
    }


def fingerprint(path: Path) -> tuple[float, str] | None:
    try:
        out = subprocess.run(["fpcalc", "-json", str(path)], capture_output=True, text=True, timeout=120)
        data = json.loads(out.stdout)
        return float(data["duration"]), data["fingerprint"]
    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired):
        return None


def acoustid_lookup(duration: float, fp: str, api_key: str) -> dict | None:
    """Best AcoustID match -> {'mbid', 'title', 'artist', 'artist_mbids'} or None."""
    r = httpx.post(
        ACOUSTID_URL,
        data={
            "client": api_key,
            "duration": int(duration),
            "fingerprint": fp,
            "meta": "recordings",
            "format": "json",
        },
        timeout=20,
    )
    r.raise_for_status()
    results = sorted(r.json().get("results", []), key=lambda x: -x.get("score", 0))
    for res in results:
        if res.get("score", 0) < 0.8:
            break
        for rec in res.get("recordings", []):
            if rec.get("title"):
                artists = rec.get("artists", [])
                return {
                    "mbid": rec["id"],
                    "title": rec["title"],
                    "artist": " & ".join(a["name"] for a in artists) or None,
                    "artist_mbids": [a["id"] for a in artists],
                }
    return None


def identify(path: Path, acoustid_key: str | None = None) -> FileIdentity:
    tags = read_tags(path)
    ident = FileIdentity(path=str(path), sha1=content_hash(path), **tags)
    if ident.mbid:
        return ident
    fp = fingerprint(path)
    if fp:
        ident.duration_s = ident.duration_s or fp[0]
        ident.fingerprint = fp[1]
        if acoustid_key:
            try:
                match = acoustid_lookup(fp[0], fp[1], acoustid_key)
            except httpx.HTTPError:
                match = None
            if match:
                ident.mbid = match["mbid"]
                ident.title = match["title"] or ident.title
                ident.artist = match["artist"] or ident.artist
                ident.artist_mbids = match["artist_mbids"] or ident.artist_mbids
    return ident


def track_id(ident: FileIdentity) -> str:
    return ident.mbid or f"local:{ident.sha1}"
