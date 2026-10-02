from griot_pipelines.lyrics import edges, parse_lyrics


def test_parse_synced_lyrics_drops_markers_and_blanks():
    synced = (
        "[00:13.13] Yeah\n[00:16.56] ♪\n[00:27.16] I've been tryna call\n[00:30.00] \n[03:10.50] Last line"
    )
    lines = parse_lyrics(synced, None)
    assert lines == [(13.13, "Yeah"), (27.16, "I've been tryna call"), (190.5, "Last line")]


def test_plain_fallback_and_edges():
    lines = parse_lyrics(None, "[Verse 1]\na\nb\n\nc\nd\ne\nf")
    opening, closing, full = edges(lines, n=2)
    assert opening == "a / b" and closing == "e / f" and full.count("\n") == 5
