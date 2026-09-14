#!/bin/bash
# VDL2 decoder -- dongle, gain, ppm and frequencies are in config.json (receivers.vdl2).
exec python3 "$(dirname "$0")/receiver.py" vdl2
