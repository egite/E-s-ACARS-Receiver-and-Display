"""Clear-text formats read from structure alone, checked on real examples."""
import translate


def test_connection_request_flight_number():
    msg = {"label": "48", "flight": "NW0470", "kind": "acars",
           "text": "301848 KLAS KDTW7\r\n/FN 0470\r\n1274                    N"}
    r = translate.translate(msg)
    assert r["category"] == "request"
    assert r["summary"] == "Connection request: flight 1274"


def test_connection_request_cities_and_blank():
    two = {"label": "48", "flight": "DL0361", "kind": "acars",
           "text": "062048 KSFO KDTW6\r\n0SLC0RDU                N"}
    blank = {"label": "48", "flight": "DL2089", "kind": "acars",
             "text": "300448 KMSP KSFO6\r\n                        N"}
    assert translate.translate(two)["summary"].startswith("Connection request: Salt Lake City, ")
    assert translate.translate(blank)["summary"] == "Connection request (blank)"


def test_hydraulic_report():
    msg = {"label": "H1", "flight": "UA1010", "kind": "acars",
           "text": "#DFBHYDN12221300926KPSPKDEN10101200100000000000003WB0900  -8.0 39.78 3\r\n9.84 76.69 76.72 -33.5"
                   "  300310.731142800 56369 73.0 83.830603036   0.\r\n000   0.000:::AIRFALSE  -7.5 43.50 42.16 78.91 78.50"
                   " -33.3  298520.#DFB73\r\n1142800 56374 73.0 83.830643036   0.000   0.000:::AIRFALSE"}
    r = translate.translate(msg)
    assert r["summary"] == "Hydraulic monitoring report (2 samples), 30,031 ft → 29,852 ft"
    assert "Hydraulic pressure A 3,060 psi, B 3,036 psi" in r["details"]
    assert "Outside air -33.5°C (total -8.0°C)" in r["details"]


def test_hydraulic_report_fields_run_together():
    # N1 at 100% leaves no space before it.
    msg = {"label": "H1", "flight": "UA0476", "kind": "acars",
           "text": "#DFBHYDN76265300926KDENKABQ04761100 60000000000003WB0900  -6.8100.25100.25 98.66 98.44 -32.5"
                   "  299920.734144200 72132 78.0 91.330683068   0.000   0.000///AIRFALSE"}
    r = translate.translate(msg)
    assert r["summary"] == "Hydraulic monitoring report (1 sample), 29,992 ft"
