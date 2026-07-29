#!/usr/bin/env python3
"""GLASSDECK installer — personalizes and installs the dashboard on an adsb.im feeder.

Reads the feeder's OWN configuration (read-only) and injects it into the
dashboard template, so every station gets its own name, position, aggregator
list, and a measured range — no hand-editing.

Usage (as root, with glassdeck.template.html + gd_exporter.py in the same dir):
    python3 gd_install.py [--town "Town Name"] [--join | --leave | --rotate | --check]

--join / --leave (optional): feed a copy of your traffic to the GLASSDECK
network aggregator (additive — your existing aggregators are untouched).
Applied THROUGH the adsb.im app's own /expert endpoint, never by editing its
files; briefly restarts the feed containers, exactly like any settings change.

--rotate: mint a new network id and retire the old one, for when the current
one may have been seen by someone else. Coverage history, map alias and Uplink
access all follow the feeder across.

--check: probe every assumption this dashboard makes about the feeder image —
config paths, HTTP endpoints, the container name, RRD archives, the webroot —
and report what still holds. Run it after a feeder image update: some breakages
announce themselves, others just make live data quietly disappear.

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
    hub had already revoked, and it would stop being counted. The switch is now
    CONFIRMED before the hub is told, so "the hub never heard" is the only
    failure left, and UUID.prev plus the retry below is what closes that.
    """
    cur = read_extra_env()
    if cur is None:
        sys.exit("cannot read the Expert extra-env box — this feeder's join state is\n"
                 "unknown, so nothing was rotated. Run: sudo python3 gd_install.py --check")
    if GLASSDECK_HOST not in cur:
        sys.exit("this feeder is not on the GLASSDECK network — nothing to rotate.\n"
                 "  (join first: sudo python3 gd_install.py --join)")
    old = feeder_uuid()
    new = str(uuidlib.uuid4())

    # An earlier --rotate may have switched the connector and then failed to
    # reach the hub. Finish that first: without this, `old` below is the id from
    # the LAST rotation, so the genuinely leaked one is never named to the hub
    # again and the "re-run this" advice can never do what it promises.
    try:
        pending = open(UUID_FILE + ".prev").read().strip()
    except OSError:
        pending = ""
    if pending and pending != old:
        print("an earlier rotation never reached the network — retiring %s… first" % pending[:8])
        if tell_hub(pending, old):
            print("  retired.")
            try:
                os.remove(UUID_FILE + ".prev")
            except OSError:
                pass
        else:
            print("  still unreachable; it stays valid until it lapses on its own.")

    print("rotating this feeder's GLASSDECK id…")
    # Keep the outgoing id BEFORE anything overwrites it. If the rest of this
    # fails, `--rotate` re-run must still know which credential it was trying to
    # retire; it used to read UUID_FILE, which by then held the new one, so the
    # "just re-run it" advice below could never retire the leaked id at all.
    try:
        with open(UUID_FILE + ".prev", "w") as f:
            f.write(old + "\n")
        os.chmod(UUID_FILE + ".prev", 0o600)
    except OSError:
        pass

    with open(UUID_FILE, "w") as f:
        f.write(new + "\n")
    os.chmod(UUID_FILE, 0o600)

    # The connector must be PROVEN to carry the new id before the hub is told to
    # retire the old one. Otherwise the hub revokes a credential this feeder is
    # still sending, the feeder silently stops being counted, and seven days
    # later prune() expires the revocation and quietly re-legitimises the very
    # id the user asked to retire.
    if not set_network(True):
        with open(UUID_FILE, "w") as f:     # put the working id back
            f.write(old + "\n")
        os.chmod(UUID_FILE, 0o600)
        sys.exit("\nThe feeder's config was NOT changed, so nothing has been rotated and your\n"
                 "old id is still in use and still valid. Check the adsb.im Expert page,\n"
                 "then run this again.")

    # Only now: the connector demonstrably carries the new id, so retiring the
    # old one cannot orphan a feeder that is still using it.
    if tell_hub(old, new):
        print("done — the network moved your coverage and access to the new id.")
        try:
            os.remove(UUID_FILE + ".prev")   # nothing left owing
        except OSError:
            pass
        return
    print("\nThis feeder is now using its NEW id and keeps feeding normally, but the\n"
          "network was not told, so the OLD id has NOT been retired and stays valid\n"
          "until it lapses on its own (7 days after this feeder last sent it).\n"
          "The old id is kept at %s.prev — run --rotate again when the network is\n"
          "reachable and it will retire it then." % UUID_FILE)


def tell_hub(old, new):
    """Ask the network to retire `old` in favour of `new`. True only on success."""
    body = json.dumps({"old": old, "new": new}).encode()
    req = urllib.request.Request(GLASSDECK_API + "/rotate", data=body,
                                 headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=15) as r:
                return bool(json.load(r).get("ok"))
        except urllib.error.HTTPError as e:
            # A refusal is a decision, not a blip — retrying cannot change it.
            print("  the network refused the rotation (HTTP %s)" % e.code)
            return False
        except Exception as e:
            print("  (attempt %d: %s)" % (attempt + 1, e))
            if attempt < 2:
                time.sleep(5)
    return False


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
    # No id on file — but that does not mean this feeder has no id. uninstall.sh
    # deletes UUID and cannot delete the connector (that lives in the feeder's
    # own config), so after uninstall+reinstall the Pi is still SENDING id X
    # while this file has forgotten it. Minting a fresh one there forks the
    # identity permanently: the hub keeps counting X, the new id starts from
    # nothing, and --rotate cannot merge them because it would offer the hub an
    # `old` it never issued. The wire is the authority; adopt what it carries.
    new = uuid_from_connector()
    if new:
        print("recovered this feeder's GLASSDECK id from its own connector (%s…)" % new[:8])
    else:
        new = str(uuidlib.uuid4())
    with open(UUID_FILE, "w") as f:
        f.write(new + "\n")
    os.chmod(UUID_FILE, 0o600)   # a bearer credential, not world-readable
    return new


def uuid_from_connector():
    """The id this feeder is currently SENDING, read back out of its connector.

    Only ids on a GLASSDECK entry count — the same box may carry uuid= for other
    aggregators, and adopting one of those would hand our hub the feeder's
    identity on that network."""
    cur = read_extra_env()
    if not cur:
        return ""
    for entry in re.split(r"[;\r\n]+", cur):
        if GLASSDECK_HOST in entry:
            m = re.search(r"uuid=([0-9a-fA-F-]{36})", entry)
            if m:
                return m.group(1)
    return ""


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
    """Current value of the Expert-page 'extra env' box — or None if unreadable.

    Newer adsb.im versions persist it in config.json; older ones in .env
    (where a multi-line value spans lines joined by CRLF — continuation lines
    end with \r, real .env lines don't; value ends at the first bare \n).

    None and "" must stay distinguishable, because set_network() does not append
    to this box — it REBUILDS it from what this returns. Reporting an unreadable
    store as an empty box would post a single READSB_NET_CONNECTOR line over the
    user's other Expert settings (custom readsb options, gain overrides, other
    connectors), as root, with no backup. The key has already moved store once,
    so "I cannot find it" is a live possibility, not a theoretical one."""
    doc = None
    try:
        doc = json.load(open(CONFIG_JSON))
    except (OSError, ValueError):
        pass
    if doc is not None:
        val = doc.get(EXTRA_ENV_KEY)
        if val is not None:
            return val.strip()
    try:
        raw = open(ENV_PATH, newline="").read()
    except OSError:
        return None
    idx = raw.find(EXTRA_ENV_KEY + "=")
    if idx < 0:
        # Absent from a config.json we could read AND from .env is a genuinely
        # empty box. Absent from .env after config.json failed to parse says
        # nothing about the box at all — the value may be sitting in the file we
        # could not read, so refuse rather than guess.
        return "" if doc is not None else None
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
    if cur is None:
        # Everything below rebuilds the box and posts the result. With no
        # trustworthy "before" there is nothing to rebuild FROM, and posting
        # anyway would wipe whatever the user has in there.
        print("cannot read the Expert extra-env box (%s / %s) — refusing to rewrite it.\n"
              "  Nothing was changed. Run: sudo python3 gd_install.py --check"
              % (CONFIG_JSON, ENV_PATH))
        return False
    joined = GLASSDECK_HOST in cur
    want = glassdeck_connector() if join else None
    if join and want in cur:
        print("already feeding the GLASSDECK network — nothing to do")
        return True
    if not join and not joined:
        print("not currently feeding the GLASSDECK network — nothing to do")
        return True
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
        cur = read_extra_env()
        if cur is None:
            # This loop runs DURING the app's rewrite of config.json, which is
            # the likeliest moment in the whole program to catch a torn write —
            # and on a newer image a half-written config.json plus a .env that
            # never carried the key reads as None. Unreadable is "not yet", not
            # "confirmed"; falling through to `want in cur` raised TypeError out
            # of set_network, and on --rotate that skipped the rollback below it
            # and left UUID holding an id the feeder was never sending. If it
            # never becomes readable the loop ends and returns False, unchanged.
            continue
        # Confirm what was ASKED FOR, not something that was already true. This
        # used to test `GLASSDECK_HOST in cur`, which on a --rotate is already
        # satisfied by the OLD connector before the write — so it announced
        # "confirmed" on the first tick whether or not the new uuid ever landed,
        # and --rotate went on to tell the hub to retire a credential the feeder
        # was still sending. If a confirmation's predicate could have been
        # evaluated BEFORE the write, it is not a confirmation.
        if (want in cur) if join else (GLASSDECK_HOST not in cur):
            print("confirmed: feeder is %s the GLASSDECK network" % ("feeding" if join else "no longer feeding"))
            return True
    print("could not confirm the change — check the adsb.im Expert page")
    return False


# ---------------- --check: is every assumption we make about the image still true?
# GLASSDECK reads and writes a handful of things that belong to the feeder image:
# config files, HTTP endpoints, a container name, RRD paths, a tmpfs webroot.
# None of those are contracts — they are just where things happen to live today,
# and three of them have already moved once (hence the fallbacks in read_env,
# read_extra_env and tar1090_port). Some breakages are obvious: the dashboard
# 404s. Others are silent — a renamed status endpoint just makes the live data
# quietly disappear and the page falls back to install-time values that still
# look plausible. This turns the silent ones into a line of output.

PASS, WARN, FAIL = "ok", "warn", "FAIL"

# these mirror gd_exporter.py, which is what actually consumes them at runtime —
# duplicated rather than imported so each script stays independently removable
RRD_BASE = "/run/collectd/localhost"
AGG_STATUS_KEYS = {"adsb.lol": "adsblol", "adsb.fi": "adsbfi", "airplanes.live": "alive"}


def http_probe(url, timeout=5):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read()
    except Exception as e:
        return None, str(e).encode()


def run_check():
    env = read_env(ENV_PATH)
    port, t_port = web_port(), tar1090_port(env)
    results = []

    def probe(name, fn):
        try:
            state, detail = fn()
        except Exception as e:
            state, detail = FAIL, "raised %s: %s" % (type(e).__name__, e)
        results.append((name, state, detail))

    def c_env():
        if not env:
            return FAIL, "cannot read %s" % ENV_PATH
        if not env.get("FEEDER_ULTRAFEEDER_CONFIG"):
            return FAIL, "FEEDER_ULTRAFEEDER_CONFIG missing — aggregator list unreadable"
        return PASS, "%d keys" % len(env)

    def c_aggs():
        ups = [u for u in parse_uplinks(env.get("FEEDER_ULTRAFEEDER_CONFIG", ""))
               if u["name"] != "GLASSDECK"]
        if not ups:
            return FAIL, "no aggregators parsed — the config format may have changed"
        return PASS, ", ".join(u["name"] for u in ups)

    def c_extra():
        cur = read_extra_env()
        where = "config.json" if os.path.exists(CONFIG_JSON) else ".env"
        if cur is None:
            return FAIL, "unreadable — join/leave/rotate would fail"
        return PASS, "%s, %d chars" % (where, len(cur))

    def c_webapi():
        st, body = http_probe("http://127.0.0.1:%s/api/base_info" % port)
        if st != 200:
            return FAIL, "GET /api/base_info -> %s" % (st or body.decode()[:60])
        try:
            d = json.loads(body)
        except ValueError:
            return FAIL, "base_info is not JSON any more"
        missing = [k for k in ("name", "lat", "lon", "alt") if k not in d]
        return (WARN, "missing %s" % missing) if missing else (PASS, "name/lat/lon/alt present")

    def c_aggstatus():
        good = []
        for name, key in AGG_STATUS_KEYS.items():
            st, body = http_probe("http://127.0.0.1:%s/api/status/%s" % (port, key))
            if st == 200 and b'"beast"' in body:
                good.append(name)
        if not good:
            return FAIL, "no /api/status/<agg> responded — live feed health is dark"
        if len(good) < len(AGG_STATUS_KEYS):
            return WARN, "only %s responded" % ", ".join(good)
        return PASS, "%d aggregators reporting" % len(good)

    def c_webroot():
        if not os.path.isdir(RUN_WEBROOT):
            return FAIL, "%s does not exist — nothing is served" % RUN_WEBROOT
        if not os.path.exists(os.path.join(RUN_WEBROOT, "glassdeck.html")):
            return WARN, "webroot exists but the page is not in it (self-heal runs each minute)"
        return PASS, RUN_WEBROOT

    def c_served():
        url = "http://127.0.0.1:%s/chunks/glassdeck.html" % t_port
        st, body = http_probe(url)
        if st != 200:
            return FAIL, "GET :%s/chunks/glassdeck.html -> %s" % (t_port, st or "no answer")
        return PASS, "%d KB on :%s" % (len(body) // 1024, t_port)

    def c_exporter():
        p = os.path.join(RUN_WEBROOT, "gd-data", "system.json")
        try:
            d = json.load(open(p))
        except (OSError, ValueError) as e:
            return FAIL, "no readable system.json (%s)" % type(e).__name__
        age = int(time.time() - d.get("ts", 0))
        if age > 180:
            return FAIL, "stale by %ds — is the exporter cron running?" % age
        bits = []
        if not d.get("sharing", {}).get("aggs"):
            bits.append("sharing.aggs empty (Data sharing card falls back to install-time)")
        if not d.get("uplinks"):
            bits.append("uplinks empty")
        return (WARN, "; ".join(bits)) if bits else (PASS, "%ds old, complete" % age)

    def c_rrd():
        rrd = f"{RRD_BASE}/dump1090-localhost/dump1090_messages-local_accepted.rrd"
        out = subprocess.run(["docker", "exec", CONTAINER, "rrdtool", "xport", "--json",
                              "-s", "-1h", "--step", "300",
                              f"DEF:a={rrd}:value:AVERAGE", "XPORT:a:v"],
                             capture_output=True, text=True, timeout=30)
        if out.returncode != 0:
            return FAIL, "rrdtool in '%s' failed — history graphs will be empty" % CONTAINER
        return PASS, "archives readable in '%s'" % CONTAINER

    def c_cron():
        tab = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
        ours = [l for l in tab.splitlines() if "glassdeck" in l or "gd_exporter" in l]
        if len(ours) < 3:
            return FAIL, "%d of 3 required cron lines present — re-run the installer" % len(ours)
        return PASS, "%d lines" % len(ours)

    def c_identity():
        if not os.path.exists(UUID_FILE):
            return WARN, "no network id yet (created on --join)"
        mode = os.stat(UUID_FILE).st_mode & 0o777
        if mode != 0o600:
            return WARN, "id file is mode %o — should be 600" % mode
        return PASS, "present, mode 600"

    def c_network():
        cur = read_extra_env()
        if cur is None:
            # Do NOT say "not joined" here: c_extra already FAILs on this, and
            # the old wording sent the user to --join, which is the one command
            # that rewrites the box it cannot read.
            return FAIL, "extra-env unreadable — join state unknown, do not run --join"
        if GLASSDECK_HOST not in cur:
            return WARN, "not feeding the GLASSDECK network (join with --join)"
        want = glassdeck_connector()
        if want not in cur:
            return WARN, "connector present but does not carry this feeder's id — re-run --join"
        return PASS, "joined, connector carries this feeder's id"

    def c_hub():
        cur = read_extra_env()
        if cur is None:
            return WARN, "cannot tell whether this feeder is joined (see extra-env)"
        if GLASSDECK_HOST not in cur:
            return PASS, "not joined — nothing to reach"
        st, body = http_probe("%s/status?uuid=%s" % (GLASSDECK_API, feeder_uuid()), timeout=10)
        if st != 200:
            return WARN, "network unreachable (%s) — local feeding is unaffected" % (st or "timeout")
        d = json.loads(body)
        if not d.get("known"):
            return WARN, "the network does not recognise this id yet"
        if d.get("rotated_away"):
            return FAIL, "this id was rotated away — run --rotate to re-sync"
        return PASS, "recognised, eligible=%s" % d.get("eligible")

    for name, fn in [
        ("feeder config (.env)", c_env),
        ("aggregator list", c_aggs),
        ("expert extra-env", c_extra),
        ("adsb.im web api", c_webapi),
        ("aggregator status api", c_aggstatus),
        ("tar1090 webroot", c_webroot),
        ("dashboard served", c_served),
        ("exporter output", c_exporter),
        ("rrd archives", c_rrd),
        ("cron lines", c_cron),
        ("network id", c_identity),
        ("network join", c_network),
        ("network reachable", c_hub),
    ]:
        probe(name, fn)

    ver = (env.get("AF_FEEDER_VERSION") or env.get("AF_FEEDER_INITIAL_VERSION")
           or "unknown image")
    print("\nGLASSDECK compatibility check — %s\n" % ver)
    for name, state, detail in results:
        print("  %-5s %-24s %s" % (state, name, detail))
    bad = [n for n, s, _ in results if s == FAIL]
    warn = [n for n, s, _ in results if s == WARN]
    print()
    if bad:
        print("%d BROKEN: %s" % (len(bad), ", ".join(bad)))
        print("Your feeding to other aggregators is unaffected — GLASSDECK sits beside it.")
    elif warn:
        print("%d to look at: %s" % (len(warn), ", ".join(warn)))
    else:
        print("All %d checks passed." % len(results))
    return 1 if bad else 0


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
    if "--check" in sys.argv:
        sys.exit(run_check())
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
    # A plain install/update must survive an unreadable extra-env box: it only
    # needs it to label the Uplinks card, so say so and carry on rather than
    # dying halfway through a dashboard refresh.
    extra = read_extra_env()
    if extra is None:
        print("  warning: could not read the Expert extra-env box — the Uplinks card will"
              " show GLASSDECK as not joined (run --check)")
        extra = ""
    uplinks = parse_uplinks(env.get("FEEDER_ULTRAFEEDER_CONFIG", ""), joined=GLASSDECK_HOST in extra)
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
