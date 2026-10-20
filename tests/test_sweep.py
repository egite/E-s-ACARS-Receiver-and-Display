"""The known-plaintext scorer in tools/sweep_unread.py, on synthetic messages."""
import random
import string
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
import sweep_unread  # noqa: E402


def rows(cipher):
    random.seed(0)
    out = []
    for _ in range(200):
        code = "K" + "".join(random.choice(string.ascii_uppercase) for _ in range(3))
        noise = "".join(random.choice(string.ascii_uppercase) for _ in range(6))
        out.append(("XY" + code.translate(cipher) + noise, "01", {"origin": code}))
    return out


def test_finds_a_substituted_field_and_not_its_neighbours():
    shift = str.maketrans(string.ascii_uppercase, string.ascii_uppercase[3:] + string.ascii_uppercase[:3])
    _, clear, cipher = sweep_unread.crib_score(rows(shift), "origin", 2)
    assert clear == 0 and cipher == 1.0
    _, _, off = sweep_unread.crib_score(rows(shift), "origin", 5)
    assert off < 0.6


def test_finds_a_clear_field():
    n, clear, _ = sweep_unread.crib_score(rows(str.maketrans("", "")), "origin", 2)
    assert n == 200 and clear == 1.0
