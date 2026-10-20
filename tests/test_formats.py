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
    # AK is an acknowledgement (received), not AC (accepted): ARINC 702A.
    assert ack["summary"] == "Flight computer acknowledged the predicted winds sent from the ground"
    pos = read("#M1B/.POS/TS065719,300926N41040W105051,,065719,410,CYS,065819,BFF,M56,24063,367,850F974")
    assert pos["summary"] == "Position report: over 41.07°N 105.08°W at 06:57:19 UTC, FL410"
    assert "Wind 240° at 63 kt" in pos["details"]


def test_airbus_header_with_c1_tail():
    msg = {"label": "H1", "flight": "UA2270", "kind": "acars",
           "text": "#DFBA37/A32037,1,1/C1N487UA,SEP30,151936,KORF,KDEN,2270/C28100,I23014,06,010822,011215/"}
    r = translate.translate(msg)
    assert r["summary"] == "Aircraft monitoring report A37"
    assert r["details"] == ["Flight KORF Norfolk → KDEN Denver", "Generated 15:19:36 UTC"]


def test_united_a86_report():
    msg = {"label": "H1", "sublabel": "DF", "flight": "UA2736", "kind": "acars",
           "text": "A86BBSM25-H200-0003\r\nN37547 3009133516092341\r\n27365ERKGPIKDEN60A89660B311\r\n60B-      \r\n"
                   "0811-00001100 27000206206CLSDCLSD 41 41 39 381  -260.090.083643550.694 79 79\r\n"
                   "1311-00001100 27002207207CLSDCLSD 41 41 39 391  -260.090.083573500.693 79 79\r\n"}
    r = translate.translate(msg)
    assert r["summary"] == "Aircraft monitoring report A86, en route (2 samples), 27,000 ft → 27,002 ft"
    assert r["details"] == ["Flight KGPI Kalispell → KDEN Denver", "N37547", "Mach 0.694", "Outside air -26°C"]


def test_takeoff_reports():
    def read(text):
        return translate.translate({"label": "H1", "flight": "UA0001", "kind": "acars", "text": text})
    r = read("#DFBTKON67827KDENKLAS30091356 687 1900 140  5935030114440023161TOFF4G3\r\n900660782658971 951")
    assert r["summary"] == "Takeoff engine report, KDEN Denver → KLAS Las Vegas"
    assert "Took off 13:56 UTC" in r["details"]
    r = read("#DFBTKO6134972149KDENKSMF093026142204\r\n114190005338014501503<<<<00125<\r\n1407086905200920")
    assert "Took off 14:22:04 UTC" in r["details"]


def test_delta_fd_report():
    msg = {"label": "H1", "sublabel": "DF", "flight": "DL2834", "kind": "acars",
           "text": "FD0A8115N873DN30090901 850155328341KLASKMSPCRS \r\n2700\r\n34350X11081614070985090113\r\n"
                   "34350X18081614072700090113\r\n"}
    r = translate.translate(msg)
    assert r["summary"] == "Maintenance data report (FD), cruise, 2 codes"
    assert r["details"] == ["Flight KLAS Las Vegas → KMSP Minneapolis", "N873DN",
                            "34350X11 08:16:14–09:01:13 UTC", "34350X18 08:16:14–09:01:13 UTC"]


def test_crzn_cruise_report():
    def read(text):
        return translate.translate({"label": "H1", "flight": "UA2255", "kind": "acars", "text": text})
    r = read("CRZN76529KHDNKDEN30091327 845  625-138 21004062013568022551SCR14G4\r\n100802454658610 791 792 918 918596562"
             " 2861 2860  574  5744849 98 9901\r\n3013012019229137061024023016153148110LO LO 000110027913788N403863N403\r\n"
             "863S1057386S1057386 81  -16137440")
    assert r["summary"] == "Cruise performance report, 21,004 ft, Mach 0.620"
    assert r["position"] == {"lat": 40.3863, "lon": -105.7386, "src": "United CRZN report"}
    assert "Outside air -13.8°C" in r["details"]
    r = read("CRZN343PN1483PHX ORD 3009152527351907 -227-492 37002077115248027651SCR1U A30  38703 38428 ")
    assert r["summary"] == "Cruise performance report, 37,002 ft, Mach 0.771"


def test_report_239_snapshot():
    def read(text):
        return translate.translate({"label": "H1", "flight": "DL1028", "kind": "acars", "text": text})
    boeing = read("239N904DN1028 93026 72212778 4145-10292350-18-46228 61AA-528 23880 264 5040048 835100J8IH-KSLCKBWI")
    assert boeing["position"] == {"lat": 41.45, "lon": -102.92, "src": "Report 239"}
    assert boeing["details"][-4:] == ["41.45°N 102.92°W", "FL350", "Outside air -46°C (total -18°C)",
                                      "Wind 228° at 61 kt"]
    airbus = read("A321,059330,1,1,TB000000/REP239,00,00,4/239N113DX0453093026043217775N38764W105 15340-14-43209 64AF0501")
    assert airbus["position"]["lat"] == 38.764 and airbus["position"]["lon"] == -105.015
    assert "FL340" in airbus["details"]


def test_flight_computer_replies_per_arinc_702a():
    def read(text):
        return translate.translate({"label": "H1", "flight": "UA0001", "kind": "acars", "text": text})
    accepted = read("/HDQDLUA.RESPWI/AC,085/GAHDQDLUA/TS124812,300926055B")
    assert accepted["summary"] == "Flight computer accepted the predicted winds sent from the ground"
    assert accepted["details"] == ["Company wind data accepted"]
    rejected = read("#M1B/HDQDLUA.REJPWI,124749,053,,CB007,CRUISE/GAHDQDLUA/TS124751,300926B9FA")
    assert rejected["details"] == ["Reason: not allowed in this flight phase"]
    request = read("/HDQDLUA.REQPWI/WQ390.340.300.240:CHUWY.ONL.FOD/DQ290/WRKORD.KSTL/GAHDQDLUA/TS173221,300926469B")
    assert request["details"][:3] == ["Winds at FL390, FL340, FL300, FL240", "Along 3 waypoints: CHUWY ONL FOD",
                                      "Descent forecast from FL290"]


def test_position_report_fuel_remaining():
    r = translate.translate({"label": "H1", "flight": "UA0001", "kind": "acars",
                             "text": "POSN38317W103402,LAYLA,054019,388,KD60A,060202,OVR,M50,226075,2112834"})
    assert r["details"][-1] == "Fuel remaining 21,100 lb"


def test_arinc620_fixed_formats():
    def read(label, text):
        return translate.translate({"label": label, "flight": "XX0001", "kind": "acars", "text": text})
    assert read("Q3", "0918 0031")["summary"] == "Aircraft clock reset from 09:18 to 00:31 UTC"
    assert read("QF", "DEN2346FAR")["summary"] == "Took off from DEN at 23:46 UTC, bound for FAR"
    assert read("Q2", "   1656  76/DS KDEN")["summary"] == "ETA 16:56 UTC at KDEN Denver"
    assert read("QF", "\x00\x00\x002115\x00\x00\x00OS    /FUL0892")["summary"].startswith("Took off")


def test_afn_logon():
    def read(label, text):
        return translate.translate({"label": label, "flight": "XX0001", "kind": "acars", "text": text})
    r = read("B0", "/KUSA.AFN/FMHEJA18,..N18QS,A13FC2,131209/FPON40033W105048,1/FCOADS,01/FCOATC,01AEEC")
    assert r["summary"] == "Datalink logon to ATC centre KUSA"
    assert r["details"] == ["Flight EJA18", "N18QS", "ICAO A13FC2", "Sent 13:12:09 UTC", "40.05°N 105.08°W",
                            "Offers ADS-C v1, CPDLC v1"]
    peripheral = read("H1", "#M1B/B0 KUSA.AFN/FMHUAL1767,.N77261,,133405/FPON42489W103082,1/FCOATC,01/FCOADS,0185F5")
    assert peripheral["summary"] == "Datalink logon to ATC centre KUSA" and peripheral["position"]
