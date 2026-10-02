import numpy as np

from griot_analyzer.extract import EDGE_S, _span, pool_embeddings


def segs(*spec):
    return [{"start": a, "end": b, "label": lab} for a, b, lab in spec]


def test_span_uses_labelled_intro_and_outro():
    s = segs(
        (0, 1, "start"),
        (1, 17, "intro"),
        (17, 60, "verse"),
        (60, 160, "chorus"),
        (160, 190, "outro"),
        (190, 191, "end"),
    )
    assert _span(s, {"intro"}, True, 191) == (1, 17)
    assert _span(s, {"outro"}, False, 191) == (160, 190)


def test_span_falls_back_when_missing_or_implausible():
    s = segs((0, 2, "intro"), (2, 200, "verse"))  # 2 s intro is too short to trust
    assert _span(s, {"intro"}, True, 200) == (0.0, EDGE_S)
    assert _span(s, {"outro"}, False, 200) == (200 - EDGE_S, 200)


def test_pool_embeddings_separates_intro_and_outro():
    centres = np.arange(5, 200, 10.0)
    embs = np.zeros((len(centres), 4))
    embs[:, 0] = centres < 30  # intro region points along axis 0
    embs[:, 1] = centres > 170  # outro region along axis 1
    embs[:, 2] = 0.1
    p = pool_embeddings(centres, embs, segs((0, 30, "intro"), (30, 170, "verse"), (170, 200, "outro")), 200)
    assert np.argmax(p["intro"]) == 0 and np.argmax(p["outro"]) == 1
    assert p["chorus"] is None
    assert np.isclose(np.linalg.norm(p["full"]), 1)
