#!/bin/bash
# ACARS decoder -- dongle, gain, ppm and frequencies are in config.json (receivers.acars).
exec python3 "$(dirname "$0")/receiver.py" acars
