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
