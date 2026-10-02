"""Read YouTube playlists (public or unlisted) and single videos with the Data API v3 and an
API key. Accepts any mix of links: playlist, watch?v=, youtu.be/, Shorts, YouTube Music.

Quota: playlistItems.list and videos.list cost 1 unit per call of up to 50 items; the free
tier is 10,000 units/day, so a 1,000-song playlist costs ~40 units.
"""

from __future__ import annotations

import os
import re

import httpx

from griot_core.schema import ImportItem

API = "https://www.googleapis.com/youtube/v3"
_DUR = re.compile(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?")


def playlist_id(url_or_id: str) -> str:
    m = re.search(r"[?&]list=([\w-]+)", url_or_id)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{10,}", url_or_id.strip()):
        return url_or_id.strip()
    raise ValueError("not a YouTube playlist URL or id")


_VIDEO = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([\w-]{11})")
_LINK_SPLIT = re.compile(r"[\s,]+")


def parse_link(link: str) -> tuple[str, str]:
    """-> ("playlist", id) or ("video", id). Raises ValueError for anything else.

    A watch link inside a playlist (watch?v=…&list=…) means the playlist, except YouTube's
    auto-generated mixes (list=RD…), which the API can't read; those fall back to the video.
    """
    link = link.strip()
    lst = re.search(r"[?&]list=([\w-]+)", link)
    if lst and not lst.group(1).startswith("RD"):
        return "playlist", lst.group(1)
    vid = _VIDEO.search(link)
    if vid:
        return "video", vid.group(1)
    if re.fullmatch(r"(PL|OL|UU|FL|LL)[\w-]{10,}", link):
        return "playlist", link
    raise ValueError("not a YouTube playlist or video link")


def split_links(text: str) -> list[str]:
    return [x for x in _LINK_SPLIT.split(text or "") if x]


def iso_seconds(s: str) -> float | None:
    m = _DUR.fullmatch(s or "")
    if not m:
        return None
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return float(((d * 24 + h) * 60 + mi) * 60 + se)


def fetch_playlist(url_or_id: str, api_key: str | None = None, limit: int = 2000) -> list[ImportItem]:
    key = api_key or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured on the server")
    pid = playlist_id(url_or_id)
    items: list[dict] = []
    with httpx.Client(base_url=API, timeout=20) as c:
        token = None
        while len(items) < limit:
            params = {"part": "snippet", "playlistId": pid, "maxResults": 50, "key": key}
            if token:
                params["pageToken"] = token
            r = c.get("/playlistItems", params=params)
            r.raise_for_status()
            data = r.json()
            items += data.get("items", [])
            token = data.get("nextPageToken")
            if not token:
                break
        ids = [i["snippet"]["resourceId"]["videoId"] for i in items if i["snippet"].get("resourceId")]
        durations: dict[str, float | None] = {}
        for i in range(0, len(ids), 50):
            r = c.get(
                "/videos", params={"part": "contentDetails", "id": ",".join(ids[i : i + 50]), "key": key}
            )
            r.raise_for_status()
            for v in r.json().get("items", []):
                durations[v["id"]] = iso_seconds(v["contentDetails"].get("duration", ""))
    out = []
    for i in items:
        sn = i["snippet"]
        vid = (sn.get("resourceId") or {}).get("videoId")
        if not vid or sn.get("title") in {"Private video", "Deleted video"}:
            continue
        out.append(
            ImportItem(
                source="youtube",
                source_id=vid,
                title=sn["title"],
                artist=sn.get("videoOwnerChannelTitle"),
                duration_s=durations.get(vid),
            )
        )
    return out


def fetch_videos(video_ids: list[str], api_key: str | None = None) -> list[ImportItem]:
    key = api_key or os.environ.get("YOUTUBE_API_KEY")
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not configured on the server")
    out: list[ImportItem] = []
    with httpx.Client(base_url=API, timeout=20) as c:
        for i in range(0, len(video_ids), 50):
            r = c.get(
                "/videos",
                params={"part": "snippet,contentDetails", "id": ",".join(video_ids[i : i + 50]), "key": key},
            )
            r.raise_for_status()
            for v in r.json().get("items", []):
                out.append(
                    ImportItem(
                        source="youtube",
                        source_id=v["id"],
                        title=v["snippet"]["title"],
                        artist=v["snippet"].get("channelTitle"),
                        duration_s=iso_seconds(v["contentDetails"].get("duration", "")),
                    )
                )
    return out


def fetch_links(links: list[str], api_key: str | None = None) -> tuple[list[ImportItem], list[dict]]:
    """Fetch every link; returns (items, per-link report). One bad link never sinks the rest."""
    items: list[ImportItem] = []
    report: list[dict] = []
    videos: list[tuple[int, str]] = []  # (report index, video id), fetched together in batches
    for link in links:
        row = {"link": link, "kind": None, "count": 0, "error": None}
        report.append(row)
        try:
            kind, ident = parse_link(link)
            row["kind"] = kind
            if kind == "playlist":
                got = fetch_playlist(ident, api_key)
                items += got
                row["count"] = len(got)
                if not got:
                    row["error"] = "playlist is empty, private, or doesn't exist"
            else:
                videos.append((len(report) - 1, ident))
        except ValueError as e:
            row["error"] = str(e)
        except httpx.HTTPStatusError as e:
            row["error"] = (
                "not found or private"
                if e.response.status_code == 404
                else f"YouTube error {e.response.status_code}"
            )
    if videos:
        got = {it.source_id: it for it in fetch_videos([v for _, v in videos], api_key)}
        for idx, vid in videos:
            if vid in got:
                items.append(got[vid])
                report[idx]["count"] = 1
            else:
                report[idx]["error"] = "video not found or private"
    return items, report
