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


def test_transient_disconnects_are_retried_then_succeed(monkeypatch):
    import httpx

    from griot_pipelines import sources

    monkeypatch.setattr(sources.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.RemoteProtocolError("Server disconnected without sending a response.")
        return httpx.Response(200, json={"id": 1, "title": "ok"})

    dz = sources.Deezer(client=httpx.Client(transport=httpx.MockTransport(handler)))
    dz.limit.wait = lambda: None
    assert dz._get("/track/1") == {"id": 1, "title": "ok"} and calls["n"] == 3


def test_resolver_marks_song_unmatched_when_catalog_is_down_and_does_not_cache():
    from griot_core.schema import ImportItem
    from griot_pipelines.resolve import Resolver
    from griot_pipelines.sources import SourceUnavailable

    class Down:
        def by_isrc(self, isrc):
            raise SourceUnavailable("down")

        def search(self, *a):
            raise SourceUnavailable("down")

    r = Resolver(lookup=lambda **k: None, deezer=Down())
    res = r.resolve(ImportItem(source="spotify", title="Water", artist="Tyla"))
    assert res.status == "unmatched" and r._cache == {}
