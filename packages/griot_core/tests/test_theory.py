import pytest

from griot_core.theory import camelot, camelot_penalty, camelot_steps, tempo_penalty


@pytest.mark.parametrize(
    ("key", "mode", "code"),
    [
        ("C", "major", "8B"),
        ("A", "minor", "8A"),
        ("G", "major", "9B"),
        ("E", "minor", "9A"),
        ("B", "major", "1B"),
        ("G#", "minor", "1A"),
        ("Ab", "minor", "1A"),
        ("F", "major", "7B"),
        ("D", "minor", "7A"),
        ("Db", "major", "3B"),
        ("Bb", "minor", "3A"),
    ],
)
def test_camelot_codes(key, mode, code):
    assert camelot(key, mode) == code


def test_camelot_steps_wraps_and_counts_letter():
    assert camelot_steps("8A", "8A") == 0
    assert camelot_steps("12B", "1B") == 1  # wraps around the wheel
    assert camelot_steps("8A", "8B") == 1  # relative major/minor
    assert camelot_steps("8A", "9B") == 2
    assert camelot_steps("1A", "7A") == 6


def test_camelot_penalty_ordering_and_strength():
    assert camelot_penalty("8A", "8A") == 0
    assert camelot_penalty("8A", "9A") < camelot_penalty("8A", "10A") < camelot_penalty("8A", "2A")
    assert camelot_penalty("8A", "2A", strength=0.0) == 0


def test_tempo_penalty_half_double_time():
    assert tempo_penalty(120, 120) == 0
    assert tempo_penalty(120, 124) < 0.3  # within ±6%
    assert tempo_penalty(140, 70) == pytest.approx(0.1)  # double time costs only the small extra
    assert tempo_penalty(90, 140) > 0.9  # far apart even after considering half-time (90 vs 70)
    assert tempo_penalty(120, 128) < tempo_penalty(120, 135)
