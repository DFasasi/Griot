import pytest
from fastapi.testclient import TestClient

from griot_api import sync
from griot_api.main import create_app
from griot_api.repo import MemoryRepo
from griot_api.service import BridgeService
from griot_core.synthetic import make_tracks


def entry(sid, status="missing", checked=1, source="youtube"):
    return {"item": {"source": source, "source_id": sid, "title": f"T{sid}", "artist": "A"}, "status": status,
            "added": 1, "checked": checked}  # fmt: skip


@pytest.fixture()
def client():
    return TestClient(create_app(BridgeService(MemoryRepo(make_tracks(n=10, seed=1)))))


def test_codes_are_random_formatted_and_tolerant():
    a, b = sync.new_code(), sync.new_code()
    assert a != b and len(a) == 24 and a.count("-") == 4
    assert sync.library_id(a.lower().replace("-", " ")) == sync.library_id(a)
    with pytest.raises(ValueError):
        sync.normalize_code("too-short")


def test_two_devices_converge_without_losing_songs(client):
    code = client.post("/sync/new").json()["code"]
    h = {"X-Griot-Sync": code}
    safari = client.post("/sync/merge", json={"entries": [entry("a"), entry("b")]}, headers=h).json()
    assert safari["count"] == 2
    chrome = client.post(
        "/sync/merge", json={"entries": [entry("b", "full", checked=5), entry("c")]}, headers=h
    ).json()
    assert chrome["count"] == 3
    by = {e["item"]["source_id"]: e for e in chrome["entries"]}
    assert by["b"]["status"] == "full"  # newer check wins
    stale = client.post("/sync/merge", json={"entries": [entry("b", "missing", checked=2)]}, headers=h).json()
    assert {e["item"]["source_id"]: e for e in stale["entries"]}["b"]["status"] == "full"


def test_unknown_or_malformed_codes_are_rejected(client):
    assert (
        client.post(
            "/sync/merge", json={"entries": []}, headers={"X-Griot-Sync": sync.new_code()}
        ).status_code
        == 404
    )
    assert (
        client.post("/sync/merge", json={"entries": []}, headers={"X-Griot-Sync": "nope"}).status_code == 422
    )
    assert client.post("/sync/merge", json={"entries": []}).status_code == 401
