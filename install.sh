#!/bin/bash
# GLASSDECK installer — a modern dashboard for adsb.im / ultrafeeder Pi feeders.
#
#   curl -sL https://raw.githubusercontent.com/debeers-labs/glassdeck/main/install.sh | sudo bash
#
# What it does (and ALL it does):
#   * downloads three files into /opt/adsb/glassdeck/
#   * reads your feeder's own config to personalise the dashboard (read-only)
#   * adds three root cron lines (data export + self-heal)
# It does NOT modify containers, tar1090, graphs1090, or the adsb.im UI in any way.
# Uninstall: /opt/adsb/glassdeck/uninstall.sh
set -euo pipefail

REPO="${GLASSDECK_REPO:-debeers-labs/glassdeck}"
BRANCH="${GLASSDECK_BRANCH:-main}"        # release channel: main (default) or beta
ARGS=()
while [ $# -gt 0 ]; do                    # --channel beta selects the beta branch;
  case "$1" in                            # everything else passes through to gd_install.py
    --channel) BRANCH="$2"; shift 2 ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
RAW="https://raw.githubusercontent.com/${REPO}/${BRANCH}"
DEST=/opt/adsb/glassdeck

if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi
if [ ! -f /opt/adsb/config/.env ]; then
  echo "this doesn't look like an adsb.im feeder (/opt/adsb/config/.env missing)"; exit 1
fi

echo "GLASSDECK: downloading…"
mkdir -p "$DEST"
curl -sfL "$RAW/glassdeck.template.html" -o "$DEST/glassdeck.template.html"
curl -sfL "$RAW/agent/gd_exporter.py"    -o "$DEST/gd_exporter.py"
curl -sfL "$RAW/agent/gd_install.py"     -o "$DEST/gd_install.py"
curl -sfL "$RAW/uninstall.sh"            -o "$DEST/uninstall.sh"
curl -sfL "$RAW/VERSION"                 -o "$DEST/VERSION" || true
printf '%s\n' "$BRANCH" > "$DEST/CHANNEL"
chmod +x "$DEST/gd_exporter.py" "$DEST/gd_install.py" "$DEST/uninstall.sh"

echo "GLASSDECK: personalising from your feeder's config…"
python3 "$DEST/gd_install.py" "${ARGS[@]}"
