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


def test_performance_report_pnm4():
    msg = {"label": "H1", "flight": "UA1767", "kind": "acars",
           "text": "#DFBPNM4G941N77261093013343349128017670KRAPKDENCRS 874670875908LBTL2411-00001100 28989 28988"
                   "    85    854335++++++ 71 -31.5  -5.810.728    93    9329---00001100 28985 28982    85    854436"
                   "++++++ 71 -31.5  -5.810.732    93    9334---00001100"}
    r = translate.translate(msg)
    assert r["summary"] == "Performance monitoring (PNM) report, cruise (2 samples), 28,989 ft → 28,985 ft"
    assert r["details"][0] == "Flight KRAP Rapid City → KDEN Denver"
    assert "Outside air -31.5°C (total -5.8°C)" in r["details"]


def test_phase_word_is_not_an_airport():
    msg = {"label": "H1", "flight": "UA0000", "kind": "acars",
           "text": "CAB3WB09N76517091415454022 88319150KDENCLMB7000CLMB-------///////5\r\n411-4340001100  5332  "}
    assert not any("CLMB" in (d or "") for d in translate.translate(msg)["details"])


def test_delta_apm_cruise_report():
    # DL1129, 30 September; ADS-B had 41.29 -103.20, 29,012 ft, 452.5 kt, track 48.
    msg = {"label": "H1", "sublabel": "DF", "flight": "DL1129", "kind": "acars",
           "text": "061A8115\r\nAPM    1N804DN          DA11299 KDENKMSP300926122910,,,.720,,438.4,27\r\n7.4,,, -4.50,,,"
                   "28981,,,138280,,,2829,2837,,, 84.56, 84.63,,, 0.6,453.\r\n7,453.7,,0,0,,0,0,,1,1,0,0,0,0,,, 41.2554,,,"
                   "-103.2571,,,  47.1,,,  0.\r\n8,,,  48.0,,,  0.000,-0.027,,,,,,17880, 8400, 8430,  780,,,,,+++++,++\r\n"
                   "+++,,,--.-,,,,,140280,,,,,,     0,,,20880, 8370, 8360, 4130,,,,,  962\r\n455,  660369,,,,,,  39.930,"
                   "  40.737,,, 93.2, 93.2,,,  17,  17,,,627.0\r\n,621.0,,,,,,,-29.8,,, -1.4,,, -0.4,,,  2.9,  2.9,,"}
    r = translate.translate(msg)
    assert r["summary"] == "Cruise performance report, 28,981 ft, Mach 0.720"
    assert "Ground speed 454 kt, track 47°" in r["details"]
    assert "Outside air -29.8°C (total -4.5°C)" in r["details"]
    assert r["position"]["lat"] == 41.2554


def test_flight_computer_messages_with_inline_sublabel():
    def read(text):
        return translate.translate({"label": "H1", "flight": "UA0001", "kind": "acars", "text": text})
    ack = read("#M1B/HDQDLUA.RESPWI/AK,071/GAHDQDLUA/TS124749,3009261EFA")
    assert ack["summary"] == "Flight computer accepted the predicted winds sent from the ground"
    pos = read("#M1B/.POS/TS065719,300926N41040W105051,,065719,410,CYS,065819,BFF,M56,24063,367,850F974")
    assert pos["summary"] == "Position report: over 41.07°N 105.08°W at 06:57:19 UTC, FL410"
    assert "Wind 240° at 63 kt" in pos["details"]


def test_airbus_header_with_c1_tail():
    msg = {"label": "H1", "flight": "UA2270", "kind": "acars",
           "text": "#DFBA37/A32037,1,1/C1N487UA,SEP30,151936,KORF,KDEN,2270/C28100,I23014,06,010822,011215/"}
    r = translate.translate(msg)
    assert r["summary"] == "Aircraft monitoring report A37"
    assert r["details"] == ["Flight KORF Norfolk → KDEN Denver", "Generated 15:19:36 UTC"]
