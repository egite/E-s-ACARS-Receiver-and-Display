"""Clear-text formats read from structure alone, checked on real examples."""
import json
from pathlib import Path

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


def test_airline_position_formats_21_october():
    def read(label, flight, text):
        return translate.translate({"label": label, "flight": flight, "kind": "acars", "text": text})
    r = read("83", "UA1149", "KSFO,KEWR,301603, 40.39,-105.17,39002,261,  80.5, 43800")
    assert r["summary"] == "Position report at 16:03 UTC, 39,002 ft"
    assert r["details"][2:] == ["Calibrated airspeed 261 kt", "Heading 80°", "Fuel on board 43,800 lb"]
    assert read("83", "UA0026", "EGLL,KDEN,302004, 41.97,-102.80,28000,301,-153.1, 26800")["details"][3] == "Heading 207°"
    r = read("19", "PD0671", "POS SNY    /1643/14253/FL359/190041")
    assert r["details"] == ["Fuel on board 14,253 lb", "Wind 190° at 41 kt"]
    r = read("4T", "AC1073", "AGFSR AC1073/30/30/YULDEN/1601Z/141/4005.2N10418.0W/150/DESCEN/0043/0077/M3 /056013/0249/220/30 /1205/1223/    /    ")
    assert r["summary"] == "Position report at 16:01 UTC, FL150" and r["position"]["src"] == "Air Canada AGFSR"
    assert "Outside air -3°C" in r["details"]
    assert read("32", "CX2091", "APR,V03,CX 209120260930 1,KDFW,PANC,N040.175,W105.568,40026, 483").get("position") is None
    assert read("33", "SY9036", "331,C,--- --.-,---- --.-,,013.2,30,KLAS,KMSP,KMSP,12R/,12L/,30L/,5")["summary"] \
        == "Runway data for KMSP Minneapolis runway 12R"


def test_united_label_49():
    r = translate.translate({"label": "49", "flight": "UA0761", "kind": "acars",
                             "text": "01ERDCUAL761    /010005EDDMKDEN\r\n+40002259.7- 21.1"})
    assert r["summary"] == "Flight phase report (en route \u2192 descent), 40,002 ft"
    assert r["details"][2:] == ["Calibrated airspeed 260 kt", "Total air temperature -21.1°C"]


def test_more_formats_21_october():
    def read(label, flight, text):
        return translate.translate({"label": label, "flight": flight, "kind": "acars", "text": text})
    climb = read("49", "UA0143", "01ICCLUAL143    /021752KDENRJAA\r\n+ 7193220.7+ 25.5")
    assert climb["summary"] == "Flight phase report (initial climb \u2192 climb), 7,193 ft"
    assert climb["details"][-1] == "Total air temperature 25.5\u00b0C"
    signed = read("16", "TK012E", "/+41.634/-105.260/34996/0210/2324/62000/0959/")
    assert signed["position"]["lon"] == -105.26
    assert read("33", "AS0497", "39,D,01,KSEA,KMCI,19R,,,,,,,,6,5,0,0,0,0,0,0,0,0,0,0,153.0,1238")["summary"] \
        == "Runway data for KMCI Kansas City runway 19R"
    assert read("33", "OO0000", "Q,H,30SEP26 12:55:42,22120661,30SEP,5829,KBIL,KDEN,4976,KDEN,59965,4200,35L,6,35R,6,,0,N")["summary"] \
        == "Runway data for KDEN Denver runway 35L"
    assert read("10", "BA09SW", "MET01KMSP")["summary"].startswith("Crew requested weather for KMSP")


def test_frontier_eta_and_landing_data_request():
    def read(label, flight, text):
        return translate.translate({"label": label, "flight": flight, "kind": "acars", "text": text})
    assert read("14", "F92141", "5,,,Y,N,Y,KPHX,191242,7275,,,,,")["summary"].startswith("ETA 19:12:42 UTC at KPHX")
    assert read("4P", "UP7831", "285,C,,1,09/30,RFD,LAX,,C083")["summary"].startswith("Crew requested landing data for LAX")


def test_report_239_feeds_the_wind_map():
    msg = {"label": "H1", "kind": "acars", "ts": 1790742745.0,
           "text": "A321,059330,1,1,TB000000/REP239,00,00,4/239N113DX0453093026043217775N38764W105 15340-14-43209 64AF0501"}
    assert translate.observations(msg) == [{"ts": 1790742745.0, "lat": 38.764, "lon": -105.015, "alt": 34000,
                                            "temp": -43, "wdir": 209, "wspd": 64}]


def test_delta_wob_is_a_wind_observation_with_position():
    msg = {"label": "H1", "sublabel": "DF", "flight": "DL1135", "kind": "acars", "ts": 1793842303,
           "text": "*WOBDTWPHX5100F 0511013143M230M52336081506N40577W101246263029:\r\n"}
    r = translate.translate(msg)
    assert r["category"] == "data" and not r.get("generic")
    assert r["details"][0] == "Flight DTW Detroit → PHX Phoenix"
    assert "36,000 ft, -52°C, wind 263° at 29 kt, Mach 0.815, at 40.96°N 101.41°W" in r["details"][1]
    assert r["position"] == {"lat": 40.9617, "lon": -101.41, "src": "Weather report"}
    obs = translate.observations(msg)
    assert len(obs) == 1 and obs[0]["alt"] == 36000 and obs[0]["temp"] == -52.3 and (obs[0]["wdir"], obs[0]["wspd"]) == (263, 29)
    assert obs[0]["ts"] == 1793842260.0  # 01:31 UTC on the message's day
    dashed = translate.translate(dict(msg, text="*WOBBOSLAX5100G 2008------M240M52338279405N41325W100431285039:\r\n"))
    assert "Latest: 38,200 ft" in dashed["details"][1]
    assert translate.observations(dict(msg, text="*WOBBOSLAX5100G 2008------M240M52338279405N41325W100431285039:\r\n"))[0]["ts"] == 1793842303


def test_sb01_weather_rows():
    text = ("SB0121BA_N791UA  KLAXKEWR1\r\n 38034-1049790807 36194-450205056098/9W022035\r\n"
            " 38105-1048160808 36877-460212061098YY,020030\r\n 38176-1046510809 36990-460216064010/9W013016")
    r = translate.translate({"label": "H1", "sublabel": "10", "flight": "UA2303", "kind": "acars", "text": text, "ts": 1793866324})
    assert r["summary"] == "Weather observations (3 samples, climbing 36,194 → 36,990 ft)"
    assert r["details"] == ["Flight KLAX Los Angeles → KEWR Newark",
                            "Latest 08:09 UTC: 36,990 ft, -46°C, wind 216° at 64 kt, at 38.18°N 104.65°W"]
    low = translate.translate({"label": "H1", "sublabel": "10", "flight": "UA1", "kind": "acars", "ts": 1793880724,
                               "text": "SB0122BA_N779UA  PHOGKDEN237\r\n 39782-1048041212 11061 030052014010GMY007009"})
    assert "11,061 ft, 3°C, wind 052° at 14 kt" in low["details"][1]


def test_american_trip_report_with_route_uses_the_last_snapshot():
    text = ("A38/A31938,1,1/C1TRP,151454,KORD,KDEN,08,8,50438/C2409113,-1004984,299,01283,421,0299,1/"
            "C3407668,-1008586,300,01280,420,0298,1/C4406956,-1012652,299,01278,417,0299,1/"
            "C5406221,-1016750,300,01276,415,0298,1/C6405464,-1020858,299,01273,412,0299,1/C70000545,000016")
    r = translate.translate({"label": "H1", "sublabel": "DF", "flight": "AA1225", "kind": "acars", "text": text})
    assert r["summary"] == "Automated trip report (5 samples, latest FL299 at 15:14:54 UTC, 412 kt)"
    assert r["details"][:2] == ["Flight KORD Chicago → KDEN Denver", "Latest position 40.55°N 102.09°W"]
    assert r["position"] == {"lat": 40.5464, "lon": -102.0858, "src": "Trip report"}
    named = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UA480", "kind": "acars",
                                 "text": "A32/A31932,1,1/HYDRAULIC,REPORT,/C1.N822UA,OCT,22,140241,KPSKPS,KDEN/FLIGHT.NUMBER/"})
    assert named["summary"] == "Hydraulic report at 14:02:41 UTC" and not named.get("generic")


def test_airbus_fault_report_and_warning():
    flr = translate.translate({"label": "H1", "flight": "UA1", "kind": "acars",
                               "text": "#CFBFLR/FR26093014140022663406AFS:FAC1/RT ACTR 10CC\r/IDAFS 1"})
    assert flr["category"] == "maintenance" and flr["summary"] == "Fault report: AFS:FAC1/RT ACTR 10CC"
    assert flr["details"][:2] == ["Logged 14:14 UTC", "ATA chapter 22-66-34"]
    wrn = translate.translate({"label": "H1", "sublabel": "CF", "flight": "UA549", "kind": "acars",
                               "text": "WRN/WN26110100150031000006RECORDER SYS FAULT\r"})
    assert wrn["summary"] == "Cockpit warning: RECORDER SYS FAULT" and wrn["details"][:2] == ["At 00:15 UTC", "ATA chapter 31"]


def test_frequency_change_uplink_and_low_level_report_239():
    r = translate.translate({"label": ":;", "kind": "acars", "direction": "up", "text": "131550"})
    assert r["category"] == "link" and r["summary"] == "Ground station moved the aircraft to 131.550 MHz"
    low = translate.translate({"label": "H1", "sublabel": "DF", "flight": "DL1", "kind": "acars",
                               "text": "239N37530751 93026175735296 3988-10461 60 19 14310  6AA-528 27720 \r\n176 1990067 8313       KDENKLGA"})
    assert "FL60" in low["details"] and "Wind 310° at 6 kt" in low["details"] and low["position"]["lat"] == 39.88


def test_more_weather_rows_ups_and_american():
    ups = {"label": "H1", "sublabel": "DF", "flight": "UP2843", "kind": "acars", "ts": 1793842303,
           "text": "E503SLC SDF N40327W10726412573901M567289051G1961N40324W10651813013\r\n901M552294057G1841N40327W10617813043900M542294052G1771"}
    r = translate.translate(ups)
    assert r["details"] == ["Flight SLC Salt Lake City → SDF Louisville",
                            "Latest 13:04 UTC: 39,000 ft, -54°C, wind 294° at 52 kt, at 40.33°N 106.18°W"]
    assert [o["temp"] for o in translate.observations(ups)] == [-56.7, -55.2, -54.2]
    aa = {"label": "H1", "sublabel": "DF", "flight": "AA317", "kind": "acars", "ts": 1793842303,
          "text": "A3E6011621DEN ORD N40285W1042372876M315239058G0009N40302W104210292\r\n3M327239061G0009N40320W1041822989M345240065G0009A2011"}
    r = translate.translate(aa)
    assert r["summary"] == "Weather observations (3 samples, climbing 28,760 → 29,890 ft)"
    assert "Latest 16:21 UTC: 29,890 ft, -34°C, wind 240° at 65 kt" in r["details"][1]


def test_united_engine_reports():
    r = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UA1141", "kind": "acars",
                             "text": "<370>FEG\r\n9  N38950UAL1141   KSFOKEWR011126085940 106ER415104 13\r\n 270022953735  -24 40769-105596  820BSM5B70000022 7166\r\n1NORM AUTO\r\n"})
    assert r["summary"] == "Engine report <370>F, en route"
    assert r["details"] == ["Flight UAL1141 (N38950), KSFO San Francisco → KEWR Newark", "Generated 08:59:40 UTC", "FL270, 40.77°N 105.60°W"]
    assert r["position"] == {"lat": 40.769, "lon": -105.596, "src": "United engine report"}
    clb = translate.translate({"label": "H1", "sublabel": "T6", "flight": "UA2303", "kind": "acars",
                               "text": "/ILNGE7X.CLB006UA N249732UAL2303   261101065402KLAXKEWR-112060274319486000013000022447M85P04"})
    assert clb["summary"] == "Engine climb report (CLB006)" and clb["details"][1] == "Generated 06:54:02 UTC"
    bldn = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UA1", "kind": "acars",
                                "text": "BLDN19136300909042611 1685 -23.50 -50.5  370060.778 150 2120002416\r\n        1CRS 065R9 000000000000KSFOKEWR1000 86.75"})
    assert bldn["summary"] == "Bleed air report (BLDN), 37,006 ft, Mach 0.778" and bldn["details"][0] == "Flight KSFO San Francisco → KEWR Newark"
    assert "Aircraft N19136, flight 2416" in bldn["details"]
    crz = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UA2201", "kind": "acars",
                               "text": "CRZ01144482201KDENKJAC1101260137061280\r\n297785030015N126111960N41200000N0045N0000000N000000400000905210473.\r\n1234"})
    assert crz["summary"] == "Cruise performance report (CRZ01)" and crz["details"] == ["Flight KDEN Denver → KJAC Jackson", "Recorded 01:37 UTC"]
    crz10 = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UA1", "kind": "acars",
                                 "text": "CRZ10N6381456  73KBW IKD 111014143197  -8.0-37.3 320020.7871649202\r\n4121SCR2A85 0986"})
    assert crz10["summary"] == "Cruise performance report (CRZ10), 32,002 ft, Mach 0.787"


def test_ups_eda_ok_replies_and_named_reports():
    eda = translate.translate({"label": "H1", "sublabel": "DF", "flight": "UP843", "kind": "acars",
                               "text": "EDA/E112UP08431N387UP  11/0704:05:03N40.51W106.6128703733700225978\r\n006-252-533000004606260541050061606008980947"})
    assert eda["summary"] == "Engine data report (EDA), flight UP0843" and eda["position"] == {"lat": 40.51, "lon": -106.61, "src": "UPS EDA report"}
    ok = translate.translate({"label": "H1", "flight": "UA1", "kind": "acars", "text": "#DFB/NDCULUA.OK\r\nXCM 90 GWMAX800 146300\r\nAL"})
    assert ok["summary"] == "Aircraft answered a company uplink: OK" and ok["details"][0] == "“XCM 90 GWMAX800 146300 / AL”"
    atlas = translate.translate({"label": "H1", "flight": "5Y4605", "kind": "acars",
                                 "text": "#CFB/BLVBOCR.\r\n\n\n\n A RPT12 PG1  R-ECS AIR COND SYS REAL\r\n B N445MC 30SEP26 1537 GTI4605 KCVG/KPDX 685-2270-012 PW-508 ER\r\n\r\n 1   19  17"})
    assert atlas["summary"] == "R-ecs air cond sys report (RPT12, page 1)"
    assert atlas["details"] == ["Flight GTI4605 (N445MC), KCVG Cincinnati / Covington → KPDX Portland", "Generated 15:37 UTC"]
    pos = translate.translate({"label": "H1", "sublabel": "M1", "flight": "ZD1", "kind": "acars",
                               "text": "/.POS/TS172331,300926N38594W104213,,172331,301,,172331,,M32,13522,66,82045AF"})
    assert pos["summary"].startswith("Position report: over 38.99°N 104.36°W at 17:23:31 UTC")
    delta = translate.translate({"label": "H1", "sublabel": "DF", "flight": "DL1", "kind": "acars",
                                 "text": "072A8115.N3753300917581171123507511KDENKLGACLMB  60156160  18.8  1\r\n0.0  78482290.3998"})
    assert delta["summary"] == "Aircraft monitoring report 72" and delta["details"] == ["Flight KDEN Denver → KLGA New York"]
    assert translate.translate({"label": "H1", "sublabel": "DF", "flight": "DL1", "kind": "acars",
                                "text": "221N3753247710 526183859150256369791-26-53  5 905 906685693 2823 2\r\n883 927"})["summary"] == "Aircraft monitoring report 221"


def test_air_france_2f_position_and_jazz_landing_data():
    r = translate.translate({"label": "2F", "flight": "AF022N", "kind": "acars",
                             "text": "1907+4044-10342/36209\r\nLFPG/KLAX/35999/-120/ -54/0.83/242603/221610/29.0/AREAF  /JASSE  / -72/   29"})
    assert r["summary"] == "Position report: over 40.44°N 103.42°W at 19:07 UTC, 36,209 ft"
    assert r["details"] == ["Flight LFPG Paris → KLAX Los Angeles", "Mach 0.83, outside air -54°C (total -12.0°C)", "Passed AREAF, next JASSE"]
    assert r["position"] == {"lat": 40.44, "lon": -103.42, "alt": 36209, "src": "Label 2F"}
    short = translate.translate({"label": "2F", "flight": "AF069T", "kind": "acars", "text": "0048+4067-10668/39525 "})
    assert short["position"]["lon"] == -106.68 and short["details"] == []
    jazz = translate.translate({"label": "4s", "flight": "RV1705", "kind": "acars", "text": "CFWTL/510/LAS/26L/L/   /  /  /   /    /100/"})
    assert jazz["category"] == "request" and jazz["summary"] == "Landing performance data requested for runway 26L at LAS"


def test_engine_report_headers():
    read = lambda text, sub="DF", flight="UA1": translate.translate({"label": "H1", "sublabel": sub, "flight": flight, "kind": "acars", "text": text})
    crz = read("*CRZ5100F 0911040645M240M51034077919708000000--000000--0775078008550854386396034880355211231137P056P056", flight="DL391")
    assert crz["summary"] == "Cruise snapshot at 04:06:45 UTC: FL340, Mach 0.779" and crz["details"] == ["Outside air -51.0°C (total -24.0°C)"]
    fx = read("/NQAASFX.086A91041110260412 4392993CLMBN179FE2914D12201KDENKMEM707000707000\r\nGE-NEWMAN-TRIG----------09TT", flight="FX1220")
    assert fx["summary"] == "Engine report 086A9, climb" and fx["details"] == ["Flight FX2914 (N179FE), KDEN Denver → KMEM Memphis", "Generated 04:12 UTC"]
    ba = read("ACM966VTG-YMMU  BA21MLBAKDENEGLL121126 213CL\r\n001232SF 8086263-00GW490920\r\n 39.895  39.8934-104.51236503\r\n36503 7678254-104.5126  39.8933 32\r\n229.39 87255  67 19.1  7274   6.7  39.8934\r\n 87   0.10000111-0.0", flight="BA21ML")
    assert ba["summary"] == "Aircraft monitoring report 966, climb"
    assert ba["details"] == ["Flight BA21ML (G-YMMU), KDEN Denver → EGLL London", "39.89°N 104.51°W, 7,678 ft, 254 kt", "Generated 00:12:32 UTC", "Gross weight 490,920 lb"]
    assert ba["position"] == {"lat": 39.8934, "lon": -104.5123, "src": "BA ACM report"}
    acmf = read("ACMF230_CRUISE_20261109223457S.CSV\nACMF SNAPSHOT REPORT 2026NOV09 22:34:57\nA/C N604UX\nSWVER 27.1", sub="CF", flight="OO5689")
    assert acmf["summary"] == "ACMF cruise snapshot report 230" and acmf["details"] == ["Aircraft N604UX", "Generated 22:34:57 UTC"]
    e23 = read("E23C12776L383PHNLKDEN  69195801109260750ER\r\n237 184183728582860-1083")
    assert e23["summary"] == "Engine report E23C, en route" and e23["details"] == ["Flight UA383, PHNL Honolulu, Oahu → KDEN Denver"]
    ver = read("<01>MMH219VER 104101126133034EGLLKDEN528040\r\n 360022741826-294 52254", flight="BA219V")
    assert ver["details"] == ["Flight BA219 (G-YMMH), EGLL London → KDEN Denver", "Generated 13:30:34 UTC"]
    elec = read("ELEC-GVIJ09111131 2031124 149 153680599362427388005941TOFF03A79 70\r\n6489706226DENSLC0 597", flight="2I0594")
    assert elec["summary"] == "Engine takeoff report (ELE)" and elec["details"] == ["Flight DEN Denver → SLC Salt Lake City"]
    end = read(",\r\n:\r\n", flight="WS2201")
    assert end["category"] == "link" and end["generic"] and end["summary"] == "End of a multi-block report (terminator only)"


def _southwest_cipher(group, plain):
    """Encipher clear text with a group's alphabet, the inverse of what the reader does."""
    sep, digits = translate.SOUTHWEST_KEYS[group]
    table = {str(i): c for i, c in enumerate(digits)}
    cipher, letters = translate.SOUTHWEST_LETTERS[group]
    table.update(zip(letters, cipher))
    table.update(zip(". -/", translate.SOUTHWEST_MARKS[group]))
    table[" "] = sep
    table["_"] = translate.SOUTHWEST_MARKS[group][1]  # the word space, used inside a field ("N_40.821")
    return "".join(c if c in "\r\n" else table[c] for c in plain)


def test_southwest_label_37_text_messages():
    # Free text, turbulence, maintenance, holding and gate reports under the same substitution
    # as the position reports; the word space, point, minus and slash complete each alphabet.
    def read(group, kind, body):
        head = group + _southwest_cipher(group, kind + "02")
        return translate.translate_southwest({"text": head + "\r\n" + _southwest_cipher(group, body)})
    ftd = read("02", "FTD", "KSMF KSTL\r\nHOW FAR AHD IS HE\r\n")
    assert ftd["category"] == "crew" and ftd["summary"] == "Crew message to dispatch: “HOW FAR AHD IS HE”"
    assert ftd["details"] == ["Flight KSMF Sacramento → KSTL St Louis"]
    trb = read("07", "TRB", "KSLC KMDW 37000 192/031 2 0\r\nSINCE SLC\r\nOCSNL LT TURB")
    assert trb["summary"] == "Turbulence report at 37,000 ft: “SINCE SLC, OCSNL LT TURB”"
    assert trb["details"] == ["Flight KSLC Salt Lake City → KMDW Chicago", "Wind 192° at 31 kt", "Report codes 2 0"]
    mxr = read("09", "MXR", "KDEN 1606 2\r\nPAX CABIN ITEM\r\n5D RCLN BUTTN INOP\r\n")
    assert mxr["category"] == "maintenance" and mxr["summary"] == "Maintenance write-up: “PAX CABIN ITEM, 5D RCLN BUTTN INOP”"
    assert mxr["details"] == ["At KDEN Denver, 16:06 UTC"]
    hld = read("01", "HLD", "BGDEE          \r\n12.3 0005 20000")
    assert hld["summary"] == "Holding at BGDEE, 20,000 ft" and hld["details"] == ["Fuel on board 12,300 lb, at 00:05 UTC"]
    out = read("05", "OUT", "KDEN KMDW 2145 16.8 08.1 2145")
    assert out["summary"] == "Left the gate at 21:45 UTC" and out["details"] == ["Flight KDEN Denver → KMDW Chicago", "Fuel on board 16,800 lb"]
    assert translate.southwest_plain("03", _southwest_cipher("03", "GOOD. -53 192/031")) == "GOOD. -53 192/031"
    # The position reports still read as before.
    assert translate.translate_southwest({"text": "01" + _southwest_cipher("01", "POS01") + "\r\n" +
                                          _southwest_cipher("01", "KSJC KMDW N_40.821 W_99.991 38995 245 458 .793 13.2 0935 079 264 049 -53 135080")})["position"] == \
        {"lat": 40.821, "lon": -99.991, "src": "Southwest label 37"}


def test_business_jet_part_number_and_landing_events():
    ami = translate.translate({"label": "88", "flight": "ZD0000", "kind": "acars",
                               "text": "00AMI02,N39441W105439,398,*,KGJT,****,****,ZD,69003082-502   "})
    assert ami["category"] == "data" and ami["summary"] == "Avionics part-number report"
    assert ami["details"][0] == "39,800 ft"
    # An ON event enciphered under key 02: two-letter event names have to pass the header check.
    sep, digits = translate.BIZJET_KEYS["02"]
    table = {str(i): c for i, c in enumerate(digits)}
    table.update({p: c for c, p in zip(*translate.BIZJET_LETTERS["02"])})
    table.update({p: c for c, p in zip(*translate.BIZJET_PUNCT["02"])})
    table[","] = sep
    on = "02" + "".join(table[c] for c in "ON03,N40274W105008,KGXY,KFNL,1107,1922,002.0,0007,    0")
    r = translate.translate({"label": "44", "flight": "GS412M", "kind": "acars", "text": on})
    assert r["category"] == "flight" and r["summary"] == "Landed: KGXY Greeley → KFNL Loveland at 19:22 UTC"


def test_clear_text_stragglers_read():
    read = lambda label, flight, text: translate.translate({"label": label, "flight": flight, "kind": "acars", "text": text})
    assert read("5Z", "MQ3834", "OS KMSO /FTM\r\nM51 DFW 3834 DLY DUE TO\r\nLATE INBOUND AIRCRAFT")["summary"] == \
        "Message to dispatch at KMSO Missoula: “M51 DFW 3834 DLY DUE TO LATE INBOUND AIRCRAFT”"
    assert read("5Z", "US1313", "OS KDEN /IR KDEN1735")["summary"] == "In range of KDEN Denver, arriving 17:35 UTC"
    off = read("22", "WN1823", "OFF02\r\nKDEN,KSMF,0435,20.4")
    assert off["summary"] == "Took off at 04:35 UTC" and off["details"] == ["Flight KDEN Denver → KSMF Sacramento", "Fuel on board 20,400 lb"]
    assert read("4C", "F94186", ".N337FR,FFT4186,KDEN,KDTW,RT1138,LT0156,0156,0143,")["details"][0] == "RT 11:38 · LT 01:56 · 01:56 · 01:43"
    assert read("10", "BA21ML", "INI0302KDENEGLL0821BENDM      CROYY  WHIRZ  -----------")["summary"] == \
        "Flight plan loaded: KDEN Denver → EGLL London via BENDM, CROYY, WHIRZ"
    assert read("80", "AS0363", "3V01 ACK    0363/01 KMCO/KPDX .N464AS\r\n27751")["summary"] == "Crew acknowledged dispatch message 27751"
    fbo = read("14", "GS0001", "(2AAAAN40563W102589SIGNATURE       N573QS   ETA 1845L.  1 PAX(Z")
    assert fbo["category"] == "crew" and fbo["position"] == {"lat": 40.563, "lon": -102.589, "src": "Label 14"}
    assert fbo["summary"] == "Message to ground handling: “SIGNATURE N573QS ETA 1845L. 1 PAX”"


def test_airbus_a02_engine_cruise_report():
    iae = {"label": "H1", "flight": "UA1741", "kind": "acars", "text": 'A02/A32002,1,1/CCN453UA,NOV14,034132,KABQ,KDEN,1741/C106,58737,5000,99,0010,0,0100,99,X/CEN236,33004,269,760,1221,305,I23014/CNN220,33048,269,763,1222,305/EC010929,00007,00004,73,27,27/EE011893,01185,00680,73/N11236,1237,3691,0765,0842,0252,08231/N21237,1237,3760,0766,0841,0253,08272/S113436,0498,1266,3910,07071,061/S213648,0520,1259,3917,07055,061/T1101,085,00,029,37,164,05577,N246/T2101,085,00,020,39,127,05577,N218/V103C04,00208,08000,232,115,N093/V203C04,00208,08000,240,114,N255/V3003,011,090/V4010,005,170/X100541,N000,0013,0001,N0009,N009/X200562,N000,0016,0001,N0009,N009/X3N002,0008,0000,N009,N006,0002,N000/X40000,N000,N000,0000,0000,0000/X50000,N001,N000,0000,0000,0000/X60131,W1065,N375,037,190,0148,XXXX/X70131,W1065,N375,035,190,0139,XXXX,/'}
    r = translate.translate(iae)
    assert r["summary"] == "Engine cruise report (A02), 33,004 ft, Mach 0.760"
    assert "N453UA, A320, IAE V2500 engines" in r["details"]
    assert "Gross weight 122,100 lb" in r["details"]
    assert "Engine serials 010929 and 011893" in r["details"]
    grid = r["tables"][0]
    labels = [c["label"] for c in grid["columns"]]
    assert labels[:5] == ["Engine", "N1", "N2", "EGT", "Fuel flow"]
    assert grid["columns"][3].get("likely") and not grid["columns"][1].get("likely")
    row = dict(zip(labels, grid["rows"][0]))
    assert (row["Engine"], row["N1"], row["Fuel flow"], row["Inlet P"], row["Compressor exit T"]) == ("Engine 1", "76.5", "2,520", "5.577", "391.0")
    cfm = {"label": "H1", "flight": "AA0923", "kind": "acars", "text": 'A02/A31902,1,1/CCN737US,NOV14,175139,KJAC,KDFW,0923/C106,47637,5000,XX,0010,0,0100,XX,X/CEN281,38995,244,790,1209,260,C83014/CNN280,39032,245,792,1209,260/EC645604,01713,05450,01090,73,18,06/EE779835,00674,00702,00424,73/N10830,0830,0921,6268,0237,XXXXX/N20830,0830,0922,6216,0239,XXXXX/S1XXXXX,0600,1058,4188,XXXX,027,N00/S2XXXXX,0586,1061,4206,XXXX,021,N00/T1099,100,025,39,066,04363,0143/T2099,099,018,36,068,04257,0142/V106,07,287,036,05,00,00000/V200,09,270,340,04,00,00000/V3XX,XX,XXX,XXX,XXXX/V4XX,XX,XXX,XXX,XXXX/V5XX,XX,XXX,XXX,XXXX/V6XX,XX,XXX,XXX,XXXX/V7037,080,00081,22222222222111/V8038,081,00061,22222222C22111/X100611,N000,0025,N001,00000,N002/X200664,N003,0024,N001,00000,N001/X3N003,N002,N004,N007,N013,0000,N000/X40000,N000,N001,N000,0000,0000/X5N000,N000,N001,N000,0000,0000/X61361,W1071,N407,018,196,0022,0825/X71363,W1071,N407,019,191,0026,XXXX,/'}
    r = translate.translate(cfm)
    assert "CFM56 engines" in r["details"][1]
    assert [c["label"] for c in r["tables"][0]["columns"]][:4] == ["Engine", "N2", "EGT", "Fuel flow"]
    # Without the engine groups (the first block alone) the report still reads, with no table.
    head = dict(iae, text=iae["text"].split("/N1")[0])
    assert "tables" not in translate.translate(head)


def test_pnm4_reads_fixed_width_engine_columns():
    # "    33    332225..." is N1 33 and 33; splitting on digits once read the second as 3.
    msg = {"label": "H1", "flight": "UA0001", "kind": "acars",
           "text": "PNM40000000KBDLKDENDESC 16621 16618    33    332225403397101  -4.0  10.010.510    75    742911-00001100 "}
    r = translate.translate(msg)
    assert "N1 33/33%" in r["details"]
    assert r["tables"][0]["rows"][0][-4:] == ["33", "33", "75", "74"]


def test_bldn_bleed_air_report():
    msg = {"label": "H1", "flight": "UA1149", "kind": "acars", "text": 'BLDN1712614110525++++  969 -26.50 -54.4  370040.798 524 2053201149\r\n        1CRS 055R9 000000000000KSFOKEWR3000 86.75 86.1900011 28.06 28\r\n.91  37002CRS 00000NORMNORM1.5961.595  259  307RUN RUN NORM724BNORM.2500011 28.06 2\r\n8.91  37002CRS 00000NORMNORM1.5991.597  259  308RUN RUN NORM724BNORMP\r\nCOTUNDRTEMP    NORMALNORMAL 86.94 86.3100011 28.16 28.97  37002CRS 00\r\n000NORMNORM1.6011.598  259  308RUN RUN NORM724BNORMPCOTUNDRTEMP    NO\r\nRMALNORMAL 86.81 86.2500011 28.09 28.91  37001CRS 00000NORMNORM1.5981\r\n.598  259  308RUN RUN NORM724BNORMPCOTUNDRTEMP    NORMALNORMAL 86.81 \r\n86.3100011 28.00 28.91  37000CRS 00000NORMNORM1.5981.598  259  307RUN\r\n RUN NORM724BNORMPCOTUNDRTEMP    NORMALNORMAL 86.81 86.2500011 28.03 \r\n28.91  37002CRS 00000NORMNORM1.5991.598  259  307RUN RUN NORM724BNORM\r\nPCOTUNDRTEMP    NORMALNORMAL'}
    r = translate.translate(msg)
    assert r["summary"] == "Bleed air report (BLDN): precooler outlet under-temperature (4 samples), 37,004 ft, Mach 0.798"
    assert "Aircraft N17126, flight 1149" in r["details"]
    grid = r["tables"][0]
    assert [c["label"] for c in grid["columns"]][5:9] == ["Duct P #1", "Duct P #2", "Precooler out T #1", "Precooler out T #2"]
    assert grid["rows"][0][:5] == ["37,002", "86.94", "86.31", "1.601", "1.598"]
    assert grid["rows"][0][-1] == "precooler outlet under-temperature"


def test_a53a_reads_times_and_temperatures():
    msg = {"label": "H1", "flight": "WN1785", "kind": "acars", "text": 'A53A\r\nN8841L 1411211234471501\r\n17859KMCOKDENESBSM23-H200-0005\r\n60A959\r\n60A965\r\n 91.4 98.319.419.8  192112\r\n 91.4 97.6  593059  3720.517.9  6 40  50  72  202113\r\n 72.9 94.130553066  3717.118.5 77 69  83  82 -552324\r\n 70.7 92.730613072  5616.818.3 78 69  80  79 -560017\r\n'}
    r = translate.translate(msg)
    assert r["summary"] == "Aircraft monitoring report A53A (4 samples)"
    assert r["details"][:2] == ["Flight KMCO Orlando → KDEN Denver", "Aircraft N8841L"]
    grid = r["tables"][0]
    assert grid["rows"][0] == ["21:12", "19", "", ""]
    assert grid["rows"][-1] == ["00:17", "-56", "3,061", "3,072"]
    assert grid["columns"][2]["likely"]
    # A tail run into the date: the first valid DDMMHHMM ends it.
    glued = {"label": "H1", "flight": "UA1272", "kind": "acars", "text": 'A53A\r\n N4734019111150  91 366\r\n12724KSFOKSFOESBSM25-H200-0003\r\n60B646\r\n60B654\r\n 91.4 89.219.620.7  131150\r\n 91.6 75.630192966  3719.115.4  - 61  1-  34  361616\r\n 71.9 82.130683094  3718.118.7 68 75  83  84 -581752\r\n 68.2 78.630693098  3718.018.7 67 76  83  83 -631831\r\n'}
    assert "Aircraft N47340" in translate.translate(glued)["details"]


def test_a02_in_the_airbus_wrapper():
    msg = {"label": "H1", "flight": "UA1184", "kind": "acars", "text": 'A320,092169,1,1,TB000000/REP002,01,01,4/CCN485UA,NOV14,040033,KMSN,KDEN,1184/C0SI055032320006/C106,21037,5000,09,0010,0,0100,09,X/CEN219,34013,275,791,1237,258,I22032/CNN211,33996,274,791,1237,258/EC011059,69983,30494,73,06,06/EE010388,02304,01302,73/N11285,1285,3881,0789,0856,0270,08288/N21285,1285,3988,0788,0852,0272,08134/S113500,0571,1301,4102,07216,064/S213712,0575,1305,4169,07207,063/T1098,084,00,027,40,156,05471,N202/T2099,084,00,021,41,148,05477,N212/V103D04,00208,08000,254,110,----/V203D04,00208,08000,240,112,----/V3002,003,200/V4008,001,276/X100777,0000,0014,0003,00002,0006/X200815,N000,0013,0003,00001,0006/X30013,0020,N012,N005,N008,0008,0000/X40000,0000,N000,0000,0000,0000/X50000,N000,0000,0000,0000,0000/X62613,W0960,N418,062,330,N007,0830/X72613,W0960,N418,061,329,N007,0824/:'}
    r = translate.translate(msg)
    assert r["summary"] == "Engine cruise report (A02), 34,013 ft, Mach 0.791"
    assert "N485UA, A320, IAE V2500 engines" in r["details"]
    assert r["tables"][0]["rows"][0][:2] == ["Engine 1", "78.9"]


def test_a37_climb_samples_with_iae_engines():
    msg = {"label": "H1", "flight": "UA1022", "kind": "acars", "text": 'A37/A31937,1,1/C1N820UA,NOV14,034340,KDEN,KSAT,1022/C29000,I73014,51,012147,011811/C30160,0095,08052,343,1315,034240/C40177,0110,08207,345,1316,034245/C50180,0110,08329,354,1315,034250/C60182,0107,08465,364,1314,034255/C70182,0105,08640,371,1314,034300/C80180,0100,08838,374,1314,034305/C90175,0092,09046,382,1314,034310/C00175,0087,09205,393,1313,034315/E10175,0085,09356,403,1313,034320/E20175,0080,09508,412,1312,034325/E30175,0075,09652,422,1312,034330/E40175,0070,09787,432,1312,034335/E50846,0913,1334,1,1,1/5E0847,0910,1333,1,1,0/E60845,0912,1327,1,1,1/6E0848,0910,1327,1,1,0/E70846,0913,1327,1,1,1/7E0848,0911,1327,1,1,0/E80850,0916,1330,1,1,1/8E0851,0913,1329,1,1,0/E90852,0918,1332,1,1,1/9E0853,0914,1332,1,1,0/E00853,0917,1333,1,1,1/0E0855,0915,1333,1,1,0/N10855,0918,1338,1,1,1/1N0857,0916,1337,1,1,0/N20857,0919,1340,1,1,1/2N0860,0917,1341,1,1,0/N30860,0921,1344,1,1,1/3N0862,0919,1343,1,1,0/N40860,0920,1345,1,1,1/4N0862,0918,1344,1,1,0/N50859,0920,1344,1,1,1/5N0861,0917,1343,1,1,0/N60857,0919,1341,1,1,1/6N0860,0917,1343,1,1,0/N70,0,1/7N0,0,1/N80,0,1/8N0,0,1/N90,0,1/9N0,0,1/N00,0,1/0N0,0,1/S10,0,1/1S0,0,1/S20,0,1/2S0,0,1/S30,0,1/3S0,0,1/S40,0,1/4S0,0,1/S50,0,1/5S0,0,1/S60,0,1/6S0,0,1/S70,0,1/7S0,0,1/S80,0,1/8S0,0,1/S9099,0044,185/9S094,0045,186/S0098,0044,186/0S094,0045,186/T1098,0044,186/1T094,0045,186/T2099,0044,186/2T094,0045,186/T3099,0044,186/3T095,0045,186/T4098,0044,187/4T094,0045,187/T5099,0044,186/5T095,0045,187/T6099,0044,187/6T095,0045,187/T7099,0044,187/7T096,0045,187/T8100,0044,187/8T096,0045,187/T9100,0044,187/9T096,0045,187/T0100,0044,188/0T096,0045,187/V10,0,0/V20,0,0/V30,0,0/V40,0,0/V50,0,0/V60,0,0/V70,0,0/V80,0,0/V90,0,0/V00,0,0/C10,0,0/C20,0,0/'}
    r = translate.translate(msg)
    assert r["summary"] == "Performance report (A37, 12 samples, climbing 8,052 → 9,787 ft)"
    assert r["details"][:2] == ["Flight KDEN Denver → KSAT San Antonio", "N820UA, A319, IAE V2500 engines"]
    grid = r["tables"][0]
    assert [c["label"] for c in grid["columns"]][-6:] == ["N1 #1", "N1 #2", "N2 #1", "N2 #2", "EPR #1", "EPR #2"]
    assert grid["rows"][0] == ["03:42:40", "8,052", "0.343", "9.5", "16.0", "131,500", "84.6", "84.7", "91.3", "91.0", "1.334", "1.333"]


def test_weather_profile_and_trip_report_tables():
    msgs = [json.loads(l) for l in open(Path(__file__).parent / "corpus.jsonl")]
    weather = [r for r in map(translate.translate, msgs) if r["summary"].startswith("Weather observations (") and r.get("tables")]
    assert weather and all(len(r["tables"][0]["rows"]) >= 2 for r in weather)
    assert [c["label"] for c in weather[0]["tables"][0]["columns"]][:5] == ["Time", "Position", "Altitude", "Outside air", "Wind"]
    trip = {"label": "H1", "kind": "acars", "text": "TRP KSNA KORD  7 4\r\n/A1 005634  37.8666 -105.3102 390 140.0 451 0.000000\r\n"
                                                    "/A2 005934  38.0506 -104.8206 390 139.8 448 0.000000\r\n"}
    r = translate.translate(trip)
    assert r["tables"][0]["rows"] == [["00:56:34", "37.87°N 105.31°W", "FL390"], ["00:59:34", "38.05°N 104.82°W", "FL390"]]
