import pytest

from griot_pipelines.resolve import parse_youtube_title
from griot_pipelines.sources import norm
from griot_pipelines.youtube import iso_seconds, parse_link, playlist_id, split_links


@pytest.mark.parametrize(
    ("title", "channel", "expected"),
    [
        ("The Weeknd - Blinding Lights (Official Video)", "TheWeekndVEVO", ("The Weeknd", "Blinding Lights")),
        ("Burna Boy - Last Last [Official Music Video]", "Burna Boy", ("Burna Boy", "Last Last")),
        ("Blinding Lights", "The Weeknd - Topic", ("The Weeknd", "Blinding Lights")),
        ("Tems - Free Mind | Audio", "Tems", ("Tems", "Free Mind")),
    ],
)
def test_parse_youtube_title(title, channel, expected):
    assert parse_youtube_title(title, channel) == expected


def test_norm_strips_editions_and_features():
    assert norm("Here Comes the Sun - Remastered 2009") == "here comes the sun"
    assert norm("Blinding Lights (feat. Someone)") == "blinding lights"


def test_youtube_helpers():
    assert playlist_id("https://www.youtube.com/playlist?list=PLabc123XYZ_-") == "PLabc123XYZ_-"
    assert iso_seconds("PT3M20S") == 200 and iso_seconds("PT1H2S") == 3602


@pytest.mark.parametrize(
    ("link", "expected"),
    [
        ("https://www.youtube.com/playlist?list=PLabcdefghijk12", ("playlist", "PLabcdefghijk12")),
        ("https://music.youtube.com/playlist?list=OLAK5uy_abcdefghijk", ("playlist", "OLAK5uy_abcdefghijk")),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PLabcdefghijk12", ("playlist", "PLabcdefghijk12")),
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),  # mix
        ("https://youtu.be/dQw4w9WgXcQ?si=abc", ("video", "dQw4w9WgXcQ")),
        ("https://www.youtube.com/shorts/dQw4w9WgXcQ", ("video", "dQw4w9WgXcQ")),
        ("https://music.youtube.com/watch?v=dQw4w9WgXcQ&feature=share", ("video", "dQw4w9WgXcQ")),
    ],
)
def test_parse_link(link, expected):
    assert parse_link(link) == expected


def test_parse_link_rejects_non_youtube():
    with pytest.raises(ValueError):
        parse_link("https://open.spotify.com/track/abc")


def test_split_links_handles_newlines_commas_spaces():
    assert split_links("a\nb, c  d\n\n") == ["a", "b", "c", "d"]


def test_fetch_links_reports_each_link(monkeypatch):
    from griot_core.schema import ImportItem
    from griot_pipelines import youtube

    def fake_playlist(pid, key=None):
        return [ImportItem(source="youtube", source_id=v, title="A - B") for v in ("v1", "v2")]

    def fake_videos(ids, key=None):
        return [ImportItem(source="youtube", source_id=i, title="X - Y") for i in ids if i != "missingvid0"]

    monkeypatch.setattr(youtube, "fetch_playlist", fake_playlist)
    monkeypatch.setattr(youtube, "fetch_videos", fake_videos)
    links = [
        "https://youtube.com/playlist?list=PLabcdefghijk12",
        "https://youtu.be/dQw4w9WgXcQ",
        "https://youtu.be/missingvid0",
        "not a link",
    ]
    items, report = youtube.fetch_links(links)
    assert len(items) == 3
    summary = [(r["kind"], r["count"], bool(r["error"])) for r in report]
    assert summary == [("playlist", 2, False), ("video", 1, False), ("video", 0, True), (None, 0, True)]


@pytest.mark.parametrize(
    ("title", "channel", "expected"),
    [
        ("BTS (방탄소년단) ‘SWIM’ Official MV", "HYBE LABELS", ("BTS", "SWIM")),
        (
            "정국 (Jung Kook) '3D (feat. Jack Harlow)' Official MV",
            "HYBE LABELS",
            ("Jung Kook", "3D (feat. Jack Harlow)"),
        ),
        (
            "Tyla, Travis Scott - Water (Remix - Official Music Video)",
            "Tyla",
            ("Tyla, Travis Scott", "Water (Remix)"),
        ),
        (
            "Song Title (Acoustic) [Official Video]",
            "Some Artist - Topic",
            ("Some Artist", "Song Title (Acoustic)"),
        ),
    ],
)
def test_parse_youtube_title_harder_formats(title, channel, expected):
    assert parse_youtube_title(title, channel) == expected
