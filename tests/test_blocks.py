"""Joining multi-block reports, as the server does live and the archive sweep does on replay."""
import blocks
import translate


def msg(letter, text, ts):
    m = {"source": "VDL2", "reg": "N12221", "flight": "UA1010", "icao": None, "label": "H1",
         "msgno": f"D17{letter}", "text": text, "kind": "acars", "ts": ts}
    m["english"] = translate.translate(dict(m, english=None))
    return m


def test_fragment_joins_the_report_it_follows():
    head = msg("A", "#DFBHYDN12221300926KPSPKDEN10101200100000000000003WB0900  -8.0 39.78 3\r\n9.84 76.69 76.72"
                    " -33.5  300310.731142800 56369 73.0 83.830603036   0.\r\n000   0.000:::AIRFALSE  -7.5 43.50"
                    " 42.16 78.91 78.50 -33.3  298520.", 0)
    tail = msg("B", "731142800 56374 73.0 83.830643036   0.000   0.000:::AIRFALSE", 30)
    assert tail["english"].get("generic")
    assembler = blocks.BlockAssembler()
    assembler.observe(head)
    changed = assembler.observe(tail)
    assert changed == [head] and tail["kind"] == "partial"
    assert head["english"]["summary"] == "Hydraulic monitoring report (2 samples), 30,031 ft → 29,852 ft"
    assert head["english"]["details"][-1] == "Assembled from 2 blocks"


def test_airbus_report_with_inline_sublabel():
    m = {"label": "H1", "flight": "UA1442", "kind": "acars",
         "text": "#DFBA319,132637,1,1,TB000000/REP998,00,00,4/-  A37/A31937,1,1/\r\nC1N872UA,SEP30,120145,KOMA,KDEN,1442/"}
    r = translate.translate(m)
    assert r["summary"].startswith("Airbus aircraft monitoring report 998") and not r.get("generic")


def test_continuation_header_is_dropped_when_joining():
    # ARINC 620 5.4.2: later blocks open with "#", the sublabel and "B", then carry on mid-text.
    head = msg("A", "#DFBHYDN12221300926KPSPKDEN10101200100000000000003WB0900  -8.0 39.78 3\r\n9.84 76.69 76.72"
                    " -33.5  300310.731142800 56369 73.0 83.830603036   0.\r\n000   0.000:::AIRFALSE  -7.5 43.50"
                    " 42.16 78.91 78.50 -33.3  298520.", 0)
    tail = msg("B", "#DFB731142800 56374 73.0 83.830643036   0.000   0.000:::AIRFALSE", 30)
    assembler = blocks.BlockAssembler()
    assembler.observe(head)
    assembler.observe(tail)
    assert "#DFB" not in head["text"][4:]
    assert head["english"]["summary"] == "Hydraulic monitoring report (2 samples), 30,031 ft → 29,852 ft"


def test_decoder_joined_text_loses_inner_headers():
    m = {"label": "H1", "flight": "UA1010", "kind": "acars",
         "text": "#DFBHYDN12221300926KPSPKDEN10101200100000000000003WB0900  -8.0 39.78 39.84 76.69 76.72 -33.5"
                 "  300310.731142800 56369 73.0 83.830603036   0.000   0.000:::AIRFALSE  -7.5 43.50 42.16 78.91"
                 " 78.50 -33.3  298520.#DFB731142800 56374 73.0 83.830643036   0.000   0.000:::AIRFALSE"}
    assert translate.translate(m)["summary"].startswith("Hydraulic monitoring report (2 samples)")
