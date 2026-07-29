#!/bin/bash
# GLASSDECK uninstaller — removes GLASSDECK's own files, cron lines and pages.
#
# It cannot remove the network join. That join is a connector line in the
# feeder's OWN config, written through the adsb.im app by --join, and only the
# app may write it back out again (--leave, or the Expert page by hand). So this
# script is not "returns the feeder to how it was": it deletes the tool that
# undoes the one thing it leaves behind, and it deletes the id the network knows
# this feeder by. Both of those are said out loud below, before anything is
# removed, because after the fact there is no undo for either.
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then echo "run with sudo"; exit 1; fi

JOINED=0
if grep -q "debeers-labs" /opt/adsb/config/config.json /opt/adsb/config/.env 2>/dev/null; then
  JOINED=1
fi

if [ "$JOINED" = 1 ] || [ -f /opt/adsb/glassdeck/UUID ]; then
  echo
  echo "before you do this — what this uninstaller cannot undo:"
fi

if [ "$JOINED" = 1 ]; then
  echo
  echo "* This feeder is feeding the GLASSDECK network, and removing these files does"
  echo "  NOT stop that. The connector keeps sending, and since Uplink access lapses"
  echo "  only after 7 days WITHOUT feeding, its access never lapses either."
fi

if [ -f /opt/adsb/glassdeck/UUID ]; then
  echo
  echo "* This deletes /opt/adsb/glassdeck/UUID — the id the network knows this feeder"
  echo "  by. Reinstalling without it mints a different id: coverage history, map alias"
  echo "  and Uplink access all start over as a new feeder, and nothing can merge the"
  echo "  two back together afterwards."
  if [ "$JOINED" = 1 ]; then
    echo "  While the connector stays in place the id is also recoverable from it — a"
    echo "  reinstall reads it back out and keeps the same identity. Once the connector"
    echo "  is gone, the copy you make below is the only one left."
  fi
fi

if [ "$JOINED" = 1 ] || [ -f /opt/adsb/glassdeck/UUID ]; then
  echo
  echo "The clean exit — all of it before this script deletes the files:"
  N=1
  if [ -f /opt/adsb/glassdeck/UUID ]; then
    echo "  $N. sudo cp /opt/adsb/glassdeck/UUID ~/glassdeck-uuid.bak   # keeps your id"
    N=$((N + 1))
  fi
  if [ "$JOINED" = 1 ]; then
    echo "  $N. sudo python3 /opt/adsb/glassdeck/gd_install.py --leave  # stops the feed"
    N=$((N + 1))
  fi
  echo "  $N. sudo /opt/adsb/glassdeck/uninstall.sh                   # then this"
  echo
fi

echo "removing GLASSDECK cron lines…"
( crontab -l 2>/dev/null | grep -v glassdeck | grep -v gd_exporter ) | crontab - || true

echo "removing served files…"
rm -f  /run/adsb-feeder-ultrafeeder/tar1090/glassdeck.html
rm -rf /run/adsb-feeder-ultrafeeder/tar1090/gd-data

echo "removing /opt/adsb/glassdeck…"
rm -rf /opt/adsb/glassdeck

echo
echo "GLASSDECK removed: its files, its cron lines and its served pages are gone."
if [ "$JOINED" = 1 ]; then
  echo "The feed.debeers-labs.xyz connector was NOT touched — this feeder is STILL"
  echo "feeding the GLASSDECK network under its existing id. Remove that connector on"
  echo "the adsb.im Expert page to stop; access lapses 7 days after it stops arriving."
else
  echo "The feeder's own configuration was not modified."
fi
