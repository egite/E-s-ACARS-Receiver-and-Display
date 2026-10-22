"""receiver.py builds each decoder's command line from config.json; check the radio-specific
parts, in particular the SDRplay path, which maps one config meaning of gain onto three
decoders that each count it differently."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import receiver  # noqa: E402

CFG = {"acars_udp_port": 5550, "vdl2_udp_port": 5555}
ACARS_FREQS = [129.125, 130.025, 130.450, 131.125, 131.550]
VDL2_FREQS = [136.650, 136.975]


def rsp(**over):
    rx = {"sdr": "sdrplay", "device": "1234567890", "gain": 38, "lna_state": 3, "ppm": 0,
          "frequencies": ACARS_FREQS}
    rx.update(over)
    return rx


def test_rtlsdr_is_the_default_and_unchanged():
    rx = {"device": "ACARS1", "gain": 40, "ppm": 2, "frequencies": ACARS_FREQS}
    cmd = receiver.acars_command(rx, CFG)
    assert cmd[cmd.index("--rtlsdr") + 1] == "ACARS1"
    assert cmd[cmd.index("-g") + 1] == "40" and cmd[cmd.index("-p") + 1] == "2"
    assert "--soapysdr" not in cmd and "-m" not in cmd
    vdl = receiver.vdl2_command({**rx, "frequencies": VDL2_FREQS}, CFG)
    assert "--rtlsdr" in vdl and vdl[vdl.index("--gain") + 1] == "40"


def test_unknown_sdr_is_refused():
    with pytest.raises(SystemExit, match="receivers.acars.sdr"):
        receiver.sdr_for("acars", {"sdr": "hackrf"})


def test_acarsdec_on_rsp_uses_soapysdr_with_ifgr_above_minimum(capsys):
    cmd = receiver.acars_command(rsp(), CFG)
    assert "--sdrplay" not in cmd  # the native input leaves the 200 kHz default IF filter
    assert cmd[cmd.index("--soapysdr") + 1] == "driver=sdrplay,serial=1234567890"
    assert cmd[cmd.index("-g") + 1] == "18"  # IFGR 38 = SoapySDR overall gain 18 (20 + 18)
    err = capsys.readouterr().err
    assert "lna_state=3 is ignored by acarsdec" in err
    assert "IF filter" in err  # 2.4 MHz span, 1.536 MHz filter


def test_rsp_auto_gain_omits_the_flag():
    assert "-g" not in receiver.acars_command(rsp(gain="auto"), CFG)
    assert "-g" not in receiver.xng_command("acars", rsp(gain=None), CFG)
    assert "--ifgr" not in receiver.dumpvdl2_input_args("vdl2", rsp(gain="auto", frequencies=VDL2_FREQS))


def test_rsp_gain_outside_ifgr_range_is_refused():
    with pytest.raises(SystemExit, match="20-59"):
        receiver.acars_command(rsp(gain=40.5), CFG)
    with pytest.raises(SystemExit, match="20-59"):
        receiver.sdrplay_ifgr("acars", rsp(gain=10))


def test_dumpvdl2_on_rsp_is_native_with_both_gain_parts(capsys):
    args = receiver.dumpvdl2_input_args("vdl2", rsp(frequencies=VDL2_FREQS, ppm=1))
    assert args[:4] == ["--sdrplay3", "1234567890", "--correction", "1"]
    assert args[args.index("--ifgr") + 1] == "38" and args[args.index("--lna-state") + 1] == "3"
    assert capsys.readouterr().err == ""  # VDL2 band fits the fixed 1.536 MHz filter


def test_xng_on_rsp_device_string_gain_and_rate_floor(capsys):
    cmd = receiver.xng_command("acars", rsp(frequencies=[130.025, 130.450, 131.125]), CFG)
    assert cmd[cmd.index("--sdr") + 1] == "driver=sdrplay,serial=1234567890"
    assert cmd[cmd.index("-g") + 1] == "18"
    assert cmd[cmd.index("-r") + 1] == "2016000"  # RSP floor: 2 MS/s rounded up to xng's 24 kHz grid
    assert "IF filter" not in capsys.readouterr().err  # 1.1 MHz span fits


def test_xng_rate_limits_depend_on_the_radio():
    wide = {"frequencies": [129.125, 131.825]}  # 2.7 MHz span: too wide for a dongle, fine on an RSP
    with pytest.raises(SystemExit, match="RTL-SDR"):
        receiver.xng_capture_plan("acars", wide)
    _, rate = receiver.xng_capture_plan("acars", {**wide, "sdr": "sdrplay"})
    assert rate == 2_760_000 and rate % 24_000 == 0
    with pytest.raises(SystemExit, match="10.66"):
        receiver.xng_capture_plan("acars", {"sdr": "sdrplay", "frequencies": [125.0, 136.975]})


def test_filter_note_clears_at_the_wide_filter(capsys):
    assert receiver.rsp_filter_note("acars", rsp(), 2_496_000, "fix it") is True
    assert "fix it" in capsys.readouterr().err
    assert receiver.rsp_filter_note("acars", rsp(), 5_000_000) is False
    assert receiver.rsp_filter_note("vdl2", rsp(frequencies=VDL2_FREQS), None) is False


def test_acarsdec_sample_rate_must_be_on_its_grid():
    rx = {"device": "ACARS1", "gain": 40, "frequencies": ACARS_FREQS, "sample_rate": 3_024_000}
    cmd = receiver.acars_command(rx, CFG)
    assert cmd[cmd.index("-m") + 1] == "252"
    with pytest.raises(SystemExit, match="12000"):
        receiver.acars_command({**rx, "sample_rate": 2_500_000}, CFG)


# ---- one RSP, two receivers: the host opens the radio through SoapyTee, the partner
# reads a slice of the capture from a FIFO as an I/Q file.

def shared_cfg(**vdl2_over):
    vdl2 = {"sdr": "shared", "decoder": "dumpvdl2", "frequencies": VDL2_FREQS}
    vdl2.update(vdl2_over)
    return {**CFG, "receivers": {"acars": rsp(gain="auto"), "vdl2": vdl2}}


def test_shared_partner_lookup_defaults_to_the_other_receiver():
    cfg = shared_cfg()
    assert receiver.shared_partner("acars", cfg)[0] == "vdl2"
    assert receiver.shared_partner("vdl2", cfg) == (None, None)
    assert receiver.shared_partner("acars", shared_cfg(enabled=False)) == (None, None)
    assert receiver.shared_partner("acars", shared_cfg(host="other")) == (None, None)


def test_host_capture_covers_both_bands_on_the_shared_rate_grid():
    cfg = shared_cfg()
    center, rate, tee = receiver.shared_capture_plan("acars", cfg["receivers"]["acars"], cfg)
    assert center == (129.125e6 + 136.975e6) / 2
    assert rate == 8_400_000  # smallest 4.2 MS/s step that holds 129.125-136.975
    assert rate % 12_000 == 0 and rate % 24_000 == 0 and rate % receiver.SHARED_SLICE_RATE == 0
    assert tee["tee_driver"] == "sdrplay" and tee["tee_rate"] == "1050000"
    assert tee["tee_center"] == "136812000" and tee["tee_fifo"].endswith("run/vdl2.iq")


def test_host_acarsdec_pins_centre_and_rate_and_opens_the_tee():
    cfg = shared_cfg()
    cmd = receiver.acars_command(cfg["receivers"]["acars"], cfg)
    dev = cmd[cmd.index("--soapysdr") + 1]
    assert dev.startswith("driver=tee,tee_driver=sdrplay,") and dev.endswith(",serial=1234567890")
    assert cmd[cmd.index("-c") + 1] == "133.050000" and cmd[cmd.index("-m") + 1] == "700"


def test_shared_dumpvdl2_reads_the_fifo_as_s16_iq():
    cfg = shared_cfg()
    args = receiver.dumpvdl2_input_args("vdl2", cfg["receivers"]["vdl2"])
    assert args == ["--iq-file", str(receiver.shared_fifo("vdl2")), "--sample-format", "S16_LE",
                    "--centerfreq", "136812000", "--oversample", "10"]
    assert "--sdrplay3" not in receiver.vdl2_command(cfg["receivers"]["vdl2"], cfg)


def test_shared_needs_an_rsp_host_and_a_narrow_slice():
    cfg = shared_cfg()
    cfg["receivers"]["acars"]["sdr"] = "rtlsdr"
    with pytest.raises(SystemExit, match="SDRplay RSP"):
        receiver.shared_host("vdl2", cfg["receivers"]["vdl2"], cfg)
    with pytest.raises(SystemExit, match="slice carries"):
        receiver.slice_center({"frequencies": [136.0, 136.975]})
    wide = shared_cfg(frequencies=[136.975, 140.0])  # 129.125-140 is more than one capture
    with pytest.raises(SystemExit, match="more than one RSP capture"):
        receiver.shared_capture_plan("acars", wide["receivers"]["acars"], wide)


def test_unshared_receivers_are_untouched_by_the_tee_code():
    cfg = {**CFG, "receivers": {"acars": rsp(), "vdl2": rsp(frequencies=VDL2_FREQS)}}
    assert receiver.shared_capture_plan("acars", cfg["receivers"]["acars"], cfg) is None
    assert "driver=tee" not in " ".join(receiver.acars_command(cfg["receivers"]["acars"], cfg))
