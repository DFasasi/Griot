import pytest

from griot_pipelines.resolve import parse_youtube_title
from griot_pipelines.sources import norm
from griot_pipelines.youtube import iso_seconds, playlist_id


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
