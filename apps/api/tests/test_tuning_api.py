from fastapi.testclient import TestClient

from griot_api.main import create_app
from griot_api.repo import MemoryRepo
from griot_api.service import BridgeService
from griot_core.synthetic import make_tracks

AUTH = {"Authorization": "Bearer test-token"}


def test_ratings_tune_the_scoring_end_to_end():
    tracks = make_tracks(n=600, seed=31)
    c = TestClient(create_app(BridgeService(MemoryRepo(tracks))))
    assert c.get("/tuning/status").json()["ratings"] == 0

    # A listener who judges transitions almost entirely by mood
    ids = [t.id for t in tracks]
    for i in range(45):
        body = {
            "waypoints": [ids[i], ids[-1 - i]],
            "length": 5,
            "filters": {"min_playcount": None, "max_per_artist": 3},
        }
        b = c.post("/bridges", json=body).json()
        moods = [t["terms"]["mood"] / b["weights"]["mood"] for t in b["transitions"]]
        cut = sorted(moods)[len(moods) // 2]
        for pos, m in enumerate(moods):
            c.post(f"/bridges/{b['id']}/feedback", json={"position": pos, "rating": 1 if m < cut else -1})

    st = c.get("/tuning/status").json()
    assert st["ratings"] >= 100 and st["ready"]
    assert c.post("/tuning/run").status_code == 401  # needs the private token
    rep = c.post("/tuning/run", headers=AUTH).json()
    assert rep["apply"] and rep["auc_tuned"] > rep["auc_current"]
    assert rep["tuned"]["mood"] > rep["current"]["mood"]
    b = c.post("/bridges", json={"waypoints": [ids[0], ids[1]], "length": 3,
                                 "filters": {"min_playcount": None, "max_per_artist": 3}}).json()  # fmt: skip
    assert b["weights"]["mood"] == rep["tuned"]["mood"]  # new bridges use the tuned scoring
    assert c.get("/tuning/status").json()["last"]["n"] == rep["n"]
