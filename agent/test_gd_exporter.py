#!/usr/bin/env python3
"""gd_exporter, against the shipped agent file.

The bug this pins: the exporter called adsb.im's API on a hardcoded port 80.
On an "app install" adsb.im listens on AF_WEBPORT instead, so every one of those
calls failed and system.json published `base: null` / `uplinks: {}` forever —
which the deck reads as "no feeder link", so Save is permanently dead and the
uplink badges freeze. Nothing on the page admits it.

So the assertion below is on the PUBLISHED FILE, not on the URL the code builds:
the only thing that proves the port was honoured is that the data arrived. The
stub answers on the configured port and refuses everything else, exactly as a
real app install does.

Run:  python3 test_gd_exporter.py
"""
import io, json, os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gd_exporter

fails = 0


def assert_(name, cond, val=None):
    global fails
    if cond:
        print("ok  ", name, "" if val is None else "→ " + str(val))
    else:
        print("FAIL", name, "→", val)
        fails += 1


APP_PORT = "1099"   # what an adsb.im app install typically ends up on

# A feeder's /proc, /sys and config, minus the host. Only enough for
# export_system() to reach the HTTP calls; the values are not under test.
FAKE_FILES = {
    "/sys/class/thermal/thermal_zone0/temp": "41200\n",
    "/proc/loadavg": "0.31 0.20 0.15 1/210 900\n",
    "/proc/meminfo": "MemTotal:  1000000 kB\nMemAvailable: 400000 kB\n",
    "/proc/net/dev": "  eth0: 1234 0 0 0 0 0 0 0 5678 0\n",
    "/proc/uptime": "86400.00 80000.00\n",
    gd_exporter.ENV_PATH: (
        "AF_WEBPORT=%s\n"
        "FEEDER_ULTRAFEEDER_CONFIG=adsb,feed.adsb.lol,30004,beast_reduce_plus_out\n"
        % APP_PORT),
}


def fake_open(path, *a, **kw):
    if path in FAKE_FILES:
        return io.StringIO(FAKE_FILES[path])
    raise OSError("no such file (test): %s" % path)


class FakeStatvfs:
    f_bavail, f_blocks = 700, 1000


def run_export(port_that_answers):
    """Export once, with adsb.im reachable ONLY on `port_that_answers`."""
    seen = []

    def fake_http_json(url, timeout=4):
        seen.append(url)
        if "127.0.0.1:%s/" % port_that_answers not in url:
            return None          # nothing is listening there — connection refused
        if url.endswith("/api/base_info"):
            return {"lat": "0.0", "lon": "0.0", "alt": "10", "site_name": "test"}
        return {"0": {"beast": "connected", "mlat": "ok"}}

    # a module global named `open` shadows the builtin for that module only, so
    # this stubs the exporter's filesystem without touching the test's own
    real_statvfs, real_http = os.statvfs, gd_exporter.http_json
    gd_exporter.open = fake_open
    os.statvfs = lambda p: FakeStatvfs()
    gd_exporter.http_json = fake_http_json
    try:
        out = tempfile.mkdtemp()
        gd_exporter.export_system(out)
        with open(os.path.join(out, "system.json")) as f:
            return json.load(f), seen
    finally:
        del gd_exporter.open
        os.statvfs = real_statvfs
        gd_exporter.http_json = real_http


# --- the app install: adsb.im answers on AF_WEBPORT and nowhere else ---------
sysj, urls = run_export(APP_PORT)

assert_("the export reached adsb.im at all", len(urls) > 0, "%d calls" % len(urls))
assert_("base_info arrives on an app install (not null)",
        isinstance(sysj.get("base"), dict) and sysj["base"].get("site_name") == "test",
        sysj.get("base"))
assert_("uplink status arrives for every aggregator adsb.im reports",
        sorted(sysj.get("uplinks") or {}) == sorted(gd_exporter.AGG_STATUS_KEYS),
        sorted(sysj.get("uplinks") or {}))
assert_("...and carries the beast/mlat pair the badges render",
        # `all()` over an empty dict is True — require the rows first, or this
        # is exactly the vacuous assertion the port bug already hid behind
        bool(sysj.get("uplinks")) and
        all(u.get("beast") and u.get("mlat") for u in sysj["uplinks"].values()),
        sysj.get("uplinks"))
assert_("nothing was still aimed at the hardcoded port 80",
        not any("127.0.0.1:80/" in u for u in urls),
        [u for u in urls if "127.0.0.1:80/" in u] or "none")

# --- the image install: AF_WEBPORT absent must still mean 80 ----------------
FAKE_FILES[gd_exporter.ENV_PATH] = \
    "FEEDER_ULTRAFEEDER_CONFIG=adsb,feed.adsb.lol,30004,beast_reduce_plus_out\n"
sysj80, urls80 = run_export("80")
assert_("an image install with no AF_WEBPORT still reaches port 80",
        isinstance(sysj80.get("base"), dict) and bool(sysj80.get("uplinks")),
        sysj80.get("base"))

print("\n" + ("%d FAILED" % fails if fails else "ALL GD_EXPORTER TESTS PASSED"))
sys.exit(1 if fails else 0)
