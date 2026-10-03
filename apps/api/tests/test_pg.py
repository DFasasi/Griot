"""Postgres integration tests; run with GRIOT_TEST_DATABASE_URL pointing at a migrated, disposable DB."""

import os

import pytest
from fastapi.testclient import TestClient

from griot_api.main import create_app
from griot_api.repo import PgRepo
from griot_api.service import BridgeService
from griot_core.synthetic import make_tracks

DSN = os.environ.get("GRIOT_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="GRIOT_TEST_DATABASE_URL not set")


@pytest.fixture()
def repo():
    r = PgRepo(DSN)
    with r.pool.connection() as c:
        c.execute("TRUNCATE recordings, submissions, bridges, bridge_feedback, wanted")
    return r


def test_pg_submit_merge_bridge_feedback(repo):
    tracks = make_tracks(n=60, dim=512, lyr_dim=384, seed=4)
    client = TestClient(create_app(BridgeService(repo)))
    docs = [t.model_dump(mode="json", by_alias=True) for t in tracks]
    auth = {"Authorization": "Bearer test-token"}
    assert client.post("/submissions", json=docs, headers=auth).status_code == 200
    # other submitters re-analyze one track with an octave-off tempo; merge keeps the true tempo
    alt = tracks[0].model_copy(deep=True)
    alt.global_.bpm = tracks[0].global_.bpm * 2
    assert len(repo.all_tracks()) == 60

    with repo.pool.connection() as c:
        n_vec = c.execute("SELECT count(*) FROM recordings WHERE emb_full IS NOT NULL").fetchone()[0]
        nn = c.execute(
            "SELECT id FROM recordings "
            "ORDER BY emb_full <=> (SELECT emb_full FROM recordings WHERE id=%s) LIMIT 1",
            (tracks[3].id,),
        ).fetchone()[0]
    assert n_vec == 60 and nn == tracks[3].id

    repo.submit("other-user", [alt])
    repo.submit("third-user", [tracks[0]])  # majority of 2 vs 1 settles the octave
    merged = next(t for t in repo.all_tracks() if t.id == tracks[0].id)
    assert merged.global_.bpm == pytest.approx(tracks[0].global_.bpm, rel=0.01)

    b = client.post("/bridges", json={"waypoints": [tracks[0].id, tracks[1].id], "length": 4}).json()
    assert len(b["tracks"]) == 6
    assert client.get(f"/bridges/{b['id']}").json()["id"] == b["id"]
    assert client.post(f"/bridges/{b['id']}/feedback", json={"position": 0, "rating": -1}).status_code == 204


def test_pg_wanted_queue_counts_requests_and_clears(repo):
    with repo.pool.connection() as c:
        c.execute("TRUNCATE wanted")
    item = {"key": "USX1", "isrc": "USX1", "deezer_id": "9", "title": "T", "artist": "A", "duration_s": 200.0,
            "preview_url": "p"}  # fmt: skip
    repo.want([item])
    repo.want([item | {"preview_url": None}])
    (w,) = repo.wanted(10)
    assert w["requests"] == 2 and w["preview_url"] == "p"  # a later null doesn't erase the preview
    repo.unwant(["USX1"])
    assert repo.wanted(10) == []


def test_pg_sync_library_roundtrip(repo):
    from griot_api import sync

    lib = sync.library_id(sync.new_code())
    repo.library_create(lib)
    assert repo.library_get(lib) == {}
    merged = sync.merge({}, [{"item": {"source": "youtube", "source_id": "v1", "title": "T"}, "added": 1}])
    repo.library_put(lib, merged)
    assert repo.library_get(lib) == merged
