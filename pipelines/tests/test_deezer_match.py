from griot_pipelines.sources import Deezer


def deezer_with(results):
    dz = Deezer.__new__(Deezer)  # no network client needed

    def fake_get(path, **params):
        if path == "/search":
            return {"data": results}
        if path == "/search/artist":
            return {"data": []}  # no artist fallback in these cases
        return {"id": int(path.rsplit("/", 1)[1]), "picked": True}

    dz._get = fake_get
    return dz


def hit(i, title, artist, duration=200, version=""):
    return {
        "id": i,
        "title": title,
        "title_version": version,
        "artist": {"name": artist},
        "duration": duration,
    }


def test_prefers_original_over_remix_listed_first():
    dz = deezer_with(
        [hit(1, "Nonsense (Remix)", "Sabrina Carpenter"), hit(2, "Nonsense", "Sabrina Carpenter")]
    )
    assert dz.search("Sabrina Carpenter", "Nonsense")["id"] == 2


def test_remix_still_chosen_when_asked_for():
    dz = deezer_with(
        [hit(1, "Bloody Mary", "Lady Gaga"), hit(2, "Bloody Mary (The Horrors Remix)", "Lady Gaga")]
    )
    assert dz.search("Lady Gaga", "Bloody Mary (The Horrors Remix)")["id"] == 2


def test_version_in_title_version_field_counts():
    dz = deezer_with([hit(1, "Water", "Tyla", version="(Sped Up)"), hit(2, "Water", "Tyla")])
    assert dz.search("Tyla", "Water")["id"] == 2


def test_only_remix_available_is_rejected_rather_than_silently_wrong():
    dz = deezer_with([hit(1, "Nonsense (Remix)", "Sabrina Carpenter")])
    assert dz.search("Sabrina Carpenter", "Nonsense") is None


def test_falls_back_to_artist_top_tracks_when_search_is_all_edits():
    dz = Deezer.__new__(Deezer)
    calls = []

    def fake_get(path, **params):
        calls.append(path)
        if path == "/search":
            return {
                "data": [
                    hit(1, "Bloody Mary (Sped Up)", "sped up viral"),
                    hit(2, "Bloody Mary (Slowed)", "x"),
                ]
            }
        if path == "/search/artist":
            return {"data": [{"id": 75491, "name": "Lady Gaga"}]}
        if path == "/artist/75491/top":
            return {"data": [hit(7, "Poker Face", "Lady Gaga"), hit(8, "Bloody Mary", "Lady Gaga", 244)]}
        return {"id": int(path.rsplit("/", 1)[1])}

    dz._get = fake_get
    assert dz.search("Lady Gaga", "Bloody Mary")["id"] == 8
    assert "/artist/75491/top" in calls
