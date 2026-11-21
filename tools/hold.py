import sys, time, numpy as np, SoapySDR
from SoapySDR import SOAPY_SDR_RX, SOAPY_SDR_CF32
serial, freq, rate, ifgr, rfgr = sys.argv[1], float(sys.argv[2])*1e6, float(sys.argv[3])*1e6, int(sys.argv[4]), int(sys.argv[5])
dev = SoapySDR.Device("driver=sdrplay,serial="+serial)
dev.setSampleRate(SOAPY_SDR_RX, 0, rate); dev.setBandwidth(SOAPY_SDR_RX, 0, 8e6 if rate>8e6 else 5e6)
dev.setGainMode(SOAPY_SDR_RX, 0, False); dev.setGain(SOAPY_SDR_RX, 0, "IFGR", ifgr); dev.setGain(SOAPY_SDR_RX, 0, "RFGR", rfgr)
dev.setFrequency(SOAPY_SDR_RX, 0, freq)
st = dev.setupStream(SOAPY_SDR_RX, SOAPY_SDR_CF32); dev.activateStream(st)
buf = np.empty(65536, np.complex64); print("HOLDING", flush=True)
while True: dev.readStream(st, [buf], len(buf))
