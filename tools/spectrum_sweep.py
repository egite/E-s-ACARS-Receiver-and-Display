# Quick RSP power sweep: python3 tools/spectrum_sweep.py <serial> <start_MHz> <stop_MHz> <out.txt>
# Needs python3-soapysdr + python3-numpy. Fixed gain IFGR 40 / RFGR 4, 8 MHz steps at 10 MS/s.
import sys, time, numpy as np, SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
serial = sys.argv[1]; f0 = float(sys.argv[2])*1e6; f1 = float(sys.argv[3])*1e6
step = 8e6; rate = 10e6; bw = 8e6; nfft = 1024
dev = SoapySDR.Device("driver=sdrplay,serial="+serial)
dev.setSampleRate(SOAPY_SDR_RX, 0, rate); dev.setBandwidth(SOAPY_SDR_RX, 0, bw)
dev.setGainMode(SOAPY_SDR_RX, 0, False)
dev.setGain(SOAPY_SDR_RX, 0, "IFGR", 40); dev.setGain(SOAPY_SDR_RX, 0, "RFGR", 4)
st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32); dev.activateStream(st)
buf = np.empty(65536, np.complex64); win = np.hanning(nfft)
f = f0 + step/2
out = open(sys.argv[4], "w")
while f - step/2 < f1:
    dev.setFrequency(SOAPY_SDR_RX, 0, f); time.sleep(0.05)
    for _ in range(3): dev.readStream(st, [buf], len(buf))   # flush
    acc = np.zeros(nfft); n = 0
    for _ in range(4):
        r = dev.readStream(st, [buf], len(buf))
        if r.ret <= 0: continue
        x = buf[:r.ret]; k = len(x)//nfft
        if k == 0: continue
        seg = x[:k*nfft].reshape(k, nfft) * win
        acc += (np.abs(np.fft.fftshift(np.fft.fft(seg, axis=1), axes=1))**2).mean(0); n += 1
    if n == 0: f += step; continue
    psd = 10*np.log10(acc/n + 1e-20)
    freqs = f + np.fft.fftshift(np.fft.fftfreq(nfft, 1/rate))
    keep = np.abs(freqs - f) <= bw/2*0.95
    for fr, p in zip(freqs[keep], psd[keep]): out.write(f"{fr/1e6:.4f} {p:.1f}\n")
    f += step
dev.deactivateStream(st); dev.closeStream(st)
