import pytest
from fastapi.testclient import TestClient

from griot_api.main import create_app
from griot_api.merge import merge
from griot_api.repo import MemoryRepo
from griot_api.service import BridgeService
from griot_core.synthetic import make_tracks


@pytest.fixture(scope="module")
def tracks():
    return make_tracks(n=800, seed=11)


@pytest.fixture()
def client(tracks):
    return TestClient(create_app(BridgeService(MemoryRepo(list(tracks)))))


def test_health_and_search(client, tracks):
    assert client.get("/health").json() == {"ok": True, "tracks": 800}
    hits = client.get("/search", params={"q": tracks[5].title}).json()
    assert any(h["id"] == tracks[5].id for h in hits)


def test_bridge_roundtrip_feedback_and_m3u(client, tracks):
    body = {"waypoints": [tracks[0].id, tracks[1].id], "length": 6, "filters": {"min_playcount": None}}
    r = client.post("/bridges", json=body)
    assert r.status_code == 200, r.text
    b = r.json()
    assert [t["role"] for t in b["tracks"]] == ["waypoint"] + ["bridge"] * 6 + ["waypoint"]
    assert len(b["transitions"]) == 7 and b["confidence"] == 1.0
    assert client.get(f"/bridges/{b['id']}").json()["total_cost"] == b["total_cost"]
    assert client.post(f"/bridges/{b['id']}/feedback", json={"position": 2, "rating": 1}).status_code == 204
    assert client.get(f"/bridges/{b['id']}/m3u").text.startswith("#EXTM3U")


def test_multi_waypoint_with_arc(client, tracks):
    body = {
        "waypoints": [tracks[2].id, tracks[3].id, tracks[4].id],
        "length": 8,
        "arc": [{"t": 0, "energy": 0.3}, {"t": 0.5, "energy": 0.9}, {"t": 1, "energy": 0.3}],
    }
    b = client.post("/bridges", json=body).json()
    assert len(b["tracks"]) == 11
    assert [t["id"] for t in b["tracks"] if t["role"] == "waypoint"] == body["waypoints"]


def test_bridge_errors(client, tracks):
    assert client.post("/bridges", json={"waypoints": ["nope", tracks[0].id]}).status_code == 422
    assert client.post("/bridges", json={"waypoints": [tracks[0].id, tracks[0].id]}).status_code == 422


def test_submissions_require_token_and_update_catalog(client):
    new = make_tracks(n=2, seed=99)
    docs = [t.model_dump(mode="json", by_alias=True) for t in new]
    assert client.post("/submissions", json=docs).status_code == 401
    r = client.post("/submissions", json=docs, headers={"Authorization": "Bearer dev-token"})
    assert r.json() == {"accepted": [t.id for t in new]}
    assert client.get("/health").json()["tracks"] == 802
    track = client.get(f"/tracks/{new[0].id}").json()
    assert "embeddings" not in track and track["global"]["bpm"] == new[0].global_.bpm


def test_merge_folds_octave_errors_and_drops_other_masters():
    base = make_tracks(n=1, seed=5)[0]
    a, b, c, d = (base.model_copy(deep=True) for _ in range(4))
    a.global_.bpm, b.global_.bpm, c.global_.bpm = 120.0, 60.0, 121.0
    d.duration_s += 40  # a different master
    d.global_.bpm = 90.0
    m = merge([a, b, c, d])
    assert m.global_.bpm == pytest.approx(120.0, abs=1)


def test_submission_with_foreign_embedding_space_is_rejected(client):
    odd = make_tracks(n=1, dim=16, seed=123)[0]
    r = client.post(
        "/submissions",
        json=[odd.model_dump(mode="json", by_alias=True)],
        headers={"Authorization": "Bearer dev-token"},
    )
    assert r.status_code == 422 and "embedding space" in r.json()["detail"]


def test_merge_prefers_clean_sources_over_flagged_rips():
    base = make_tracks(n=1, seed=9)[0]
    rip, clean = base.model_copy(deep=True), base.model_copy(deep=True)
    rip.external_ids["quality_flags"] = "lossy_transcode"
    rip.global_.bpm = clean.global_.bpm + 9
    clean.external_ids["quality_flags"] = "ok"
    assert merge([rip, clean]).global_.bpm == clean.global_.bpm
