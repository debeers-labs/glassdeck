#!/bin/bash
# GLASSDECK uninstaller — returns the feeder to exactly how it was.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi

# the network join lives in the feeder's own config (made via --join), so
# removing GLASSDECK's files does not stop the feed — say so while --leave
# is still available
if grep -q "debeers-labs" /opt/adsb/config/config.json /opt/adsb/config/.env 2>/dev/null; then
  echo "note: this feeder still feeds the GLASSDECK network. To stop that too, first run:"
  echo "  sudo python3 /opt/adsb/glassdeck/gd_install.py --leave"
  echo "(or remove the feed.debeers-labs.xyz connector on the adsb.im Expert page later)"
fi

echo "removing GLASSDECK cron lines…"
( crontab -l 2>/dev/null | grep -v glassdeck | grep -v gd_exporter ) | crontab - || true

echo "removing served files…"
rm -f  /run/adsb-feeder-ultrafeeder/tar1090/glassdeck.html
rm -rf /run/adsb-feeder-ultrafeeder/tar1090/gd-data

echo "removing /opt/adsb/glassdeck…"
rm -rf /opt/adsb/glassdeck

echo "GLASSDECK removed. Nothing else was ever modified."
