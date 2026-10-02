"""Enrich analyzed tracks with identity, popularity, previews and lyric features."""

from __future__ import annotations

from dataclasses import dataclass, field

from griot_core.schema import LyricFeatures, TrackFeatures
from griot_pipelines.sources import Deezer, LastFm, ListenBrainz, LrcLib, MusicBrainz


@dataclass
class EnrichStats:
    deezer: int = 0
    listenbrainz: int = 0
    lastfm: int = 0
    lyrics: int = 0
    instrumental: int = 0
    themes: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class Enricher:
    lyrics: bool = True
    themes: bool = True
    resolve_mbids: bool = True
    deezer: Deezer = field(default_factory=Deezer)
    lrclib: LrcLib = field(default_factory=LrcLib)
    lb: ListenBrainz = field(default_factory=ListenBrainz)
    lastfm: LastFm = field(default_factory=LastFm)
    mb: MusicBrainz = field(default_factory=MusicBrainz)

    def __post_init__(self) -> None:
        self._lyr = None
        if self.lyrics:
            from griot_pipelines.lyrics import LyricsModel

            self._lyr = LyricsModel()

    def run(self, tracks: list[TrackFeatures], progress=None) -> EnrichStats:
        stats = EnrichStats()
        for t in tracks:
            try:
                self._metadata(t, stats)
                if self._lyr is not None and t.lyrics is None:
                    self._lyrics(t, stats)
            except Exception as e:  # one bad track must not stop a long batch
                stats.errors.append(f"{t.id}: {e}")
            if progress:
                progress()
        mbids = [t.mbid for t in tracks if t.mbid]
        if mbids:
            counts = self.lb.listens(mbids)
            for t in tracks:
                if t.mbid in counts:
                    t.popularity.lb_listens = counts[t.mbid]
                    stats.listenbrainz += 1
        return stats

    def _metadata(self, t: TrackFeatures, stats: EnrichStats) -> None:
        if "deezer" not in t.external_ids:
            d = self.deezer.lookup(t.artist, t.title, t.isrc, t.duration_s)
            if d:
                t.external_ids["deezer"] = str(d["id"])
                if d.get("preview"):
                    t.external_ids["deezer_preview"] = d["preview"]
                t.popularity.deezer_rank = d.get("rank")
                t.isrc = t.isrc or d.get("isrc")
                if t.explicit is None:
                    t.explicit = bool(d.get("explicit_lyrics"))
                stats.deezer += 1
        if self.resolve_mbids and not t.mbid and t.isrc:
            t.mbid = self.mb.by_isrc(t.isrc)
        if t.popularity.lastfm_playcount is None:
            pc = self.lastfm.playcount(t.artist, t.title, t.mbid)
            if pc is not None:
                t.popularity.lastfm_playcount = pc
                stats.lastfm += 1

    def _lyrics(self, t: TrackFeatures, stats: EnrichStats) -> None:
        from griot_pipelines.lyrics import EMBED_MODEL, edges, parse_lyrics

        hit = self.lrclib.get(t.artist, t.title, t.duration_s)
        if not hit:
            return
        if hit.get("instrumental"):
            t.lyrics = LyricFeatures(model=EMBED_MODEL, instrumental=True)
            stats.instrumental += 1
            return
        lines = parse_lyrics(hit.get("syncedLyrics"), hit.get("plainLyrics"))
        if len(lines) < 4:
            return
        opening, closing, full = edges(lines)
        emb = self._lyr.embed([full, opening, closing]).round(5)
        themes = []
        if self.themes:
            try:
                tags = self._lyr.themes(full)
            except Exception as e:  # no Claude credentials: stop trying for the rest of the batch
                stats.errors.append(f"theme tagging disabled: {e}")
                self.themes, tags = False, None
            if tags:
                themes = list(tags.themes)
                stats.themes += 1
        t.lyrics = LyricFeatures(
            model=EMBED_MODEL,
            full=emb[0].tolist(),
            open=emb[1].tolist(),
            close=emb[2].tolist(),
            themes=themes,
        )
        stats.lyrics += 1
