# GLASSDECK changelog

All notable changes to the feeder dashboard. Update = re-run the install
one-liner; your preferences live in your browser and survive.

## v0.3.0 — 2026-07-17

- Software card in Settings: current version, one-click update check with
  release notes (user-initiated — the dashboard never phones home on its own),
  and copy-paste update / uninstall commands.
- Version stamp in the page footer.
- Release channels: `install.sh --channel beta` (or `GLASSDECK_BRANCH=beta`)
  installs from the beta branch; default stays main.

## v0.2.0 — 2026-07-16

- Free-flight camera: the rings are the ground, an invisible ceiling rides
  just above your highest contact — drag to look, scroll to fly along your
  gaze, right-drag to pan; Sky view lands you at the antenna, Home returns
  to the overview. The sky widens as you look up.
- km/nm units toggle (Settings → GLASSDECK preferences); "Reach" renamed
  "Range".
- Density-aware plane/label sizing, icon-size preference, and a true-scale
  "Size 1:1" toggle.
- Port auto-detection for app installs (AF_WEBPORT + AF_TAR1090_PORT_ADJUSTED)
  — deep links now work on non-default ports.
- History-chart lightbox with per-series toggles.

## v0.1.0 — 2026-07-15

- First public release: holographic deck, instruments, settings; installer
  that personalises from the feeder's own config and measures real range;
  GLASSDECK network join/leave (`--join` / `--leave`).
