#!/usr/bin/env python3
"""GLASSDECK installer — personalizes and installs the dashboard on an adsb.im feeder.

Reads the feeder's OWN configuration (read-only) and injects it into the
dashboard template, so every station gets its own name, position, aggregator
list, and a measured range — no hand-editing.

Usage (as root, with glassdeck.template.html + gd_exporter.py in the same dir):
    python3 gd_install.py [--town "Town Name"] [--join | --leave]

--join / --leave (optional): feed a copy of your traffic to the GLASSDECK
network aggregator (additive — your existing aggregators are untouched).
Applied THROUGH the adsb.im app's own /expert endpoint, never by editing its
files; briefly restarts the feed containers, exactly like any settings change.

What it touches (and nothing else):
    /opt/adsb/glassdeck/          — persistent copies (this dir)
    /run/adsb-feeder-ultrafeeder/tar1090/{glassdeck.html,gd-data/}  — served copies
    root crontab                  — three tagged lines (system/history/self-heal)
Uninstall: crontab -l | grep -v glassdeck | crontab - ; rm -rf /opt/adsb/glassdeck
"""
import json, math, os, re, socket, subprocess, sys, time
import urllib.parse, urllib.request

ENV_PATH = "/opt/adsb/config/.env"
BASE = os.path.dirname(os.path.abspath(__file__))
RUN_WEBROOT = "/run/adsb-feeder-ultrafeeder/tar1090"
CONTAINER = "ultrafeeder"

GLASSDECK_HOST = "feed.debeers-labs.xyz"
GLASSDECK_CONNECTOR = GLASSDECK_HOST + ",30004,beast_reduce_plus_out"
EXPERT_URL = "http://127.0.0.1:80/expert"
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
    req = urllib.request.Request(EXPERT_URL, data=data)
    try:
        urllib.request.urlopen(req, timeout=120)
    except Exception as e:
        # the app often redirects into a restart page that drops the connection — that's fine
        print("  (app response: %s — normal if containers are restarting)" % e)


def set_network(join):
    cur = read_extra_env()
    joined = GLASSDECK_HOST in cur
    if join and joined:
        print("already feeding the GLASSDECK network — nothing to do")
        return
    if not join and not joined:
        print("not currently feeding the GLASSDECK network — nothing to do")
        return

    lines = [l.strip() for l in re.split(r"\r?\n", cur) if l.strip()]
    if join:
        try:
            socket.gethostbyname(GLASSDECK_HOST)
        except OSError:
            print("  warning: %s does not resolve yet — readsb will keep retrying until it does" % GLASSDECK_HOST)
        merged = False
        for i, l in enumerate(lines):
            if l.startswith("READSB_NET_CONNECTOR="):
                lines[i] = l + ";" + GLASSDECK_CONNECTOR
                merged = True
                break
        if not merged:
            lines.append("READSB_NET_CONNECTOR=" + GLASSDECK_CONNECTOR)
    else:
        kept = []
        for l in lines:
            if l.startswith("READSB_NET_CONNECTOR="):
                entries = [e for e in l.split("=", 1)[1].split(";") if GLASSDECK_HOST not in e and e.strip()]
                if entries:
                    kept.append("READSB_NET_CONNECTOR=" + ";".join(entries))
            else:
                kept.append(l)
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
    town = ""
    if "--town" in sys.argv:
        town = sys.argv[sys.argv.index("--town") + 1]
    if "--join" in sys.argv:
        set_network(True)
    elif "--leave" in sys.argv:
        set_network(False)

    env = read_env(ENV_PATH)
    station = env.get("MLAT_SITE_NAME") or "MY-FEEDER"
    alt_m = env.get("FEEDER_ALT_M")
    version = (env.get("AF_FEEDER_VERSION") or env.get("AF_FEEDER_INITIAL_VERSION") or "").replace("(stable)", "") or "adsb.im"
    uplinks = parse_uplinks(env.get("FEEDER_ULTRAFEEDER_CONFIG", ""), joined=GLASSDECK_HOST in read_extra_env())
    range_km = measure_range_km()

    config = {
        "station": station, "town": town, "rangeKm": range_km,
        "altM": int(alt_m) if alt_m and alt_m.isdigit() else None,
        "imageVersion": "adsb.im " + version if not version.startswith("adsb.im") else version,
        "uplinks": uplinks,
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
    existing = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [l for l in existing.splitlines() if "glassdeck" not in l and "gd_exporter" not in l]
    new_tab = "\n".join(kept + cron_lines) + "\n"
    subprocess.run(["crontab", "-"], input=new_tab, text=True, check=True)

    host = subprocess.run(["hostname"], capture_output=True, text=True).stdout.strip() or "adsb-feeder"
    print(f"\nGLASSDECK installed — open: http://{host}.local:8080/chunks/glassdeck.html")


if __name__ == "__main__":
    main()
