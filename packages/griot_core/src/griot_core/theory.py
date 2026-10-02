"""Music-theory helpers: Camelot wheel and tempo compatibility."""

from __future__ import annotations

import math

PITCH_CLASSES = ["C", "C#", "D", "Eb", "E", "F", "F#", "G", "Ab", "A", "Bb", "B"]
_ALIASES = {"Db": "C#", "D#": "Eb", "Gb": "F#", "G#": "Ab", "A#": "Bb"}

# Penalty per Camelot "step" (wheel distance + letter change), normalized to [0, 1].
_STEP_PENALTY = {0: 0.0, 1: 0.15, 2: 0.45, 3: 0.7}


def pitch_class(name: str) -> int:
    return PITCH_CLASSES.index(_ALIASES.get(name, name))


def camelot(key: str, mode: str) -> str:
    """C major -> 8B, A minor -> 8A. Minor keys share a number with their relative major."""
    pc = pitch_class(key)
    if mode == "minor":
        pc = (pc + 3) % 12
    number = (pc * 7 + 7) % 12 + 1
    return f"{number}{'B' if mode == 'major' else 'A'}"


def parse_camelot(code: str) -> tuple[int, int]:
    """'8A' -> (8, 0); '12B' -> (12, 1)."""
    return int(code[:-1]), 0 if code[-1].upper() == "A" else 1


def camelot_steps(a: str, b: str) -> int:
    (na, la), (nb, lb) = parse_camelot(a), parse_camelot(b)
    d = abs(na - nb)
    return min(d, 12 - d) + (la != lb)


def camelot_penalty(a: str, b: str, strength: float = 1.0) -> float:
    """0 for the same key, small for adjacent/relative keys, 1 for clashing keys.

    `strength` (0..1, min of both tracks' key confidence) softens the penalty for
    tracks without a clear tonal centre (drums-only, atonal, heavy noise).
    """
    p = _STEP_PENALTY.get(camelot_steps(a, b), 1.0)
    return p * max(0.0, min(1.0, strength))


TEMPO_SAFE = math.log(1.06)  # ±6% is pitch-shift-free beatmatching territory
TEMPO_MAX = math.log(1.35)  # beyond this the jump is fully penalized
HALF_DOUBLE_EXTRA = 0.1


def tempo_penalty(bpm_a: float, bpm_b: float) -> float:
    """Log-ratio tempo distance, tolerant of half/double time, normalized to [0, 1]."""
    best = math.inf
    for factor, extra in ((1.0, 0.0), (2.0, HALF_DOUBLE_EXTRA), (0.5, HALF_DOUBLE_EXTRA)):
        x = abs(math.log(bpm_a / (bpm_b * factor)))
        if x <= TEMPO_SAFE:
            p = 0.3 * x / TEMPO_SAFE
        else:
            p = 0.3 + 0.7 * min(1.0, (x - TEMPO_SAFE) / (TEMPO_MAX - TEMPO_SAFE))
        best = min(best, min(1.0, p + extra))
    return best
