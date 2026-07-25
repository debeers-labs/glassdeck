# GLASSDECK changelog

All notable changes to the feeder dashboard. Update = re-run the install
one-liner; your preferences live in your browser and survive.

## v0.5.0 — 2026-07-25 (beta)

- Settings gains a **GLASSDECK network** card: whether this feeder is on the
  shared globe, what joining does, and the join/leave command with a copy
  button. Joining is additive and reversible — your existing aggregators are
  never touched.
- **Uplink**: feeders on the network can pull every feeder's traffic back as
  one stream, far past what one antenna hears. The card links to the setup
  guide, including the warning that it must never go into the instance that
  feeds your aggregators.
- A one-time invitation on first load if the feeder isn't on the network.
  Dismissing it is permanent — the Settings card carries the same offer.
- The dashboard now learns whether it's feeding the network **live**, so the
  card and the invitation follow a join within a minute instead of waiting for
  the next update. The read-only "GLASSDECK network" tick under Data sharing
  now explains itself instead of looking broken.

## v0.4.0 — 2026-07-24 (beta)

- Load-time intro (beta channel only): a holographic globe forms, finds this
  feeder, spins it into view, then lifts the feeder's own landmass off the
  globe and lays it flat on the table. Live contacts ride down onto the map,
  the beams raise them to their real altitudes, and they become aircraft as
  they climb; the measured-range outline draws itself in and hands off into
  the normal deck. Click or press any key to skip; plays once per browser
  session; sits out entirely when the OS "reduce motion" setting is on.
- Deck view: drag now spins the table on its axis (beta channel), like turning
  a globe, instead of only turning your head.
- Settings → Software: switch between stable and beta channels with copy-paste
  commands and step-by-step help; an automatic update check that badges the
  Settings tab when a newer build is on your channel; and a "Report it on
  GitHub" button that opens a pre-filled issue (version + channel only — never
  your location).
- Stable channel is byte-identical to v0.3.0 apart from the channel flag: none
  of the above runs unless the feeder is on the beta channel.

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
