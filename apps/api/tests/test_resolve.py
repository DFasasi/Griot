import pytest
from fastapi.testclient import TestClient

from griot_api.main import create_app
from griot_api.repo import MemoryRepo
from griot_api.service import BridgeService
from griot_core.synthetic import make_tracks
from griot_pipelines.resolve import Resolver


class FakeDeezer:
    """Deezer stand-in: knows one catalog song (by ISRC) and one song Griot lacks."""

    def __init__(self, known):
        self.known = known

    def by_isrc(self, isrc):
        return next((d for d in self.known if d["isrc"] == isrc), None)

    def search(self, artist, title, duration=None):
        return next((d for d in self.known if d["title"].lower() == title.lower()), None)


@pytest.fixture()
def setup():
    tracks = make_tracks(n=50, seed=8)
    tracks[0].isrc = "USAAA0000001"
    known = [
        {
            "id": 1,
            "title": tracks[0].title,
            "artist": {"name": tracks[0].artist},
            "isrc": "USAAA0000001",
            "preview": "p1",
        },
        {
            "id": 2,
            "title": "Brand New Song",
            "artist": {"name": "New Artist"},
            "isrc": "USNEW0000002",
            "preview": "p2",
        },
    ]
    repo = MemoryRepo(tracks)
    svc = BridgeService(repo)
    svc.resolver = Resolver(lookup=svc.lookup, deezer=FakeDeezer(known))
    return TestClient(create_app(svc)), tracks, repo


def test_resolve_spotify_youtube_and_unknown(setup):
    client, tracks, repo = setup
    items = [
        {"source": "spotify", "source_id": "sp1", "title": tracks[0].title, "artist": tracks[0].artist},
        {
            "source": "youtube",
            "source_id": "yt1",
            "title": "New Artist - Brand New Song (Official Video)",
            "artist": "NewArtistVEVO",
        },
        {"source": "spotify", "source_id": "sp3", "title": "Nobody Knows This", "artist": "Ghost"},
        # no external match, but the catalog itself knows the song by name + duration
        {
            "source": "file",
            "title": tracks[5].title,
            "artist": tracks[5].artist,
            "duration_s": tracks[5].duration_s,
        },
    ]
    r = client.post("/resolve", json=items).json()
    assert [x["status"] for x in r] == ["full", "missing", "unmatched", "full"]
    assert r[0]["track_id"] == tracks[0].id and r[0]["isrc"] == "USAAA0000001"
    assert r[1]["artist"] == "New Artist" and r[1]["preview_url"] == "p2"
    assert r[3]["track_id"] == tracks[5].id
    wanted = client.get("/wanted", headers={"Authorization": "Bearer dev-token"}).json()
    assert [w["key"] for w in wanted] == ["USNEW0000002"]


def test_preview_then_full_submission_upgrades_same_entry(setup):
    client, tracks, repo = setup
    auth = {"Authorization": "Bearer dev-token"}
    prev = make_tracks(n=1, seed=77)[0]
    prev.id, prev.isrc, prev.tier = "isrc:USNEW0000002", "USNEW0000002", "B"
    prev.analyzer.full_audio = False
    repo.want(
        [
            {
                "key": "USNEW0000002",
                "isrc": "USNEW0000002",
                "deezer_id": "2",
                "title": "Brand New Song",
                "artist": "New Artist",
                "duration_s": None,
                "preview_url": "p2",
            }
        ]
    )
    client.post("/submissions", json=[prev.model_dump(mode="json", by_alias=True)], headers=auth)
    assert repo.wanted(10) == []  # analysed (as preview) -> leaves the queue

    full = prev.model_copy(deep=True)
    full.id, full.mbid, full.tier = "some-mbid", "some-mbid", "A"
    full.analyzer.full_audio = True
    full.global_.bpm = prev.global_.bpm + 7
    client.post(
        "/submissions",
        json=[full.model_dump(mode="json", by_alias=True)],
        headers={"Authorization": "Bearer dev-token"},
    )
    t = client.get("/tracks/isrc:USNEW0000002").json()
    assert t["tier"] == "A" and t["analyzer"]["full_audio"] is True
    assert t["global"]["bpm"] == pytest.approx(full.global_.bpm)
