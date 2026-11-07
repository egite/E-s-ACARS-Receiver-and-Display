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
    assert bldn["summary"] == "Engine cruise report (BLDN19), FL370, Mach 0.778" and bldn["details"][0] == "Flight KSFO San Francisco → KEWR Newark"
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
