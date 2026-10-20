"""The obfuscated route fields: Southwest label 37 and business-aviation label 44.

The tables are checked for what a monoalphabetic substitution forbids, and the Southwest
routes against the same flight's own clear-text H1 reports, which is how they were read in
the first place. Runs on tests/corpus.jsonl, since the archive exists only on the receiver."""
import re
import string

import pytest

import translate

FAMILIES = [
    ("southwest", translate.SOUTHWEST_KEYS, translate.SOUTHWEST_LETTERS),
    ("bizjet", translate.BIZJET_KEYS, translate.BIZJET_LETTERS),
]


def route(msg):
    english = translate.translate(dict(msg, english=None))
    found = [d for d in english.get("details") or [] if d and d.startswith("Flight ")]
    return re.findall(r"\b([A-Z]{4})\b", found[0]) if found else None


@pytest.mark.parametrize("name,keys,letters", FAMILIES, ids=[f[0] for f in FAMILIES])
def test_letter_tables_are_substitutions(name, keys, letters):
    assert set(letters) == set(keys)
    for group, (cipher, plain) in letters.items():
        sep, digits = keys[group]
        assert len(cipher) == len(plain), group
        assert len(set(cipher)) == len(cipher), f"{group}: a cipher character stands for two letters"
        assert len(set(plain)) == len(plain), f"{group}: a letter has two cipher characters"
        assert set(plain) <= set(string.ascii_uppercase), group
        assert not set(cipher) & (set(digits) | {sep}), f"{group}: letter alphabet overlaps the digits"


def test_southwest_alphabets_are_complete():
    for group, (_, plain) in translate.SOUTHWEST_LETTERS.items():
        assert sorted(plain) == list(string.ascii_uppercase), group


def test_southwest_route_known_message():
    # A short-variant report from the archive (WN3406, 30 September), San Diego to Baltimore.
    msg = {"label": "37", "flight": "WN3406", "kind": "acars",
           "text": "01CGA3P\r\n)6A0Z)JH(ZN3N7ZOOMUZ0IP,MSOUZHN31MO77"}
    assert route(msg) == ["KSAN", "KBWI"]


def test_southwest_routes_match_clear_h1(corpus):
    """Every label 37 route agrees with the H1 route the same flight sent within half an hour."""
    clear = {}
    for m in corpus:
        if m.get("label") == "H1" and (m.get("flight") or "").startswith("WN") and m["text"].startswith("POS"):
            x = re.search(r",([A-Z]{4}),([A-Z]{4}),", m["text"])
            if x:
                clear.setdefault((m.get("icao"), m["flight"]), []).append((m["ts"], list(x.groups())))
    checked = 0
    for m in corpus:
        if m.get("label") != "37" or not (got := route(m)):
            continue
        near = [r for ts, r in clear.get((m.get("icao"), m["flight"]), []) if abs(ts - m["ts"]) < 1800]
        if near:
            assert got in near, m["text"]
            checked += 1
    assert checked >= 10


def test_southwest_positions_carry_routes(corpus):
    placed = [m for m in corpus if m.get("label") == "37"
              and translate.translate(dict(m, english=None)).get("position")]
    with_route = [m for m in placed if route(m)]
    assert placed and len(with_route) / len(placed) > 0.95


def test_corpus_translates(corpus):
    for m in corpus:
        translate.translate(dict(m, english=None))


def test_bizjet_punctuation_is_free_of_the_other_tables():
    for group, (cipher, plain) in translate.BIZJET_PUNCT.items():
        sep, digits = translate.BIZJET_KEYS[group]
        assert set(plain) <= set(" .-") and len(set(cipher)) == len(cipher), group
        assert not set(cipher) & (set(digits) | {sep} | set(translate.BIZJET_LETTERS[group][0])), group


def test_bizjet_poa_position_report():
    # ZD0321, 3 October 17:26 UTC; ADS-B had it at 39.11, -102.30, 43,012 ft.
    msg = {"label": "17", "flight": "ZD0321", "kind": "acars",
           "text": "02N;|,3<t,0-3&,0,03]-9oXvsolrp&r0)$,9TQ&/3,-$3AT0&-])&$Q9&m0,&7$Q)&|4e;1N&3,"}
    r = translate.translate(msg)
    assert r["summary"] == "Position report at 17:26 UTC, FL430"
    assert r["details"] == ["Flight KELM Elmira/Corning → KVNY Van Nuys", "39.11°N 102.31°W",
                            "Wind 279° at 56 kt", "Outside air -59°C"]


def test_bizjet_space_is_not_q():
    # Group 03 once had Q where the space is.
    msg = {"label": "41", "flight": "ZD998N", "kind": "acars",
           "text": "038>ox(qLJVDxPftx-DJxq--Jq.SL)q.88Xq((t9q2TqLVV9LIq2TqT0fL+m!wH+W>XOT+>q&0S+dWd0H.wfw)"}
    assert "TOWNEPLACE SUITES DOVER ROCKAWAY" in str(translate.translate(msg)["details"])


def test_bizjet_software_report():
    msg = {"label": "48", "flight": "XA0424", "kind": "acars",
           "text": "08u,qLeZq}tve`~YLvLvYZLKKZz0 WZzufRZWx\\*eaLL*Y}LvLKZWx\\*:mq*fy|*LetZatLL}e}a*vLeBBBZ,Ryv*jjjjj"}
    r = translate.translate(msg)
    assert r["summary"] == "Avionics software report, KBJC Denver → KSHV Shreveport"
