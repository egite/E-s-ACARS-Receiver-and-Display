#!/bin/bash
# Install the ACARS/VDL2 receiver on Debian / Raspberry Pi OS:
#   packages, the DVB driver blacklist, libacars + acarsdec + dumpvdl2 (built into third_party/),
#   config.json (from config.example.json) and the systemd services.
# Safe to re-run: existing builds and config.json are kept.
#
#   ./install.sh              everything
#   ./install.sh --no-services   build and configure, but don't install/enable systemd services
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
INSTALL_SERVICES=1
[[ "${1:-}" == "--no-services" ]] && INSTALL_SERVICES=0
cd "$PROJECT_DIR"

step() { printf '\n==> %s\n' "$*"; }

step "Installing packages"
sudo apt-get update
sudo apt-get install -y build-essential cmake git pkg-config \
  rtl-sdr librtlsdr-dev libusb-1.0-0-dev \
  libjansson-dev libxml2-dev zlib1g-dev libsqlite3-dev libcjson-dev libglib2.0-dev \
  python3 python3-aiohttp libjs-leaflet

step "Blacklisting the DVB-T TV driver so the dongles are free for SDR use"
if [[ ! -f /etc/modprobe.d/blacklist-rtlsdr.conf ]]; then
  printf 'blacklist dvb_usb_rtl28xxu\nblacklist rtl2832\nblacklist rtl2832_sdr\n' | sudo tee /etc/modprobe.d/blacklist-rtlsdr.conf >/dev/null
  echo "Added /etc/modprobe.d/blacklist-rtlsdr.conf (takes effect after a reboot or replugging the dongles)."
else
  echo "Already blacklisted."
fi

mkdir -p third_party
build() {  # build <name> <git url> <binary to check>
  local name=$1 url=$2 binary=$3
  if [[ -x "third_party/$name/$binary" ]]; then
    echo "$name already built."
    return
  fi
  [[ -d "third_party/$name" ]] || git clone --depth 1 "$url" "third_party/$name"
  rm -rf "third_party/$name/build"
  cmake -S "third_party/$name" -B "third_party/$name/build" -DCMAKE_BUILD_TYPE=Release
  cmake --build "third_party/$name/build" -j"$(nproc)"
}

step "Building libacars"
if ! pkg-config --exists libacars-2; then
  build libacars https://github.com/szpajder/libacars.git build/libacars/libacars-2.so
  sudo cmake --install third_party/libacars/build
  sudo ldconfig
else
  echo "libacars already installed ($(pkg-config --modversion libacars-2))."
fi

step "Building acarsdec"
build acarsdec https://github.com/f00b4r0/acarsdec.git build/acarsdec

step "Building dumpvdl2"
build dumpvdl2 https://github.com/szpajder/dumpvdl2.git build/src/dumpvdl2

step "Creating config.json"
if [[ ! -f config.json ]]; then
  cp config.example.json config.json
  # The distro librtlsdr detaches the DVB driver on its own; point the decoders at it if a second copy exists.
  distro_lib="$(ldconfig -p | awk '/librtlsdr\.so\.0 / && $NF !~ /\/usr\/local\// {print $NF; exit}')"
  if [[ -n "$distro_lib" ]] && ldconfig -p | grep -q '/usr/local/lib/librtlsdr.so.0'; then
    python3 - "$distro_lib" <<'EOF'
import json, sys
path = "config.json"
text = open(path).read().replace('"librtlsdr_preload": ""', f'"librtlsdr_preload": "{sys.argv[1]}"')
json.loads(text)
open(path, "w").write(text)
EOF
  fi
  echo "Created config.json. Edit it: home location, dongle serials, frequencies, VRS URL."
else
  echo "config.json already exists; left unchanged."
fi

if [[ $INSTALL_SERVICES == 1 ]]; then
  step "Installing systemd services (user $RUN_USER)"
  for unit in acars-decoder@.service acars-web.service; do
    sed "s#@PROJECT_DIR@#$PROJECT_DIR#g; s#@USER@#$RUN_USER#g" "systemd/$unit" | sudo tee "/etc/systemd/system/$unit" >/dev/null
  done
  sudo systemctl daemon-reload
  sudo systemctl enable --now acars-web.service acars-decoder@acars.service acars-decoder@vdl2.service
  echo "Services running. Web page: http://$(hostname -I | awk '{print $1}'):$(python3 -c 'import json; print(json.load(open("config.json"))["http_port"])')/"
fi

step "Done"
echo "Next: give each dongle a unique serial and measure its ppm (see README.md)."
