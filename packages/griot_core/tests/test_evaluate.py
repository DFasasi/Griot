from griot_core.evaluate import run, table
from griot_core.synthetic import make_tracks


def test_eval_runs_all_methods_and_griot_wins_on_seams():
    res = run(make_tracks(n=600, seed=2), pairs=6, n=5)
    assert set(res) == {
        "griot",
        "griot (preview-only)",
        "straight line (emb)",
        "artist graph (BtF)",
        "random",
    }
    mean = {k: sum(m.judge_cost for m in v) / len(v) for k, v in res.items()}
    assert mean["griot"] < mean["random"] and mean["griot"] < mean["straight line (emb)"]
    assert "| griot |" in table(res)
