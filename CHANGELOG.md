# GLASSDECK changelog

All notable changes to the feeder dashboard. Update = re-run the install
one-liner; your preferences live in your browser and survive.

## v0.6.1 — 2026-07-29

An audit of the whole codebase, and the fixes it found. Nothing here changes how
the dashboard looks; several things change whether it was telling you the truth.

- **`--rotate` could report success without rotating anything.** Its
  confirmation checked that the network host appeared in your config — already
  true before the change, since the old connector is right there. So it said
  "confirmed" whether or not the new id ever landed, then told the network to
  retire a credential your feeder was still sending. Seven days later that
  retirement expired and the id you asked to retire came quietly back. It now
  confirms the connector it actually built, and a failed change restores your
  working id instead of leaving you half-rotated.
- **A failed rotation could not be repaired.** The new id was written before the
  network was told, so re-running retired the wrong one. The old id is now kept
  until the network confirms, and a later run finishes the job.
- **The demo build published enough to locate the real feeder.** Sample
  coordinates were shifted, but the distance-to-receiver on each contact was
  not, and the two together solve for the true position. Those are now derived
  from the shifted coordinates, and the timestamps are shifted too.
- **The exporter assumed adsb.im runs on port 80**, so on an app install every
  call to it failed silently — a permanently dead Save button and frozen
  aggregator badges, with nothing on the page saying why. It now reads the port
  the way the installer already did.
- **The dashboard could freeze on the last good data and still call itself
  live.** It now says when what you are looking at is stale.
- A feeder whose network interface is not named `wlan0` or `eth0` no longer
  breaks the live refresh entirely.
- `uninstall.sh` no longer claims nothing was modified while deleting your
  network identity, and explains how to keep it.
- First tests for the installer itself — the file that runs as root on your
  feeder had none.

## v0.6.0 — 2026-07-26

**Everything the beta channel has been carrying since v0.3 is now on stable.**
No new features in this release — it is the promotion. If you have been running
stable, this is nine days of work arriving at once, and the sections below spell
it out. The load-time intro and the spin-the-table drag stay beta-only.

The short version of what changes on a stable feeder:

- **Settings tells the truth now.** Rows that looked like switches but were
  decoration are gone: every aggregator row, the MLAT privacy tick and the SDR
  gain box are read from your feeder each minute and are read-only, with links
  to the pages that actually own them. Station name, position and altitude stay
  editable, because those genuinely apply.
- **The GLASSDECK network**, if you want it — a shared globe of what feeders can
  see, joined with one command, additive to whatever you already feed, and
  reversible. Plus **Uplink**: feed the network and you can pull all of it back
  as one stream.
- **Your network id can be replaced** if it ever leaks, carrying your coverage
  and access across.
- **`--check`** probes everything this dashboard assumes about your feeder image
  and reports what still holds. Worth running after any adsb.im update.
- **Routes on the contact panel** — click a plane and see where the flight is
  going, via adsb.im's own routeset service.

## v0.5.3 — 2026-07-26 (beta)

- **Click a plane and see where it's going.** The contact panel now shows the
  flight's route under the callsign — "Colombo -> Melbourne", "Perth -> Port
  Hedland" — from adsb.im's own routeset service, which Dirk built and hosts.
  Your receiver hears a callsign, never a route, so this is the one line on that
  panel that isn't your own measurement: when the service is unsure the route
  matches where the aircraft actually is, it shows nothing rather than guess.
  One lookup per flight, cached; the browser asks, not the feeder, so a feeder
  with no internet simply shows no route line.

## v0.5.2 — 2026-07-26 (beta)

- **The SDR gain box did nothing.** It was editable, it was filled in from your
  receiver, and Save ignored it — then reported "applied by the feeder" anyway,
  because the other fields on that card had gone through. It is now read-only,
  showing your live gain, and points at the feeder's own SDR page where gain is
  actually set. Same fault as the data-sharing ticks in v0.5.1, one card over.
- The note beside Save now says what Save actually submits — station name,
  position and altitude — instead of implying it covers the whole screen.

## v0.5.1 — 2026-07-26 (beta)

- **Your network id can now be replaced** if it ever gets out — a pasted config,
  a screenshot, a sold SD card. `sudo python3 /opt/adsb/glassdeck/gd_install.py
  --rotate` retires the old id and mints a new one, carrying your coverage on
  the globe, your place in the network and your Uplink access across. The old id
  stops working at once. Until now the id was permanent, so a copy that escaped
  could never be taken back except by cutting the feeder off entirely.
- The id file is no longer world-readable on the feeder, and uninstalling now
  warns you before it destroys the id (reinstalling mints a new one, which the
  network reads as a different feeder).
- **A compatibility check for after feeder-image updates**: `sudo python3
  /opt/adsb/glassdeck/gd_install.py --check` probes the thirteen things this
  dashboard assumes about your feeder — config paths, status endpoints, the
  container name, the RRD archives, the webroot, cron, the network join — and
  prints what still holds. Some breakages announce themselves; others just make
  live data quietly disappear, and this catches those.
- The Data sharing card now says whether it is showing **live** data or the
  install-time snapshot, in amber when it has fallen back. It could previously
  degrade to stale values with nothing to show it had.
- Commands in Settings no longer wrap mid-path or get clipped — they were
  readable, then briefly truncated, and are now shown in full at any width.

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
  the next update.
- **Data sharing now shows what the feeder is actually doing.** Those rows were
  static markup: the aggregators were hardcoded as ticked whether or not you fed
  them, four of the five ticks moved when clicked and changed nothing, and the
  one honest row was the only one that looked broken. Every row is now read from
  the feeder each minute — your real aggregators, whether each gets MLAT, and
  your real MLAT privacy setting — and all of them are read-only, with a link to
  the feeder's own settings page for changing them.

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
