"""Cross-device library sync with a private code (no accounts yet).

The code is a random ~100-bit secret shown once to the user; the server stores only its
sha256, so a database leak doesn't expose libraries. Syncing is a per-entry merge: each
device sends its library and receives the union, keeping the most recently checked copy
of any song both sides have.
"""

from __future__ import annotations

import hashlib
import secrets

_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32: no I, L, O, U
MAX_ENTRIES = 50_000


def new_code() -> str:
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(20))  # 100 bits
    return "-".join(raw[i : i + 4] for i in range(0, 20, 4))


def normalize_code(code: str) -> str:
    c = code.upper().replace("-", "").replace(" ", "")
    c = c.translate(str.maketrans({"O": "0", "I": "1", "L": "1"}))
    if len(c) != 20 or any(ch not in _ALPHABET for ch in c):
        raise ValueError("that doesn't look like a Griot sync code")
    return "-".join(c[i : i + 4] for i in range(0, 20, 4))


def library_id(code: str) -> str:
    return hashlib.sha256(normalize_code(code).encode()).hexdigest()


def entry_key(e: dict) -> str:
    """Must match itemKey() in apps/web/src/lib/library.ts."""
    item = e.get("item") or {}
    sid = item.get("source_id")
    if sid is None:
        artist = item.get("artist")
        sid = f"{'null' if artist is None else artist}|{item.get('title')}"
    return f"{item.get('source')}:{sid}"


def _stamp(e: dict) -> float:
    return float(e.get("checked") or e.get("added") or 0)


def merge(existing: dict[str, dict], incoming: list[dict]) -> dict[str, dict]:
    out = dict(existing)
    for e in incoming:
        k = entry_key(e)
        if k not in out or _stamp(e) >= _stamp(out[k]):
            out[k] = e
    return out
