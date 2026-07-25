#!/usr/bin/env python3
"""GLASSDECK installer — personalizes and installs the dashboard on an adsb.im feeder.

Reads the feeder's OWN configuration (read-only) and injects it into the
dashboard template, so every station gets its own name, position, aggregator
list, and a measured range — no hand-editing.

Usage (as root, with glassdeck.template.html + gd_exporter.py in the same dir):
    python3 gd_install.py [--town "Town Name"] [--join | --leave | --rotate]

--join / --leave (optional): feed a copy of your traffic to the GLASSDECK
network aggregator (additive — your existing aggregators are untouched).
Applied THROUGH the adsb.im app's own /expert endpoint, never by editing its
files; briefly restarts the feed containers, exactly like any settings change.

--rotate: mint a new network id and retire the old one, for when the current
one may have been seen by someone else. Coverage history, map alias and Uplink
access all follow the feeder across.

What it touches (and nothing else):
    /opt/adsb/glassdeck/          — persistent copies (this dir)
    /run/adsb-feeder-ultrafeeder/tar1090/{glassdeck.html,gd-data/}  — served copies
    root crontab                  — three tagged lines (system/history/self-heal)
Uninstall: crontab -l | grep -v glassdeck | crontab - ; rm -rf /opt/adsb/glassdeck
"""
import json, math, os, re, socket, subprocess, sys, time, uuid as uuidlib
import urllib.parse, urllib.request

ENV_PATH = "/opt/adsb/config/.env"
BASE = os.path.dirname(os.path.abspath(__file__))
RUN_WEBROOT = "/run/adsb-feeder-ultrafeeder/tar1090"
CONTAINER = "ultrafeeder"

GLASSDECK_HOST = "feed.debeers-labs.xyz"
UUID_FILE = os.path.join(BASE, "UUID")


GLASSDECK_API = "https://globe.debeers-labs.xyz/api/uplink"


def rotate_uuid():
    """Replace this feeder's network id, keeping the standing it has earned.

    Why this exists: the id is a bearer credential — anyone holding it can
    authorise a machine to pull the Uplink as this feeder. Until now it was
    minted once and permanent, so a copy that got out (a pasted config, a shared
    screenshot, a sold SD card) could never be taken back except by banning the
    feeder outright, which would punish the victim.

    The connector is switched FIRST and the network told afterwards, deliberately
    — the reverse order would leave the feeder briefly sending a credential the
    hub had already revoked, and it would stop being counted. Done this way the
    worst case is a rotation the hub never hears about, which costs nothing: the
    new id simply looks like a new feeder, and re-running this repairs it.
    """
    old = feeder_uuid()
    new = str(uuidlib.uuid4())
    if GLASSDECK_HOST not in read_extra_env():
        sys.exit("this feeder is not on the GLASSDECK network — nothing to rotate.\n"
                 "  (join first: sudo python3 gd_install.py --join)")

    print("rotating this feeder's GLASSDECK id…")
    try:
        with open(UUID_FILE + ".prev", "w") as f:
            f.write(old + "\n")
        os.chmod(UUID_FILE + ".prev", 0o600)
    except OSError:
        pass
    with open(UUID_FILE, "w") as f:
        f.write(new + "\n")
    os.chmod(UUID_FILE, 0o600)

    set_network(True)          # rebuilds the connector around the new id

    # Now tell the hub, so the coverage map and the earned eligibility follow
    # the feeder instead of restarting from nothing.
    body = json.dumps({"old": old, "new": new}).encode()
    req = urllib.request.Request(GLASSDECK_API + "/rotate", data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                if json.load(r).get("ok"):
                    print("done — the network moved your coverage and access to the new id.")
                    return
        except Exception as e:
            print("  (attempt %d: %s)" % (attempt + 1, e))
        time.sleep(5)
    print("\nThe id was rotated on this feeder and the connector is using it, but the\n"
          "network did not confirm the change. Nothing is broken — this feeder keeps\n"
          "feeding — but it will appear as a new feeder on the map until it is told.\n"
          "Re-run this command once the network is reachable to repair that.")


def feeder_uuid():
    """This feeder's GLASSDECK id — generated once, then kept forever.

    Without an id on the connector the hub cannot tell one feeder's traffic from
    another's: every position lands in the same bucket, so the network map can
    only ever draw a single coverage shape no matter how many people join.

    Deliberately NOT the uuid this feeder already sends to adsb.lol or
    airplanes.live — reusing one of those would hand our hub the feeder's
    identity on those networks. It is a random id that means nothing anywhere
    else, and the public globe only ever shows an anonymous alias for it.
    """
    try:
        cur = open(UUID_FILE).read().strip()
        if cur:
            return cur
    except OSError:
        pass
    new = str(uuidlib.uuid4())
    with open(UUID_FILE, "w") as f:
        f.write(new + "\n")
    os.chmod(UUID_FILE, 0o600)   # a bearer credential, not world-readable
    return new


def glassdeck_connector():
    return "%s,30004,beast_reduce_plus_out,uuid=%s" % (GLASSDECK_HOST, feeder_uuid())
def web_port():
    """adsb.im app port — 80 on image installs, different on app installs."""
    return read_env(ENV_PATH).get("AF_WEBPORT") or "80"


def tar1090_port(env):
    """Container web port serving tar1090 (and us) — 8080 on images, often 1090 on app installs."""
    return env.get("AF_TAR1090_PORT_ADJUSTED") or env.get("AF_TAR1090_PORT") or "8080"
EXTRA_ENV_KEY = "_ADSBIM_STATE_EXTRA_ENV"

AGG_NAMES = {"adsb.lol": "adsb.lol", "adsb.fi": "adsb.fi", "airplanes.live": "airplanes.live",
             "adsbexchange": "ADSBx", "flyitalyadsb": "FlyItaly", "theairtraffic": "TheAirTraffic"}


def read_env(path):
    env = {}
    try:
        for line in open(path):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


def parse_uplinks(uf_config, joined=False):
    hosts = {}
    for entry in uf_config.split(";"):
        parts = entry.strip().split(",")
        if len(parts) < 2 or parts[0] not in ("adsb", "mlat"):
            continue
        host = parts[1]
        label = next((v for k, v in AGG_NAMES.items() if k in host), host.replace("feed.", ""))
        rec = hosts.setdefault(label, {"name": label, "feed": False, "mlat": False})
        rec[parts[0] if parts[0] == "mlat" else "feed"] = True
    ups = list(hosts.values())
    ups.append({"name": "GLASSDECK", "feed": True} if joined else {"name": "GLASSDECK", "slot": True})
    return ups


# ---------- GLASSDECK network join/leave (through the adsb.im app, never its files) ----------

CONFIG_JSON = "/opt/adsb/config/config.json"


def read_extra_env():
    """Current value of the Expert-page 'extra env' box.

    Newer adsb.im versions persist it in config.json; older ones in .env
    (where a multi-line value spans lines joined by CRLF — continuation lines
    end with \r, real .env lines don't; value ends at the first bare \n)."""
    try:
        val = json.load(open(CONFIG_JSON)).get(EXTRA_ENV_KEY)
        if val is not None:
            return val.strip()
    except (OSError, ValueError):
        pass
    try:
        raw = open(ENV_PATH, newline="").read()
    except OSError:
        return ""
    idx = raw.find(EXTRA_ENV_KEY + "=")
    if idx < 0:
        return ""
    rest = raw[idx + len(EXTRA_ENV_KEY) + 1:]
    end = 0
    while True:
        nl = rest.find("\n", end)
        if nl < 0:
            end = len(rest)
            break
        if nl > 0 and rest[nl - 1] == "\r":
            end = nl + 1
            continue
        end = nl
        break
    return rest[:end].strip()


def post_extra_env(value):
    data = urllib.parse.urlencode({
        "ultrafeeder_extra_env": value,
        "ultrafeeder_extra_env--submit": "go",
    }).encode()
    req = urllib.request.Request("http://127.0.0.1:%s/expert" % web_port(), data=data)
    try:
        urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        # the app often redirects into a restart page that drops the connection — that's fine
        print("  (app response: %s — normal if containers are restarting)" % e)


def set_network(join):
    cur = read_extra_env()
    joined = GLASSDECK_HOST in cur
    want = glassdeck_connector() if join else None
    if join and want in cur:
        print("already feeding the GLASSDECK network — nothing to do")
        return
    if not join and not joined:
        print("not currently feeding the GLASSDECK network — nothing to do")
        return
    if join and joined:
        # an older join carried no uuid, so the hub had no way to tell this
        # feeder apart from any other — re-joining rewrites the entry
        print("repairing the GLASSDECK connector (adding this feeder's id)…")

    lines = [l.strip() for l in re.split(r"\r?\n", cur) if l.strip()]
    if join:
        try:
            socket.gethostbyname(GLASSDECK_HOST)
        except OSError:
            print("  warning: %s does not resolve yet — readsb will keep retrying until it does" % GLASSDECK_HOST)
    # drop any existing GLASSDECK entry, then add the wanted one back on a join.
    # Rebuilding rather than appending keeps a re-join idempotent AND lets it
    # replace a stale entry instead of stacking a second connector to the hub.
    kept, added = [], False
    for l in lines:
        if l.startswith("READSB_NET_CONNECTOR="):
            entries = [e for e in l.split("=", 1)[1].split(";") if GLASSDECK_HOST not in e and e.strip()]
            if join and not added:
                entries.append(want)
                added = True
            if entries:
                kept.append("READSB_NET_CONNECTOR=" + ";".join(entries))
        else:
            kept.append(l)
    if join and not added:
        kept.append("READSB_NET_CONNECTOR=" + want)
    lines = kept

    print("%s the GLASSDECK network (via the feeder's own /expert endpoint)…" % ("joining" if join else "leaving"))
    post_extra_env("\r\n".join(lines))

    for _ in range(45):  # the app rewrites .env, then restarts containers
        time.sleep(2)
        now = GLASSDECK_HOST in read_extra_env()
        if now == join:
            print("confirmed: feeder is %s the GLASSDECK network" % ("feeding" if join else "no longer feeding"))
            return
    print("could not confirm the change — check the adsb.im Expert page")


def measure_range_km():
    """Round the station's real 34-day max range up to a friendly ring scale."""
    try:
        out = subprocess.run(
            ["docker", "exec", CONTAINER, "rrdtool", "xport", "--json", "-s", "-34d", "--step", "14400",
             "DEF:a=/run/collectd/localhost/dump1090-localhost/dump1090_range-max_range.rrd:value:MAX",
             "XPORT:a:v"],
            capture_output=True, text=True, timeout=30)
        vals = [r[0] for r in json.loads(out.stdout).get("data", []) if r[0] is not None]
        if vals:
            return int(min(500, max(150, math.ceil(max(vals) / 1000 / 50) * 50)))
    except Exception as e:
        print("  range measurement failed (%s) — defaulting" % e)
    return 300


def main():
    # town is cosmetic and not in .env, so remember it across updates the same
    # way CHANNEL is: --town writes it, a plain update reuses the stored value
    TOWN_FILE = os.path.join(BASE, "TOWN")
    if "--town" in sys.argv:
        town = sys.argv[sys.argv.index("--town") + 1]
        try:
            open(TOWN_FILE, "w").write(town + "\n")
        except OSError:
            pass
    else:
        try:
            town = open(TOWN_FILE).read().strip()
        except OSError:
            town = ""
    if "--join" in sys.argv:
        set_network(True)
    elif "--leave" in sys.argv:
        set_network(False)
    elif "--rotate" in sys.argv:
        rotate_uuid()

    env = read_env(ENV_PATH)
    station = env.get("MLAT_SITE_NAME") or "MY-FEEDER"
    alt_m = env.get("FEEDER_ALT_M")
    version = (env.get("AF_FEEDER_VERSION") or env.get("AF_FEEDER_INITIAL_VERSION") or "").replace("(stable)", "") or "adsb.im"
    uplinks = parse_uplinks(env.get("FEEDER_ULTRAFEEDER_CONFIG", ""), joined=GLASSDECK_HOST in read_extra_env())
    range_km = measure_range_km()

    def sibling(name, default):
        try:
            return open(os.path.join(BASE, name)).read().strip() or default
        except OSError:
            return default

    config = {
        "station": station, "town": town, "rangeKm": range_km,
        "altM": int(alt_m) if alt_m and alt_m.isdigit() else None,
        "imageVersion": "adsb.im " + version if not version.startswith("adsb.im") else version,
        "uplinks": uplinks,
        "webPort": int(web_port()),
        "gdVersion": sibling("VERSION", "dev"),
        "gdChannel": sibling("CHANNEL", "main"),
    }
    print("detected config:", json.dumps(config, indent=2))

    template = open(os.path.join(BASE, "glassdeck.template.html")).read()
    assert template.count("__GD_CONFIG__") == 1, "template placeholder missing"
    html = template.replace("__GD_CONFIG__", json.dumps(config, separators=(",", ":")))
    out_html = os.path.join(BASE, "glassdeck.html")
    open(out_html, "w").write(html)

    os.makedirs(RUN_WEBROOT + "/gd-data", exist_ok=True)
    subprocess.run(["cp", out_html, RUN_WEBROOT + "/glassdeck.html"], check=True)

    exporter = os.path.join(BASE, "gd_exporter.py")
    subprocess.run(["python3", exporter, "all", RUN_WEBROOT + "/gd-data"], check=True)

    cron_lines = [
        f"* * * * * python3 {exporter} system {RUN_WEBROOT}/gd-data >/dev/null 2>&1",
        f"*/5 * * * * python3 {exporter} history {RUN_WEBROOT}/gd-data >/dev/null 2>&1",
        f"* * * * * mkdir -p {RUN_WEBROOT}/gd-data; cmp -s {out_html} {RUN_WEBROOT}/glassdeck.html || cp {out_html} {RUN_WEBROOT}/glassdeck.html",
    ]
    recorder = os.path.join(BASE, "gd_recorder.py")
    if os.path.exists(recorder):  # optional flight recorder — drop the file in, it records
        cron_lines.append(f"* * * * * python3 {recorder} >/dev/null 2>&1")
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [l for l in existing.splitlines() if "glassdeck" not in l and "gd_exporter" not in l]
    new_tab = "\n".join(kept + cron_lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)

    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip() or "adsb-feeder"
    print(f"\nGLASSDECK installed — open: http://{host}.local:{tar1090_port(env)}/chunks/glassdeck.html")


if __name__ == "__main__":
    main()
