#!/bin/bash
# ACARS/VDL2 web display -- settings in config.json. Receives JSON from run_acars.sh and run_vdl2.sh.
cd "$(dirname "$0")/web"
exec python3 server.py
