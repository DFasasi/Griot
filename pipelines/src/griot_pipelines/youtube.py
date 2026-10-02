"""Read a YouTube playlist (public or unlisted) with the Data API v3 and an API key.

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
